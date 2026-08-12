"""Typed events emitted by the agent core.

The agent never talks to a UI directly. It emits events onto an `EventBus`;
the TUI, the headless runner and the session recorder all subscribe. That is
what lets the exact same agent loop drive a Textual app and a CI pipeline.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


@dataclass
class TurnStarted:
    """A user turn began."""

    prompt: str


@dataclass
class StepStarted:
    """One model call within a turn (a turn may take many steps)."""

    step: int
    max_steps: int


@dataclass
class ThinkingDelta:
    """A chunk of the model's reasoning stream."""

    text: str


@dataclass
class AssistantDelta:
    """A chunk of user-visible assistant text."""

    text: str


@dataclass
class AssistantMessage:
    """A complete assistant message (after streaming finished)."""

    content: str
    thinking: str = ""


@dataclass
class ToolStarted:
    call_id: str
    name: str
    args: dict[str, Any]
    headline: str = ""


@dataclass
class ToolFinished:
    call_id: str
    name: str
    ok: bool
    headline: str
    output: str
    error: str | None = None
    duration_ms: int = 0


@dataclass
class ToolDenied:
    call_id: str
    name: str
    reason: str
    # "permission" -- a human refused (or would have to be asked);
    # "unknown"    -- the model invented a tool name;
    # "unavailable"-- the tool exists but is not in this agent's allow-list.
    # Only "permission" means a human was actually needed.
    kind: str = "permission"


@dataclass
class PermissionAsk:
    """Ask the UI whether a tool call may proceed.

    The UI must resolve `future` with a `PermissionReply`. If nobody handles
    the event the agent falls back to the configured default policy.
    """

    call_id: str
    tool: str
    title: str
    detail: str
    kind: str  # "bash" | "write" | "edit" | "network" | "git" | "mcp" | "other"
    args: dict[str, Any]
    future: asyncio.Future[PermissionReply] = field(repr=False, default=None)  # type: ignore[assignment]
    diff: str | None = None


@dataclass
class PermissionReply:
    allow: bool
    scope: str = "once"  # "once" | "session" | "always"
    feedback: str = ""


@dataclass
class TodosChanged:
    todos: list[dict[str, Any]]


@dataclass
class UsageUpdate:
    prompt_tokens: int
    completion_tokens: int
    context_used: int
    context_window: int
    duration_ms: int = 0


@dataclass
class Notice:
    """Informational message for the user (level: info|warn|error|success)."""

    text: str
    level: str = "info"


@dataclass
class TurnFinished:
    content: str
    steps: int
    interrupted: bool = False
    error: str | None = None


@dataclass
class SubagentStarted:
    call_id: str
    agent: str
    task: str


@dataclass
class SubagentFinished:
    call_id: str
    agent: str
    result: str
    ok: bool = True


Event = (
    TurnStarted
    | StepStarted
    | ThinkingDelta
    | AssistantDelta
    | AssistantMessage
    | ToolStarted
    | ToolFinished
    | ToolDenied
    | PermissionAsk
    | TodosChanged
    | UsageUpdate
    | Notice
    | TurnFinished
    | SubagentStarted
    | SubagentFinished
)

Subscriber = Callable[[Event], None | Awaitable[None]]


# ---------------------------------------------------------------------------
# Bus
# ---------------------------------------------------------------------------


class EventBus:
    """Fan-out bus. Subscribers may be sync or async callables."""

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []
        self._permission_handler: Subscriber | None = None

    def set_permission_handler(self, fn: Subscriber | None) -> None:
        """Register the single component responsible for resolving prompts.

        Only a real UI should claim this. Without a handler the agent falls
        back to its configured default policy instead of blocking forever.
        """
        self._permission_handler = fn

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        self._subscribers.append(fn)

        def _unsubscribe() -> None:
            if fn in self._subscribers:
                self._subscribers.remove(fn)

        return _unsubscribe

    async def emit(self, event: Event) -> None:
        for fn in list(self._subscribers):
            try:
                result = fn(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # a broken listener must never kill the agent
                continue

    async def ask_permission(
        self,
        *,
        call_id: str,
        tool: str,
        title: str,
        detail: str,
        kind: str,
        args: dict[str, Any],
        diff: str | None = None,
        timeout: float | None = None,
    ) -> PermissionReply | None:
        """Emit a PermissionAsk and wait for a UI to resolve it.

        Returns None when no UI claimed the request, letting the caller apply
        its default policy.
        """
        if self._permission_handler is None:
            return None

        loop = asyncio.get_running_loop()
        future: asyncio.Future[PermissionReply] = loop.create_future()
        ask = PermissionAsk(
            call_id=call_id,
            tool=tool,
            title=title,
            detail=detail,
            kind=kind,
            args=args,
            future=future,
            diff=diff,
        )
        try:
            result = self._permission_handler(ask)
            if inspect.isawaitable(result):
                await result
        except Exception:
            return PermissionReply(allow=False, feedback="permission handler failed")

        await self.emit(ask)
        if future.done():
            return future.result()
        try:
            if timeout:
                return await asyncio.wait_for(future, timeout)
            return await future
        except asyncio.TimeoutError:
            return PermissionReply(allow=False, feedback="permission request timed out")
