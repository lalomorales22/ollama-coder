"""Permission engine.

Every tool call passes through `PermissionEngine.check`. The decision order is
deliberate: hard denials can never be overridden, and "ask" only reaches the
user when nothing cheaper has already settled it.

    hard deny  ->  yolo  ->  auto-allow list  ->  session grants
               ->  per-tool heuristics (bash patterns, read-only git, ...)
               ->  ask the user  ->  configured default
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..tools.base import Tool, ToolContext
from ..tools.git import GitRunTool
from ..tools.shell import classify_command
from .events import EventBus, PermissionReply

if TYPE_CHECKING:  # pragma: no cover
    from .config import Config


@dataclass
class Decision:
    allow: bool
    reason: str = ""
    asked: bool = False
    feedback: str = ""


@dataclass
class Grants:
    """Approvals remembered for the rest of the session."""

    tools: set[str] = field(default_factory=set)
    signatures: set[str] = field(default_factory=set)
    bash_prefixes: set[str] = field(default_factory=set)

    def clear(self) -> None:
        self.tools.clear()
        self.signatures.clear()
        self.bash_prefixes.clear()


def _signature(tool_name: str, args: dict[str, Any]) -> str:
    payload = repr(sorted((k, str(v)[:200]) for k, v in args.items()))
    return f"{tool_name}:{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


def _bash_prefix(command: str) -> str:
    """First two words -- enough to distinguish 'npm test' from 'npm publish'."""
    words = command.strip().split()
    return " ".join(words[:2]) if words else ""


class PermissionEngine:
    def __init__(self, config: Config, bus: EventBus):
        self.config = config
        self.bus = bus
        self.grants = Grants()
        self._allow_bash_res = [
            re.compile(pattern) for pattern in config.get("permissions.allow_bash", []) or []
        ]

    # -- public ----------------------------------------------------------

    @property
    def yolo(self) -> bool:
        return bool(self.config.get("permissions.yolo", False))

    @yolo.setter
    def yolo(self, value: bool) -> None:
        self.config.set("permissions.yolo", bool(value))

    async def check(
        self, tool: Tool, args: dict[str, Any], ctx: ToolContext, call_id: str
    ) -> Decision:
        name = tool.name

        denied = self.config.get("permissions.deny", []) or []
        if name in denied:
            return Decision(False, f"{name} is denied by configuration")

        # A blocked shell command is refused even in yolo mode.
        if name == "bash":
            verdict, reason = classify_command(str(args.get("command", "")))
            if verdict == "blocked":
                return Decision(False, f"blocked command: {reason}")

        if self.yolo:
            return Decision(True, "yolo mode")

        if name in (self.config.get("permissions.auto_allow", []) or []):
            if not self._touches_protected(tool, args, ctx):
                return Decision(True, "auto-allowed")

        if name in self.grants.tools:
            return Decision(True, "allowed for this session")

        signature = _signature(name, args)
        if signature in self.grants.signatures:
            return Decision(True, "identical call already approved")

        heuristic = self._heuristic(tool, args, ctx)
        if heuristic is not None:
            return heuristic

        return await self._ask(tool, args, ctx, call_id, signature)

    def grant(self, tool_name: str, args: dict[str, Any], scope: str) -> None:
        if scope == "session":
            if tool_name == "bash":
                self.grants.bash_prefixes.add(_bash_prefix(str(args.get("command", ""))))
            else:
                self.grants.tools.add(tool_name)
        elif scope == "once":
            # remembered only so an immediate identical retry is not re-asked
            self.grants.signatures.add(_signature(tool_name, args))

    # -- internals -------------------------------------------------------

    def _touches_protected(self, tool: Tool, args: dict[str, Any], ctx: ToolContext) -> bool:
        raw = args.get("path") or args.get("file")
        if not raw:
            return False
        try:
            return ctx.is_protected(ctx.resolve(str(raw)))
        except Exception:
            return False

    def _heuristic(
        self, tool: Tool, args: dict[str, Any], ctx: ToolContext
    ) -> Decision | None:
        name = tool.name

        if name == "bash":
            command = str(args.get("command", "")).strip()
            verdict, _ = classify_command(command)
            if verdict == "dangerous":
                return None  # always ask
            if _bash_prefix(command) in self.grants.bash_prefixes:
                return Decision(True, "command family approved this session")
            if any(rx.search(command) for rx in self._allow_bash_res):
                if "&&" not in command and ";" not in command and "|" not in command:
                    return Decision(True, "matches an auto-allowed command pattern")
            return None

        if name == "git_run":
            raw = str(args.get("args", ""))
            if GitRunTool.is_destructive(raw):
                return None
            if GitRunTool.is_read_only(raw):
                return Decision(True, "read-only git command")
            return None

        if tool.read_only and not self._touches_protected(tool, args, ctx):
            if self.config.get("permissions.default", "ask") != "deny":
                return Decision(True, "read-only tool")

        return None

    async def _ask(
        self,
        tool: Tool,
        args: dict[str, Any],
        ctx: ToolContext,
        call_id: str,
        signature: str,
    ) -> Decision:
        try:
            preview = tool.preview(args, ctx)
        except Exception as exc:  # a broken preview must not block the call
            from ..tools.base import Preview

            preview = Preview(title=tool.name, detail=f"(preview failed: {exc})", kind=tool.kind)

        reply: PermissionReply | None = await self.bus.ask_permission(
            call_id=call_id,
            tool=tool.name,
            title=preview.title,
            detail=preview.detail,
            kind=preview.kind,
            args=args,
            diff=preview.diff,
        )

        if reply is None:
            # Nobody to ask (headless): fall back to the configured default.
            default = self.config.get("permissions.default", "ask")
            if default == "allow":
                return Decision(True, "default policy: allow")
            return Decision(
                False,
                f"{tool.name} needs approval but this run is non-interactive. "
                "Re-run with --yolo, or add the tool to permissions.auto_allow.",
            )

        if reply.allow:
            self.grant(tool.name, args, reply.scope)
            return Decision(True, "approved by user", asked=True)

        reason = reply.feedback or "the user declined this action"
        return Decision(False, reason, asked=True, feedback=reply.feedback)
