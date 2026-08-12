"""Shared fixtures."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ollama_coder.core.config import Config
from ollama_coder.core.events import EventBus
from ollama_coder.tools.base import ToolContext


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "def greet(name):\n    return f'hi {name}'\n\n\ndef main():\n    print(greet('world'))\n"
    )
    (tmp_path / "README.md").write_text("# demo project\n\nA fixture.\n")
    return tmp_path


@pytest.fixture
def config(project: Path, monkeypatch, tmp_path: Path) -> Config:
    # keep the suite out of the real ~/.ollamacode
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("OLLAMA_CODER_MODEL", raising=False)
    import ollama_coder.core.config as config_module

    monkeypatch.setattr(config_module, "USER_DIR", home / ".ollamacode")
    return Config(project_dir=project)


@pytest.fixture
def ctx(project: Path, config: Config) -> ToolContext:
    return ToolContext(workdir=project, config=config, bus=EventBus())
