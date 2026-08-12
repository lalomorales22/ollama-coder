"""Modal screens: tool approval, model picker, session picker, help."""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from ..core.events import PermissionAsk, PermissionReply

KIND_LABEL = {
    "bash": ("RUN COMMAND", "#fbbf24"),
    "write": ("WRITE FILE", "#f472b6"),
    "edit": ("EDIT FILE", "#f472b6"),
    "git": ("GIT", "#a78bfa"),
    "network": ("NETWORK", "#60a5fa"),
    "mcp": ("MCP TOOL", "#22d3ee"),
    "task": ("DELEGATE", "#22d3ee"),
    "read": ("READ", "#4ade80"),
    "other": ("TOOL", "#94a3b8"),
}


class ApprovalScreen(ModalScreen[PermissionReply]):
    """Shows what is about to happen and waits for a decision."""

    BINDINGS = [
        Binding("y", "allow_once", "Allow once", show=True),
        Binding("a", "allow_session", "Allow all session", show=True),
        Binding("n", "deny", "Deny", show=True),
        Binding("e", "deny_feedback", "Deny + tell why", show=True),
        Binding("escape", "deny", "Deny", show=False),
    ]

    def __init__(self, ask: PermissionAsk):
        super().__init__()
        self.ask = ask

    def compose(self) -> ComposeResult:
        label, colour = KIND_LABEL.get(self.ask.kind, KIND_LABEL["other"])

        header = Text()
        header.append(f" {label} ", style=f"bold reverse {colour}")
        header.append("  ")
        header.append(self.ask.title, style="bold #e2e8f0")

        with Vertical(id="approval-box"):
            yield Static(header, id="approval-header")
            with VerticalScroll(id="approval-body"):
                yield Static(self._body(), id="approval-detail")
            yield Static(self._footer(), id="approval-footer")
            with Horizontal(id="approval-buttons"):
                yield Button("Allow once  (y)", variant="success", id="allow-once")
                yield Button("Allow session  (a)", variant="primary", id="allow-session")
                yield Button("Deny  (n)", variant="error", id="deny")

    def _body(self) -> RenderableType:
        parts: list[RenderableType] = []
        if self.ask.detail:
            style = "bold #fbbf24" if self.ask.kind == "bash" else "#cbd5e1"
            parts.append(Text(self.ask.detail, style=style))
        if self.ask.diff:
            parts.append(Text(""))
            try:
                parts.append(
                    Syntax(
                        self.ask.diff[:20000],
                        "diff",
                        theme="ansi_dark",
                        background_color="default",
                        word_wrap=False,
                    )
                )
            except Exception:
                parts.append(Text(self.ask.diff[:20000], style="#94a3b8"))
        if not parts:
            parts.append(Text("(no details)", style="#475569"))
        return Group(*parts)

    def _footer(self) -> Text:
        line = Text()
        line.append(" y ", style="reverse #4ade80")
        line.append(" allow once   ", style="#64748b")
        line.append(" a ", style="reverse #60a5fa")
        line.append(f" allow {self.ask.tool} all session   ", style="#64748b")
        line.append(" n ", style="reverse #f87171")
        line.append(" deny   ", style="#64748b")
        line.append(" e ", style="reverse #fbbf24")
        line.append(" deny with feedback", style="#64748b")
        return line

    # -- actions ---------------------------------------------------------

    def action_allow_once(self) -> None:
        self.dismiss(PermissionReply(allow=True, scope="once"))

    def action_allow_session(self) -> None:
        self.dismiss(PermissionReply(allow=True, scope="session"))

    def action_deny(self) -> None:
        self.dismiss(PermissionReply(allow=False))

    def action_deny_feedback(self) -> None:
        self.app.push_screen(FeedbackScreen(), self._got_feedback)

    def _got_feedback(self, feedback: str | None) -> None:
        self.dismiss(PermissionReply(allow=False, feedback=feedback or ""))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "allow-once":
            self.action_allow_once()
        elif event.button.id == "allow-session":
            self.action_allow_session()
        else:
            self.action_deny()


class FeedbackScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="feedback-box"):
            yield Static(
                Text("Tell the agent what to do instead:", style="bold #e2e8f0"),
                id="feedback-label",
            )
            yield Input(placeholder="e.g. use uv instead of pip", id="feedback-input")

    def on_mount(self) -> None:
        self.query_one("#feedback-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class PickerScreen(ModalScreen[str | None]):
    """Generic searchable list."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, options: list[tuple[str, str]], placeholder: str = "filter…"):
        super().__init__()
        self.title_text = title
        self.options = options  # (value, label)
        self.placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Static(Text(self.title_text, style="bold #22d3ee"), id="picker-title")
            yield Input(placeholder=self.placeholder, id="picker-filter")
            yield OptionList(
                *[Option(label, id=value) for value, label in self.options],
                id="picker-list",
            )

    def on_mount(self) -> None:
        self.query_one("#picker-filter", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        needle = event.value.lower()
        option_list = self.query_one("#picker-list", OptionList)
        option_list.clear_options()
        for value, label in self.options:
            if needle in label.lower():
                option_list.add_option(Option(label, id=value))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        option_list = self.query_one("#picker-list", OptionList)
        if option_list.option_count:
            option = option_list.get_option_at_index(0)
            self.dismiss(option.id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,q,question_mark", "close", "Close")]

    COMMANDS: list[tuple[str, str]] = [
        ("/help", "this screen"),
        ("/model [name]", "show or switch model (no arg opens the picker)"),
        ("/models", "list installed models"),
        ("/pull <name>", "download a model from the registry"),
        ("/new", "start a fresh session"),
        ("/sessions", "list recent sessions"),
        ("/resume [id]", "resume a session (blank = most recent)"),
        ("/search <text>", "full-text search across all sessions"),
        ("/branch", "fork the current session"),
        ("/export", "write the session to markdown"),
        ("/compact", "summarise and shrink the context now"),
        ("/context", "context and token usage"),
        ("/clear", "clear the conversation, keep the session"),
        ("/undo", "revert the last file the agent changed"),
        ("/checkpoints", "list revertible file snapshots"),
        ("/diff", "show uncommitted changes"),
        ("/commit [msg]", "stage everything and commit"),
        ("/git <args>", "run any git command"),
        ("/tools", "list every tool, built-in and MCP"),
        ("/mcp", "MCP server status"),
        ("/agents", "list subagents"),
        ("/skills", "list skills and what is active"),
        ("/commands", "list custom slash commands"),
        ("/permissions", "show the permission rules in force"),
        ("/yolo", "toggle skip-all-approvals mode"),
        ("/init", "generate an OLLAMA.md for this project"),
        ("/config", "show resolved configuration"),
        ("/quit", "exit"),
    ]

    KEYS: list[tuple[str, str]] = [
        ("enter", "send"),
        ("ctrl+j", "newline in the composer"),
        ("↑ / ↓", "input history (when empty)"),
        ("ctrl+c", "interrupt the running turn"),
        ("ctrl+d", "quit"),
        ("ctrl+b", "toggle sidebar"),
        ("ctrl+y", "toggle yolo mode"),
        ("ctrl+r", "undo last file change"),
        ("ctrl+l", "clear the transcript view"),
        ("click a tool card", "expand its output"),
    ]

    def compose(self) -> ComposeResult:
        commands = Table.grid(padding=(0, 2))
        commands.add_column(style="bold #22d3ee", no_wrap=True)
        commands.add_column(style="#94a3b8")
        for name, description in self.COMMANDS:
            commands.add_row(name, description)

        keys = Table.grid(padding=(0, 2))
        keys.add_column(style="bold #f472b6", no_wrap=True)
        keys.add_column(style="#94a3b8")
        for key, description in self.KEYS:
            keys.add_row(key, description)

        with Vertical(id="help-box"):
            yield Static(Text("OLLAMACODER — HELP", style="bold #22d3ee"), id="help-title")
            with VerticalScroll(id="help-body"):
                yield Static(
                    Group(
                        Text("SLASH COMMANDS", style="bold #e2e8f0"),
                        commands,
                        Text(""),
                        Text("KEYS", style="bold #e2e8f0"),
                        keys,
                        Text(""),
                        Text(
                            "Files: OLLAMA.md for project rules · .ollamacode/settings.json "
                            "for config · .ollamacode/mcp.json for MCP servers · "
                            "~/.ollamacode/{agents,commands,skills}/ for extensions.",
                            style="italic #64748b",
                        ),
                    )
                )
            yield Static(Text("esc to close", style="#475569"), id="help-footer")

    def action_close(self) -> None:
        self.dismiss(None)
