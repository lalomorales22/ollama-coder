"""Planning tools: think and todo_write.

`todo_write` is not busywork -- for a local model it is the single most
effective anti-drift device available. The list is echoed back into the
conversation and rendered in the sidebar, so both the model and the human can
see what is left.
"""

from __future__ import annotations

from typing import Any

from ..core.events import TodosChanged
from .base import Preview, Tool, ToolContext, ToolResult

VALID_STATUS = ("pending", "in_progress", "completed")
STATUS_MARK = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}


class ThinkTool(Tool):
    name = "think"
    kind = "read"
    read_only = True
    description = (
        "Think a problem through in writing before acting. Use it when a task "
        "has several possible approaches, when a tool result surprised you, or "
        "before a risky change. Nothing is executed -- this is scratch space."
    )
    parameters = {
        "type": "object",
        "properties": {
            "thought": {"type": "string", "description": "Your reasoning."},
        },
        "required": ["thought"],
    }

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        return Preview(title="Think", detail=str(args.get("thought", ""))[:400], kind="read")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        thought = str(args.get("thought", "")).strip()
        if not thought:
            return ToolResult.fail("thought is empty")
        first_line = thought.splitlines()[0][:70]
        return ToolResult.succeed(
            "Noted. Now act on that reasoning.",
            headline=f"thought: {first_line}",
            thought=thought,
        )


class TodoWriteTool(Tool):
    name = "todo_write"
    kind = "read"
    read_only = True
    description = (
        "Record or update the task list for multi-step work. Send the FULL list "
        "every time -- it replaces the previous one. Exactly one item should be "
        "in_progress at a time. Mark items completed as soon as they are done, "
        "not in a batch at the end."
    )
    parameters = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "Imperative, e.g. 'Add retry to the fetch client'."},
                        "status": {"type": "string", "enum": list(VALID_STATUS)},
                    },
                    "required": ["task", "status"],
                },
            }
        },
        "required": ["todos"],
    }

    def __init__(self) -> None:
        self.todos: list[dict[str, str]] = []

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        return Preview(title="Update task list", detail="", kind="read")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raw = args.get("todos")
        if not isinstance(raw, list):
            return ToolResult.fail("todos must be a list")

        cleaned: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            task = str(item.get("task") or item.get("content") or "").strip()
            if not task:
                continue
            status = str(item.get("status", "pending")).lower()
            if status not in VALID_STATUS:
                status = "pending"
            cleaned.append({"task": task, "status": status})

        if not cleaned:
            return ToolResult.fail("no valid todo items")

        active = [t for t in cleaned if t["status"] == "in_progress"]
        self.todos = cleaned
        ctx.runtime["todos"] = cleaned
        await ctx.bus.emit(TodosChanged(todos=cleaned))

        rendered = "\n".join(f"{STATUS_MARK[t['status']]} {t['task']}" for t in cleaned)
        done = sum(1 for t in cleaned if t["status"] == "completed")
        note = ""
        if len(active) > 1:
            note = "\n(keep only one task in_progress at a time)"
        return ToolResult.succeed(
            f"Task list ({done}/{len(cleaned)} done):\n{rendered}{note}",
            headline=f"todos {done}/{len(cleaned)}",
            todos=cleaned,
        )
