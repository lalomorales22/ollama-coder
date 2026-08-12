"""Non-interactive runner for pipes, scripts and CI.

    ollama-coder -p "fix the failing test" --yolo
    ollama-coder -p "review the diff" --output json --read-only
    git diff | ollama-coder -p "review this diff"

Exit codes: 0 success · 1 error · 2 needed a human (approval or interrupt).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any

from .core.agent import Agent
from .core.checkpoints import CheckpointStore
from .core.config import Config
from .core.events import (
    AssistantDelta,
    Event,
    EventBus,
    Notice,
    ThinkingDelta,
    ToolDenied,
    ToolFinished,
    ToolStarted,
)
from .core.extensions import AgentRegistry, SkillRegistry
from .core.llm import OllamaBackend
from .core.permissions import PermissionEngine
from .core.session import SessionStore
from .mcpx import MCPManager
from .tools import build_registry

RESET = "\033[0m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"


class HeadlessRunner:
    def __init__(
        self,
        config: Config,
        *,
        output_format: str = "text",
        quiet: bool = False,
        read_only: bool = False,
        show_thinking: bool = False,
    ):
        self.config = config
        self.output_format = output_format
        self.quiet = quiet or output_format == "json"
        self.read_only = read_only
        self.show_thinking = show_thinking

        self.bus = EventBus()
        self.backend = OllamaBackend(config)
        self.tool_events: list[dict[str, Any]] = []
        self.denied = False
        self.colour = sys.stderr.isatty()

    def _c(self, code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if self.colour else text

    def _on_event(self, event: Event) -> None:
        if isinstance(event, AssistantDelta):
            if self.output_format == "text":
                sys.stdout.write(event.text)
                sys.stdout.flush()
        elif isinstance(event, ThinkingDelta) and self.show_thinking:
            sys.stderr.write(self._c(DIM, event.text))
        elif isinstance(event, ToolStarted):
            if not self.quiet:
                sys.stderr.write(self._c(CYAN, f"\n▸ {event.name}: {event.headline}\n"))
        elif isinstance(event, ToolFinished):
            self.tool_events.append({
                "tool": event.name,
                "ok": event.ok,
                "headline": event.headline,
                "duration_ms": event.duration_ms,
                "error": event.error,
            })
            if not self.quiet:
                mark = self._c(GREEN, "  ✓ ") if event.ok else self._c(RED, "  ✗ ")
                sys.stderr.write(f"{mark}{event.headline}\n")
        elif isinstance(event, ToolDenied):
            # Only a real permission refusal means a human was needed; a
            # hallucinated tool name is just a failed call the model recovers from.
            if event.kind == "permission":
                self.denied = True
            self.tool_events.append({
                "tool": event.name, "ok": False, "error": event.reason, "denied": event.kind,
            })
            if not self.quiet:
                sys.stderr.write(self._c(YELLOW, f"  ⊘ {event.reason}\n"))
        elif isinstance(event, Notice) and not self.quiet:
            colour = {"error": RED, "warn": YELLOW, "success": GREEN}.get(event.level, DIM)
            sys.stderr.write(self._c(colour, f"· {event.text}\n"))

    async def run(self, prompt: str, timeout: float | None = None) -> int:
        started = time.monotonic()
        result: dict[str, Any] = {
            "ok": False,
            "response": "",
            "error": None,
            "tools": self.tool_events,
            "model": self.config.get("model"),
        }

        reachable, error = await self.backend.ping()
        if not reachable:
            return self._finish(result, error, 1, started)

        model = self.config.get("model")
        available = await self.backend.model_names()
        if not model or (available and model not in available):
            if not available:
                return self._finish(result, "no models installed", 1, started)
            model = available[0]
            self.config.set("model", model)
            result["model"] = model

        self.bus.subscribe(self._on_event)

        sessions = SessionStore()
        session_id = sessions.create(project_path=str(self.config.project_dir), model=model)
        checkpoints = CheckpointStore(
            sessions.directory(session_id),
            enabled=bool(self.config.get("checkpoints.enabled", True)),
        )

        agent_registry = AgentRegistry(self.config.project_dir)
        tools = build_registry(self.config.project_dir, self.config, agent_registry)
        if self.read_only:
            # bash belongs on this list: a shell can modify anything, so leaving
            # it registered would make --read-only a promise we cannot keep.
            # `--allow bash` is the explicit opt-back-in (to run a test suite).
            keep = set(self.config.get("permissions.auto_allow", []) or [])
            for name in ("write_file", "edit_file", "multi_edit", "git_commit",
                         "git_run", "task", "bash"):
                if name not in keep:
                    tools.unregister(name)

        mcp = MCPManager(self.config.project_dir, self.config)
        if self.config.get("mcp.enabled", True):
            for line in await mcp.connect_all():
                if not self.quiet:
                    sys.stderr.write(self._c(DIM, f"· {line}\n"))
            for tool in mcp.tools:
                tools.register(tool)

        agent = Agent(
            config=self.config,
            backend=self.backend,
            tools=tools,
            bus=self.bus,
            permissions=PermissionEngine(self.config, self.bus),
            workdir=self.config.project_dir,
            session=sessions,
            checkpoints=checkpoints,
            skills=SkillRegistry(self.config.project_dir),
        )

        exit_code = 0
        try:
            if timeout:
                response = await asyncio.wait_for(agent.run(prompt), timeout=timeout)
            else:
                response = await agent.run(prompt)
            result["response"] = response
            result["ok"] = True
            result["session"] = session_id
            if self.denied:
                exit_code = 2
                result["error"] = "one or more tool calls needed approval"
        except asyncio.TimeoutError:
            result["error"] = f"timed out after {timeout}s"
            exit_code = 2
        except KeyboardInterrupt:
            result["error"] = "interrupted"
            exit_code = 2
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            exit_code = 1
        finally:
            await tools.aclose()
            await mcp.aclose()
            await self.backend.aclose()

        return self._finish(result, result.get("error"), exit_code, started)

    def _finish(self, result: dict[str, Any], error: str | None, code: int, started: float) -> int:
        result["error"] = error
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
        if self.output_format == "json":
            result["exit_code"] = code
            print(json.dumps(result, indent=2, default=str))
        else:
            if error:
                sys.stderr.write(self._c(RED, f"\nerror: {error}\n"))
            else:
                sys.stdout.write("\n")
        return code


def read_stdin_context() -> str:
    """Piped input becomes context for the prompt."""
    if sys.stdin.isatty():
        return ""
    try:
        return sys.stdin.read()
    except (OSError, UnicodeDecodeError):
        return ""
