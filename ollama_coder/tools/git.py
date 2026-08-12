"""Git tools.

Split three ways so the permission layer can be precise:

  * ``git_read``  -- inspection only, safe to auto-approve;
  * ``git_commit``-- stages and commits, shows the diff for approval;
  * ``git_run``   -- escape hatch for everything else, always reviewed.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from pathlib import Path
from typing import Any

from .base import Preview, Tool, ToolContext, ToolResult, truncate_output

# Sub-commands that cannot change the repository.
READ_ONLY_SUBCOMMANDS = {
    "status", "diff", "log", "show", "blame", "branch", "tag", "remote",
    "ls-files", "ls-tree", "rev-parse", "describe", "shortlog", "config",
    "reflog", "stash", "whatchanged", "cat-file", "diff-tree", "count-objects",
    "grep", "help", "var", "check-ignore", "merge-base", "name-rev",
}

# Never run these without an explicit human decision, whatever the settings say.
DESTRUCTIVE = {
    ("push", "--force"), ("push", "-f"), ("reset", "--hard"),
    ("clean", "-f"), ("clean", "-fd"), ("clean", "-xdf"),
    ("branch", "-D"), ("filter-branch", ""), ("gc", "--prune"),
}


async def git(args: list[str], cwd: Path, timeout: float = 60.0) -> tuple[int, str]:
    """Run a git command, returning (exit_code, combined_output)."""
    if not shutil.which("git"):
        return 127, "git is not installed or not on PATH"
    # Inherit the real environment (ssh agent, credential helpers) but never
    # let git open a pager or block on an interactive credential prompt.
    env = {**os.environ, "GIT_PAGER": "cat", "PAGER": "cat", "GIT_TERMINAL_PROMPT": "0"}
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return -1, f"git timed out after {timeout:.0f}s"
    except OSError as exc:
        return -1, str(exc)
    return proc.returncode or 0, stdout.decode("utf-8", errors="replace")


async def repo_summary(cwd: Path) -> dict[str, Any]:
    """Lightweight repo state for the status bar."""
    code, _ = await git(["rev-parse", "--is-inside-work-tree"], cwd, timeout=5)
    if code != 0:
        return {"is_repo": False}
    # `branch --show-current` works before the first commit, unlike rev-parse HEAD
    _, branch = await git(["branch", "--show-current"], cwd, timeout=5)
    if not branch.strip():
        branch = "(no commits)"
    _, porcelain = await git(["status", "--porcelain"], cwd, timeout=5)
    lines = [ln for ln in porcelain.splitlines() if ln.strip()]
    staged = sum(1 for ln in lines if ln[:1] not in (" ", "?"))
    unstaged = sum(1 for ln in lines if ln[1:2] not in (" ",))
    untracked = sum(1 for ln in lines if ln.startswith("??"))
    return {
        "is_repo": True,
        "branch": branch.strip(),
        "dirty": bool(lines),
        "staged": staged,
        "unstaged": unstaged - untracked,
        "untracked": untracked,
    }


class GitReadTool(Tool):
    name = "git_read"
    kind = "read"
    read_only = True
    description = (
        "Inspect the git repository: status, diff, log, show, branch, blame. "
        "Always use this before committing so you know exactly what changed."
    )
    parameters = {
        "type": "object",
        "properties": {
            "op": {
                "type": "string",
                "enum": ["status", "diff", "staged_diff", "log", "show", "branches", "blame", "stashes", "remotes"],
                "description": "What to inspect.",
            },
            "target": {
                "type": "string",
                "description": "Path, ref or commit, depending on op (e.g. a file for blame, a sha for show).",
            },
            "limit": {"type": "integer", "description": "Entries for log (default 15)."},
        },
        "required": ["op"],
    }

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        return Preview(title=f"git {args.get('op')}", detail="", kind="read")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        op = str(args.get("op", "status"))
        target = str(args.get("target") or "").strip()
        limit = max(1, min(int(args.get("limit") or 15), 200))

        commands: dict[str, list[str]] = {
            "status": ["status", "--short", "--branch"],
            "diff": ["diff", "--stat", "-p"] + ([target] if target else []),
            "staged_diff": ["diff", "--cached", "--stat", "-p"] + ([target] if target else []),
            "log": ["log", f"-{limit}", "--oneline", "--decorate", "--graph"]
                   + ([target] if target else []),
            "show": ["show", "--stat", "-p", target or "HEAD"],
            "branches": ["branch", "-vv", "--all"],
            "blame": ["blame", "--date=short", "-L", "1,120", target] if target else [],
            "stashes": ["stash", "list"],
            "remotes": ["remote", "-v"],
        }

        if op not in commands:
            return ToolResult.fail(f"unknown op {op!r}")
        if op == "blame" and not target:
            return ToolResult.fail("blame needs a target file")

        code, output = await git(commands[op], ctx.workdir)
        if code == 127:
            return ToolResult.fail(output)
        if code != 0 and "not a git repository" in output.lower():
            return ToolResult.fail("not a git repository")

        body = truncate_output(output.strip(), 20000) or "(clean)"
        return ToolResult(
            ok=code == 0,
            output=body,
            error=None if code == 0 else f"git exited {code}",
            headline=f"git {op}",
        )


class GitCommitTool(Tool):
    name = "git_commit"
    kind = "git"
    description = (
        "Stage files and create a commit. Pass paths to stage specific files, "
        "or all=true to stage every tracked modification. Write a message that "
        "explains why the change was made, not just what changed."
    )
    parameters = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Commit message. First line <= 72 chars."},
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Files to stage before committing.",
            },
            "all": {"type": "boolean", "description": "Stage all tracked modifications."},
            "amend": {"type": "boolean", "description": "Amend the previous commit."},
        },
        "required": ["message"],
    }

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        message = str(args.get("message", ""))
        paths = args.get("paths") or []
        scope = "all tracked changes" if args.get("all") else ", ".join(map(str, paths)) or "already-staged changes"
        title = "Amend commit" if args.get("amend") else "Create commit"
        return Preview(
            title=f"{title}: {message.splitlines()[0][:60] if message else ''}",
            detail=f"staging: {scope}\n\n{message}",
            kind="git",
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        message = str(args.get("message", "")).strip()
        if not message:
            return ToolResult.fail("commit message is required")

        code, _ = await git(["rev-parse", "--is-inside-work-tree"], ctx.workdir, timeout=10)
        if code != 0:
            return ToolResult.fail("not a git repository")

        paths = [str(p) for p in (args.get("paths") or [])]
        if paths:
            for path in paths:
                try:
                    ctx.resolve(path)
                except Exception as exc:
                    return ToolResult.fail(str(exc))
            code, output = await git(["add", "--"] + paths, ctx.workdir)
            if code != 0:
                return ToolResult.fail(f"git add failed: {output.strip()}")
        elif args.get("all"):
            code, output = await git(["add", "-u"], ctx.workdir)
            if code != 0:
                return ToolResult.fail(f"git add failed: {output.strip()}")

        _, staged = await git(["diff", "--cached", "--stat"], ctx.workdir)
        if not staged.strip() and not args.get("amend"):
            return ToolResult.fail(
                "nothing staged. Pass paths=[...] or all=true, or stage with git_run."
            )

        cmd = ["commit", "-m", message]
        if args.get("amend"):
            cmd.append("--amend")
        code, output = await git(cmd, ctx.workdir)
        if code != 0:
            return ToolResult.fail(f"commit failed: {output.strip()}")

        _, sha = await git(["rev-parse", "--short", "HEAD"], ctx.workdir)
        return ToolResult.succeed(
            f"committed {sha.strip()}\n{staged.strip()}\n{output.strip()}",
            headline=f"commit {sha.strip()}: {message.splitlines()[0][:50]}",
        )


class GitRunTool(Tool):
    name = "git_run"
    kind = "git"
    description = (
        "Run any other git command by arguments, e.g. 'checkout -b feature/x', "
        "'stash push -m wip', 'revert HEAD'. Read-only commands run directly; "
        "anything that changes the repository is shown to the user first."
    )
    parameters = {
        "type": "object",
        "properties": {
            "args": {"type": "string", "description": "Arguments after 'git', e.g. 'checkout -b fix/bug'."},
        },
        "required": ["args"],
    }

    @staticmethod
    def is_read_only(raw_args: str) -> bool:
        try:
            parts = shlex.split(raw_args)
        except ValueError:
            return False
        if not parts:
            return False
        sub = parts[0]
        if sub == "stash" and len(parts) > 1 and parts[1] != "list":
            return False
        if sub == "config" and any(p in ("--unset", "--replace-all") for p in parts):
            return False
        return sub in READ_ONLY_SUBCOMMANDS

    @staticmethod
    def is_destructive(raw_args: str) -> str | None:
        try:
            parts = shlex.split(raw_args)
        except ValueError:
            return "unparseable arguments"
        if not parts:
            return None
        joined = " ".join(parts)
        for sub, flag in DESTRUCTIVE:
            if parts[0] == sub and (not flag or flag in parts):
                return f"git {sub} {flag}".strip()
        if "--force" in parts or "-f" in parts:
            return f"forced git {parts[0]}"
        if joined.startswith("push"):
            return "pushes to a remote"
        return None

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        raw = str(args.get("args", ""))
        warning = self.is_destructive(raw)
        detail = f"$ git {raw}"
        if warning:
            detail += f"\n\n⚠️  {warning}"
        return Preview(title=f"git {raw[:60]}", detail=detail, kind="git")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raw = str(args.get("args", "")).strip()
        if not raw:
            return ToolResult.fail("args is required")
        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            return ToolResult.fail(f"cannot parse arguments: {exc}")

        code, output = await git(parts, ctx.workdir, timeout=180)
        body = truncate_output(output.strip(), 20000)
        if code == 0:
            return ToolResult.succeed(body or "(ok)", headline=f"git {parts[0]}")
        return ToolResult(
            ok=False, output=body, error=f"git exited {code}", headline=f"git {parts[0]} failed"
        )
