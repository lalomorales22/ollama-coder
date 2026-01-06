# OllamaCoder Handoff Document

**Date**: January 6, 2026  
**Current Version**: 0.2.7  
**Status**: Development Complete → Ready for User Testing & Debugging

---

## 🚨 NEXT AGENT: READ THIS FIRST

**Your task**: Help the user (Lalo) test and debug OllamaCoder.

The user will be:
1. Running the app interactively
2. Testing the new features
3. Identifying bugs or issues
4. Asking you to fix any problems found

Be ready to:
- Debug errors when running `ollama-coder`
- Fix any import issues or runtime errors
- Help test headless mode, custom commands, subagents, etc.
- Make quick fixes as needed

---

## 🏗️ What Was Built (Summary)

### Phase 1.5: Session Persistence ✅
- SQLite + JSONL storage
- Commands: `/sessions`, `/resume`, `/search`, `/branch`, `/new`
- Auto-save every message, auto-title after 3

### Phase 1.6: Safety Hooks ✅
- `BashSafetyParser` - blocks dangerous commands (rm -rf /, sudo, fork bombs)
- `HookManager` - pre/post tool execution hooks

### Phase 1: Core Tools ✅
- `GlobTool` - file pattern matching
- `GrepTool` - regex search with context
- `URLFetchTool` - fetch web content
- `ScreenshotTool` - browser screenshots (playwright)

### Phase 2: Extensibility ✅
- `CommandManager` - custom slash commands (~/.ollamacode/commands/)
- `SubagentManager` - specialized agents (code-reviewer, test-writer, documenter)
- `SkillManager` - progressive expertise loading

### Phase 2.6: Headless Mode ✅
- `--headless` flag for CI/CD
- `--output json` for machine-readable output
- `--no-write`, `--max-tools`, `--timeout` safety flags
- Exit codes: 0=success, 1=failure, 2=needs-human

---

## 📁 Key Files

| File | Purpose |
|------|---------|
| `ollama_coder/cli.py` | Main CLI (~3000 lines) |
| `ollama_coder/session.py` | Session persistence |
| `ollama_coder/hooks.py` | Safety hooks |
| `ollama_coder/commands.py` | Custom commands |
| `ollama_coder/subagent.py` | Subagent system |
| `ollama_coder/skills.py` | Skills system |
| `examples/` | Git hooks, GitHub Actions |
| `tests/` | 45 tests (all passing) |

---

## 🧪 Testing Commands

```bash
# Install locally
cd /Users/minibrain/Desktop/ollama-coder
pip install -e .

# Run tests
pytest tests/ -v

# Run interactive mode
ollama-coder

# Test headless mode
ollama-coder --headless -p "hello world" --output json

# List custom commands
ollama-coder
> /commands

# List subagents
ollama-coder
> /subagents
```

---

## ⚠️ Known Areas to Test

1. **New tools**: glob, grep, fetch_url, screenshot
2. **Custom commands**: Create a command in ~/.ollamacode/commands/
3. **Headless mode**: Test JSON output and exit codes
4. **Safety hooks**: Try a dangerous command to see it blocked

---

## 📊 Test Results

```
45 passed in 0.29s
```

All tests passing as of January 6, 2026.
