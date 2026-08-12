"""Built-in tool registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Preview, SandboxError, Tool, ToolContext, ToolRegistry, ToolResult
from .files import EditFileTool, MultiEditTool, ReadFileTool, WriteFileTool, unified_diff
from .git import GitCommitTool, GitReadTool, GitRunTool
from .plan import ThinkTool, TodoWriteTool
from .search import GlobTool, GrepTool, ListDirTool
from .shell import BashTool, PersistentShell, classify_command
from .task import TaskTool
from .web import FetchUrlTool, WebSearchTool

__all__ = [
    "Preview", "SandboxError", "Tool", "ToolContext", "ToolRegistry", "ToolResult",
    "BashTool", "PersistentShell", "classify_command", "unified_diff",
    "ReadFileTool", "WriteFileTool", "EditFileTool", "MultiEditTool",
    "ListDirTool", "GlobTool", "GrepTool",
    "GitReadTool", "GitCommitTool", "GitRunTool",
    "ThinkTool", "TodoWriteTool", "TaskTool",
    "FetchUrlTool", "WebSearchTool",
    "build_registry",
]


def build_registry(
    workdir: Path,
    config: Any,
    agent_registry: Any | None = None,
    extra: list[Tool] | None = None,
) -> ToolRegistry:
    tools: list[Tool] = [
        ThinkTool(),
        TodoWriteTool(),
        BashTool(workdir, config),
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        MultiEditTool(),
        ListDirTool(),
        GlobTool(),
        GrepTool(),
        GitReadTool(),
        GitCommitTool(),
        GitRunTool(),
    ]

    if config.get("web.enabled", True):
        tools.append(FetchUrlTool())
        tools.append(WebSearchTool())

    if agent_registry is not None and config.get("subagents.enabled", True):
        tools.append(TaskTool(agent_registry))

    if extra:
        tools.extend(extra)

    return ToolRegistry(tools)
