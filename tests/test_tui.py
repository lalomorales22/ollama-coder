"""TUI smoke tests -- the app boots, renders and dispatches commands offline."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from ollama_coder.core.llm import ModelInfo

pytestmark = pytest.mark.asyncio


class OfflineBackend:
    """Stands in for OllamaBackend so the TUI can boot without a daemon."""

    def __init__(self, config: Any) -> None:
        self.config = config

    async def ping(self):
        return True, ""

    async def list_models(self, refresh: bool = False) -> List[Dict[str, Any]]:
        return [{"name": "test-model", "size": 1 << 30, "family": "test",
                 "parameter_size": "7B", "modified": ""}]

    async def model_names(self, refresh: bool = False) -> List[str]:
        return ["test-model"]

    async def info(self, model: str) -> ModelInfo:
        return ModelInfo(name=model, context_length=8192, capabilities=["tools", "thinking"])

    async def effective_num_ctx(self, model: str) -> int:
        return 8192

    async def chat_stream(self, *args, **kwargs):
        if False:  # pragma: no cover - never iterated in these tests
            yield None

    async def chat_once(self, *args, **kwargs) -> str:
        return "Title Here"

    async def aclose(self) -> None:
        return None


@pytest.fixture
def app(config, monkeypatch, tmp_path):
    import ollama_coder.tui.app as app_module

    monkeypatch.setattr(app_module, "OllamaBackend", OfflineBackend)
    monkeypatch.setattr(
        app_module, "SessionStore", lambda *a, **k: _store(tmp_path)
    )
    config.set("model", "test-model")
    config.set("mcp.enabled", False)
    return app_module.OllamaCoderApp(config)


def _store(tmp_path):
    from ollama_coder.core.session import SessionStore

    return SessionStore(base_dir=tmp_path / "sessions")


class TestBoot:
    async def test_app_boots_and_populates_the_status_bar(self, app):
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.6)
            assert app.status.model == "test-model"
            assert app.status.tokens_window == 8192
            assert app.status.session, "a session id should be shown"
            assert app.agent is not None

    async def test_status_bar_has_a_visible_content_row(self, app):
        """A border on a height:1 bar consumed the only row and blanked it."""
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.6)
            assert app.status.size.height == 1

    async def test_tools_are_registered(self, app):
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.6)
            names = app.tools.names()
            for expected in ("bash", "read_file", "edit_file", "grep", "git_read", "todo_write"):
                assert expected in names


class TestInteraction:
    async def test_help_screen_opens_and_closes(self, app):
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.6)
            await pilot.press("f1")
            await pilot.pause(0.2)
            assert app.screen.__class__.__name__ == "HelpScreen"
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert app.screen.__class__.__name__ != "HelpScreen"

    async def test_yolo_toggle(self, app):
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.6)
            assert app.status.mode == "ask"
            await pilot.press("ctrl+y")
            await pilot.pause(0.2)
            assert app.status.mode == "yolo"
            assert app.permissions.yolo is True

    async def test_unknown_slash_command_is_reported(self, app):
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.6)
            await app._handle_command("/definitely-not-a-command")
            await pilot.pause(0.2)
            rendered = str(app.transcript.children[-1].render())
            assert "unknown command" in rendered

    async def test_tools_command_renders(self, app):
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.6)
            before = len(app.transcript.children)
            await app._handle_command("/tools")
            await pilot.pause(0.2)
            assert len(app.transcript.children) > before

    @pytest.mark.parametrize(
        "command",
        ["/context", "/permissions", "/mcp", "/agents", "/skills", "/commands",
         "/checkpoints", "/sessions", "/config", "/git status", "/compact",
         "/undo", "/branch", "/search test", "/models", "/tools"],
    )
    async def test_every_slash_command_runs(self, app, command):
        """@work handlers return a Worker, which is not awaitable -- the
        dispatcher must not blindly await what a handler returns."""
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.6)
            await app._handle_command(command)
            await pilot.pause(0.1)

    async def test_new_session_resets_state(self, app):
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.6)
            first = app.status.session
            app.status.tokens_used = 500
            await app._handle_command("/new")
            await pilot.pause(0.2)
            assert app.status.session != first
            assert app.status.tokens_used == 0

    async def test_custom_command_is_dispatched(self, app, project, monkeypatch):
        commands = project / ".ollamacode" / "commands"
        commands.mkdir(parents=True, exist_ok=True)
        (commands / "hello.md").write_text("---\nname: hello\n---\nSay hello to $ARGUMENTS.\n")

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.6)
            app.command_registry.reload()
            captured: List[str] = []
            monkeypatch.setattr(app, "submit_turn", lambda prompt: captured.append(prompt))
            await app._handle_command("/hello world")
            assert captured == ["Say hello to world."]


class TestTranscript:
    async def test_tool_card_lifecycle(self, app):
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.6)
            transcript = app.transcript
            transcript.tool_started("c1", "bash", "Run: ls")
            await pilot.pause(0.1)
            card = transcript._cards["c1"]
            assert card.state == "running"
            transcript.tool_finished("c1", True, "listed", "a.txt\nb.txt", None, 120)
            await pilot.pause(0.1)
            assert card.state == "ok"
            transcript.tool_denied("c1", "user said no")
            await pilot.pause(0.1)
            assert card.state == "denied"

    async def test_streaming_then_markdown(self, app):
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.6)
            transcript = app.transcript
            transcript.assistant_delta("# Heading\n\nsome **bold** text")
            await pilot.pause(0.15)
            block = transcript._assistant
            assert block is not None
            transcript.close_streams()
            await pilot.pause(0.1)
            assert transcript._assistant is None
