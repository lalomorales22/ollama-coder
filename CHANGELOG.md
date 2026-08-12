# Changelog

## 0.3.1

### Added

- **Dependency awareness.** The project is fingerprinted each session
  (`package.json` + `node_modules`, `pyproject.toml` + venv, `Cargo.toml`,
  `go.mod`) and the *installed* versions go into the system prompt. Fast-moving
  libraries are flagged so the model checks the API instead of recalling it.
- **Bundled `threejs` skill** — procedural rather than a list of API facts:
  check the installed version, read the typings, search with the revision.
- **`--import-skills`** (and `/skills import`) brings Claude Code skills across
  from `~/.claude/skills` and `~/.claude/plugins`, deriving keywords from each
  skill's name and description since Claude's format carries none.
- Skills are now **ranked and capped** (`skills.max_active`, default 2) rather
  than all-matching-at-once, which used to risk swamping a small context.
- `web_search` gained a provider chain: DuckDuckGo free by default, Ollama's
  hosted search when `OLLAMA_API_KEY` is set, or your own endpoint.

### Fixed

- **`web_search` returned unusable URLs.** Results came back as DuckDuckGo
  redirect wrappers (`//duckduckgo.com/l/?uddg=...`) with no scheme, so
  `fetch_url` rejected every one of them. They are now unwrapped to the real
  destination, making search → read actually chain.
- DuckDuckGo scraping now sends a browser user-agent, POSTs the query and falls
  back to the `lite` endpoint, so the key-less path is reliable.
- Skill parsing accepts Claude Code's format (frontmatter in `SKILL.md`, no
  separate `skill.yaml`).

## 0.3.0

A rewrite. The agent core is now async and event-driven, the front end is a
Textual TUI, and every tool call passes through a permission engine.

### Added

- **`install.sh`** — one command to set everything up: finds a suitable Python,
  installs into an isolated venv at `~/.ollamacode/venv` (no PEP 668 fights),
  links `ollama-coder` into `~/.local/bin` and offers to put that on your PATH,
  installs and starts Ollama if missing, and offers a model sized to your RAM.
  Idempotent (re-run to upgrade), with `--yes`, `--dev`, `--no-model`,
  `--model`, `--from` and `--uninstall`.
- **Textual TUI** — streaming transcript with per-tool cards, inline diffs,
  a task sidebar, context gauge, model picker and searchable help.
- **Approval flow** — every write shows a unified diff and every command shows
  its command line before running. Allow once / allow for session / deny /
  deny-with-feedback (the note is fed back to the model).
- **Permission engine** — hard denies → yolo → auto-allow list → session
  grants → per-tool heuristics → ask. Regex allow-lists for safe bash.
- **Path sandbox** — file tools are confined to the project root; `..`
  traversal and absolute paths outside it are refused. Secrets-ish files
  (`.env`, `*.pem`, `~/.ssh/**`) always require explicit approval.
- **Persistent bash session** — `cd`, exported variables and activated
  virtualenvs survive between calls. Timeouts restart the shell and restore cwd.
- **Checkpoints and undo** — every file the agent writes is snapshotted first;
  `/undo` and `ctrl+r` walk the stack back, including for untracked files.
- **MCP client** — stdio and HTTP servers from `.ollamacode/mcp.json`
  (Claude Code's format). Remote tools appear as `mcp__<server>__<tool>` and go
  through the same approval flow.
- **Subagent delegation** — a `task` tool spawns a scoped agent with its own
  context window and a narrower toolset; only its conclusion returns.
- **Todo tool** with a live sidebar, to keep multi-step work on track.
- **Git tools** split by risk: `git_read` (free), `git_commit` (reviewed),
  `git_run` (classified per sub-command).
- **`--doctor`** environment check, **`--scaffold`** example extensions,
  `--read-only`, `--yolo`, `--allow/--deny`, `--num-ctx`, `--think`, and stdin
  piping for headless runs.
- Model capability detection: `--models` shows which of yours support tools,
  vision and thinking, plus their real context length.

### Fixed

- **`pyyaml` was imported but never declared as a dependency.** Because those
  imports were wrapped in bare `except ImportError`, a clean install ran with
  the safety hooks, custom commands, subagents and skills all silently
  disabled — including the bash guardrails.
- **The system prompt was only attached when an `OLLAMA.md` existed.** Without
  one the agent ran with no system prompt at all.
- **`num_ctx` was never sent to Ollama**, so every model silently truncated to
  its default window no matter what the advertised context length was. The real
  context length is now read from the model metadata and requested.
- **Compaction did not summarise.** It truncated each message to 200 characters
  and labelled the result a summary. It now asks the model for a structured
  summary and never orphans a tool result at the window boundary.
- **Session resume dropped `tool_calls`**, replaying a corrupted history.
  Transcripts are now stored and restored losslessly.
- Subagents, skills and the todo/task systems existed but were never wired to
  anything callable.
- `--headless` with no prompt sent an empty message to the model.
- `--max-tools` claimed to cap tool calls but set the tool-round limit.
- Token accounting used `len(text)/4`; it now uses the counts Ollama reports.
- Interrupting mid-turn left tool calls unanswered, corrupting the next request.

### Changed

- Requires Python 3.10+.
- `--no-write` → `--read-only`; `--max-tools` → `--max-steps`; `--headless` is
  implied by `-p/--prompt`.
- `search_code` → `grep`, `list_directory` → `list_dir`, `git` → `git_read` /
  `git_commit` / `git_run`.
- Config keys are dotted paths (`permissions.default`) and merge deeply across
  defaults → user → project → env → flags.
- 123 tests, all running offline against a scripted fake backend.

### Removed

- `screenshot` (Playwright): a heavyweight optional dependency for a tool the
  agent almost never chose. Use `bash` with your own tooling.
- The `hooks.py` YAML hook system, superseded by the permission engine.

## 0.2.x

See the git history.
