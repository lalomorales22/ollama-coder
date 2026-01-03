# OllamaCode

Agentic coding assistant for Ollama with tool use, autonomous mode, and project/user configuration.

## Install

From PyPI (after you publish):

```bash
pip install ollama-code
```

For local development:

```bash
pip install -e .
```

## Run

```bash
ollama-code
```

You will be prompted to choose a model on startup. The selected model can be saved as your default.

## Common Commands

- `/auto` toggle autonomous mode
- `/models` list installed models
- `/model` show or set the active model
- `/host` show or set the Ollama host (for remote/cloud)
- `/config` show effective configuration
- `/clear` clear conversation history
- `/quit` exit

## Configuration

User config lives at `~/.ollamacode/settings.json`. Example:

```json
{
  "model": "llama3:latest",
  "max_iterations": 25,
  "max_tool_rounds": 8,
  "ollama": {
    "host": "http://127.0.0.1:11434",
    "timeout_sec": 60,
    "headers": {},
    "api_key": ""
  },
  "web_search": {
    "enabled": false,
    "provider": "custom",
    "endpoint": "",
    "api_key": "",
    "timeout_sec": 15,
    "max_results": 5
  }
}
```

You can also set `OLLAMA_HOST` in your environment to point at a remote server. The `/host` command updates the config and rebuilds the client at runtime.

## Requirements

- Ollama server running locally or reachable via `OLLAMA_HOST`
- Python 3.9+

## GitHub

Repository: https://github.com/lalomorales22/ollama-code

## License

MIT

## Publish to PyPI

```bash
python -m build
python -m twine upload dist/*
```
