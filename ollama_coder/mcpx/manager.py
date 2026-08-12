"""Model Context Protocol client.

Servers are declared in `mcp.json` (the same shape Claude Code uses) or under
`mcp.servers` in settings.json:

    {
      "mcpServers": {
        "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
        "docs":       {"url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ..."}}
      }
    }

Each server's tools are exposed to the model as `mcp__<server>__<tool>`.

Connection lifetime is owned by one dedicated task per server: the MCP session
is entered and exited inside the same task (anyio requires it), while calls are
made from whichever task needs them.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from ..tools.base import Preview, Tool, ToolContext, ToolResult, truncate_output

try:
    from mcp import Client, StdioServerParameters, stdio_client

    MCP_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    MCP_AVAILABLE = False
    Client = None  # type: ignore[assignment]


def load_server_specs(project_dir: Path, config: Any) -> dict[str, dict[str, Any]]:
    """Merge mcp.json files and settings.json; project entries win."""
    specs: dict[str, dict[str, Any]] = {}

    for path in (
        Path.home() / ".ollamacode" / "mcp.json",
        Path(project_dir) / ".ollamacode" / "mcp.json",
        Path(project_dir) / ".mcp.json",
    ):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        servers = data.get("mcpServers") or data.get("servers") or {}
        if isinstance(servers, dict):
            specs.update({str(k): v for k, v in servers.items() if isinstance(v, dict)})

    configured = config.get("mcp.servers", {}) or {}
    if isinstance(configured, dict):
        specs.update({str(k): v for k, v in configured.items() if isinstance(v, dict)})

    return {name: spec for name, spec in specs.items() if not spec.get("disabled")}


def _content_to_text(result: Any) -> str:
    """Flatten an MCP CallToolResult into something a text model can read."""
    chunks: list[str] = []
    for block in getattr(result, "content", None) or []:
        kind = getattr(block, "type", "")
        if kind == "text":
            chunks.append(getattr(block, "text", "") or "")
        elif kind == "image":
            mime = getattr(block, "mimeType", None) or getattr(block, "mime_type", "image")
            chunks.append(f"[image content: {mime}]")
        elif kind == "resource":
            resource = getattr(block, "resource", None)
            text = getattr(resource, "text", None)
            uri = getattr(resource, "uri", "")
            chunks.append(text or f"[resource: {uri}]")
        else:
            chunks.append(str(block))

    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if structured and not chunks:
        try:
            chunks.append(json.dumps(structured, indent=2, default=str))
        except (TypeError, ValueError):
            chunks.append(str(structured))

    return "\n".join(c for c in chunks if c).strip()


class MCPServer:
    """One connected server, kept alive by its own task."""

    def __init__(self, name: str, spec: dict[str, Any], timeout: float = 30.0):
        self.name = name
        self.spec = spec
        self.timeout = timeout
        self.tools: list[Any] = []
        self.error: str | None = None
        self.instructions: str = ""
        self._client: Any = None
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self._client is not None and self.error is None

    def _make_transport(self) -> Any:
        url = self.spec.get("url") or self.spec.get("endpoint")
        if url:
            return str(url)
        command = self.spec.get("command")
        if not command:
            raise ValueError("server spec needs either 'command' or 'url'")
        env = {**os.environ, **{str(k): str(v) for k, v in (self.spec.get("env") or {}).items()}}
        params = StdioServerParameters(
            command=str(command),
            args=[str(a) for a in (self.spec.get("args") or [])],
            env=env,
            cwd=self.spec.get("cwd"),
        )
        return stdio_client(params)

    async def _run(self) -> None:
        try:
            async with Client(self._make_transport(), read_timeout_seconds=self.timeout) as client:
                listing = await client.list_tools()
                self.tools = list(getattr(listing, "tools", []) or [])
                self.instructions = getattr(client, "instructions", "") or ""
                self._client = client
                self._ready.set()
                await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self._client = None
            self._ready.set()

    async def start(self) -> bool:
        self._task = asyncio.create_task(self._run(), name=f"mcp:{self.name}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self.timeout)
        except asyncio.TimeoutError:
            self.error = f"timed out after {self.timeout:.0f}s"
            await self.stop()
            return False
        return self.connected

    async def call(self, tool: str, args: dict[str, Any], timeout: float = 120.0) -> Any:
        if not self._client:
            raise RuntimeError(f"MCP server '{self.name}' is not connected")
        return await asyncio.wait_for(self._client.call_tool(tool, args), timeout=timeout)

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
            except Exception:
                pass


class MCPTool(Tool):
    """Bridges one remote MCP tool into the local tool registry."""

    kind = "mcp"

    def __init__(self, server: MCPServer, spec: Any):
        self.server = server
        self.remote_name = getattr(spec, "name", "tool")
        self.name = f"mcp__{server.name}__{self.remote_name}"
        remote_description = getattr(spec, "description", "") or ""
        self.description = f"[{server.name}] {remote_description}".strip()
        schema = (
            getattr(spec, "input_schema", None)
            or getattr(spec, "inputSchema", None)
            or {"type": "object", "properties": {}}
        )
        self.parameters = schema if isinstance(schema, dict) else {"type": "object", "properties": {}}
        annotations = getattr(spec, "annotations", None)
        self.read_only = bool(getattr(annotations, "readOnlyHint", False)) if annotations else False

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        try:
            rendered = json.dumps(args, indent=2, default=str)[:1500]
        except (TypeError, ValueError):
            rendered = str(args)[:1500]
        return Preview(
            title=f"MCP {self.server.name} → {self.remote_name}",
            detail=rendered,
            kind="mcp",
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if not self.server.connected:
            return ToolResult.fail(f"MCP server '{self.server.name}' is not connected")
        try:
            result = await self.server.call(self.remote_name, args)
        except asyncio.TimeoutError:
            return ToolResult.fail(f"MCP call to {self.name} timed out")
        except Exception as exc:
            return ToolResult.fail(f"MCP call failed: {type(exc).__name__}: {exc}")

        text = _content_to_text(result) or "(no content)"
        is_error = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
        if is_error:
            return ToolResult.fail(truncate_output(text, 8000), headline=f"{self.remote_name} failed")
        return ToolResult.succeed(
            truncate_output(text, 20000), headline=f"{self.server.name}/{self.remote_name}"
        )


class MCPManager:
    def __init__(self, project_dir: Path, config: Any):
        self.project_dir = Path(project_dir)
        self.config = config
        self.servers: dict[str, MCPServer] = {}
        self.tools: list[MCPTool] = []

    @property
    def available(self) -> bool:
        return MCP_AVAILABLE

    async def connect_all(self) -> list[str]:
        """Connect every configured server. Returns human-readable status lines."""
        if not self.config.get("mcp.enabled", True):
            return []
        specs = load_server_specs(self.project_dir, self.config)
        if not specs:
            return []
        if not MCP_AVAILABLE:
            return [f"⚠️  {len(specs)} MCP server(s) configured but the `mcp` package is missing"]

        timeout = float(self.config.get("mcp.connect_timeout_sec", 30))
        servers = [MCPServer(name, spec, timeout) for name, spec in specs.items()]
        results = await asyncio.gather(
            *(server.start() for server in servers), return_exceptions=True
        )

        report: list[str] = []
        for server, outcome in zip(servers, results, strict=False):
            if outcome is True:
                self.servers[server.name] = server
                for spec in server.tools:
                    self.tools.append(MCPTool(server, spec))
                report.append(f"✓ mcp/{server.name}: {len(server.tools)} tool(s)")
            else:
                detail = server.error or (str(outcome) if isinstance(outcome, Exception) else "failed")
                report.append(f"✗ mcp/{server.name}: {detail}")
        return report

    def status(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "connected": server.connected,
                "tools": [getattr(t, "name", "?") for t in server.tools],
                "error": server.error,
            }
            for name, server in self.servers.items()
        ]

    async def aclose(self) -> None:
        await asyncio.gather(
            *(server.stop() for server in self.servers.values()), return_exceptions=True
        )
        self.servers.clear()
        self.tools.clear()
