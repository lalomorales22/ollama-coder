"""File tools: read, write, edit, multi_edit.

Two habits are enforced here because they are what separates an agent that
edits code from one that corrupts it:

  * every mutation produces a unified diff *before* it is applied, so the
    approval dialog shows exactly what will change;
  * a file must be read before it can be edited, so the model never rewrites
    content it has not seen.
"""

from __future__ import annotations

import asyncio
import difflib
from pathlib import Path
from typing import Any

from .base import Preview, SandboxError, Tool, ToolContext, ToolResult, truncate_output

MAX_READ_BYTES = 400_000
DEFAULT_READ_LIMIT = 2000

BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".pdf", ".zip",
    ".gz", ".tar", ".bz2", ".xz", ".7z", ".exe", ".dll", ".so", ".dylib",
    ".pyc", ".woff", ".woff2", ".ttf", ".mp4", ".mp3", ".wav", ".mov",
}


def unified_diff(before: str, after: str, path: str) -> str:
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
    )
    return "".join(diff)


def diff_stats(before: str, after: str) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.ndiff(before.splitlines(), after.splitlines()):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


class ReadFileTool(Tool):
    name = "read_file"
    kind = "read"
    read_only = True
    description = (
        "Read a text file. Returns numbered lines so you can refer to them "
        "precisely. Use offset/limit for large files. You MUST read a file "
        "before editing it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path, absolute or relative to the project."},
            "offset": {"type": "integer", "description": "1-indexed first line to read."},
            "limit": {"type": "integer", "description": f"Max lines to return (default {DEFAULT_READ_LIMIT})."},
        },
        "required": ["path"],
    }

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        return Preview(title=f"Read {args.get('path')}", detail="", kind="read")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            path = ctx.resolve(str(args.get("path", "")), must_exist=True)
        except (SandboxError, FileNotFoundError) as exc:
            return ToolResult.fail(str(exc))

        if path.is_dir():
            return ToolResult.fail(f"{ctx.display(path)} is a directory; use list_dir")
        if path.suffix.lower() in BINARY_SUFFIXES:
            size = path.stat().st_size
            return ToolResult.fail(
                f"{ctx.display(path)} looks binary ({size:,} bytes). "
                "Images can be attached to the conversation instead."
            )

        try:
            size = path.stat().st_size
            if size > MAX_READ_BYTES:
                return ToolResult.fail(
                    f"{ctx.display(path)} is {size:,} bytes -- too large to read whole. "
                    "Use offset/limit, or grep for what you need."
                )
            content = await asyncio.to_thread(_read_text, path)
        except OSError as exc:
            return ToolResult.fail(str(exc))

        lines = content.splitlines()
        total = len(lines)
        offset = max(1, int(args.get("offset") or 1))
        limit = int(args.get("limit") or DEFAULT_READ_LIMIT)
        window = lines[offset - 1 : offset - 1 + limit]

        width = len(str(offset + len(window)))
        numbered = "\n".join(
            f"{str(offset + i).rjust(width)}\t{line}" for i, line in enumerate(window)
        )

        ctx.read_files.add(str(path))

        header = f"{ctx.display(path)} ({total} lines)"
        if offset > 1 or offset - 1 + limit < total:
            shown_to = min(total, offset - 1 + len(window))
            header += f" -- showing {offset}-{shown_to}"
        body = numbered or "(empty file)"
        return ToolResult.succeed(
            f"{header}\n{body}",
            headline=f"read {ctx.display(path)}",
            lines=total,
        )


class WriteFileTool(Tool):
    name = "write_file"
    kind = "write"
    description = (
        "Create a new file or replace an existing one entirely. For changing "
        "part of an existing file prefer edit_file -- it is safer and cheaper."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string", "description": "Full file contents."},
        },
        "required": ["path", "content"],
    }

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        raw = str(args.get("path", ""))
        content = str(args.get("content", ""))
        try:
            path = ctx.resolve(raw)
        except SandboxError as exc:
            return Preview(title="Blocked write", detail=str(exc), kind="write")

        exists = path.exists()
        before = _read_text(path) if exists and path.is_file() else ""
        diff = unified_diff(before, content, ctx.display(path))
        verb = "Overwrite" if exists else "Create"
        added, removed = diff_stats(before, content)
        return Preview(
            title=f"{verb} {ctx.display(path)} (+{added} -{removed})",
            detail=f"{len(content.splitlines())} lines",
            kind="write",
            diff=diff or f"(new empty file {ctx.display(path)})",
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            path = ctx.resolve(str(args.get("path", "")))
        except SandboxError as exc:
            return ToolResult.fail(str(exc))

        content = str(args.get("content", ""))
        existed = path.exists()
        before = ""
        if existed:
            if path.is_dir():
                return ToolResult.fail(f"{ctx.display(path)} is a directory")
            before = await asyncio.to_thread(_read_text, path)

        if ctx.checkpoints is not None:
            await ctx.checkpoints.snapshot(path)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_text, content, "utf-8")
        except OSError as exc:
            return ToolResult.fail(str(exc))

        ctx.read_files.add(str(path))
        added, removed = diff_stats(before, content)
        verb = "overwrote" if existed else "created"
        return ToolResult.succeed(
            f"{verb} {ctx.display(path)} ({len(content.splitlines())} lines, +{added} -{removed})",
            headline=f"{verb} {ctx.display(path)} (+{added} -{removed})",
            diff=unified_diff(before, content, ctx.display(path)),
        )


def _apply_replacement(
    content: str, old: str, new: str, replace_all: bool
) -> tuple[str | None, str | None]:
    """Returns (new_content, error)."""
    if old == new:
        return None, "old_str and new_str are identical"
    count = content.count(old)
    if count == 0:
        return None, (
            "old_str not found. It must match the file byte-for-byte, "
            "including indentation. Re-read the file and copy the exact text."
        )
    if count > 1 and not replace_all:
        return None, (
            f"old_str appears {count} times -- ambiguous. Include more "
            "surrounding context to make it unique, or pass replace_all=true."
        )
    return (content.replace(old, new) if replace_all else content.replace(old, new, 1)), None


class EditFileTool(Tool):
    name = "edit_file"
    kind = "edit"
    description = (
        "Replace an exact string in a file. old_str must match the file "
        "byte-for-byte (indentation included) and be unique unless replace_all "
        "is set. Read the file first."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_str": {"type": "string", "description": "Exact text to find."},
            "new_str": {"type": "string", "description": "Replacement text."},
            "replace_all": {"type": "boolean", "description": "Replace every occurrence."},
        },
        "required": ["path", "old_str", "new_str"],
    }

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        try:
            path = ctx.resolve(str(args.get("path", "")))
        except SandboxError as exc:
            return Preview(title="Blocked edit", detail=str(exc), kind="edit")
        if not path.is_file():
            return Preview(title=f"Edit {ctx.display(path)}", detail="file does not exist", kind="edit")

        before = _read_text(path)
        after, error = _apply_replacement(
            before, str(args.get("old_str", "")), str(args.get("new_str", "")),
            bool(args.get("replace_all")),
        )
        if error or after is None:
            return Preview(title=f"Edit {ctx.display(path)}", detail=error or "no change", kind="edit")
        added, removed = diff_stats(before, after)
        return Preview(
            title=f"Edit {ctx.display(path)} (+{added} -{removed})",
            detail="",
            kind="edit",
            diff=unified_diff(before, after, ctx.display(path)),
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            path = ctx.resolve(str(args.get("path", "")), must_exist=True)
        except (SandboxError, FileNotFoundError) as exc:
            return ToolResult.fail(str(exc))

        if str(path) not in ctx.read_files:
            return ToolResult.fail(
                f"read {ctx.display(path)} before editing it -- call read_file first"
            )

        before = await asyncio.to_thread(_read_text, path)
        after, error = _apply_replacement(
            before, str(args.get("old_str", "")), str(args.get("new_str", "")),
            bool(args.get("replace_all")),
        )
        if error or after is None:
            return ToolResult.fail(error or "no change produced")

        if ctx.checkpoints is not None:
            await ctx.checkpoints.snapshot(path)

        try:
            await asyncio.to_thread(path.write_text, after, "utf-8")
        except OSError as exc:
            return ToolResult.fail(str(exc))

        added, removed = diff_stats(before, after)
        diff = unified_diff(before, after, ctx.display(path))
        return ToolResult.succeed(
            f"edited {ctx.display(path)} (+{added} -{removed})\n{truncate_output(diff, 4000)}",
            headline=f"edited {ctx.display(path)} (+{added} -{removed})",
            diff=diff,
        )


class MultiEditTool(Tool):
    name = "multi_edit"
    kind = "edit"
    description = (
        "Apply several edits atomically -- either all succeed or the files are "
        "left untouched. Use this when one logical change spans multiple spots "
        "or files."
    )
    parameters = {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "description": "Edits applied in order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_str": {"type": "string"},
                        "new_str": {"type": "string"},
                        "replace_all": {"type": "boolean"},
                    },
                    "required": ["path", "old_str", "new_str"],
                },
            }
        },
        "required": ["edits"],
    }

    def _plan(
        self, args: dict[str, Any], ctx: ToolContext
    ) -> tuple[dict[Path, str], dict[Path, str], str | None]:
        """Compute final contents for every touched file without writing."""
        originals: dict[Path, str] = {}
        working: dict[Path, str] = {}

        for index, edit in enumerate(args.get("edits") or [], start=1):
            if not isinstance(edit, dict):
                return {}, {}, f"edit #{index} is not an object"
            try:
                path = ctx.resolve(str(edit.get("path", "")), must_exist=True)
            except (SandboxError, FileNotFoundError) as exc:
                return {}, {}, f"edit #{index}: {exc}"

            if path not in working:
                content = _read_text(path)
                originals[path] = content
                working[path] = content

            after, error = _apply_replacement(
                working[path], str(edit.get("old_str", "")), str(edit.get("new_str", "")),
                bool(edit.get("replace_all")),
            )
            if error or after is None:
                return {}, {}, f"edit #{index} ({ctx.display(path)}): {error}"
            working[path] = after

        if not working:
            return {}, {}, "no edits supplied"
        return originals, working, None

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        originals, working, error = self._plan(args, ctx)
        if error:
            return Preview(title="Multi-edit", detail=error, kind="edit")
        diffs = [
            unified_diff(originals[path], content, ctx.display(path))
            for path, content in working.items()
        ]
        total_added = total_removed = 0
        for path, content in working.items():
            a, r = diff_stats(originals[path], content)
            total_added += a
            total_removed += r
        return Preview(
            title=f"Edit {len(working)} file(s) (+{total_added} -{total_removed})",
            detail="\n".join(ctx.display(p) for p in working),
            kind="edit",
            diff="\n".join(diffs),
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        originals, working, error = await asyncio.to_thread(self._plan, args, ctx)
        if error:
            return ToolResult.fail(error)

        unread = [ctx.display(p) for p in working if str(p) not in ctx.read_files]
        if unread:
            return ToolResult.fail(
                "read these files before editing them: " + ", ".join(unread)
            )

        if ctx.checkpoints is not None:
            for path in working:
                await ctx.checkpoints.snapshot(path)

        written: list[Path] = []
        try:
            for path, content in working.items():
                await asyncio.to_thread(path.write_text, content, "utf-8")
                written.append(path)
        except OSError as exc:
            # roll back so a partial multi-edit never survives
            for path in written:
                try:
                    path.write_text(originals[path], encoding="utf-8")
                except OSError:
                    pass
            return ToolResult.fail(f"write failed ({exc}); all edits rolled back")

        summary_lines = []
        diffs = []
        for path, content in working.items():
            added, removed = diff_stats(originals[path], content)
            summary_lines.append(f"  {ctx.display(path)} (+{added} -{removed})")
            diffs.append(unified_diff(originals[path], content, ctx.display(path)))

        return ToolResult.succeed(
            f"applied {len(args.get('edits') or [])} edit(s) across {len(working)} file(s):\n"
            + "\n".join(summary_lines),
            headline=f"edited {len(working)} file(s)",
            diff="\n".join(diffs),
        )
