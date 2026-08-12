"""Transcript, tool cards, status bar, sidebar and composer."""

from __future__ import annotations

import time
from typing import Any

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static, TextArea

from .banner import SPINNER_FRAMES, context_gauge

TOOL_ICONS = {
    "bash": "$",
    "read_file": "▪",
    "write_file": "◆",
    "edit_file": "◆",
    "multi_edit": "◆",
    "glob": "▫",
    "grep": "▫",
    "list_dir": "▫",
    "git_read": "◇",
    "git_commit": "◇",
    "git_run": "◇",
    "fetch_url": "↗",
    "web_search": "↗",
    "think": "~",
    "todo_write": "≡",
    "task": "▣",
}

STATUS_ICON = {"pending": "○", "in_progress": "◐", "completed": "●"}
STATUS_STYLE = {"pending": "#64748b", "in_progress": "#fbbf24", "completed": "#4ade80"}


def tool_icon(name: str) -> str:
    if name.startswith("mcp__"):
        return "*"
    return TOOL_ICONS.get(name, "◇")


class UserMessage(Static):
    """What the human said."""

    def __init__(self, text: str):
        body = Text()
        body.append("❯ ", style="bold #22d3ee")
        body.append(text, style="#e2e8f0")
        super().__init__(body, classes="msg-user")


class AssistantMessage(Static):
    """Streams plain text, then re-renders as markdown when the turn ends."""

    def __init__(self) -> None:
        super().__init__("", classes="msg-assistant")
        self.buffer = ""
        self._last_render = 0.0
        self._finished = False

    def append(self, text: str) -> None:
        self.buffer += text
        now = time.monotonic()
        if now - self._last_render > 0.06:
            self._last_render = now
            self.update(Text(self.buffer, style="#e2e8f0"))
            self.scroll_visible()

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        if not self.buffer.strip():
            self.display = False
            return
        try:
            self.update(Markdown(self.buffer, code_theme="native", inline_code_theme="native"))
        except Exception:
            self.update(Text(self.buffer, style="#e2e8f0"))


class ThinkingBlock(Static):
    """Dim, collapsed reasoning stream."""

    def __init__(self) -> None:
        super().__init__("", classes="msg-thinking")
        self.buffer = ""
        self._last_render = 0.0
        self.expanded = False

    def append(self, text: str) -> None:
        self.buffer += text
        now = time.monotonic()
        if now - self._last_render > 0.12:
            self._last_render = now
            self._repaint()

    def _repaint(self) -> None:
        if self.expanded:
            body = Text(self.buffer, style="italic #64748b")
        else:
            tail = " ".join(self.buffer.split())[-160:]
            body = Text(f"◌ {tail}", style="italic #52606d", overflow="ellipsis", no_wrap=True)
        self.update(body)

    def finish(self) -> None:
        if not self.buffer.strip():
            self.display = False
            return
        words = len(self.buffer.split())
        self.update(Text(f"◌ thought for {words} words", style="italic #52606d"))

    def toggle(self) -> None:
        self.expanded = not self.expanded
        self._repaint()


class ToolCard(Static):
    """One tool call: spinner while running, result summary when done."""

    def __init__(self, call_id: str, name: str, headline: str):
        super().__init__("", classes="tool-card running")
        self.call_id = call_id
        self.tool_name = name
        self.headline = headline
        self.state = "running"
        self.output = ""
        self.error: str | None = None
        self.duration_ms = 0
        self.expanded = False
        self._frame = 0
        self._timer: Any = None

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.08, self._tick)
        self._repaint()

    def _tick(self) -> None:
        if self.state != "running":
            if self._timer:
                self._timer.stop()
            return
        self._frame = (self._frame + 1) % len(SPINNER_FRAMES)
        self._repaint()

    def finish(self, ok: bool, headline: str, output: str, error: str | None, duration_ms: int) -> None:
        self.state = "ok" if ok else "error"
        self.headline = headline or self.headline
        self.output = output or ""
        self.error = error
        self.duration_ms = duration_ms
        self.remove_class("running")
        self.add_class("ok" if ok else "error")
        if self._timer:
            self._timer.stop()
        self._repaint()

    def deny(self, reason: str) -> None:
        self.state = "denied"
        self.error = reason
        self.remove_class("running")
        self.add_class("denied")
        if self._timer:
            self._timer.stop()
        self._repaint()

    def toggle(self) -> None:
        self.expanded = not self.expanded
        self._repaint()

    def _repaint(self) -> None:
        icon = tool_icon(self.tool_name)
        line = Text()

        if self.state == "running":
            line.append(f"{SPINNER_FRAMES[self._frame]} ", style="#22d3ee")
            line.append(f"{icon} {self.tool_name}", style="bold #22d3ee")
        elif self.state == "ok":
            line.append("✓ ", style="#4ade80")
            line.append(f"{icon} {self.tool_name}", style="bold #94a3b8")
        elif self.state == "denied":
            line.append("⊘ ", style="#fbbf24")
            line.append(f"{icon} {self.tool_name}", style="bold #fbbf24")
        else:
            line.append("✗ ", style="#f87171")
            line.append(f"{icon} {self.tool_name}", style="bold #f87171")

        if self.headline:
            line.append("  ", style="")
            line.append(self.headline[:100], style="#cbd5e1")
        if self.duration_ms > 900:
            line.append(f"  {self.duration_ms / 1000:.1f}s", style="#475569")

        parts: list[RenderableType] = [line]

        if self.state == "denied" and self.error:
            parts.append(Text(f"   {self.error}", style="#fbbf24"))
        elif self.error and self.state == "error":
            parts.append(Text(f"   {self.error}", style="#f87171"))

        body = self.output.strip()
        if body:
            if self.expanded:
                shown = body[:20000]
            else:
                lines = body.splitlines()
                shown = "\n".join(lines[:6])
                if len(lines) > 6:
                    shown += f"\n   … {len(lines) - 6} more lines (click to expand)"
            if self.tool_name in ("edit_file", "write_file", "multi_edit") and shown.count("\n@@") >= 0 and "---" in shown:
                try:
                    parts.append(Syntax(shown, "diff", theme="ansi_dark", background_color="default"))
                except Exception:
                    parts.append(Text(shown, style="#64748b"))
            else:
                parts.append(
                    Text(
                        "\n".join("   " + ln for ln in shown.splitlines()),
                        style="#64748b",
                    )
                )

        self.update(Group(*parts))

    def on_click(self) -> None:
        if self.output.strip():
            self.toggle()


class NoticeLine(Static):
    STYLES = {
        "info": ("#60a5fa", "·"),
        "warn": ("#fbbf24", "⚠"),
        "error": ("#f87171", "✗"),
        "success": ("#4ade80", "✓"),
    }

    def __init__(self, text: str, level: str = "info"):
        colour, glyph = self.STYLES.get(level, self.STYLES["info"])
        body = Text()
        body.append(f"{glyph} ", style=colour)
        body.append(text, style=colour)
        super().__init__(body, classes="notice")


class Transcript(VerticalScroll):
    """The scrolling conversation."""

    def __init__(self) -> None:
        super().__init__(id="transcript")
        self._assistant: AssistantMessage | None = None
        self._thinking: ThinkingBlock | None = None
        self._cards: dict[str, ToolCard] = {}
        self._pinned = True

    def _append(self, widget: Widget) -> None:
        self.mount(widget)
        if self._pinned:
            self.scroll_end(animate=False)

    def write_renderable(self, renderable: RenderableType, classes: str = "") -> None:
        self._append(Static(renderable, classes=classes or "raw"))

    def add_user(self, text: str) -> None:
        self.close_streams()
        self._append(UserMessage(text))

    def thinking_delta(self, text: str) -> None:
        if self._thinking is None:
            self._thinking = ThinkingBlock()
            self._append(self._thinking)
        self._thinking.append(text)

    def assistant_delta(self, text: str) -> None:
        if self._thinking is not None:
            self._thinking.finish()
            self._thinking = None
        if self._assistant is None:
            self._assistant = AssistantMessage()
            self._append(self._assistant)
        self._assistant.append(text)

    def close_streams(self) -> None:
        if self._thinking is not None:
            self._thinking.finish()
            self._thinking = None
        if self._assistant is not None:
            self._assistant.finish()
            self._assistant = None
            if self._pinned:
                self.scroll_end(animate=False)

    def tool_started(self, call_id: str, name: str, headline: str) -> None:
        self.close_streams()
        card = ToolCard(call_id, name, headline)
        self._cards[call_id] = card
        self._append(card)

    def tool_finished(
        self, call_id: str, ok: bool, headline: str, output: str,
        error: str | None, duration_ms: int,
    ) -> None:
        card = self._cards.get(call_id)
        if card is not None:
            card.finish(ok, headline, output, error, duration_ms)
            if self._pinned:
                self.scroll_end(animate=False)

    def tool_denied(self, call_id: str, reason: str) -> None:
        card = self._cards.get(call_id)
        if card is not None:
            card.deny(reason)

    def notice(self, text: str, level: str = "info") -> None:
        self._append(NoticeLine(text, level))

    def on_mouse_scroll_up(self, event: Any) -> None:  # pragma: no cover - UI
        self._pinned = False

    def on_mouse_scroll_down(self, event: Any) -> None:  # pragma: no cover - UI
        if self.scroll_offset.y >= self.max_scroll_y - 2:
            self._pinned = True

    def pin(self) -> None:
        self._pinned = True
        self.scroll_end(animate=False)


class StatusBar(Static):
    """Model, session, context gauge, git branch, permission mode."""

    model = reactive("")
    session = reactive("")
    branch = reactive("")
    tokens_used = reactive(0)
    tokens_window = reactive(0)
    mode = reactive("ask")
    activity = reactive("")

    def __init__(self) -> None:
        super().__init__("", id="statusbar")

    def watch_model(self) -> None:
        self.refresh_bar()

    def watch_session(self) -> None:
        self.refresh_bar()

    def watch_branch(self) -> None:
        self.refresh_bar()

    def watch_tokens_used(self) -> None:
        self.refresh_bar()

    def watch_mode(self) -> None:
        self.refresh_bar()

    def watch_activity(self) -> None:
        self.refresh_bar()

    def refresh_bar(self) -> None:
        line = Text()
        line.append("◢◤ ", style="bold #22d3ee")
        line.append(self.model or "no model", style="bold #e2e8f0")

        if self.branch:
            line.append("  git:", style="#475569")
            line.append(self.branch, style="#a78bfa")

        if self.session:
            line.append("  ses:", style="#475569")
            line.append(self.session, style="#64748b")

        if self.mode == "yolo":
            line.append("  [YOLO]", style="bold #f87171")
        else:
            line.append("  [ask]", style="#4ade80")

        if self.tokens_window:
            line.append("  ", style="")
            gauge, _ = context_gauge(self.tokens_used, self.tokens_window, width=14)
            line.append_text(gauge)

        if self.activity:
            line.append("   ", style="")
            line.append(self.activity, style="italic #22d3ee")

        self.update(line)


class TodoPanel(Static):
    def __init__(self) -> None:
        super().__init__("", classes="panel")
        self.todos: list[dict[str, str]] = []

    def set_todos(self, todos: list[dict[str, str]]) -> None:
        self.todos = todos
        self.render_panel()

    def render_panel(self) -> None:
        body = Text()
        body.append("TASKS\n", style="bold #22d3ee")
        if not self.todos:
            body.append("(none yet)", style="#475569")
        else:
            for todo in self.todos:
                status = todo.get("status", "pending")
                body.append(f"{STATUS_ICON.get(status, '○')} ", style=STATUS_STYLE.get(status, "#64748b"))
                style = "#e2e8f0" if status == "in_progress" else "#94a3b8"
                if status == "completed":
                    style = "#475569 strike"
                body.append(todo.get("task", "")[:60] + "\n", style=style)
        self.update(body)


class InfoPanel(Static):
    def __init__(self, title: str) -> None:
        super().__init__("", classes="panel")
        self.title_text = title
        self.rows: list[tuple[str, str]] = []

    def set_rows(self, rows: list[tuple[str, str]]) -> None:
        self.rows = rows
        body = Text()
        body.append(f"{self.title_text}\n", style="bold #22d3ee")
        if not self.rows:
            body.append("(none)", style="#475569")
        for label, value in self.rows:
            body.append(f"{label} ", style="#64748b")
            body.append(f"{value}\n", style="#94a3b8")
        self.update(body)


class Sidebar(VerticalScroll):
    def __init__(self) -> None:
        super().__init__(id="sidebar")
        self.todo_panel = TodoPanel()
        self.mcp_panel = InfoPanel("MCP")
        self.tips_panel = InfoPanel("KEYS")

    def compose(self) -> ComposeResult:
        yield self.todo_panel
        yield self.mcp_panel
        yield self.tips_panel

    def on_mount(self) -> None:
        self.todo_panel.render_panel()
        self.mcp_panel.set_rows([])
        self.tips_panel.set_rows([
            ("ctrl+c", "interrupt"),
            ("ctrl+b", "sidebar"),
            ("ctrl+y", "yolo"),
            ("ctrl+j", "newline"),
            ("ctrl+r", "undo edit"),
            ("/help", "commands"),
        ])


class Composer(TextArea):
    """Multi-line input. Enter submits, ctrl+j inserts a newline."""

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def __init__(self) -> None:
        super().__init__(
            "",
            id="composer",
            soft_wrap=True,
            show_line_numbers=False,
            tab_behavior="focus",
            placeholder="ask anything · / for commands · ctrl+j newline",
        )
        self.input_history: list[str] = []
        self._history_index = 0

    async def _on_key(self, event: Any) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            value = self.text.strip()
            if value:
                self.input_history.append(value)
                self._history_index = len(self.input_history)
                self.text = ""
                self.post_message(self.Submitted(value))
            return
        if event.key == "ctrl+j":
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        if event.key == "up" and not self.text.strip() and self.input_history:
            event.prevent_default()
            event.stop()
            self._history_index = max(0, self._history_index - 1)
            self.text = self.input_history[self._history_index]
            self.move_cursor(self.document.end)
            return
        if event.key == "down" and self.input_history and self._history_index < len(self.input_history):
            event.prevent_default()
            event.stop()
            self._history_index += 1
            self.text = (
                self.input_history[self._history_index]
                if self._history_index < len(self.input_history)
                else ""
            )
            self.move_cursor(self.document.end)
            return
        await super()._on_key(event)
