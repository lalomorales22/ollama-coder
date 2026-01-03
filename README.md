# OllamaCoder
<img width="476" height="305" alt="Screenshot 2026-01-03 at 10 57 16 AM" src="https://github.com/user-attachments/assets/9a78f32f-0cf9-46b5-acb4-a780a1732875" />

Agentic coding assistant for Ollama with tool use, autonomous mode, and project/user configuration.

## Install

From PyPI (after you publish):

```bash
pip install ollama-coder
```

For local development:

```bash
pip install -e .
```

## Run

```bash
ollama-coder
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

Manual publish:

```bash
python -m build
python -m twine upload dist/*
```

## Auto Publish (GitHub Actions)

This repo includes a GitHub Actions workflow that publishes to PyPI on tags.

Steps:
1) Bump the version in `pyproject.toml` and `ollama_code/__init__.py`
2) Commit the change
3) Tag and push:
   ```bash
   git tag v0.1.1
   git push origin v0.1.1
   ```

Trusted Publisher setup (one-time):
- In PyPI, add a Trusted Publisher for this repo.
- Workflow name (file): `publish.yml` (or `.github/workflows/publish.yml`)
- Environment name: leave blank (unless you add one)
