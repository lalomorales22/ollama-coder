"""Delegation: hand a scoped job to a subagent with its own context window.

Worth it when the work would otherwise flood the main context (searching a
large repo, reviewing a big diff) or when a narrower toolset makes the model
safer. The subagent's transcript is discarded; only its conclusion comes back.
"""

from __future__ import annotations

from typing import Any

from .base import Preview, Tool, ToolContext, ToolResult


class TaskTool(Tool):
    name = "task"
    kind = "task"
    read_only = False
    description = (
        "Delegate a self-contained job to a specialist subagent. It gets a fresh "
        "context and its own tools, and returns only its findings. Use it for "
        "open-ended searching or review that would otherwise fill your context. "
        "Give it everything it needs in one prompt -- you cannot talk to it again."
    )

    def __init__(self, registry: Any):
        self.registry = registry
        names = registry.names()
        described = "\n".join(
            f"- {n}: {registry.get(n).description}" for n in names if registry.get(n)
        )
        self.description = f"{TaskTool.description}\n\nAvailable agents:\n{described}"
        self.parameters = {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "enum": names, "description": "Which agent to use."},
                "task": {
                    "type": "string",
                    "description": (
                        "Full self-contained instructions: what to look for, where, "
                        "and what to report back."
                    ),
                },
            },
            "required": ["agent", "task"],
        }

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        return Preview(
            title=f"Delegate to {args.get('agent')}",
            detail=str(args.get("task", ""))[:800],
            kind="task",
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if not ctx.config.get("subagents.enabled", True):
            return ToolResult.fail("subagents are disabled in settings")

        agent_name = str(args.get("agent", "")).strip()
        task = str(args.get("task", "")).strip()
        if not agent_name or not task:
            return ToolResult.fail("both 'agent' and 'task' are required")

        parent = ctx.runtime.get("agent")
        if parent is None:
            return ToolResult.fail("no parent agent available")

        from ..core.agent import SubagentRunner

        runner = SubagentRunner(parent, self.registry)
        call_id = ctx.runtime.get("current_call_id", "task")

        depth = int(ctx.runtime.get("subagent_depth", 0))
        if depth >= 2:
            return ToolResult.fail("subagents cannot nest more than two levels deep")
        ctx.runtime["subagent_depth"] = depth + 1
        try:
            result = await runner.run(agent_name, task, call_id)
        finally:
            ctx.runtime["subagent_depth"] = depth

        ok = not result.startswith("ERROR:")
        return ToolResult(
            ok=ok,
            output=result,
            error=None if ok else result,
            headline=f"{agent_name}: {task.splitlines()[0][:50]}",
        )
