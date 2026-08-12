"""The Textual application.

Owns the runtime (backend, tools, agent, MCP, session) and translates agent
events into widgets. Slash commands live here too, since they are UI concerns
rather than agent concerns.
"""

from __future__ import annotations

import asyncio
import inspect
import shlex
from pathlib import Path
from typing import Any

from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical

from .. import __version__
from ..core.agent import Agent
from ..core.checkpoints import CheckpointStore
from ..core.config import Config
from ..core.events import (
    AssistantDelta,
    AssistantMessage,
    Event,
    EventBus,
    Notice,
    PermissionAsk,
    PermissionReply,
    SubagentFinished,
    SubagentStarted,
    ThinkingDelta,
    TodosChanged,
    ToolDenied,
    ToolFinished,
    ToolStarted,
    TurnFinished,
    TurnStarted,
    UsageUpdate,
)
from ..core.extensions import AgentRegistry, CommandRegistry, SkillRegistry, scaffold_examples
from ..core.llm import OllamaBackend
from ..core.permissions import PermissionEngine
from ..core.session import SessionStore
from ..mcpx import MCPManager
from ..tools import build_registry
from ..tools.git import git as run_git
from ..tools.git import repo_summary
from .banner import THINKING_WORDS, splash
from .screens import ApprovalScreen, HelpScreen, PickerScreen
from .widgets import Composer, Sidebar, StatusBar, Transcript


class OllamaCoderApp(App[None]):
    CSS_PATH = "app.tcss"
    TITLE = "OllamaCoder"

    # priority=True throughout: the composer holds focus almost always, and a
    # focused TextArea otherwise swallows control keys it does not bind.
    BINDINGS = [
        Binding("ctrl+c", "interrupt", "Interrupt", priority=True, show=True),
        Binding("ctrl+d", "quit", "Quit", priority=True, show=False),
        Binding("ctrl+b", "toggle_sidebar", "Sidebar", priority=True, show=True),
        Binding("ctrl+y", "toggle_yolo", "Yolo", priority=True, show=True),
        Binding("ctrl+r", "undo", "Undo edit", priority=True, show=True),
        Binding("ctrl+l", "clear_view", "Clear view", priority=True, show=False),
        Binding("f1", "help", "Help", priority=True, show=True),
    ]

    def __init__(self, config: Config, initial_prompt: str | None = None,
                 resume: str | None = None):
        super().__init__()
        self.config = config
        self.workdir = config.project_dir
        self.initial_prompt = initial_prompt
        self.resume_target = resume

        self.bus = EventBus()
        self.backend = OllamaBackend(config)
        self.sessions = SessionStore()
        self.agent_registry = AgentRegistry(self.workdir)
        self.command_registry = CommandRegistry(self.workdir)
        self.skill_registry = SkillRegistry(self.workdir)
        self.mcp = MCPManager(self.workdir, config)
        self.permissions = PermissionEngine(config, self.bus)

        self.tools = build_registry(self.workdir, config, self.agent_registry)
        self.checkpoints: CheckpointStore | None = None
        self.agent: Agent | None = None
        self._turn_task: asyncio.Task | None = None
        self._activity_timer: Any = None
        self._activity_index = 0

        self.transcript = Transcript()
        self.status = StatusBar()
        self.sidebar = Sidebar()
        self.composer = Composer()

    # -- layout ----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield self.status
        with Horizontal(id="main"):
            with Vertical(id="conversation"):
                yield self.transcript
                yield self.composer
            yield self.sidebar

    async def on_mount(self) -> None:
        self.composer.focus()
        self.bus.subscribe(self._on_event)
        self.bus.set_permission_handler(self._on_permission)
        if not self.config.get("ui.sidebar", True):
            self.sidebar.display = False
        self.status.mode = "yolo" if self.permissions.yolo else "ask"
        self.boot()

    # -- boot ------------------------------------------------------------

    @work(exclusive=True)
    async def boot(self) -> None:
        self.transcript.write_renderable(
            splash(
                __version__,
                model=str(self.config.get("model") or ""),
                workdir=str(self.workdir),
                extra="/help for commands · ctrl+c interrupts · enter sends",
            )
        )

        reachable, error = await self.backend.ping()
        if not reachable:
            self.transcript.notice(error, "error")
            return

        model = await self._resolve_model()
        if not model:
            return

        session_id = self.sessions.create(project_path=str(self.workdir), model=model)
        self.checkpoints = CheckpointStore(
            self.sessions.directory(session_id),
            enabled=bool(self.config.get("checkpoints.enabled", True)),
            max_entries=int(self.config.get("checkpoints.max_per_session", 200)),
        )

        if self.config.get("mcp.enabled", True):
            report = await self.mcp.connect_all()
            for line in report:
                self.transcript.notice(line, "success" if line.startswith("✓") else "warn")
            for tool in self.mcp.tools:
                self.tools.register(tool)
            self._refresh_mcp_panel()

        self.agent = Agent(
            config=self.config,
            backend=self.backend,
            tools=self.tools,
            bus=self.bus,
            permissions=self.permissions,
            workdir=self.workdir,
            session=self.sessions,
            checkpoints=self.checkpoints,
            skills=self.skill_registry,
        )

        info = await self.backend.info(model)
        window = await self.backend.effective_num_ctx(model)
        self.status.model = model
        self.status.session = session_id[:6]
        self.status.tokens_window = window

        capabilities = [c for c in ("tools", "vision", "thinking") if c in info.capabilities]
        detail = f"{len(self.tools)} tools · {window:,} ctx"
        if capabilities:
            detail += " · " + "+".join(capabilities)
        if not info.supports_tools:
            self.transcript.notice(
                f"{model} does not advertise tool support -- it will not be able to "
                "read or edit files. Use /model to pick one that does.",
                "warn",
            )
        self.transcript.notice(f"ready · {detail}", "success")

        await self._refresh_git()

        if self.resume_target is not None:
            await self._resume(self.resume_target)

        if self.initial_prompt:
            self.submit_turn(self.initial_prompt)

    async def _resolve_model(self) -> str | None:
        configured = self.config.get("model")
        available = await self.backend.model_names()
        if configured and (not available or configured in available):
            return configured
        if not available:
            self.transcript.notice(
                "No models installed. Run `ollama pull qwen3:8b` and restart.", "error"
            )
            return None
        if configured:
            self.transcript.notice(
                f"configured model '{configured}' is not installed; using {available[0]}", "warn"
            )
        chosen = available[0]
        self.config.set("model", chosen)
        return chosen

    # -- events ----------------------------------------------------------

    def _on_event(self, event: Event) -> None:
        if isinstance(event, ThinkingDelta):
            if self.config.get("ui.show_thinking", True):
                self.transcript.thinking_delta(event.text)
        elif isinstance(event, AssistantDelta):
            self.transcript.assistant_delta(event.text)
        elif isinstance(event, AssistantMessage):
            self.transcript.close_streams()
        elif isinstance(event, ToolStarted):
            self.transcript.tool_started(event.call_id, event.name, event.headline)
        elif isinstance(event, ToolFinished):
            self.transcript.tool_finished(
                event.call_id, event.ok, event.headline, event.output,
                event.error, event.duration_ms,
            )
            if event.name in ("git_commit", "git_run", "bash") and event.ok:
                self.call_later(self._refresh_git_task)
        elif isinstance(event, ToolDenied):
            self.transcript.tool_denied(event.call_id, event.reason)
        elif isinstance(event, TodosChanged):
            self.sidebar.todo_panel.set_todos(event.todos)
        elif isinstance(event, UsageUpdate):
            self.status.tokens_used = event.context_used
            if event.context_window:
                self.status.tokens_window = event.context_window
        elif isinstance(event, Notice):
            self.transcript.notice(event.text, event.level)
        elif isinstance(event, SubagentStarted):
            self.transcript.notice(f"delegating to {event.agent}…", "info")
        elif isinstance(event, SubagentFinished):
            self.transcript.notice(f"{event.agent} finished", "success" if event.ok else "warn")
        elif isinstance(event, TurnStarted):
            self._start_activity()
        elif isinstance(event, TurnFinished):
            self._stop_activity()
            self.transcript.close_streams()
            if event.interrupted:
                self.transcript.notice("interrupted", "warn")

    async def _on_permission(self, ask: PermissionAsk) -> None:
        """Route an approval request to the modal and resolve its future."""

        def resolve(reply: PermissionReply | None) -> None:
            if not ask.future.done():
                ask.future.set_result(reply or PermissionReply(allow=False))

        self.push_screen(ApprovalScreen(ask), resolve)

    # -- turns -----------------------------------------------------------

    @work(exclusive=False)
    async def submit_turn(self, prompt: str) -> None:
        if self.agent is None:
            self.transcript.notice("still starting up…", "warn")
            return
        if self._turn_task and not self._turn_task.done():
            self.transcript.notice("a turn is already running (ctrl+c to interrupt)", "warn")
            return

        self._turn_task = asyncio.create_task(self.agent.run(prompt))
        try:
            await self._turn_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.transcript.notice(f"{type(exc).__name__}: {exc}", "error")
        finally:
            self._turn_task = None
            self._stop_activity()

    def _start_activity(self) -> None:
        if self._activity_timer is None:
            self._activity_timer = self.set_interval(1.4, self._cycle_activity)
        self._cycle_activity()

    def _cycle_activity(self) -> None:
        word = THINKING_WORDS[self._activity_index % len(THINKING_WORDS)]
        self._activity_index += 1
        self.status.activity = f"{word}… (ctrl+c)"

    def _stop_activity(self) -> None:
        if self._activity_timer is not None:
            self._activity_timer.stop()
            self._activity_timer = None
        self.status.activity = ""

    async def on_composer_submitted(self, message: Composer.Submitted) -> None:
        value = message.value.strip()
        if not value:
            return
        if value.startswith("/"):
            await self._handle_command(value)
            return
        self.transcript.add_user(value)
        self.transcript.pin()
        self.submit_turn(value)

    # -- actions ---------------------------------------------------------

    def action_interrupt(self) -> None:
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
            self.transcript.notice("interrupting…", "warn")
        else:
            self.composer.text = ""

    def action_toggle_sidebar(self) -> None:
        self.sidebar.display = not self.sidebar.display

    def action_toggle_yolo(self) -> None:
        self.permissions.yolo = not self.permissions.yolo
        self.status.mode = "yolo" if self.permissions.yolo else "ask"
        self.transcript.notice(
            "YOLO mode ON — tool calls run without asking (hard-blocked commands still refused)"
            if self.permissions.yolo
            else "YOLO mode off — approvals required again",
            "warn" if self.permissions.yolo else "success",
        )

    def action_clear_view(self) -> None:
        self.transcript.remove_children()
        self.transcript.notice("view cleared (conversation kept — use /clear to reset it)", "info")

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    @work
    async def action_undo(self) -> None:
        await self._undo()

    # -- slash commands --------------------------------------------------

    async def _handle_command(self, raw: str) -> None:
        parts = raw[1:].split(maxsplit=1)
        name = parts[0].lower() if parts else ""
        args = parts[1].strip() if len(parts) > 1 else ""

        handlers = {
            "help": self._cmd_help,
            "quit": self._cmd_quit,
            "exit": self._cmd_quit,
            "model": self._cmd_model,
            "models": self._cmd_models,
            "pull": self._cmd_pull,
            "new": self._cmd_new,
            "sessions": self._cmd_sessions,
            "resume": self._cmd_resume,
            "search": self._cmd_search,
            "branch": self._cmd_branch,
            "export": self._cmd_export,
            "compact": self._cmd_compact,
            "context": self._cmd_context,
            "clear": self._cmd_clear,
            "undo": self._cmd_undo,
            "checkpoints": self._cmd_checkpoints,
            "diff": self._cmd_diff,
            "commit": self._cmd_commit,
            "git": self._cmd_git,
            "tools": self._cmd_tools,
            "mcp": self._cmd_mcp,
            "agents": self._cmd_agents,
            "skills": self._cmd_skills,
            "commands": self._cmd_commands,
            "permissions": self._cmd_permissions,
            "yolo": self._cmd_yolo,
            "init": self._cmd_init,
            "config": self._cmd_config,
            "scaffold": self._cmd_scaffold,
        }

        handler = handlers.get(name)
        if handler is not None:
            # Handlers are either plain coroutines or @work-decorated methods.
            # A @work method returns a Worker, which is *not* awaitable -- it is
            # already running in the background, so only await real coroutines.
            outcome = handler(args)
            if inspect.isawaitable(outcome):
                await outcome
            return

        rendered = self.command_registry.render(name, args)
        if rendered is not None:
            self.transcript.add_user(raw)
            self.submit_turn(rendered)
            return

        self.transcript.notice(f"unknown command /{name} — try /help", "warn")

    async def _cmd_help(self, args: str) -> None:
        self.push_screen(HelpScreen())

    async def _cmd_quit(self, args: str) -> None:
        self.exit()

    async def _cmd_model(self, args: str) -> None:
        available = await self.backend.model_names(refresh=True)
        if args:
            if available and args not in available:
                self.transcript.notice(f"{args} is not installed — /models to list", "warn")
                return
            await self._set_model(args)
            return

        infos = await self.backend.list_models()
        options = [
            (m["name"], f"{m['name']:<40} {m['parameter_size'] or '':>8}  {_size(m['size'])}")
            for m in infos
        ]
        if not options:
            self.transcript.notice("no models installed", "warn")
            return

        def chosen(value: str | None) -> None:
            if value:
                self.run_worker(self._set_model(value))

        self.push_screen(PickerScreen("SELECT MODEL", options, "type to filter…"), chosen)

    async def _set_model(self, model: str) -> None:
        self.config.set("model", model)
        self.status.model = model
        window = await self.backend.effective_num_ctx(model)
        self.status.tokens_window = window
        info = await self.backend.info(model)
        self.sessions.set_model(model)
        if self.agent:
            await self.agent.ensure_system_prompt(force=True)
        note = f"model → {model} ({window:,} ctx)"
        if not info.supports_tools:
            note += " — warning: no tool support"
        self.transcript.notice(note, "success" if info.supports_tools else "warn")

    async def _cmd_models(self, args: str) -> None:
        models = await self.backend.list_models(refresh=True)
        if not models:
            self.transcript.notice("no models installed", "warn")
            return
        table = Table(show_header=True, header_style="bold #22d3ee", box=None, padding=(0, 2))
        table.add_column("model")
        table.add_column("params")
        table.add_column("size")
        current = self.config.get("model")
        for model in models:
            marker = " ←" if model["name"] == current else ""
            table.add_row(
                f"[#e2e8f0]{model['name']}[/]{marker}",
                model["parameter_size"] or "",
                _size(model["size"]),
            )
        self.transcript.write_renderable(table)

    @work
    async def _cmd_pull(self, args: str) -> None:
        if not args:
            self.transcript.notice("usage: /pull <model>", "warn")
            return
        self.transcript.notice(f"pulling {args}…", "info")
        async for line in self.backend.pull(args):
            self.status.activity = f"pull: {line}"
        self.status.activity = ""
        await self.backend.list_models(refresh=True)
        self.transcript.notice(f"pull finished: {args}", "success")

    async def _cmd_new(self, args: str) -> None:
        session_id = self.sessions.create(
            project_path=str(self.workdir), model=self.config.get("model")
        )
        self.checkpoints = CheckpointStore(self.sessions.directory(session_id))
        if self.agent:
            self.agent.clear()
            self.agent.ctx.checkpoints = self.checkpoints
            self.agent.ctx.read_files.clear()
        self.status.session = session_id[:6]
        self.status.tokens_used = 0
        self.sidebar.todo_panel.set_todos([])
        self.transcript.notice(f"new session {session_id[:6]}", "success")

    async def _cmd_sessions(self, args: str) -> None:
        sessions = self.sessions.list(limit=15)
        if not sessions:
            self.transcript.notice("no saved sessions yet", "info")
            return
        table = Table(show_header=True, header_style="bold #22d3ee", box=None, padding=(0, 2))
        table.add_column("id")
        table.add_column("title")
        table.add_column("msgs", justify="right")
        table.add_column("updated")
        for session in sessions:
            marker = " ←" if session["id"] == self.sessions.current_id else ""
            table.add_row(
                f"[#a78bfa]{session['id'][:6]}[/]{marker}",
                (session.get("title") or "(untitled)")[:44],
                str(session.get("message_count", 0)),
                str(session.get("updated_at", ""))[:16].replace("T", " "),
            )
        self.transcript.write_renderable(table)
        self.transcript.notice("/resume <id> to reopen", "info")

    async def _cmd_resume(self, args: str) -> None:
        await self._resume(args)

    async def _resume(self, target: str) -> None:
        session_id = None
        if target:
            session_id = self.sessions.resolve_id(target)
        else:
            recent = self.sessions.list(limit=2, project_path=str(self.workdir))
            recent = [s for s in recent if s["id"] != self.sessions.current_id]
            session_id = recent[0]["id"] if recent else None

        if not session_id:
            self.transcript.notice(f"no session matching {target!r}", "warn")
            return

        info = self.sessions.info(session_id)
        messages = self.sessions.load_messages(session_id)
        if info is None:
            self.transcript.notice("session not found", "warn")
            return

        self.sessions.current_id = session_id
        self.checkpoints = CheckpointStore(self.sessions.directory(session_id))
        if self.agent:
            self.agent.load_messages(messages)
            self.agent.ctx.checkpoints = self.checkpoints
            await self.agent.ensure_system_prompt(force=True)

        self.status.session = session_id[:6]
        self.transcript.notice(
            f"resumed {session_id[:6]} — {info.get('title') or 'untitled'} "
            f"({len(messages)} messages)",
            "success",
        )
        for message in messages[-6:]:
            role = message.get("role")
            content = str(message.get("content") or "")[:400]
            if role == "user" and content:
                self.transcript.add_user(content)
            elif role == "assistant" and content:
                self.transcript.assistant_delta(content)
                self.transcript.close_streams()

    async def _cmd_search(self, args: str) -> None:
        if not args:
            self.transcript.notice("usage: /search <text>", "warn")
            return
        results = self.sessions.search(args, limit=15)
        if not results:
            self.transcript.notice(f"no matches for {args!r}", "info")
            return
        table = Table(show_header=True, header_style="bold #22d3ee", box=None, padding=(0, 2))
        table.add_column("id")
        table.add_column("session")
        table.add_column("match")
        for result in results:
            table.add_row(
                f"[#a78bfa]{result['session_id'][:6]}[/]",
                (result.get("title") or "(untitled)")[:28],
                str(result.get("snippet", "")).replace("\n", " ")[:70],
            )
        self.transcript.write_renderable(table)

    async def _cmd_branch(self, args: str) -> None:
        try:
            new_id = await asyncio.to_thread(self.sessions.branch)
        except Exception as exc:
            self.transcript.notice(f"branch failed: {exc}", "error")
            return
        self.sessions.current_id = new_id
        self.status.session = new_id[:6]
        self.transcript.notice(f"branched → {new_id[:6]} (original preserved)", "success")

    async def _cmd_export(self, args: str) -> None:
        markdown = await asyncio.to_thread(self.sessions.export_markdown)
        target = Path(args) if args else self.workdir / f"session-{self.sessions.short_id}.md"
        try:
            target.write_text(markdown)
        except OSError as exc:
            self.transcript.notice(f"export failed: {exc}", "error")
            return
        self.transcript.notice(f"exported → {target}", "success")

    @work
    async def _cmd_compact(self, args: str) -> None:
        if not self.agent:
            return
        if await self.agent.compact():
            self.status.tokens_used = 0
        else:
            self.transcript.notice("nothing to compact yet", "info")

    async def _cmd_context(self, args: str) -> None:
        if not self.agent:
            return
        used = self.agent.estimated_tokens()
        window = self.status.tokens_window or 1
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="#64748b")
        table.add_column(style="#e2e8f0")
        table.add_row("model", str(self.config.get("model")))
        table.add_row("context window", f"{window:,} tokens")
        table.add_row("in use", f"{used:,} ({100 * used / window:.0f}%)")
        table.add_row("messages", str(len(self.agent.messages)))
        table.add_row("generated this session", f"{self.agent.last_usage['completion']:,} tokens")
        table.add_row("compaction at", f"{float(self.config.get('compact_threshold', 0.82)) * 100:.0f}%")
        self.transcript.write_renderable(table)

    async def _cmd_clear(self, args: str) -> None:
        if self.agent:
            self.agent.clear()
            self.agent.ctx.read_files.clear()
        self.status.tokens_used = 0
        self.transcript.notice("conversation cleared", "success")

    async def _cmd_undo(self, args: str) -> None:
        await self._undo()

    async def _undo(self) -> None:
        if not self.checkpoints:
            self.transcript.notice("checkpoints are disabled", "warn")
            return
        result = await self.checkpoints.undo_last()
        if result is None:
            self.transcript.notice("nothing to undo", "info")
        else:
            self.transcript.notice(f"undo: {result}", "success")

    async def _cmd_checkpoints(self, args: str) -> None:
        if not self.checkpoints or not len(self.checkpoints):
            self.transcript.notice("no checkpoints yet", "info")
            return
        table = Table(show_header=True, header_style="bold #22d3ee", box=None, padding=(0, 2))
        table.add_column("#")
        table.add_column("file")
        table.add_column("state")
        for index, entry in enumerate(self.checkpoints.recent(15), 1):
            table.add_row(
                str(index),
                Path(entry.path).name,
                "modified" if entry.existed else "created",
            )
        self.transcript.write_renderable(table)
        self.transcript.notice("/undo or ctrl+r reverts the most recent", "info")

    async def _cmd_diff(self, args: str) -> None:
        code, output = await run_git(["diff", "--stat", "-p"] + shlex.split(args), self.workdir)
        if code != 0:
            self.transcript.notice(output.strip()[:300] or "git diff failed", "warn")
            return
        if not output.strip():
            self.transcript.notice("working tree is clean", "success")
            return
        from rich.syntax import Syntax

        self.transcript.write_renderable(
            Syntax(output[:20000], "diff", theme="ansi_dark", background_color="default")
        )

    @work
    async def _cmd_commit(self, args: str) -> None:
        if not args:
            self.transcript.notice(
                "usage: /commit <message>  (or just ask the agent to commit)", "warn"
            )
            return
        code, output = await run_git(["add", "-A"], self.workdir)
        if code != 0:
            self.transcript.notice(output.strip()[:300], "error")
            return
        code, output = await run_git(["commit", "-m", args], self.workdir)
        self.transcript.notice(output.strip()[:400], "success" if code == 0 else "error")
        await self._refresh_git()

    async def _cmd_git(self, args: str) -> None:
        if not args:
            args = "status --short --branch"
        code, output = await run_git(shlex.split(args), self.workdir)
        self.transcript.write_renderable(
            Text(output.strip()[:8000] or "(no output)", style="#94a3b8" if code == 0 else "#f87171")
        )
        await self._refresh_git()

    async def _cmd_tools(self, args: str) -> None:
        table = Table(show_header=True, header_style="bold #22d3ee", box=None, padding=(0, 2))
        table.add_column("tool")
        table.add_column("access")
        table.add_column("what it does")
        for tool in sorted(self.tools.all(), key=lambda t: t.name):
            access = "read" if tool.read_only else "write"
            description = tool.description.split(".")[0][:64]
            table.add_row(f"[#e2e8f0]{tool.name}[/]", access, description)
        self.transcript.write_renderable(table)

    async def _cmd_mcp(self, args: str) -> None:
        if not self.mcp.available:
            self.transcript.notice("the `mcp` package is not installed", "warn")
            return
        status = self.mcp.status()
        if not status:
            self.transcript.notice(
                "no MCP servers configured. Add them to .ollamacode/mcp.json:\n"
                '  {"mcpServers": {"fs": {"command": "npx", '
                '"args": ["-y","@modelcontextprotocol/server-filesystem","."]}}}',
                "info",
            )
            return
        for server in status:
            if server["connected"]:
                self.transcript.notice(
                    f"✓ {server['name']}: {', '.join(server['tools'][:12]) or 'no tools'}", "success"
                )
            else:
                self.transcript.notice(f"✗ {server['name']}: {server['error']}", "error")

    async def _cmd_agents(self, args: str) -> None:
        self.agent_registry.reload()
        self.transcript.write_renderable(
            Text(self.agent_registry.describe() or "(none)", style="#94a3b8")
        )

    async def _cmd_skills(self, args: str) -> None:
        if args.strip().split()[:1] == ["import"]:
            await self._import_skills(args.strip()[len("import"):].split())
            return
        self.skill_registry.reload()
        if not self.skill_registry.skills:
            self.transcript.notice(
                "no skills. Create ~/.ollamacode/skills/<name>/SKILL.md (see /scaffold)", "info"
            )
            return
        lines = []
        for name in self.skill_registry.names():
            skill = self.skill_registry.skills[name]
            active = "●" if name in self.skill_registry.active else "○"
            lines.append(f"{active} {name}: {skill.description} [{', '.join(skill.keywords[:4])}]")
        self.transcript.write_renderable(Text("\n".join(lines), style="#94a3b8"))

    async def _import_skills(self, names: list[str]) -> None:
        from ..core.extensions import import_claude_skills

        imported, skipped = await asyncio.to_thread(
            import_claude_skills, None, None, False, names or None
        )
        for name in imported:
            self.transcript.notice(f"imported skill: {name}", "success")
        for note in skipped:
            self.transcript.notice(f"skipped: {note}", "info")
        if not imported and not skipped:
            self.transcript.notice(
                "no Claude Code skills found under ~/.claude/skills or ~/.claude/plugins", "warn"
            )
        self.skill_registry.reload()

    async def _cmd_commands(self, args: str) -> None:
        self.command_registry.reload()
        if not self.command_registry.commands:
            self.transcript.notice(
                "no custom commands. Create ~/.ollamacode/commands/<name>.md (see /scaffold)", "info"
            )
            return
        lines = [
            f"/{name}: {self.command_registry.commands[name].description}"
            for name in self.command_registry.names()
        ]
        self.transcript.write_renderable(Text("\n".join(lines), style="#94a3b8"))

    async def _cmd_permissions(self, args: str) -> None:
        permissions = self.config.get("permissions", {})
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="#64748b")
        table.add_column(style="#e2e8f0")
        table.add_row("mode", "yolo (no prompts)" if self.permissions.yolo else "ask before writes")
        table.add_row("default", str(permissions.get("default")))
        table.add_row("auto-allowed", ", ".join(permissions.get("auto_allow", [])))
        table.add_row("denied", ", ".join(permissions.get("deny", [])) or "(none)")
        table.add_row(
            "session grants",
            ", ".join(sorted(self.permissions.grants.tools | self.permissions.grants.bash_prefixes))
            or "(none)",
        )
        table.add_row("sandbox roots", ", ".join(str(p) for p in self.config.sandbox_roots))
        self.transcript.write_renderable(table)

    async def _cmd_yolo(self, args: str) -> None:
        self.action_toggle_yolo()

    async def _cmd_init(self, args: str) -> None:
        target = self.workdir / "OLLAMA.md"
        if target.exists():
            self.transcript.notice(f"{target.name} already exists — ask me to update it", "warn")
            return
        self.transcript.add_user("/init")
        self.submit_turn(
            "Create an OLLAMA.md at the project root. First explore the repository "
            "(build files, source layout, tests, CI, existing docs) and then write a "
            "concise file covering: what this project is, how to build/run/test it, "
            "the code conventions an agent must follow here, and anything surprising "
            "about the layout. Keep it under 60 lines. Facts only -- no filler."
        )

    async def _cmd_config(self, args: str) -> None:
        import json

        self.transcript.write_renderable(
            Text(json.dumps(self.config.data, indent=2)[:8000], style="#94a3b8")
        )

    async def _cmd_scaffold(self, args: str) -> None:
        created = await asyncio.to_thread(scaffold_examples, self.workdir)
        if not created:
            self.transcript.notice("example extensions already exist", "info")
            return
        for path in created:
            self.transcript.notice(f"created {path}", "success")
        self.agent_registry.reload()
        self.command_registry.reload()
        self.skill_registry.reload()

    # -- helpers ---------------------------------------------------------

    async def _refresh_git(self) -> None:
        info = await repo_summary(self.workdir)
        if info.get("is_repo"):
            branch = str(info.get("branch", ""))
            if info.get("dirty"):
                branch += "*"
            self.status.branch = branch
        else:
            self.status.branch = ""

    def _refresh_git_task(self) -> None:
        self.run_worker(self._refresh_git(), exclusive=False)

    def _refresh_mcp_panel(self) -> None:
        rows = [
            (server["name"], f"{len(server['tools'])} tools" if server["connected"] else "offline")
            for server in self.mcp.status()
        ]
        self.sidebar.mcp_panel.set_rows(rows)

    async def on_unmount(self) -> None:
        await self.tools.aclose()
        await self.mcp.aclose()
        await self.backend.aclose()


def _size(num_bytes: int) -> str:
    if not num_bytes:
        return ""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"
