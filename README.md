# OllamaCoder 🦙💻

<img width="476" height="305" alt="OllamaCoder Screenshot" src="https://github.com/user-attachments/assets/9a78f32f-0cf9-46b5-acb4-a780a1732875" />

**An agentic coding assistant for Ollama - like Claude Code, but running locally!**

OllamaCoder transforms your local Ollama models into a powerful autonomous coding agent with tool use, multi-step task execution, and project-aware context.

## ✨ Features

- **🤖 Agentic Architecture**: Autonomous multi-step task execution with planning, execution, and verification
- **🛡️ Safety Hooks**: Blocks dangerous bash commands (rm -rf /, sudo, fork bombs, etc.)
- **🔧 Full Tool System**: bash, file operations, git, code search, web search, and more
- **💭 Think Tool**: Structured reasoning for complex problems (like extended thinking)
- **📝 Multi-Edit**: Batch file edits in a single operation
- **🔄 Autonomous Mode**: Let the AI work through complex tasks independently
- **📡 Streaming Responses**: Real-time token streaming for better UX
- **🖼️ Vision/Image Support**: Analyze images with multimodal models
- **📊 Context Management**: Automatic conversation summarization to stay within context limits
- **⚙️ Hierarchical Config**: User and project-level settings with OLLAMA.md context files
- **🌐 Remote Ollama**: Connect to remote Ollama servers via OLLAMA_HOST
- **💾 Session Persistence**: SQLite-backed session storage with full-text search
- **🚀 Custom Commands**: Create your own slash commands in markdown
- **🤖 Subagents**: Spawn specialized AI agents for focused tasks
- **🎯 Skills**: Progressive expertise loading based on keywords
- **🎨 Rich Output**: Beautiful terminal output with syntax highlighting (optional)

## 📦 Installation

### Via Homebrew (macOS) - Recommended

```bash
brew tap lalomorales22/ollama-coder
brew install ollama-coder
```

### Via PyPI

```bash
pip install ollama-coder
```

For enhanced terminal output with streaming (recommended):

```bash
pip install ollama-coder[rich]
# or
pip install ollama-coder rich
```

### From Source

```bash
git clone https://github.com/lalomorales22/ollama-coder.git
cd ollama-coder
pip install -e .
```

## 🚀 Quick Start

\`\`\`bash
# Start interactive mode
ollama-coder

# Start with autonomous mode enabled
ollama-coder --auto

# Run a single command (headless mode)
ollama-coder -p "fix the bug in app.py"

# Use a specific model
ollama-coder --model gpt-oss:20b

# Work in a specific directory
ollama-coder --dir /path/to/project
\`\`\`

## 🤖 Headless Mode (CI/CD)

\`\`\`bash
# Basic headless execution
ollama-coder --headless -p "fix lint errors"

# JSON output for parsing
ollama-coder --headless --output json -p "analyze code"

# Safety limits (read-only, max tools, timeout)
ollama-coder --headless --no-write --max-tools 10 --timeout 120 -p "review"

# Longer bash timeout for local AI (default: 300s)
ollama-coder --bash-timeout 600 -p "create a react app"
\`\`\`

**Exit Codes:**
- `0` - Success
- `1` - Failure/Error
- `2` - Needs human intervention

## 🛠️ Available Tools

| Tool | Description |
|------|-------------|
| \`think\` | Structured reasoning for complex problems |
| \`bash\` | Execute shell commands |
| \`read_file\` | Read file contents with optional line ranges |
| \`write_file\` | Create or overwrite files |
| \`edit_file\` | Make surgical edits using find/replace |
| \`multi_edit\` | Batch multiple edits in one operation |
| \`list_directory\` | Explore project structure |
| \`search_code\` | Search for patterns using grep/ripgrep |
| \`glob\` | **NEW** Find files matching glob patterns |
| \`grep\` | **NEW** Regex search with context lines |
| \`fetch_url\` | **NEW** Fetch and parse web content |
| \`screenshot\` | **NEW** Browser screenshots (requires playwright) |
| \`git\` | Version control operations |
| \`web_search\` | Search the web (when configured) |

## ⌨️ Commands

| Command | Description |
|---------|-------------|
| `/auto` | Toggle autonomous mode |
| `/sessions` | List recent sessions |
| `/resume` | Resume session: `/resume [id]` |
| `/search` | Search sessions: `/search <query>` |
| `/session` | Session info/actions: `/session title|export|archive` |
| `/branch` | Branch current session |
| `/new` | Start new session |
| `/commands` | List custom commands |
| `/subagents` | List available subagents |
| `/skills` | List and manage skills |
| `/model` | Show or set the active model |
| `/models` | List installed Ollama models |
| `/streaming` | Toggle streaming responses |
| `/image` | Attach image: `/image <path> <message>` |
| `/context` | Show context usage stats |
| `/config` | Show current configuration |
| `/clear` | Clear conversation history |
| `/help` | Show available commands |
| `/quit` | Exit OllamaCoder |


## ⚙️ Configuration

User config: \`~/.ollamacode/settings.json\`

\`\`\`json
{
  "model": "gpt-oss:20b-cloud",
  "max_iterations": 25,
  "max_tool_rounds": 8,
  "temperature": 0.7,
  "streaming": true,
  "bash": {
    "timeout_sec": 300,
    "long_running_timeout_sec": 600
  },
  "vision": {
    "enabled": true,
    "max_image_size": 4194304
  },
  "context_management": {
    "enabled": true,
    "summarize_threshold": 0.75,
    "keep_recent_messages": 10
  },
  "ollama": {
    "host": "http://127.0.0.1:11434",
    "timeout_sec": 300
  },
  "web_search": {
    "enabled": false,
    "provider": "custom",
    "endpoint": "",
    "api_key": ""
  }
}
\`\`\`

### Project Context

Create \`OLLAMA.md\` files to provide project-specific context:

- \`~/.ollamacode/OLLAMA.md\` - User-level context (applies to all projects)
- \`.ollamacode/OLLAMA.md\` - Project-level context (in your project root)

## 🔌 Remote Ollama

Connect to remote Ollama servers:

\`\`\`bash
# Via environment variable
export OLLAMA_HOST=http://your-server:11434
ollama-coder

# Or use the /host command
ollama-coder
> /host http://your-server:11434
\`\`\`

## 📋 Requirements

- Python 3.9+
- Ollama server running locally or accessible remotely
- Optional: \`rich\` package for enhanced terminal output

## 🔄 Comparison with Claude Code

| Feature | OllamaCoder | Claude Code |
|---------|-------------|-------------|
| Local/Private | ✅ | ❌ |
| Free | ✅ | ❌ |
| Tool Use | ✅ | ✅ |
| Autonomous Mode | ✅ | ✅ |
| Thinking Tool | ✅ | ✅ |
| Multi-Edit | ✅ | ✅ |
| Streaming | ✅ | ✅ |
| Image Analysis | ✅ | ✅ |
| Context Management | ✅ | ✅ |
| Web Search | ✅ | ✅ |
| Project Context | ✅ | ✅ |
| Session Persistence | ✅ | ✅ |
| Safety Hooks | ✅ | ✅ |
| Headless/CI Mode | ✅ | ✅ |
| Subagents | ✅ | ✅ |
| Custom Commands | ✅ | ✅ |
| Skills System | ✅ | ✅ |
| MCP Support | 🔜 | ✅ |

## 📝 License

MIT

## 🔗 Links

- **GitHub**: https://github.com/lalomorales22/ollama-coder
- **PyPI**: https://pypi.org/project/ollama-coder/
- **Ollama**: https://ollama.ai/

## 📤 Publishing

Version bumping and publishing is automated via GitHub Actions. Just:

1. Bump version in `pyproject.toml` and `ollama_coder/__init__.py`
2. Commit and push
3. Create and push a tag:
   ```bash
   git tag v0.2.7
   git push origin v0.2.7
   ```

---

**Current Version**: 0.2.7

