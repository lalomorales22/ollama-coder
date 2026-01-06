# OllamaCoder Agentic Architecture Roadmap

**Target**: Give OllamaCoder the same agentic capabilities as Claude Code  
**Current Version**: 0.2.7  
**Status**: Phase 2.6 (Headless) Complete - Ready for Phase 3 (MCP)

---

## 🧠 Core Principles (From Anthropic's Claude Code Team)

### 1. Bash Is All You Need
> "The bash tool allows you to compose functionality, store results to files, dynamically generate scripts"

**Application**: Bash is king. Tools for atomic actions only.

### 2. Verification Everywhere
> "Verification can happen anywhere and should happen anywhere"

**Application**: Hooks before/after tool execution. Lint, test, verify.

### 3. Progressive Context Disclosure
> "Skills are a form of progressive context disclosure"

**Application**: Don't frontload everything. Load expertise on demand.

### 4. File System = Context Engineering
> "We think of the file system as a way of doing context engineering"

**Application**: OLLAMA.md is project constitution. Skills are folders with expertise.

### 5. Read Transcripts Obsessively
> "The metalearning is to read the transcripts over and over again"

**Application**: Build transcript viewer. Debug by reading full context.

### 6. Subagents for Scale
> "We're using more and more subagents inside Claude Code"

**Application**: Heavy research in subagent, return summary. Parallel execution.

---

## 📊 Current State

### ✅ Already Implemented
- **Core Tools**: `bash`, `read_file`, `write_file`, `edit_file`, `multi_edit`, `list_directory`, `search_code`, `git`, `web_search`, `think`
- **New Tools**: `glob`, `grep`, `fetch_url`, `screenshot` (Phase 1 ✅)
- **Config System**: Hierarchical user/project config with `OLLAMA.md` context files
- **Agentic Loop**: Plan → Execute → Verify → Iterate
- **Session Persistence**: SQLite + JSONL storage (Phase 1.5 ✅)
- **Safety Hooks**: BashSafetyParser + HookManager (Phase 1.6 ✅)
- **Extensibility**: Commands, Subagents, Skills (Phase 2 ✅)
- **Headless Mode**: CI/CD automation with JSON output (Phase 2.6 ✅)

### ❌ Remaining
| Feature | Status | Priority |
|---------|--------|----------|
| MCP Protocol | ❌ | LOW |
| Plugin System | ❌ | LOW |
| Transcript Viewer | ❌ | LOW |

---

## 🚀 Phase 1: Core Tools Enhancement ✅ COMPLETE

**Goal**: Fill tool gaps with bash-first philosophy  
**Status**: ✅ DONE  
**Priority**: ✅ COMPLETE

### 1.1 GlobTool - File Pattern Matching
- [ ] Create `GlobTool` class
- [ ] Support patterns: `*.py`, `**/*.ts`, `src/**/*.{js,jsx}`
- [ ] Return file paths with metadata

### 1.2 GrepTool - Advanced Search
- [ ] Create `GrepTool` class (enhanced `search_code`)
- [ ] Support regex, context lines, file filtering
- [ ] Use `ripgrep` if available

### 1.3 URLFetchTool - Read Web Content
- [ ] Create `URLFetchTool` class
- [ ] Fetch and parse HTML to markdown
- [ ] Configurable timeout and max length

### 1.4 ScreenshotTool - Browser Screenshots
- [ ] Create `ScreenshotTool` class
- [ ] Use Playwright for browser automation
- [ ] Return base64 for vision analysis

---

## 💾 Phase 1.5: Session Persistence ✅ COMPLETE

**Goal**: Never forget a conversation  
**Status**: ✅ DONE

### 1.5.1 Session Storage Backend ✅
- [x] `SessionManager` class in `session.py`
- [x] SQLite database with FTS5 search
- [x] JSONL append-only message storage
- [x] Storage at `~/.ollamacode/sessions/`

### 1.5.2 Session Lifecycle ✅
- [x] Auto-save every message
- [x] Auto-generate title after 3 messages
- [x] Session ID in prompt: `[abc123] You:`
- [x] Token tracking per session

### 1.5.3 Session Commands ✅
- [x] `/sessions` - List recent sessions
- [x] `/resume <id>` - Resume session
- [x] `/search <query>` - Full-text search
- [x] `/branch` - Fork session
- [x] `/session title|export|archive` - Management
- [x] `/new` - Start fresh session

### 1.5.8 Transcript Viewer (TODO)
- [ ] `/transcript` - View current session transcript
- [ ] `/transcript <id>` - View specific session
- [ ] Pretty-print with tool calls highlighted
- [ ] Export to HTML for browser viewing

---

## 🔐 Phase 1.6: Hooks & Verification System ✅ COMPLETE

**Goal**: Safety and verification at every step  
**Status**: ✅ DONE  
**Priority**: ✅ COMPLETE

### 1.6.1 Hook Infrastructure
- [ ] Create `HookManager` class
- [ ] Hook events:
  - `pre_tool` - Before any tool execution
  - `post_tool` - After tool execution
  - `pre_bash` - Before bash command (CRITICAL)
  - `post_edit` - After file edits
  - `session_start` / `session_end`

### 1.6.2 Bash Safety Parser (CRITICAL)
- [ ] Pattern-based command analysis
- [ ] Block dangerous commands:
  ```
  rm -rf /
  sudo *
  chmod 777
  curl | bash
  :(){ :|:& };:  # Fork bomb
  ```
- [ ] Configurable allow/deny lists
- [ ] Hook: `pre_bash` calls safety parser

### 1.6.3 Auto-Verification Hooks
- [ ] `post_edit_file.py`:
  - Run linter (ruff, eslint) after edits
  - Block if syntax errors
- [ ] `post_write_file.py`:
  - Validate file format
  - Check for secrets/passwords

### 1.6.4 Hook Configuration
```yaml
# .ollamacode/hooks.yaml
hooks:
  pre_bash:
    - safety_parser.py
  post_edit:
    - run_linter.py
    - run_tests.py
  blocked_commands:
    - "rm -rf /"
    - "sudo *"
```

### 1.6.5 Hook Locations
- `~/.ollamacode/hooks/` - Global hooks
- `.ollamacode/hooks/` - Project hooks (override global)

---

## 🔌 Phase 2: Extensibility System ✅ COMPLETE

**Goal**: Subagents, commands, skills  
**Status**: ✅ DONE

### 2.1 Slash Commands
- [ ] Create `.ollamacode/commands/` structure
- [ ] Command format: markdown with frontmatter
- [ ] Auto-complete in terminal
- [ ] Global commands at `~/.ollamacode/commands/`

### 2.2 Subagent System
- [ ] Create `SubagentTool` class
- [ ] Agent definition in `.ollamacode/agents/`:
  ```yaml
  name: code-reviewer
  model: qwen3:latest
  system_prompt: |
    You are a code review specialist...
  allowed_tools: [read_file, search_code]
  max_tokens: 4096
  ```
- [ ] **Permissions system**: Sandboxed file access
- [ ] **Parallel execution**: Run multiple subagents
- [ ] Result aggregation back to main agent

### 2.3 Skills System
- [ ] Skills = folders with code + SKILL.md
- [ ] Structure:
  ```
  .ollamacode/skills/trade-journal/
  ├── SKILL.md           # Agent reads this for expertise
  ├── skill.yaml         # Metadata, keywords, tools
  ├── journal.py         # Skill code
  └── requirements.txt
  ```
- [ ] Progressive loading: Only load when keyword matches
- [ ] Built-in skills: docx, xlsx, pdf generators (optional)

---

## 🖥️ Phase 2.6: Headless Mode ✅ COMPLETE

**Goal**: CI/CD automation, pre-commit hooks  
**Status**: ✅ DONE

### 2.6.1 Headless Execution
- [ ] `ollama-coder --headless -p "fix lint errors"`
- [ ] Exit codes: 0=success, 1=failure, 2=needs-human
- [ ] Machine-readable output: `--output json`

### 2.6.2 Git Integration
- [ ] Pre-commit hook: `ollama-coder --headless -p "review staged changes"`
- [ ] Post-merge hook: `ollama-coder --headless -p "check for breaking changes"`
- [ ] GitHub Actions integration example

### 2.6.3 Safety for Headless
- [ ] `--max-tools N` - Limit tool calls
- [ ] `--no-write` - Read-only mode
- [ ] `--approve-list` - Pre-approved commands only

---

## 🌐 Phase 3: MCP & Plugin Ecosystem

**Goal**: Connect external services  
**Estimated Time**: 5-7 days  
**Priority**: LOW (Nice to have)

### 3.1 MCP Client
- [ ] Implement MCP protocol in Python
- [ ] Transport types: stdio, SSE, HTTP
- [ ] Configuration in `settings.json`

### 3.2 Plugin System
- [ ] Plugin manifest format
- [ ] Install/uninstall commands
- [ ] Plugin registry (GitHub-based)

---

## ✅ Implementation Checklist

### Phase 1 - Core Tools ✅ COMPLETE
- [x] GlobTool
- [x] GrepTool
- [x] URLFetchTool
- [x] ScreenshotTool

### Phase 1.5 - Sessions ✅ COMPLETE
- [x] SessionManager class
- [x] SQLite + JSONL storage
- [x] All session commands
- [ ] Transcript viewer

### Phase 1.6 - Hooks ✅ COMPLETE
- [x] HookManager class
- [x] Bash safety parser (30+ patterns)
- [ ] Auto-linting hooks (Future)
- [x] Hook configuration

### Phase 2 - Extensibility ✅ COMPLETE
- [x] Subagent system
- [x] Slash commands
- [x] Skills system
- [x] Permissions (in SubagentExecutor)

### Phase 2.6 - Headless ✅ COMPLETE
- [x] Headless mode (--headless flag)
- [x] Git hooks (examples/)
- [x] CI/CD integration (GitHub Actions)

### Phase 3 - MCP
- [ ] MCP client
- [ ] Plugin system

---

## 🎯 Enhanced Success Criteria

OllamaCoder should:

1. ✅ **Match Claude Code's tools** - Core tools available
2. ✅ **Never forget** - Session persistence with SQLite + JSONL
3. ⬜ **Verify constantly** - Hooks block errors before they happen
4. ⬜ **Scale with subagents** - Parallel execution
5. ⬜ **Learn from transcripts** - Transcript viewer for debugging
6. ⬜ **Enforce permissions** - Sandboxed agents
7. ⬜ **Run headless** - CI/CD automation
8. ⬜ **Progressive disclosure** - Skills load on demand
9. ⬜ **Be customizable** - Per-project agents, commands, hooks
10. ⬜ **Connect externally** - MCP protocol

---

## 📅 Revised Timeline

| Phase | Duration | Focus | Priority |
|-------|----------|-------|----------|
| Phase 1 | Days 1-5 | Core tools | HIGH |
| **Phase 1.5** | **Days 3-10** | **Sessions** | ✅ DONE |
| **Phase 1.6** | **Days 8-12** | **Hooks & safety** | 🔴 NEXT |
| Phase 2 | Days 13-18 | Extensibility | HIGH |
| Phase 2.6 | Days 18-22 | Headless mode | MEDIUM |
| Phase 3 | Days 22-30 | MCP & plugins | LOW |

**Recommended Order**:
1. ✅ Phase 1.5: Sessions (DONE)
2. 🔴 Phase 1.6: Hooks (NEXT - safety & verification)
3. Phase 1: Core tools
4. Phase 2: Subagents, commands, skills
5. Phase 2.6: Headless mode
6. Phase 3: MCP (optional)

---

## 🔗 References

- [Claude Code Architecture](https://docs.anthropic.com/claude-code)
- [Model Context Protocol (MCP)](https://github.com/anthropics/mcp)
- [Anthropic's Agent Design Principles](https://www.anthropic.com/research/building-effective-agents)
