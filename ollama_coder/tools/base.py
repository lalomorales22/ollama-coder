"""Tool contract shared by built-in tools, subagents and MCP bridges."""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ..core.config import Config
    from ..core.events import EventBus


class SandboxError(Exception):
    """Raised when a tool tries to escape its allowed roots."""


@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    error: str | None = None
    # short one-liner for the UI ("edited src/app.py (+12 -3)")
    headline: str = ""
    # arbitrary structured payload for the UI (diffs, todo lists, ...)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_model_payload(self) -> str:
        """What the model actually sees. Terse and unambiguous."""
        if self.ok:
            return self.output or "(ok, no output)"
        parts = ["ERROR: " + (self.error or "tool failed")]
        if self.output:
            parts.append(self.output)
        return "\n".join(parts)

    @classmethod
    def fail(cls, error: str, headline: str = "") -> ToolResult:
        return cls(ok=False, error=error, headline=headline or error[:80])

    @classmethod
    def succeed(cls, output: str, headline: str = "", **meta: Any) -> ToolResult:
        return cls(ok=True, output=output, headline=headline, meta=meta)


@dataclass
class Preview:
    """What the user is shown before approving a call."""

    title: str
    detail: str
    kind: str = "other"
    diff: str | None = None


@dataclass
class ToolContext:
    """Everything a tool needs that is not one of its arguments."""

    workdir: Path
    config: Config
    bus: EventBus
    # files the model has read this session; edit tools require a prior read
    read_files: set[str] = field(default_factory=set)
    # set by the agent so tools can record undo points
    checkpoints: Any = None
    session_id: str | None = None
    # populated by the agent for the task/subagent tool
    runtime: dict[str, Any] = field(default_factory=dict)

    # -- path safety -----------------------------------------------------

    def resolve(self, path: str, *, must_exist: bool = False) -> Path:
        """Resolve a user/model supplied path inside the sandbox."""
        raw = os.path.expanduser(str(path)).strip()
        candidate = Path(raw)
        if not candidate.is_absolute():
            relative = self.workdir / candidate
            # Models routinely drop the leading slash off an absolute path
            # ("private/tmp/x" for "/private/tmp/x"). If the relative reading
            # does not exist but the absolute one does, take the absolute one --
            # it still has to pass the sandbox check below.
            if not relative.exists() and Path("/" + raw).exists():
                candidate = Path("/" + raw)
            else:
                candidate = relative

        # resolve() also collapses ".." so traversal cannot slip past the check
        resolved = candidate.resolve()

        if self.config.get("sandbox.enabled", True):
            roots = self.config.sandbox_roots
            if not any(_is_within(resolved, root) for root in roots):
                allowed = ", ".join(str(r) for r in roots)
                raise SandboxError(
                    f"path {resolved} is outside the allowed roots ({allowed}). "
                    "Add it to sandbox.extra_roots to permit access."
                )

        if must_exist and not resolved.exists():
            raise FileNotFoundError(
                f"no such file: {resolved} (working directory is {self.workdir}). "
                "Use glob or list_dir to find the correct path."
            )

        return resolved

    def is_protected(self, path: Path) -> bool:
        """True for secrets-ish files that always need explicit approval."""
        patterns = self.config.get("sandbox.protected_globs", []) or []
        text = str(path)
        name = path.name
        for pattern in patterns:
            if fnmatch.fnmatch(text, pattern) or fnmatch.fnmatch(name, pattern.lstrip("*/")):
                return True
        return False

    def display(self, path: Path) -> str:
        """Path relative to the project when possible -- nicer in transcripts."""
        try:
            return str(path.relative_to(self.workdir))
        except ValueError:
            return str(path)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class Tool:
    """Base class for every tool the model can call.

    Subclasses implement `run`; `preview` is optional but is what powers the
    approval dialog (a diff for edits, the command line for bash, ...).
    """

    name: str = ""
    description: str = ""
    # "read" tools may run in parallel and are candidates for auto-approval
    kind: str = "other"
    read_only: bool = False
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        detail = "\n".join(f"{k}: {_short(v)}" for k, v in args.items())
        return Preview(title=f"{self.name}", detail=detail or "(no arguments)", kind=self.kind)

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:  # pragma: no cover
        raise NotImplementedError

    # tools that hold resources (the persistent shell) override this
    async def aclose(self) -> None:
        return None


def _short(value: Any, limit: int = 300) -> str:
    text = str(value)
    if len(text) > limit:
        return text[:limit] + f"... (+{len(text) - limit} chars)"
    return text


def truncate_output(text: str, limit: int) -> str:
    """Keep the head and tail -- the middle of a long log is rarely the point."""
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.7)]
    tail = text[-int(limit * 0.25) :]
    dropped = len(text) - len(head) - len(tail)
    return f"{head}\n\n... [{dropped:,} characters truncated] ...\n\n{tail}"


class ToolRegistry:
    """Name -> Tool, with allow-list filtering for subagents."""

    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self, allow: list[str] | None = None) -> list[dict[str, Any]]:
        tools = self.all()
        if allow:
            wanted = set(allow)
            tools = [t for t in tools if t.name in wanted]
        return [t.schema() for t in tools]

    async def aclose(self) -> None:
        for tool in self.all():
            try:
                await tool.aclose()
            except Exception:
                continue

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
