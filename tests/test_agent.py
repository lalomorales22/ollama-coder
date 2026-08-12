"""Agent loop, permissions, context management -- driven by a fake backend."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

from ollama_coder.core.agent import Agent, estimate_tokens
from ollama_coder.core.events import (
    AssistantDelta,
    EventBus,
    PermissionReply,
    ToolDenied,
    ToolFinished,
    ToolStarted,
    TurnFinished,
)
from ollama_coder.core.llm import ModelInfo
from ollama_coder.core.permissions import PermissionEngine
from ollama_coder.tools.base import Tool, ToolRegistry, ToolResult

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeFunction:
    name: str
    arguments: Dict[str, Any]


@dataclass
class FakeToolCall:
    function: FakeFunction


@dataclass
class FakeMessage:
    content: str = ""
    thinking: str = ""
    tool_calls: List[FakeToolCall] = field(default_factory=list)


@dataclass
class FakeChunk:
    message: FakeMessage
    done: bool = False
    prompt_eval_count: Optional[int] = None
    eval_count: Optional[int] = None


class FakeBackend:
    """Replays scripted turns; records the messages it was sent."""

    def __init__(self, turns: List[Dict[str, Any]], capabilities: Optional[List[str]] = None):
        self.turns = turns
        self.calls: List[List[Dict[str, Any]]] = []
        self.capabilities = capabilities if capabilities is not None else ["tools"]
        self.summaries = 0

    async def info(self, model: str) -> ModelInfo:
        return ModelInfo(name=model, context_length=8192, capabilities=list(self.capabilities))

    async def effective_num_ctx(self, model: str) -> int:
        return 8192

    async def chat_stream(self, model, messages, tools=None, max_tokens=None):
        self.calls.append([dict(m) for m in messages])
        turn = self.turns.pop(0) if self.turns else {"content": "done"}
        for piece in turn.get("content", ""):
            yield FakeChunk(FakeMessage(content=piece))
        calls = [
            FakeToolCall(FakeFunction(name=c["name"], arguments=c.get("arguments", {})))
            for c in turn.get("tool_calls", [])
        ]
        yield FakeChunk(
            FakeMessage(tool_calls=calls), done=True, prompt_eval_count=120, eval_count=30
        )

    async def chat_once(self, model, messages, max_tokens=None, think=False) -> str:
        self.summaries += 1
        return "**Goal** test\n**Done** things\n**State** ok\n**Next** continue"


class EchoTool(Tool):
    name = "echo"
    description = "echo back"
    read_only = True
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def run(self, args, ctx) -> ToolResult:
        self.calls.append(args)
        return ToolResult.succeed(f"echo: {args.get('text', '')}", headline="echoed")


class SlowTool(Tool):
    name = "slow"
    description = "sleeps"
    read_only = True
    parameters = {"type": "object", "properties": {}}

    async def run(self, args, ctx) -> ToolResult:
        await asyncio.sleep(0.25)
        return ToolResult.succeed("slept")


class WriterTool(Tool):
    name = "writer"
    description = "pretends to write"
    kind = "write"
    read_only = False
    parameters = {"type": "object", "properties": {}}

    async def run(self, args, ctx) -> ToolResult:
        return ToolResult.succeed("wrote")


class BoomTool(Tool):
    name = "boom"
    description = "raises"
    read_only = True
    parameters = {"type": "object", "properties": {}}

    async def run(self, args, ctx) -> ToolResult:
        raise RuntimeError("kaboom")


def make_agent(config, backend, tools: List[Tool], bus: Optional[EventBus] = None) -> Agent:
    bus = bus or EventBus()
    config.set("model", "fake-model")
    return Agent(
        config=config,
        backend=backend,
        tools=ToolRegistry(tools),
        bus=bus,
        permissions=PermissionEngine(config, bus),
        workdir=config.project_dir,
    )


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------


class TestAgentLoop:
    async def test_plain_reply_without_tools(self, config):
        backend = FakeBackend([{"content": "hello"}])
        agent = make_agent(config, backend, [EchoTool()])
        assert await agent.run("hi") == "hello"

    async def test_system_prompt_is_always_present(self, config):
        """0.2.x only added a system prompt when OLLAMA.md existed."""
        backend = FakeBackend([{"content": "ok"}])
        agent = make_agent(config, backend, [EchoTool()])
        await agent.run("hi")
        assert agent.messages[0]["role"] == "system"
        assert "OllamaCoder" in agent.messages[0]["content"]

    async def test_tool_call_then_answer(self, config):
        echo = EchoTool()
        backend = FakeBackend([
            {"content": "", "tool_calls": [{"name": "echo", "arguments": {"text": "hi"}}]},
            {"content": "the tool said hi"},
        ])
        agent = make_agent(config, backend, [echo])
        result = await agent.run("use the tool")
        assert result == "the tool said hi"
        assert echo.calls == [{"text": "hi"}]
        assert [m["role"] for m in agent.messages][-3:] == ["assistant", "tool", "assistant"]

    async def test_unknown_tool_is_reported_to_the_model(self, config):
        bus = EventBus()
        denials: List[Any] = []
        bus.subscribe(lambda e: denials.append(e) if isinstance(e, ToolDenied) else None)
        backend = FakeBackend([
            {"tool_calls": [{"name": "nope", "arguments": {}}]},
            {"content": "recovered"},
        ])
        agent = make_agent(config, backend, [EchoTool()], bus)
        await agent.run("go")
        tool_message = [m for m in agent.messages if m["role"] == "tool"][0]
        assert "unknown tool" in tool_message["content"]
        # a hallucinated name is not a human-approval event; CI must not exit 2
        assert denials[0].kind == "unknown"

    async def test_permission_denial_is_tagged_as_such(self, config):
        bus = EventBus()
        denials: List[Any] = []
        bus.subscribe(lambda e: denials.append(e) if isinstance(e, ToolDenied) else None)
        backend = FakeBackend([
            {"tool_calls": [{"name": "writer", "arguments": {}}]},
            {"content": "ok"},
        ])
        agent = make_agent(config, backend, [WriterTool()], bus)
        await agent.run("go")
        assert denials[0].kind == "permission"

    async def test_tool_exception_becomes_an_error_result(self, config):
        backend = FakeBackend([
            {"tool_calls": [{"name": "boom", "arguments": {}}]},
            {"content": "handled"},
        ])
        agent = make_agent(config, backend, [BoomTool()])
        assert await agent.run("go") == "handled"
        tool_message = [m for m in agent.messages if m["role"] == "tool"][0]
        assert "kaboom" in tool_message["content"]

    async def test_step_limit_is_enforced(self, config):
        backend = FakeBackend([
            {"tool_calls": [{"name": "echo", "arguments": {}}]} for _ in range(20)
        ])
        config.set("max_steps", 3)
        agent = make_agent(config, backend, [EchoTool()])
        await agent.run("loop")
        assert len(backend.calls) == 3

    async def test_read_only_tools_run_concurrently(self, config):
        backend = FakeBackend([
            {"tool_calls": [{"name": "slow", "arguments": {}} for _ in range(4)]},
            {"content": "done"},
        ])
        agent = make_agent(config, backend, [SlowTool()])
        started = asyncio.get_event_loop().time()
        await agent.run("go")
        elapsed = asyncio.get_event_loop().time() - started
        assert elapsed < 0.8, f"4x0.25s serial would exceed this; took {elapsed:.2f}s"

    async def test_events_are_emitted_in_order(self, config):
        bus = EventBus()
        seen: List[str] = []
        bus.subscribe(lambda e: seen.append(type(e).__name__))
        backend = FakeBackend([
            {"content": "hi", "tool_calls": [{"name": "echo", "arguments": {}}]},
            {"content": "bye"},
        ])
        agent = make_agent(config, backend, [EchoTool()], bus)
        await agent.run("go")
        assert seen.index("ToolStarted") < seen.index("ToolFinished")
        assert seen[-1] == "TurnFinished"

    async def test_interrupt_closes_dangling_tool_calls(self, config):
        backend = FakeBackend([
            {"tool_calls": [{"name": "slow", "arguments": {}}]},
            {"content": "never"},
        ])
        agent = make_agent(config, backend, [SlowTool()])
        task = asyncio.create_task(agent.run("go"))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert agent.interrupted


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


class TestPermissions:
    async def test_write_tool_is_denied_without_a_ui(self, config):
        backend = FakeBackend([
            {"tool_calls": [{"name": "writer", "arguments": {}}]},
            {"content": "ok"},
        ])
        agent = make_agent(config, backend, [WriterTool()])
        await agent.run("go")
        tool_message = [m for m in agent.messages if m["role"] == "tool"][0]
        assert "needs approval" in tool_message["content"]

    async def test_yolo_allows_writes(self, config):
        config.set("permissions.yolo", True)
        backend = FakeBackend([
            {"tool_calls": [{"name": "writer", "arguments": {}}]},
            {"content": "ok"},
        ])
        agent = make_agent(config, backend, [WriterTool()])
        await agent.run("go")
        tool_message = [m for m in agent.messages if m["role"] == "tool"][0]
        assert "wrote" in tool_message["content"]

    async def test_yolo_still_refuses_hard_blocked_commands(self, config):
        from ollama_coder.tools.shell import BashTool

        config.set("permissions.yolo", True)
        bus = EventBus()
        engine = PermissionEngine(config, bus)
        tool = BashTool(config.project_dir, config)
        from ollama_coder.tools.base import ToolContext

        ctx = ToolContext(workdir=config.project_dir, config=config, bus=bus)
        decision = await engine.check(tool, {"command": "rm -rf /"}, ctx, "1")
        assert not decision.allow

    async def test_ui_approval_is_honoured(self, config):
        bus = EventBus()

        async def approve(ask) -> None:
            ask.future.set_result(PermissionReply(allow=True, scope="session"))

        bus.set_permission_handler(approve)
        backend = FakeBackend([
            {"tool_calls": [{"name": "writer", "arguments": {}}]},
            {"content": "ok"},
        ])
        agent = make_agent(config, backend, [WriterTool()], bus)
        await agent.run("go")
        assert "wrote" in [m for m in agent.messages if m["role"] == "tool"][0]["content"]

    async def test_session_grant_avoids_a_second_prompt(self, config):
        bus = EventBus()
        asked = []

        async def approve(ask) -> None:
            asked.append(ask.tool)
            ask.future.set_result(PermissionReply(allow=True, scope="session"))

        bus.set_permission_handler(approve)
        backend = FakeBackend([
            {"tool_calls": [{"name": "writer", "arguments": {}}]},
            {"tool_calls": [{"name": "writer", "arguments": {}}]},
            {"content": "ok"},
        ])
        agent = make_agent(config, backend, [WriterTool()], bus)
        await agent.run("go")
        assert len(asked) == 1

    async def test_denial_feedback_reaches_the_model(self, config):
        bus = EventBus()

        async def deny(ask) -> None:
            ask.future.set_result(PermissionReply(allow=False, feedback="use uv instead"))

        bus.set_permission_handler(deny)
        backend = FakeBackend([
            {"tool_calls": [{"name": "writer", "arguments": {}}]},
            {"content": "understood"},
        ])
        agent = make_agent(config, backend, [WriterTool()], bus)
        await agent.run("go")
        assert "use uv instead" in [m for m in agent.messages if m["role"] == "tool"][0]["content"]

    async def test_configured_deny_list_wins_over_yolo(self, config):
        config.set("permissions.yolo", True)
        config.set("permissions.deny", ["writer"])
        bus = EventBus()
        engine = PermissionEngine(config, bus)
        from ollama_coder.tools.base import ToolContext

        ctx = ToolContext(workdir=config.project_dir, config=config, bus=bus)
        decision = await engine.check(WriterTool(), {}, ctx, "1")
        assert not decision.allow

    async def test_safe_bash_pattern_is_auto_allowed(self, config):
        from ollama_coder.tools.base import ToolContext
        from ollama_coder.tools.shell import BashTool

        bus = EventBus()
        engine = PermissionEngine(config, bus)
        ctx = ToolContext(workdir=config.project_dir, config=config, bus=bus)
        tool = BashTool(config.project_dir, config)
        assert (await engine.check(tool, {"command": "ls -la"}, ctx, "1")).allow
        # ...but not once it is chained into something else
        assert not (await engine.check(tool, {"command": "ls && rm -rf build"}, ctx, "2")).allow


# ---------------------------------------------------------------------------
# Context management
# ---------------------------------------------------------------------------


class TestContext:
    async def test_compaction_summarises_instead_of_truncating(self, config):
        backend = FakeBackend([{"content": "ok"}])
        agent = make_agent(config, backend, [EchoTool()])
        await agent.ensure_system_prompt()
        for index in range(30):
            agent.messages.append({"role": "user", "content": f"message {index}"})
            agent.messages.append({"role": "assistant", "content": f"reply {index}"})

        before = len(agent.messages)
        assert await agent.compact()
        assert backend.summaries == 1
        assert len(agent.messages) < before
        assert agent.messages[0]["role"] == "system"
        assert "compacted" in agent.messages[1]["content"]

    async def test_compaction_never_orphans_a_tool_result(self, config):
        backend = FakeBackend([{"content": "ok"}])
        agent = make_agent(config, backend, [EchoTool()])
        await agent.ensure_system_prompt()
        for _ in range(12):
            agent.messages.append({"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "echo", "arguments": {}}}
            ]})
            agent.messages.append({"role": "tool", "name": "echo", "content": "result"})
        config.set("keep_recent_messages", 5)
        await agent.compact()
        first_kept = agent.messages[3]  # system, summary, ack, then history
        assert first_kept["role"] != "tool"

    async def test_auto_compaction_triggers_on_threshold(self, config):
        backend = FakeBackend([{"content": "ok"}])
        agent = make_agent(config, backend, [EchoTool()])
        await agent.ensure_system_prompt()
        agent.context_window = 1000
        agent.last_usage["prompt"] = 950
        for index in range(30):
            agent.messages.append({"role": "user", "content": f"m{index}"})
        assert await agent.maybe_compact()

    def test_token_estimate_is_sane(self):
        assert 20 < estimate_tokens("x" * 100) < 40
