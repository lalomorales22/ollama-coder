"""Navigation and search: list_dir, glob, grep.

Prefers `ripgrep`/`fd` when present and falls back to pure Python, so the tools
behave identically on a bare machine.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .base import Preview, SandboxError, Tool, ToolContext, ToolResult

IGNORED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".next", ".nuxt",
    "target", ".gradle", ".idea", ".tox", "site-packages", ".terraform",
}

MAX_RESULTS = 200


async def _run(cmd: list[str], cwd: Path, timeout: float = 30.0) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return -1, "search timed out"
    except (OSError, FileNotFoundError) as exc:
        return -1, str(exc)
    text = stdout.decode("utf-8", errors="replace")
    if proc.returncode not in (0, 1):
        text = text or stderr.decode("utf-8", errors="replace")
    return proc.returncode if proc.returncode is not None else -1, text


class ListDirTool(Tool):
    name = "list_dir"
    kind = "read"
    read_only = True
    description = (
        "List a directory. Set depth>1 for a shallow tree. Noise directories "
        "(.git, node_modules, __pycache__, ...) are skipped automatically."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory, defaults to the project root."},
            "depth": {"type": "integer", "description": "Recursion depth, default 1, max 4."},
        },
    }

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        return Preview(title=f"List {args.get('path') or '.'}", detail="", kind="read")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            root = ctx.resolve(str(args.get("path") or "."), must_exist=True)
        except (SandboxError, FileNotFoundError) as exc:
            return ToolResult.fail(str(exc))
        if not root.is_dir():
            return ToolResult.fail(f"{ctx.display(root)} is not a directory")

        depth = max(1, min(int(args.get("depth") or 1), 4))
        lines: list[str] = []
        truncated = False

        def walk(directory: Path, level: int, prefix: str) -> None:
            nonlocal truncated
            if level > depth or truncated:
                return
            try:
                entries = sorted(
                    directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
                )
            except OSError:
                return
            for entry in entries:
                if len(lines) >= MAX_RESULTS:
                    truncated = True
                    return
                if entry.name in IGNORED_DIRS:
                    continue
                if entry.is_dir():
                    lines.append(f"{prefix}{entry.name}/")
                    walk(entry, level + 1, prefix + "  ")
                else:
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        size = 0
                    lines.append(f"{prefix}{entry.name}  ({_human(size)})")

        await asyncio.to_thread(walk, root, 1, "")
        body = "\n".join(lines) or "(empty)"
        if truncated:
            body += f"\n... truncated at {MAX_RESULTS} entries"
        return ToolResult.succeed(
            f"{ctx.display(root)}/\n{body}", headline=f"listed {ctx.display(root)}"
        )


class GlobTool(Tool):
    name = "glob"
    kind = "read"
    read_only = True
    description = (
        "Find files by glob pattern, newest first. Examples: '**/*.py', "
        "'src/**/*.ts', 'test_*.py'. Use this to locate files by name; use "
        "grep to search their contents."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern."},
            "path": {"type": "string", "description": "Directory to search in."},
            "limit": {"type": "integer", "description": f"Max results (default {MAX_RESULTS})."},
        },
        "required": ["pattern"],
    }

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        return Preview(title=f"Glob {args.get('pattern')}", detail="", kind="read")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        pattern = str(args.get("pattern", "")).strip()
        if not pattern:
            return ToolResult.fail("pattern is required")
        try:
            root = ctx.resolve(str(args.get("path") or "."), must_exist=True)
        except (SandboxError, FileNotFoundError) as exc:
            return ToolResult.fail(str(exc))

        limit = max(1, min(int(args.get("limit") or MAX_RESULTS), 1000))
        matches = await asyncio.to_thread(_glob_files, root, pattern, limit)

        if not matches:
            return ToolResult.succeed(
                f"no files match {pattern!r} under {ctx.display(root)}",
                headline=f"glob {pattern}: 0",
            )

        rendered = "\n".join(ctx.display(p) for p in matches)
        more = "" if len(matches) < limit else f"\n... (capped at {limit})"
        return ToolResult.succeed(
            f"{len(matches)} file(s) matching {pattern!r}:\n{rendered}{more}",
            headline=f"glob {pattern}: {len(matches)}",
        )


def _glob_files(root: Path, pattern: str, limit: int) -> list[Path]:
    results: list[Path] = []
    try:
        iterator: Iterable[Path] = root.glob(pattern)
    except (ValueError, OSError):
        return []
    for path in iterator:
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if path.is_file():
            results.append(path)
        if len(results) > limit * 4:
            break
    results.sort(key=lambda p: _mtime(p), reverse=True)
    return results[:limit]


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


class GrepTool(Tool):
    name = "grep"
    kind = "read"
    read_only = True
    description = (
        "Search file contents with a regular expression. Returns file:line:text. "
        "This is the fastest way to find where something is defined or used."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regular expression."},
            "path": {"type": "string", "description": "File or directory to search."},
            "glob": {"type": "string", "description": "Only search files matching this glob, e.g. '*.py'."},
            "context": {"type": "integer", "description": "Lines of context around each match."},
            "case_insensitive": {"type": "boolean"},
            "files_only": {"type": "boolean", "description": "List matching file names only."},
            "limit": {"type": "integer", "description": "Max matching lines (default 200)."},
        },
        "required": ["pattern"],
    }

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        return Preview(title=f"Grep {args.get('pattern')}", detail="", kind="read")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        pattern = str(args.get("pattern", ""))
        if not pattern:
            return ToolResult.fail("pattern is required")
        try:
            re.compile(pattern)
        except re.error as exc:
            return ToolResult.fail(f"invalid regex: {exc}")

        try:
            target = ctx.resolve(str(args.get("path") or "."), must_exist=True)
        except (SandboxError, FileNotFoundError) as exc:
            return ToolResult.fail(str(exc))

        limit = max(1, min(int(args.get("limit") or MAX_RESULTS), 2000))
        context_lines = max(0, min(int(args.get("context") or 0), 10))
        insensitive = bool(args.get("case_insensitive"))
        files_only = bool(args.get("files_only"))
        file_glob = args.get("glob")

        if shutil.which("rg"):
            cmd = ["rg", "--line-number", "--no-heading", "--color", "never", "--max-count", "50"]
            if insensitive:
                cmd.append("-i")
            if files_only:
                cmd.append("--files-with-matches")
            if context_lines:
                cmd += ["-C", str(context_lines)]
            if file_glob:
                cmd += ["-g", str(file_glob)]
            cmd += ["--", pattern, str(target)]
            code, output = await _run(cmd, ctx.workdir)
            if code in (0, 1):
                return self._format(output, pattern, limit, ctx)

        output = await asyncio.to_thread(
            _python_grep, target, pattern, file_glob, insensitive, context_lines, files_only, limit
        )
        return self._format(output, pattern, limit, ctx)

    def _format(self, output: str, pattern: str, limit: int, ctx: ToolContext) -> ToolResult:
        lines = [ln for ln in output.splitlines() if ln.strip()]
        cleaned = []
        root = str(ctx.workdir) + os.sep
        for line in lines[:limit]:
            cleaned.append(line[len(root) :] if line.startswith(root) else line)
        if not cleaned:
            return ToolResult.succeed(f"no matches for {pattern!r}", headline=f"grep {pattern}: 0")
        suffix = f"\n... ({len(lines) - limit} more matches)" if len(lines) > limit else ""
        return ToolResult.succeed(
            f"{len(lines)} match(es) for {pattern!r}:\n" + "\n".join(cleaned) + suffix,
            headline=f"grep {pattern}: {len(lines)}",
        )


def _python_grep(
    target: Path,
    pattern: str,
    file_glob: str | None,
    insensitive: bool,
    context_lines: int,
    files_only: bool,
    limit: int,
) -> str:
    flags = re.IGNORECASE if insensitive else 0
    regex = re.compile(pattern, flags)
    out: list[str] = []

    paths: Iterable[Path]
    if target.is_file():
        paths = [target]
    else:
        paths = (p for p in target.rglob("*") if p.is_file())

    for path in paths:
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if file_glob and not fnmatch.fnmatch(path.name, file_glob):
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\x00" in content[:1024]:
            continue

        lines = content.splitlines()
        for index, line in enumerate(lines):
            if not regex.search(line):
                continue
            if files_only:
                out.append(str(path))
                break
            start = max(0, index - context_lines)
            end = min(len(lines), index + context_lines + 1)
            for i in range(start, end):
                sep = ":" if i == index else "-"
                out.append(f"{path}{sep}{i + 1}{sep}{lines[i]}")
            if len(out) >= limit * 2:
                return "\n".join(out)
    return "\n".join(out)


def _human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}GB"
