# OllamaCoder — project instructions

This file is read by OllamaCoder itself when working in this repository.

## What this is

A local coding agent. `cli.py` chooses between the Textual TUI (`tui/`) and the
headless runner (`headless.py`); both subscribe to an event bus that the agent
core (`core/`) emits onto. The agent never touches a UI directly — if you find
yourself printing from `core/`, emit an event instead.

## Layout

| path | responsibility |
|---|---|
| `core/agent.py` | the loop: stream → tools → results → repeat |
| `core/llm.py` | Ollama client, capability + context-length detection |
| `core/permissions.py` | the allow/ask/deny decision order |
| `core/events.py` | typed events and the bus |
| `core/session.py` | SQLite + JSONL persistence |
| `core/checkpoints.py` | the undo stack |
| `tools/` | one module per family; all subclass `tools/base.py:Tool` |
| `mcpx/` | MCP client (named `mcpx` so it never shadows the `mcp` package) |
| `tui/` | Textual widgets, screens and `app.tcss` |

## Conventions

- Everything I/O bound is `async`. Wrap blocking calls in `asyncio.to_thread`.
- New tools subclass `Tool`, set `read_only` honestly (it decides both parallel
  execution and auto-approval), and implement `preview()` — that is what the
  approval dialog shows.
- Tools return `ToolResult`, never raise for expected failures. The `error`
  string is read by the model, so make it say what to do next.
- Paths from the model go through `ctx.resolve()`. Never touch `Path` directly
  in a tool; that is the sandbox boundary.
- No new runtime dependencies without asking. `mcp` is optional and every
  import of it is guarded.

## Testing

```bash
pytest -q                       # whole suite, ~10s, no Ollama needed
pytest tests/test_agent.py -q   # loop and permissions
```

Tests drive a scripted `FakeBackend` (see `tests/test_agent.py`). Add cases
there rather than mocking `ollama` itself. TUI tests use Textual's
`run_test()` harness with an `OfflineBackend`.

Run the suite after every change. A change that has not been run is a guess.

## Hardware notes (measured on a 32GB M4)

- MoE beats dense on Apple Silicon: `qwen3.6` (36B MoE) runs 30 tok/s where a
  12B dense model runs 12. Memory bandwidth is the bottleneck, and MoE
  activates a fraction of its weights per token.
- `OLLAMA_FLASH_ATTENTION=1` + `OLLAMA_KV_CACHE_TYPE=q8_0` roughly halve KV
  cache memory for no speed cost. Without them, 36B MoE at 64k spills to CPU.
- Watch `ollama ps`: anything less than `100%` GPU means it spilled and speed
  has fallen off a cliff.

## Traps that have already bitten us

- A Textual widget method named `_render` **overrides** `Widget._render()` and
  silently breaks layout. Name repaint helpers `_repaint`.
- A `height: 1` widget cannot also have a border — the border eats the only row.
- App key bindings need `priority=True`, because the focused `TextArea`
  swallows control keys it does not bind.
- `CheckpointStore` defines `__len__`, so `if store:` is False when empty.
  Compare with `is not None`.
- Never re-add a bare `except ImportError: Module = None` around a hard
  dependency. That is how 0.2.x shipped with its safety hooks silently disabled.
