"""Bash: a *persistent* shell session, not a series of one-shot subprocesses.

`cd`, exported variables, activated virtualenvs and shell functions all survive
between tool calls, which is what makes "bash is all you need" actually true.
Each command is bracketed by a sentinel so we can recover its exit status and
the resulting working directory.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base import Preview, Tool, ToolContext, ToolResult, truncate_output

_SENTINEL = f"__OC_DONE_{uuid.uuid4().hex[:12]}__"
_SENTINEL_RE = re.compile(rf"^{re.escape(_SENTINEL)} (-?\d+) (.*)$")

# Commands that are refused outright, no matter what the user approves.
HARD_BLOCKED = [
    (r"\brm\s+(-[a-zA-Z]*\s+)*(/|/\*|~|~/\*|\$HOME)\s*$", "recursive delete of / or $HOME"),
    (r"\bmkfs(\.\w+)?\b", "filesystem format"),
    (r"\bdd\b[^|]*\bof=/dev/(disk|sd|nvme|rdisk)", "raw write to a block device"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork bomb"),
    (r">\s*/dev/(sd[a-z]|nvme\d|disk\d)", "overwrite of a block device"),
    (r"\bshutdown\b|\breboot\b|\bhalt\b|\bpoweroff\b", "host power state change"),
]

# Recognised as mutating even though they are not blocked; these always prompt.
_DANGEROUS_HINTS = [
    (r"\bsudo\b|\bdoas\b", "runs with elevated privileges"),
    (r"\brm\s+-[a-zA-Z]*r", "recursive delete"),
    (r"\bgit\s+(push|reset\s+--hard|clean\s+-[a-zA-Z]*f)", "destructive git operation"),
    (r"\bcurl\b[^|]*\|\s*(ba)?sh", "pipes a remote script into a shell"),
    (r"\bwget\b[^|]*\|\s*(ba)?sh", "pipes a remote script into a shell"),
    (r"\bchmod\s+(-R\s+)?777", "world-writable permissions"),
    (r"\bkillall\b|\bpkill\b", "kills processes by name"),
    (r"\bnpm\s+publish|\bpip\s+upload|\btwine\s+upload", "publishes a package"),
    (r"\bdocker\s+(rm|rmi|system\s+prune)", "removes container resources"),
]


def classify_command(command: str) -> tuple[str, str]:
    """Return (verdict, reason) where verdict is blocked | dangerous | normal."""
    flat = " ".join(command.split())
    for pattern, reason in HARD_BLOCKED:
        if re.search(pattern, flat, re.IGNORECASE):
            return "blocked", reason
    for pattern, reason in _DANGEROUS_HINTS:
        if re.search(pattern, flat, re.IGNORECASE):
            return "dangerous", reason
    return "normal", ""


class PersistentShell:
    """A long-lived `bash` process driven over pipes."""

    def __init__(self, cwd: Path, env: dict[str, str] | None = None):
        self.cwd = Path(cwd)
        self.env = {**os.environ, **(env or {})}
        self.env.setdefault("TERM", "dumb")
        self.env["PS1"] = ""
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self.last_cwd = str(self.cwd)

    async def start(self) -> None:
        if self._process and self._process.returncode is None:
            return
        self._process = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-s",
            cwd=self.last_cwd if Path(self.last_cwd).is_dir() else str(self.cwd),
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        # Merge stderr, disable pagers and job-control chatter.
        await self._write("exec 2>&1\nset +m\nexport PAGER=cat GIT_PAGER=cat\n")

    async def _write(self, text: str) -> None:
        assert self._process and self._process.stdin
        self._process.stdin.write(text.encode())
        await self._process.stdin.drain()

    async def run(
        self,
        command: str,
        timeout: float = 240.0,
        on_output: Callable[[str], None] | None = None,
    ) -> tuple[int, str, bool]:
        """Execute `command`. Returns (exit_code, output, timed_out)."""
        async with self._lock:
            await self.start()
            assert self._process and self._process.stdout

            # `</dev/null` stops a stray interactive command from eating our
            # sentinel; a heredoc inside the command still wins over it.
            payload = (
                "{ " + command + "\n} </dev/null\n"
                f'printf "\\n{_SENTINEL} %s %s\\n" "$?" "$PWD"\n'
            )
            await self._write(payload)

            lines: list[str] = []
            exit_code = -1
            timed_out = False
            deadline = time.monotonic() + timeout

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    raw = await asyncio.wait_for(
                        self._process.stdout.readline(), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    timed_out = True
                    break

                if not raw:  # shell died
                    timed_out = False
                    exit_code = -1
                    lines.append("(shell terminated unexpectedly)")
                    self._process = None
                    break

                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                match = _SENTINEL_RE.match(line)
                if match:
                    exit_code = int(match.group(1))
                    self.last_cwd = match.group(2) or self.last_cwd
                    break

                lines.append(line)
                if on_output:
                    try:
                        on_output(line)
                    except Exception:
                        pass

            if timed_out:
                await self._interrupt()

            output = "\n".join(lines)
            return exit_code, output, timed_out

    async def _interrupt(self) -> None:
        """A timed-out command owns the pipe forever -- restart the shell."""
        proc = self._process
        self._process = None
        if not proc or proc.returncode is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
            await asyncio.wait_for(proc.wait(), timeout=2)
        except (ProcessLookupError, PermissionError, asyncio.TimeoutError):
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        except Exception:
            pass

    async def aclose(self) -> None:
        proc = self._process
        self._process = None
        if not proc or proc.returncode is not None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            await asyncio.wait_for(proc.wait(), timeout=2)
        except Exception:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass


class BashTool(Tool):
    name = "bash"
    kind = "bash"
    read_only = False
    description = (
        "Run a bash command in a persistent shell. State (cwd, exported vars, "
        "activated virtualenvs) carries over between calls. Use this for builds, "
        "tests, package managers and any CLI. Prefer the dedicated read_file / "
        "grep / glob tools for reading and searching -- they are cheaper. "
        "Never use interactive commands that wait for input."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The bash command to run."},
            "description": {
                "type": "string",
                "description": "5-10 word description of what this does, shown to the user.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 240, max 1800).",
            },
        },
        "required": ["command"],
    }

    def __init__(self, workdir: Path, config: Any):
        self.workdir = Path(workdir)
        self.config = config
        self._shell: PersistentShell | None = None

    def _get_shell(self) -> PersistentShell:
        if self._shell is None:
            self._shell = PersistentShell(self.workdir)
        return self._shell

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        command = str(args.get("command", ""))
        verdict, reason = classify_command(command)
        note = f"\n\n⚠️  {reason}" if verdict == "dangerous" else ""
        desc = args.get("description")
        title = f"Run: {desc}" if desc else "Run shell command"
        return Preview(title=title, detail=f"$ {command}{note}", kind="bash")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        command = str(args.get("command", "")).strip()
        if not command:
            return ToolResult.fail("no command given")

        verdict, reason = classify_command(command)
        if verdict == "blocked":
            return ToolResult.fail(
                f"refused: {reason}. This command is blocked unconditionally; "
                "if you truly need it, the user must run it themselves."
            )

        timeout = float(args.get("timeout") or ctx.config.get("bash.timeout_sec", 240))
        timeout = max(1.0, min(timeout, 1800.0))
        max_chars = int(ctx.config.get("bash.max_output_chars", 30000))

        started = time.monotonic()
        if ctx.config.get("bash.persistent", True):
            shell = self._get_shell()
            shell.cwd = ctx.workdir
            code, output, timed_out = await shell.run(command, timeout=timeout)
        else:
            code, output, timed_out = await _run_once(command, ctx.workdir, timeout)
        elapsed = int((time.monotonic() - started) * 1000)

        body = truncate_output(output.strip(), max_chars)
        if timed_out:
            return ToolResult(
                ok=False,
                output=body,
                error=f"timed out after {timeout:.0f}s (the shell was restarted)",
                headline=f"timeout after {timeout:.0f}s",
                meta={"exit_code": None, "duration_ms": elapsed},
            )

        headline = args.get("description") or command.splitlines()[0][:60]
        if code == 0:
            return ToolResult(
                ok=True,
                output=body or "(no output)",
                headline=str(headline),
                meta={"exit_code": 0, "duration_ms": elapsed},
            )
        return ToolResult(
            ok=False,
            output=body,
            error=f"exit code {code}",
            headline=f"{headline} → exit {code}",
            meta={"exit_code": code, "duration_ms": elapsed},
        )

    async def aclose(self) -> None:
        if self._shell:
            await self._shell.aclose()
            self._shell = None


async def _run_once(command: str, cwd: Path, timeout: float) -> tuple[int, str, bool]:
    """Non-persistent fallback."""
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode("utf-8", errors="replace"), False
    except asyncio.TimeoutError:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
        return -1, "", True
