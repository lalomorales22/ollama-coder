"""Hierarchical configuration.

Precedence, lowest to highest:

    defaults  <  ~/.ollamacode/settings.json  <  <project>/.ollamacode/settings.json
              <  environment variables  <  command-line flags

Project context (`OLLAMA.md`) is discovered the same way, plus any `OLLAMA.md`
found by walking up from the working directory -- the file system *is* the
context engineering layer.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

USER_DIR = Path.home() / ".ollamacode"
PROJECT_DIRNAME = ".ollamacode"
CONTEXT_FILENAMES = ("OLLAMA.md", "ollama.md", "AGENTS.md")


DEFAULTS: dict[str, Any] = {
    # --- model ---------------------------------------------------------
    "model": None,
    "fallback_model": None,
    "temperature": 0.6,
    "top_p": 0.95,
    "max_tokens": 8192,
    # `null` means "detect from the model itself and use as much as fits"
    "num_ctx": None,
    "think": "auto",  # auto | always | never | low | medium | high
    "keep_alive": "30m",
    "ollama": {
        "host": "",
        "timeout_sec": 900,
        "connect_timeout_sec": 15,
        "headers": {},
        "api_key": "",
        "allow_cloud_models": False,
    },
    # --- agent loop ----------------------------------------------------
    "max_steps": 40,
    "max_parallel_tools": 4,
    "auto_compact": True,
    "compact_threshold": 0.82,  # fraction of the window that triggers compaction
    "keep_recent_messages": 8,
    # --- safety / permissions -----------------------------------------
    "permissions": {
        # "ask" | "allow" | "deny" -- the fallback when no rule matches
        "default": "ask",
        # tools that never need approval (read-only by nature)
        "auto_allow": [
            "think",
            "todo_write",
            "read_file",
            "list_dir",
            "glob",
            "grep",
            "git_read",
        ],
        "deny": [],
        # bash commands matching these are auto-approved
        "allow_bash": [
            r"^ls(\s|$)",
            r"^cat\s",
            r"^pwd$",
            r"^echo\s",
            r"^which\s",
            r"^head\s",
            r"^tail\s",
            r"^wc\s",
            r"^grep\s",
            r"^rg\s",
            r"^find\s",
            r"^python3?\s+-c\s",
            r"^node\s+-e\s",
            r"^pytest(\s|$)",
            r"^npm\s+(test|run\s+test|ls)(\s|$)",
            r"^cargo\s+(check|test|build)(\s|$)",
            r"^go\s+(test|build|vet)(\s|$)",
        ],
        "yolo": False,  # skip every prompt (still honours hard blocks)
    },
    "sandbox": {
        # file tools refuse to touch anything outside these roots
        "enabled": True,
        "extra_roots": [],
        # reading these requires explicit approval even inside the sandbox
        "protected_globs": [
            "**/.env",
            "**/.env.*",
            "**/*.pem",
            "**/*.key",
            "**/id_rsa*",
            "**/.ssh/**",
            "**/.aws/credentials",
            "**/.netrc",
        ],
    },
    "checkpoints": {
        "enabled": True,
        "max_per_session": 200,
    },
    # --- tools ---------------------------------------------------------
    "bash": {
        "timeout_sec": 240,
        "max_output_chars": 30000,
        "persistent": True,
    },
    "web": {
        "enabled": True,
        "timeout_sec": 20,
        "max_length": 20000,
        "search_endpoint": "",
        "search_api_key": "",
    },
    "mcp": {
        "enabled": True,
        "connect_timeout_sec": 30,
        "servers": {},
    },
    "subagents": {
        "enabled": True,
        "max_parallel": 3,
        "max_steps": 20,
    },
    # --- ui ------------------------------------------------------------
    "ui": {
        "theme": "neon",
        "show_thinking": True,
        "splash": True,
        "sidebar": True,
        "sound": False,
    },
}


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


class Config:
    """Loaded settings plus project context files."""

    def __init__(self, project_dir: Path | None = None, overrides: dict[str, Any] | None = None):
        self.project_dir = Path(project_dir or Path.cwd()).resolve()
        self.user_dir = USER_DIR
        self.project_config_dir = self.project_dir / PROJECT_DIRNAME

        self.user_dir.mkdir(parents=True, exist_ok=True)

        self.data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self._load_file(self.user_dir / "settings.json")
        self._load_file(self.project_config_dir / "settings.json")
        self._load_env()
        if overrides:
            _deep_merge(self.data, overrides)

        self.context: str = self._load_context()

    # -- loading ---------------------------------------------------------

    def _load_file(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            _deep_merge(self.data, json.loads(path.read_text()))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"⚠️  Ignoring malformed config {path}: {exc}")

    def _load_env(self) -> None:
        host = os.environ.get("OLLAMA_HOST", "").strip()
        if host and not self.data["ollama"]["host"]:
            self.data["ollama"]["host"] = host
        model = os.environ.get("OLLAMA_CODER_MODEL", "").strip()
        if model:
            self.data["model"] = model
        if os.environ.get("OLLAMA_CODER_YOLO", "").strip().lower() in ("1", "true", "yes"):
            self.data["permissions"]["yolo"] = True

    def _load_context(self) -> str:
        """Collect OLLAMA.md files: user level, then repo root down to cwd."""
        parts: list[str] = []

        for name in CONTEXT_FILENAMES:
            user_ctx = self.user_dir / name
            if user_ctx.exists():
                parts.append(f"<!-- {user_ctx} -->\n{user_ctx.read_text()}")
                break

        seen: list[Path] = []
        current = self.project_dir
        for _ in range(12):
            seen.append(current)
            if (current / ".git").exists() or current.parent == current:
                break
            current = current.parent

        for directory in reversed(seen):
            for name in CONTEXT_FILENAMES:
                candidate = directory / name
                if candidate.exists():
                    try:
                        parts.append(f"<!-- {candidate} -->\n{candidate.read_text()}")
                    except OSError:
                        pass
                    break

        return "\n\n".join(parts)

    def reload_context(self) -> None:
        self.context = self._load_context()

    # -- access ----------------------------------------------------------

    def get(self, path: str, default: Any = None) -> Any:
        """Dotted lookup: cfg.get("permissions.default")."""
        node: Any = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, path: str, value: Any) -> None:
        parts = path.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    # -- persistence -----------------------------------------------------

    def save(self, scope: str = "user") -> Path:
        target_dir = self.user_dir if scope == "user" else self.project_config_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / "settings.json"
        path.write_text(json.dumps(self.data, indent=2) + "\n")
        return path

    # -- convenience -----------------------------------------------------

    @property
    def sandbox_roots(self) -> list[Path]:
        roots = [self.project_dir]
        for extra in self.get("sandbox.extra_roots", []) or []:
            try:
                roots.append(Path(os.path.expanduser(str(extra))).resolve())
            except OSError:
                continue
        return roots

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Config project={self.project_dir} model={self.get('model')}>"
