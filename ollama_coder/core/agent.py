"""The agent loop.

One turn is: call the model, stream what it says, run whatever tools it asks
for, feed the results back, repeat until it stops asking for tools. Everything
observable is emitted onto the event bus rather than printed, which is what
lets the same loop drive the TUI and a CI run.

Read-only tool calls in a single batch run concurrently; anything that writes
runs one at a time so two edits can never race on the same file.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..tools.base import ToolContext, ToolRegistry, ToolResult
from .config import Config
from .events import (
    AssistantDelta,
    AssistantMessage,
    EventBus,
    Notice,
    StepStarted,
    SubagentFinished,
    SubagentStarted,
    ThinkingDelta,
    ToolDenied,
    ToolFinished,
    ToolStarted,
    TurnFinished,
    TurnStarted,
    UsageUpdate,
)
from .extensions import AgentDefinition, SkillRegistry
from .llm import OllamaBackend
from .permissions import PermissionEngine
from .prompts import SUMMARY_PROMPT, TITLE_PROMPT, build_system_prompt

CHARS_PER_TOKEN = 3.6  # code and JSON tokenize denser than prose


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            return {}
    if raw is None:
        return {}
    try:
        return dict(raw)
    except (TypeError, ValueError):
        return {}


class Agent:
    def __init__(
        self,
        *,
        config: Config,
        backend: OllamaBackend,
        tools: ToolRegistry,
        bus: EventBus,
        permissions: PermissionEngine,
        workdir: Path,
        session: Any = None,
        checkpoints: Any = None,
        skills: SkillRegistry | None = None,
        system_prompt: str | None = None,
        allowed_tools: list[str] | None = None,
        max_steps: int | None = None,
        label: str = "main",
    ):
        self.config = config
        self.backend = backend
        self.tools = tools
        self.bus = bus
        self.permissions = permissions
        self.workdir = Path(workdir)
        self.session = session
        self.skills = skills
        self.label = label
        self.allowed_tools = allowed_tools
        self.max_steps = max_steps or int(config.get("max_steps", 40))

        self.messages: list[dict[str, Any]] = []
        self._system_prompt = system_prompt
        self.last_usage: dict[str, int] = {"prompt": 0, "completion": 0}
        self.context_window = 0
        self.interrupted = False
        self._running = False

        self.ctx = ToolContext(
            workdir=self.workdir,
            config=config,
            bus=bus,
            checkpoints=checkpoints,
            session_id=getattr(session, "current_id", None),
        )
        self.ctx.runtime["agent"] = self

    # -- prompt ----------------------------------------------------------

    async def ensure_system_prompt(self, force: bool = False) -> None:
        if self.messages and self.messages[0].get("role") == "system" and not force:
            return

        if self._system_prompt is not None:
            content = self._system_prompt
        else:
            from ..tools.git import repo_summary
            from .project import describe_dependencies

            model = self.config.get("model") or ""
            info = await self.backend.info(model) if model else None
            content = build_system_prompt(
                workdir=self.workdir,
                config=self.config,
                tool_names=self._active_tool_names(),
                model_info=info,
                git_info=await repo_summary(self.workdir),
                dependencies=await asyncio.to_thread(describe_dependencies, self.workdir),
            )

        message = {"role": "system", "content": content}
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0] = message
        else:
            self.messages.insert(0, message)

    def _active_tool_names(self) -> list[str]:
        names = self.tools.names()
        if self.allowed_tools:
            allowed = set(self.allowed_tools)
            names = [n for n in names if n in allowed]
        return names

    # -- public ----------------------------------------------------------

    async def run(self, user_message: str, images: list[str] | None = None) -> str:
        """Run one full turn. Returns the assistant's final text."""
        if self._running:
            return "(agent is already running)"

        self._running = True
        self.interrupted = False
        started = time.monotonic()
        await self.bus.emit(TurnStarted(prompt=user_message))

        try:
            await self.ensure_system_prompt()
            await self._inject_skills(user_message)

            message: dict[str, Any] = {"role": "user", "content": user_message}
            if images:
                message["images"] = images
            self._record(message)

            if self.config.get("auto_compact", True):
                await self.maybe_compact()

            content, steps = await self._loop()
            await self._emit_usage(started)
            await self.bus.emit(
                TurnFinished(content=content, steps=steps, interrupted=self.interrupted)
            )
            await self._maybe_title()
            return content

        except asyncio.CancelledError:
            self.interrupted = True
            await self._close_dangling_tool_calls("interrupted by the user")
            await self._emit_usage(started)
            await self.bus.emit(TurnFinished(content="", steps=0, interrupted=True))
            raise
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            await self.bus.emit(Notice(text=detail, level="error"))
            await self._emit_usage(started)
            await self.bus.emit(TurnFinished(content="", steps=0, error=detail))
            return f"Error: {detail}"
        finally:
            self._running = False

    async def _emit_usage(self, started: float) -> None:
        """Final usage for the turn, including wall-clock duration."""
        if self.label != "main":
            return
        await self.bus.emit(
            UsageUpdate(
                prompt_tokens=self.last_usage["prompt"],
                completion_tokens=self.last_usage["completion"],
                context_used=self.last_usage["prompt"],
                context_window=self.context_window,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        )

    # -- loop ------------------------------------------------------------

    async def _loop(self) -> tuple[str, int]:
        model = self.config.get("model")
        if not model:
            return "No model selected. Use /model to pick one.", 0

        info = await self.backend.info(model)
        if not info.supports_tools:
            await self.bus.emit(
                Notice(
                    text=f"{model} does not support tool calling; replying without tools.",
                    level="warn",
                )
            )

        self.context_window = await self.backend.effective_num_ctx(model)
        schemas = self.tools.schemas(self.allowed_tools) if info.supports_tools else None

        final_text = ""
        step = 0

        while step < self.max_steps:
            step += 1
            await self.bus.emit(StepStarted(step=step, max_steps=self.max_steps))

            assistant, tool_calls = await self._stream_step(model, schemas)
            if assistant.get("content"):
                final_text = assistant["content"]
            self._record(assistant)

            if not tool_calls:
                return final_text, step

            await self._execute_tool_calls(tool_calls)

            if self.config.get("auto_compact", True):
                await self.maybe_compact()

        await self.bus.emit(
            Notice(
                text=f"stopped after {self.max_steps} steps -- the task may be unfinished",
                level="warn",
            )
        )
        return final_text, step

    async def _stream_step(
        self, model: str, schemas: list[dict[str, Any]] | None
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        async for chunk in self.backend.chat_stream(model, self.messages, schemas):
            message = getattr(chunk, "message", None)
            if message is not None:
                thinking = getattr(message, "thinking", None)
                if thinking:
                    thinking_parts.append(thinking)
                    await self.bus.emit(ThinkingDelta(text=thinking))

                piece = getattr(message, "content", None)
                if piece:
                    content_parts.append(piece)
                    await self.bus.emit(AssistantDelta(text=piece))

                for call in getattr(message, "tool_calls", None) or []:
                    function = getattr(call, "function", None)
                    if function is None:
                        continue
                    tool_calls.append({
                        "id": uuid.uuid4().hex[:8],
                        "name": getattr(function, "name", "") or "",
                        "arguments": _parse_arguments(getattr(function, "arguments", None)),
                    })

            if getattr(chunk, "done", False):
                prompt_tokens = getattr(chunk, "prompt_eval_count", None)
                completion_tokens = getattr(chunk, "eval_count", None)
                if prompt_tokens:
                    self.last_usage["prompt"] = int(prompt_tokens)
                if completion_tokens:
                    self.last_usage["completion"] += int(completion_tokens)
                if self.label == "main" and (prompt_tokens or completion_tokens):
                    await self.bus.emit(
                        UsageUpdate(
                            prompt_tokens=self.last_usage["prompt"],
                            completion_tokens=self.last_usage["completion"],
                            context_used=self.last_usage["prompt"],
                            context_window=self.context_window,
                        )
                    )

        content = "".join(content_parts)
        thinking = "".join(thinking_parts)
        if content or thinking:
            await self.bus.emit(AssistantMessage(content=content, thinking=thinking))

        assistant: dict[str, Any] = {"role": "assistant", "content": content}
        if thinking:
            assistant["thinking"] = thinking
        if tool_calls:
            assistant["tool_calls"] = [
                {"function": {"name": c["name"], "arguments": c["arguments"]}} for c in tool_calls
            ]
        return assistant, tool_calls

    # -- tools -----------------------------------------------------------

    async def _execute_tool_calls(self, calls: list[dict[str, Any]]) -> None:
        """Run a batch: read-only calls concurrently, mutations serially."""
        parallel: list[dict[str, Any]] = []
        serial: list[dict[str, Any]] = []
        for call in calls:
            tool = self.tools.get(call["name"])
            (parallel if tool is not None and tool.read_only else serial).append(call)

        results: dict[str, str] = {}

        if parallel:
            limit = max(1, int(self.config.get("max_parallel_tools", 4)))
            semaphore = asyncio.Semaphore(limit)

            async def guarded(call: dict[str, Any]) -> None:
                async with semaphore:
                    results[call["id"]] = await self._execute_one(call)

            await asyncio.gather(*(guarded(call) for call in parallel))

        for call in serial:
            results[call["id"]] = await self._execute_one(call)

        # keep the original order so the transcript reads naturally
        for call in calls:
            self._record({
                "role": "tool",
                "name": call["name"],
                "content": results.get(call["id"], "(no result)"),
            })

    async def _execute_one(self, call: dict[str, Any]) -> str:
        name = call["name"]
        args = call["arguments"]
        call_id = call["id"]

        tool = self.tools.get(name)
        if tool is None:
            available = ", ".join(self._active_tool_names())
            message = f"unknown tool '{name}'. Available tools: {available}"
            await self.bus.emit(
                ToolDenied(call_id=call_id, name=name, reason=message, kind="unknown")
            )
            return f"ERROR: {message}"

        if self.allowed_tools and name not in self.allowed_tools:
            message = f"tool '{name}' is not available to this agent"
            await self.bus.emit(
                ToolDenied(call_id=call_id, name=name, reason=message, kind="unavailable")
            )
            return f"ERROR: {message}"

        try:
            headline = tool.preview(args, self.ctx).title
        except Exception:
            headline = name
        await self.bus.emit(ToolStarted(call_id=call_id, name=name, args=args, headline=headline))

        decision = await self.permissions.check(tool, args, self.ctx, call_id)
        if not decision.allow:
            await self.bus.emit(
                ToolDenied(
                    call_id=call_id, name=name, reason=decision.reason, kind="permission"
                )
            )
            return (
                f"ERROR: {decision.reason}\n"
                "Do not retry this exact call. Either take a different approach "
                "or ask the user how they would like to proceed."
            )

        started = time.monotonic()
        self.ctx.runtime["current_call_id"] = call_id
        try:
            result = await tool.run(args, self.ctx)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result = ToolResult.fail(f"{type(exc).__name__}: {exc}")
        elapsed = int((time.monotonic() - started) * 1000)

        await self.bus.emit(
            ToolFinished(
                call_id=call_id,
                name=name,
                ok=result.ok,
                headline=result.headline or name,
                output=result.output,
                error=result.error,
                duration_ms=elapsed,
            )
        )
        return result.to_model_payload()

    async def _close_dangling_tool_calls(self, reason: str) -> None:
        """After an interrupt, answer any tool call the model is still awaiting."""
        if not self.messages:
            return
        last = self.messages[-1]
        if last.get("role") == "assistant" and last.get("tool_calls"):
            for call in last["tool_calls"]:
                self._record({
                    "role": "tool",
                    "name": call.get("function", {}).get("name", "?"),
                    "content": f"ERROR: {reason}",
                })

    # -- context ---------------------------------------------------------

    def estimated_tokens(self) -> int:
        if self.last_usage["prompt"]:
            return self.last_usage["prompt"]
        total = 0
        for message in self.messages:
            content = message.get("content")
            if isinstance(content, str):
                total += estimate_tokens(content)
            for call in message.get("tool_calls") or []:
                total += estimate_tokens(json.dumps(call, default=str))
        return total

    async def maybe_compact(self) -> bool:
        window = self.context_window or await self.backend.effective_num_ctx(
            self.config.get("model") or ""
        )
        if not window:
            return False
        threshold = float(self.config.get("compact_threshold", 0.82))
        if self.estimated_tokens() < window * threshold:
            return False
        return await self.compact()

    async def compact(self) -> bool:
        """Replace old history with a model-written summary of it.

        The previous implementation truncated each message to 200 characters and
        called that a summary; this asks the model to actually write one.
        """
        keep = max(2, int(self.config.get("keep_recent_messages", 8)))
        system = self.messages[0] if self.messages and self.messages[0]["role"] == "system" else None
        body = self.messages[1:] if system else self.messages[:]
        if len(body) <= keep + 2:
            return False

        older, recent = body[:-keep], body[-keep:]
        # never start the retained window with an orphaned tool result
        while recent and recent[0].get("role") == "tool":
            older.append(recent.pop(0))
        if not older:
            return False

        await self.bus.emit(Notice(text="compacting conversation…", level="info"))

        transcript_lines: list[str] = []
        for message in older:
            role = message.get("role", "?")
            content = message.get("content") or ""
            if role == "tool":
                content = str(content)[:600]
            transcript_lines.append(f"[{role}] {content}")
            for call in message.get("tool_calls") or []:
                function = call.get("function", {})
                transcript_lines.append(
                    f"[assistant calls {function.get('name')}] {str(function.get('arguments'))[:300]}"
                )

        try:
            summary = await self.backend.chat_once(
                self.config.get("model"),
                [
                    {"role": "system", "content": SUMMARY_PROMPT},
                    {"role": "user", "content": "\n".join(transcript_lines)[-40000:]},
                ],
                max_tokens=1600,
            )
        except Exception as exc:
            await self.bus.emit(Notice(text=f"compaction failed: {exc}", level="warn"))
            return False

        if not summary.strip():
            return False

        rebuilt: list[dict[str, Any]] = []
        if system:
            rebuilt.append(system)
        rebuilt.append({
            "role": "user",
            "content": (
                "[Earlier conversation was compacted to save context. "
                "Summary of what happened:]\n\n" + summary
            ),
        })
        rebuilt.append({
            "role": "assistant",
            "content": "Understood -- continuing from that state.",
        })
        rebuilt.extend(recent)
        self.messages = rebuilt
        self.last_usage["prompt"] = 0

        await self.bus.emit(
            Notice(text=f"compacted {len(older)} messages into a summary", level="success")
        )
        return True

    # -- extras ----------------------------------------------------------

    async def _inject_skills(self, text: str) -> None:
        if not self.skills:
            return
        limit = int(self.config.get('skills.max_active', 2))
        matched = self.skills.activate_for(text, limit=limit)
        if not matched:
            return
        for skill in matched:
            self.messages.append({
                "role": "system",
                "content": f"# Skill: {skill.name}\n\n{skill.content}",
            })
            await self.bus.emit(Notice(text=f"loaded skill: {skill.name}", level="info"))

    async def _maybe_title(self) -> None:
        if not self.session or self.label != "main":
            return
        info = self.session.info()
        if not info or info.get("title"):
            return
        exchanges = [m for m in self.messages if m.get("role") in ("user", "assistant")]
        if len(exchanges) < 2:
            return
        excerpt = "\n".join(
            f"{m['role']}: {str(m.get('content') or '')[:400]}" for m in exchanges[:4]
        )
        try:
            title = await self.backend.chat_once(
                self.config.get("model"),
                [{"role": "system", "content": TITLE_PROMPT}, {"role": "user", "content": excerpt}],
                max_tokens=32,
            )
        except Exception:
            return
        title = title.strip().strip('"').splitlines()[0][:60] if title.strip() else ""
        if title:
            await asyncio.to_thread(self.session.set_title, title)

    def _record(self, message: dict[str, Any]) -> None:
        self.messages.append(message)
        if self.session and self.label == "main":
            try:
                self.session.append(message)
            except Exception:
                pass

    def load_messages(self, messages: Sequence[dict[str, Any]]) -> None:
        self.messages = [dict(m) for m in messages]
        self.last_usage = {"prompt": 0, "completion": 0}

    def clear(self) -> None:
        system = self.messages[0] if self.messages and self.messages[0]["role"] == "system" else None
        self.messages = [system] if system else []
        self.last_usage = {"prompt": 0, "completion": 0}


# ---------------------------------------------------------------------------
# Subagents
# ---------------------------------------------------------------------------


class SubagentRunner:
    """Runs a scoped agent in its own context and returns only its final text."""

    def __init__(self, parent: Agent, registry: Any):
        self.parent = parent
        self.registry = registry

    async def run(self, agent_name: str, task: str, call_id: str) -> str:
        definition: AgentDefinition | None = self.registry.get(agent_name)
        if definition is None:
            return (
                f"ERROR: unknown subagent '{agent_name}'. "
                f"Available: {', '.join(self.registry.names())}"
            )

        await self.parent.bus.emit(
            SubagentStarted(call_id=call_id, agent=agent_name, task=task)
        )

        system_prompt = definition.system_prompt or f"You are the {agent_name} agent."
        system_prompt += (
            f"\n\nWorking directory: {self.parent.workdir}\n"
            "Your entire reply is returned to the agent that delegated to you. "
            "Report findings and outcomes, not narration."
        )

        child = Agent(
            config=self.parent.config,
            backend=self.parent.backend,
            tools=self.parent.tools,
            bus=self.parent.bus,
            permissions=self.parent.permissions,
            workdir=self.parent.workdir,
            session=None,
            checkpoints=self.parent.ctx.checkpoints,
            skills=None,
            system_prompt=system_prompt,
            allowed_tools=definition.tools or None,
            max_steps=definition.max_steps,
            label=f"sub:{agent_name}",
        )
        child.ctx.read_files = self.parent.ctx.read_files

        try:
            result = await child.run(task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result = f"ERROR: subagent failed: {exc}"

        await self.parent.bus.emit(
            SubagentFinished(
                call_id=call_id,
                agent=agent_name,
                result=result,
                ok=not result.startswith("ERROR:"),
            )
        )
        return result or "(subagent returned nothing)"
