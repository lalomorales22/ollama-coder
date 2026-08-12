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

Two things matter, in this order.

**1. It must support tool calling.** Without it the model cannot read or edit
anything — it can only talk. `--models` marks which of yours qualify:

```
$ ollama-coder --models
→ qwen3.6:latest    36.0B   262,144 ctx  tools+vision+thinking
  gpt-oss:20b       20.9B   131,072 ctx  tools+thinking
  gemma4:12b-it-qat 11.9B   262,144 ctx  tools+vision+thinking
  codellama:13b     13.0B    16,384 ctx
```

**2. Prefer a Mixture-of-Experts model.** This matters more than parameter count
on Apple Silicon, where memory bandwidth is the bottleneck. An MoE model only
activates a fraction of its weights per token, so a *bigger* MoE routinely beats
a smaller dense model. Measured on a 32GB M4 Mac mini, warm, at 32k context:

| model | | tok/s |
|---|---|---|
| `qwen3.6` | 36B **MoE** (256 experts, 8 active), 23GB | **30.6** |
| `Qwythos-9B-1M` | 9B dense, 6.8GB | 16.8 |
| `gpt-oss:20b` | 20B **MoE**, 13GB | 14.0 |
| `gemma4:12b-it-qat` | 12B dense, 7.2GB | 12.4 |

The largest model on the list is also the fastest, by 2×.

Rough guidance: **`qwen3.6`** or another 30B-class MoE if you have 32GB;
**`gpt-oss:20b`** at 16GB, or when you want more room for context; `qwen3:4b`
for a quick laptop. Below ~7B, tool calling gets unreliable enough to be
frustrating.

### Context size, and why bigger is not better

Models advertise enormous windows — 262k, even 1M. Those numbers are not usable
memory budgets. The KV cache grows linearly with context and lives in the same
RAM as the weights, so the ceiling is set by your machine, not the model card.

Measured on the same 32GB M4, footprint by context (`ollama ps`):

| | 32k | 128k | 256k | 512k | 1M |
|---|---|---|---|---|---|
| 9B dense, default KV | 7.3 GB | 10 GB | *failed to load* | — | — |
| 9B dense, **8-bit KV** | 6.8 GB | 7.9 GB | 10 GB | 15 GB | 26 GB ⚠️ |

⚠️ = spilled to CPU. A "1M context" model on a 32GB machine is arithmetic that
does not close: the cache alone wants ~28GB, and prompt processing at that
length costs minutes per turn before a single token comes back.

**Quantise the KV cache.** It roughly halves the cost for no measurable speed
penalty, and it is the single highest-value setting on this hardware:

```bash
OLLAMA_FLASH_ATTENTION=1
OLLAMA_KV_CACHE_TYPE=q8_0
```

Export those where your Ollama **server** can see them, then restart it. On
macOS the server is launched by the GUI and will not read your shell profile —
use `launchctl setenv OLLAMA_FLASH_ATTENTION 1` before relaunching `Ollama.app`,
or a small LaunchAgent to make it stick across reboots.

With those set, `qwen3.6` went from spilling to CPU at 64k to sitting at 23GB
**fully on the GPU**, at the same 30 tok/s.

OllamaCoder requests 32k by default, or 64k when it can tell the cache is
quantised. Set it explicitly with `ollama.context_ceiling`, and confirm with
`ollama-coder --doctor`:

```
context      requesting 65,536 of 262,144 advertised
```

After raising it, check `ollama ps` still reports `100%` GPU. The moment it
shows a CPU percentage you have crossed the cliff and everything gets slow.

None of this limits how much work you can do in a session — the agent compacts
the conversation automatically as it fills the window.

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

## Keeping the model current

A local model's training data is frozen. Ask it for three.js and it will happily
write `THREE.Geometry`, deleted back in r125. Three mechanisms push against that,
and they compose:

**It is told what you actually have installed.** On every session the project is
fingerprinted — `package.json` + `node_modules`, `pyproject.toml` + your venv,
`Cargo.toml`, `go.mod` — and the *installed* versions go into the system prompt,
not the declared ranges. `^0.160.0` says very little; `three 0.185.1` is a fact
the model can act on. Libraries known to churn are called out explicitly, which
turns "recall the API" into "go check the API".

**It can search, with no API key.** `web_search` scrapes DuckDuckGo by default —
free, nothing to sign up for — and `fetch_url` reads any result in full. Set
`OLLAMA_API_KEY` and it upgrades to Ollama's hosted search automatically; set
`web.search_endpoint` to use your own provider.

**It checks the source first.** The bundled `threejs` skill teaches it to read
`node_modules/three` and the bundled typings before writing anything, and to
search with the revision in the query. That is deliberately procedural rather
than a list of API facts — a hardcoded list would go stale the same way the
model did.

```
❯ what version of three is here, and does THREE.Geometry still exist?
  $ bash   cat node_modules/three/package.json | grep version
  $ bash   grep -rn "class Geometry" node_modules/three/src/
  three.js 0.185.1 (r185). THREE.Geometry does not exist — no match in the
  installed source. It was removed in r125; everything is BufferGeometry.
```

### Skills

Skills are folders whose content is injected **only when your message matches
their keywords**, so expertise costs no context until it is relevant. At most
two load per turn (`skills.max_active`), best match first.

`threejs` ships built in. To bring over everything you have written for Claude
Code:

```bash
ollama-coder --import-skills              # all of them
ollama-coder --import-skills frontend-design math-olympiad
```

This reads `~/.claude/skills` and `~/.claude/plugins`, copies each skill into
`~/.ollamacode/skills/`, and writes a `skill.yaml` with keywords inferred from
the name and description — Claude's format has none, and progressive loading
needs something to match on. Inferred keywords are a starting point: edit
`<skill>/skill.yaml` to tune when it fires. Re-running never overwrites your
edits. `/skills` lists what is loaded, `/skills import` does the same from
inside the app.

Writing one from scratch is just a folder:

```
~/.ollamacode/skills/our-api/
  SKILL.md     # the expertise, injected verbatim on a match
  skill.yaml   # name, description, keywords
```

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

**Skills** — see [Keeping the model current](#keeping-the-model-current) above; `--import-skills` brings over your Claude Code ones.

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
