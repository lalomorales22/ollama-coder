# OllamaCoder

**An autonomous coding agent that runs entirely on your machine.** Point it at a project, give it a task, and it reads, edits, runs and verifies your code — using local models through [Ollama](https://ollama.com). No API keys, no telemetry, nothing leaves the box.

```bash
git clone https://github.com/lalomorales22/ollama-coder && cd ollama-coder
./install.sh
```

Then, from any project:

```bash
cd ~/your-project
ollama-coder
```

---

## What it does

```
┌─ ◢◤ qwen3:8b   git:main*   ses:7cb6c5   [ask]   ██████████░░░░ 65% ────────────┐
│                                                                    ┌─ TASKS ─┐ │
│ ❯ the auth middleware still reads the legacy cache — move it to    │ ● locate │ │
│   TokenStore and make the tests pass                               │ ◐ rewrite│ │
│                                                                    │ ○ verify │ │
│ ~ thought for 82 words                                             └──────────┘ │
│   ▫ grep    TokenStore: 4 matches                                               │
│   ▪ read_file    src/auth/middleware.py                                         │
│                                                                                 │
│ Found it. `middleware.py:41` still reads `SESSION_CACHE` directly.              │
│                                                                                 │
│   ◆ edit_file   src/auth/middleware.py (+6 -4)                                  │
│     - token = SESSION_CACHE.get(raw)                                            │
│     + token = await store.get(raw)                                              │
│   $ bash   pytest tests/test_auth.py → 8 passed                        2.4s     │
└─────────────────────────────────────────────────────────────────────────────────┘
```

- **Real agent loop** — plans, calls tools, reads the results, corrects itself, verifies with your test suite.
- **Approval before anything lands.** Every edit is shown as a diff and every command as a command line, before it runs. Allow once, allow for the session, or deny with a note that steers the model.
- **Persistent shell.** `cd`, `export` and activated virtualenvs survive between tool calls, because it is one long-lived bash session, not a series of subprocesses.
- **Deep git integration** — status/diff/log/blame are free, commits are reviewed, and destructive operations always ask.
- **MCP client** — connect any [Model Context Protocol](https://modelcontextprotocol.io) server and its tools join the agent's toolset.
- **Undo.** Every file the agent touches is snapshotted first. `ctrl+r` walks it back, even for untracked files and non-git directories.
- **Sessions** that persist, resume, branch and full-text search.
- **Subagents** with their own context window and a narrower toolset, for work that would otherwise flood the conversation.

---

## Install

```bash
git clone https://github.com/lalomorales22/ollama-coder && cd ollama-coder
./install.sh
```

`install.sh` does the whole setup and tells you what it is doing at each step:

- finds a Python 3.10+ (and says how to get one if you have none);
- installs into its own virtualenv at `~/.ollamacode/venv`, so it can never
  collide with your system Python or trip over a PEP 668 *"externally managed
  environment"* error;
- links the launcher into `~/.local/bin`, and offers to add that to your `PATH`
  if it isn't already there — after this, `ollama-coder` just works from any
  directory;
- installs and starts **Ollama** if it's missing;
- checks whether any of your models can actually call tools, and offers to pull
  one sized to your RAM if not;
- finishes with `--doctor` so you can see the result.

It is safe to re-run — that is also how you upgrade.

```bash
./install.sh --yes                  # don't ask anything (CI, dotfiles, scripts)
./install.sh --no-model             # skip the model download
./install.sh --model qwen3:14b      # pull a specific one
./install.sh --dev                  # editable install: tracks your edits to the clone
./install.sh --uninstall            # remove it (sessions and settings are kept)
```

<details>
<summary>Other ways to install</summary>

```bash
# no clone — installs straight from main
curl -fsSL https://raw.githubusercontent.com/lalomorales22/ollama-coder/main/install.sh | bash

# your own Python tooling, if you'd rather manage it yourself
uv tool install "ollama-coder[all]"
pipx install "ollama-coder[all]"
pip install "ollama-coder[all]"     # needs a venv on most modern systems
```

`OLLAMACODER_HOME` and `OLLAMACODER_BIN` override where the venv and the
launcher go. Note that PyPI may lag behind `main`; the installer's default
source is this repository.

</details>

Requires Python 3.10+ and Ollama. To re-check the environment at any point:

```bash
ollama-coder --doctor
```

> **`command not found: ollama-coder`?** The launcher lives at
> `~/.local/bin/ollama-coder`. Open a new terminal (shell profile changes only
> apply to new sessions), or add it yourself:
> `echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc`

### Choosing a model

The agent needs a model with **tool-calling support** — without it the model can't read or edit anything. `--models` marks which of yours qualify:

```
$ ollama-coder --models
→ qwen3:8b          8.2B    32,768 ctx  tools+thinking
  gemma3:12b       11.9B   131,072 ctx  tools+vision+thinking
  codellama:13b    13.0B    16,384 ctx
```

Good starting points: `qwen3:8b` (best quality/size trade-off), `gpt-oss:20b` (stronger reasoning), `qwen3:4b` (fast on a laptop). Below ~7B parameters, tool calling gets unreliable.

---

## Using it

### Interactive

```bash
ollama-coder                     # in the project you want to work on
ollama-coder --dir ~/code/api    # or point at one
ollama-coder --continue          # pick up the last session here
```

| Key | |
|---|---|
| `enter` | send · `ctrl+j` for a newline |
| `ctrl+c` | interrupt the current turn (it keeps everything done so far) |
| `ctrl+y` | toggle YOLO mode (no approval prompts) |
| `ctrl+r` | undo the last file change |
| `ctrl+b` | toggle the sidebar |
| `F1` | full help |
| click a tool card | expand its output |

`/help` lists every slash command. The ones you'll actually use:

| | |
|---|---|
| `/model` | pick a model from a searchable list |
| `/diff` `/commit <msg>` `/git <args>` | git, without leaving |
| `/undo` `/checkpoints` | revert what the agent wrote |
| `/compact` `/context` | manage the context window |
| `/resume` `/sessions` `/search <text>` `/branch` | session control |
| `/init` | write an `OLLAMA.md` for the current project |
| `/mcp` `/tools` `/agents` `/skills` | see what's connected |

### Headless

```bash
ollama-coder -p "why is test_auth failing?"          # streams to stdout
ollama-coder -p "fix the lint errors" --yolo         # no prompts
git diff | ollama-coder -p "review this"             # stdin becomes context
ollama-coder -p "audit deps" --output json           # machine-readable
ollama-coder -p "check for TODOs" --read-only        # nothing can write, bash included
```

Exit codes: `0` success · `1` error · `2` needed a human (approval or timeout). Without `--yolo`, anything that would modify the project is refused in headless mode and the run exits `2` — safe by default in CI.

`--read-only` goes further and unregisters the mutating tools outright, `bash` included, since a shell can modify anything. Add one back explicitly when you need it: `--read-only --allow bash` for a review that also runs the test suite.

---

## Permissions

The agent asks before it changes anything. Reading, searching and inspecting git are free.

```
┌ EDIT FILE   src/auth/middleware.py (+6 -4) ──────────────────────┐
│  @@ -36,12 +36,14 @@ async def authenticate(request):           │
│  -    token = SESSION_CACHE.get(raw)                             │
│  +    token = await store.get(raw)                               │
│                                                                  │
│   y allow once    a allow all session    n deny    e deny + why  │
└──────────────────────────────────────────────────────────────────┘
```

**e** is the useful one: deny and tell the agent what to do instead ("use `uv`, not pip") — the note goes straight back into the conversation.

Tune it in `.ollamacode/settings.json`:

```jsonc
{
  "permissions": {
    "default": "ask",
    "auto_allow": ["read_file", "grep", "glob", "list_dir", "git_read"],
    "allow_bash": ["^pytest", "^npm run "],   // regexes, auto-approved
    "deny": ["web_search"],                   // never, not even in YOLO
    "yolo": false
  },
  "sandbox": {
    "enabled": true,                          // file tools stay in the project
    "extra_roots": ["~/shared-libs"],
    "protected_globs": ["**/.env", "**/*.pem"]  // always require approval
  }
}
```

Some things are refused unconditionally, YOLO or not: `rm -rf /`, `mkfs`, raw writes to block devices, fork bombs, and shutting down the host.

**One honest caveat:** the path sandbox constrains the *file* tools. `bash` is a shell — it can reach anywhere your user can. What bounds it is the approval prompt, so think before turning on YOLO in a directory that matters.

---

## Configure

Settings merge lowest-to-highest: defaults → `~/.ollamacode/settings.json` → `<project>/.ollamacode/settings.json` → environment → CLI flags.

```jsonc
{
  "model": "qwen3:8b",
  "temperature": 0.6,
  "num_ctx": null,          // null = read the model's real context length
  "max_steps": 40,
  "auto_compact": true,     // summarise the conversation before it overflows
  "bash": { "timeout_sec": 240, "persistent": true },
  "ui": { "show_thinking": true, "sidebar": true }
}
```

### `OLLAMA.md` — project instructions

Anything in `OLLAMA.md` at the project root becomes standing instruction. This is the highest-leverage file in the whole system; `/init` will draft one by reading your repo.

```markdown
# Conventions
- Package manager is `uv`. Never call pip.
- Tests: `uv run pytest -x -q`. Run them after every change.
- No new dependencies without asking.
```

### MCP servers

`.ollamacode/mcp.json` (same format Claude Code uses):

```json
{
  "mcpServers": {
    "filesystem": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."] },
    "github":     { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": { "GITHUB_TOKEN": "ghp_..." } },
    "internal":   { "url": "https://mcp.internal.example.com/mcp" }
  }
}
```

Their tools appear as `mcp__<server>__<tool>` and go through the same approval flow. `/mcp` shows status.

### Extensions

Drop files in `~/.ollamacode/` (global) or `<project>/.ollamacode/` (project wins). `ollama-coder --scaffold` writes working examples of all three.

**Commands** — `commands/review.md` becomes `/review`:

```markdown
---
name: review
description: Review uncommitted changes
---
Review my uncommitted changes. Report only real defects: file, line, what
breaks and when. $ARGUMENTS
```

**Subagents** — `agents/security-auditor.yaml`, invoked by the model through the `task` tool:

```yaml
name: security-auditor
description: Audits code for security problems. Read-only.
tools: [read_file, grep, glob, git_read]
temperature: 0.2
system_prompt: |
  You audit for injection, authz gaps and secret exposure. Report only
  issues you can point at with a file and line.
```

**Skills** — `skills/<name>/SKILL.md` plus a `skill.yaml` of keywords. The content is injected only when a keyword matches what you asked, so expertise costs no context until it's relevant.

---

## How it works

```
  cli.py ──┬── tui/          Textual app: transcript, approvals, sidebar
           └── headless.py   stdout streaming, JSON output, exit codes
                 │
                 │  both subscribe to the same event bus
                 ▼
       core/agent.py     the loop: stream → tools → results → repeat
           │
           ├── core/llm.py           Ollama: real num_ctx, capability detection
           ├── core/permissions.py   deny → yolo → auto-allow → grants → ask
           ├── core/context.py       compaction via model-written summaries
           ├── core/session.py       SQLite + JSONL, lossless resume
           ├── core/checkpoints.py   the undo stack
           ├── tools/                bash · files · search · git · web · task
           └── mcpx/                 external MCP servers
```

The agent never touches a UI. It emits typed events (`ToolStarted`, `AssistantDelta`, `PermissionAsk`, …) onto a bus; the TUI and the headless runner are both just subscribers. That is why one loop serves an interactive terminal and a CI pipeline without a branch in the agent code.

Read-only tool calls in the same batch run concurrently; anything that writes is serialised, so two edits can never race on one file.

---

## Development

```bash
./install.sh --dev         # editable: the ollama-coder command tracks your edits
pytest                     # 142 tests, no Ollama required
ruff check ollama_coder
textual run --dev ollama_coder.tui.app:OllamaCoderApp   # live CSS reload
```

Tests drive a scripted fake backend, so the whole suite runs offline in ~25s.
`OLLAMA.md` in this repo documents the layout, the conventions and the traps
that have already bitten us — worth reading before your first change.

## License

MIT — see [LICENSE](LICENSE).
