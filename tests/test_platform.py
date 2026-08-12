"""Config, sessions, checkpoints, extensions, MCP wiring and the CLI surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ollama_coder.core.checkpoints import CheckpointStore
from ollama_coder.core.config import Config
from ollama_coder.core.extensions import (
    AgentRegistry,
    CommandRegistry,
    SkillRegistry,
    scaffold_examples,
)
from ollama_coder.core.session import SessionStore


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_defaults_are_available_by_dotted_path(self, config):
        assert config.get("permissions.default") == "ask"
        assert config.get("bash.timeout_sec") > 0
        assert config.get("nothing.here", "fallback") == "fallback"

    def test_project_settings_override_user_settings(self, project, config):
        (config.user_dir / "settings.json").write_text(json.dumps({"temperature": 0.1}))
        (project / ".ollamacode").mkdir(exist_ok=True)
        (project / ".ollamacode" / "settings.json").write_text(json.dumps({"temperature": 0.9}))
        reloaded = Config(project_dir=project)
        assert reloaded.get("temperature") == 0.9

    def test_nested_merge_keeps_untouched_keys(self, project, config):
        (project / ".ollamacode").mkdir(exist_ok=True)
        (project / ".ollamacode" / "settings.json").write_text(
            json.dumps({"permissions": {"yolo": True}})
        )
        reloaded = Config(project_dir=project)
        assert reloaded.get("permissions.yolo") is True
        assert reloaded.get("permissions.default") == "ask", "sibling defaults must survive"

    def test_malformed_settings_do_not_crash(self, project, config, capsys):
        (project / ".ollamacode").mkdir(exist_ok=True)
        (project / ".ollamacode" / "settings.json").write_text("{not json")
        reloaded = Config(project_dir=project)
        assert reloaded.get("permissions.default") == "ask"
        assert "malformed" in capsys.readouterr().out

    def test_context_file_is_discovered(self, project):
        (project / "OLLAMA.md").write_text("# House rules\n\nAlways use tabs.\n")
        assert "Always use tabs" in Config(project_dir=project).context

    def test_set_and_save_round_trip(self, config):
        config.set("permissions.yolo", True)
        path = config.save("user")
        assert json.loads(path.read_text())["permissions"]["yolo"] is True


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class TestSessions:
    def test_round_trips_tool_calls(self, tmp_path):
        """0.2.x dropped tool_calls on resume, corrupting the replayed history."""
        store = SessionStore(base_dir=tmp_path)
        store.create(project_path="/x", model="m")
        store.append({"role": "user", "content": "hi"})
        store.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "bash", "arguments": {"command": "ls"}}}],
        })
        store.append({"role": "tool", "name": "bash", "content": "a.txt"})

        messages = store.load_messages()
        assert len(messages) == 3
        assert messages[1]["tool_calls"][0]["function"]["name"] == "bash"
        assert messages[2]["name"] == "bash"

    def test_listing_and_metadata(self, tmp_path):
        store = SessionStore(base_dir=tmp_path)
        session_id = store.create(project_path="/x", model="m")
        store.append({"role": "user", "content": "hello"})
        store.set_title("My Session")
        info = store.info()
        assert info["title"] == "My Session" and info["message_count"] == 1
        assert store.list()[0]["id"] == session_id

    def test_full_text_search(self, tmp_path):
        store = SessionStore(base_dir=tmp_path)
        store.create()
        store.append({"role": "user", "content": "how do I configure webpack"})
        assert store.search("webpack")

    def test_short_id_resolution(self, tmp_path):
        store = SessionStore(base_dir=tmp_path)
        session_id = store.create()
        assert store.resolve_id(session_id[:4]) == session_id
        assert store.resolve_id("zzzz") is None

    def test_branching_copies_history(self, tmp_path):
        store = SessionStore(base_dir=tmp_path)
        original = store.create()
        store.append({"role": "user", "content": "one"})
        store.append({"role": "assistant", "content": "two"})
        branch = store.branch()
        assert branch != original
        assert len(store.load_messages(branch)) == 2
        assert store.info(branch)["parent_id"] == original

    def test_markdown_export(self, tmp_path):
        store = SessionStore(base_dir=tmp_path)
        store.create(model="m")
        store.append({"role": "user", "content": "question"})
        store.append({"role": "assistant", "content": "answer"})
        exported = store.export_markdown()
        assert "question" in exported and "answer" in exported

    def test_hard_delete_removes_everything(self, tmp_path):
        store = SessionStore(base_dir=tmp_path)
        session_id = store.create()
        store.append({"role": "user", "content": "x"})
        store.delete(session_id, hard=True)
        assert store.info(session_id) is None
        assert not (store.sessions_dir / session_id).exists()


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------


class TestCheckpoints:
    @pytest.mark.asyncio
    async def test_restores_modified_file(self, tmp_path):
        target = tmp_path / "f.txt"
        target.write_text("before\n")
        store = CheckpointStore(tmp_path / "cp")
        await store.snapshot(target)
        target.write_text("after\n")
        assert await store.undo_last()
        assert target.read_text() == "before\n"

    @pytest.mark.asyncio
    async def test_removes_file_that_did_not_exist(self, tmp_path):
        target = tmp_path / "new.txt"
        store = CheckpointStore(tmp_path / "cp")
        await store.snapshot(target)
        target.write_text("created\n")
        await store.undo_last()
        assert not target.exists()

    @pytest.mark.asyncio
    async def test_undo_stack_unwinds_in_order(self, tmp_path):
        target = tmp_path / "f.txt"
        target.write_text("v1\n")
        store = CheckpointStore(tmp_path / "cp")
        await store.snapshot(target)
        target.write_text("v2\n")
        await store.snapshot(target)
        target.write_text("v3\n")
        await store.undo_last()
        assert target.read_text() == "v2\n"
        await store.undo_last()
        assert target.read_text() == "v1\n"
        assert await store.undo_last() is None

    @pytest.mark.asyncio
    async def test_disabled_store_is_a_noop(self, tmp_path):
        target = tmp_path / "f.txt"
        target.write_text("x\n")
        store = CheckpointStore(tmp_path / "cp", enabled=False)
        assert await store.snapshot(target) is None

    @pytest.mark.asyncio
    async def test_empty_store_is_still_a_usable_object(self, tmp_path):
        """It defines __len__, so `if store:` was silently false when empty."""
        store = CheckpointStore(tmp_path / "cp")
        assert store is not None
        assert len(store) == 0


# ---------------------------------------------------------------------------
# Extensions
# ---------------------------------------------------------------------------


class TestExtensions:
    def test_builtin_agents_are_present(self, project):
        registry = AgentRegistry(project)
        assert "explorer" in registry.names()
        assert registry.get("explorer").tools

    def test_project_agent_overrides_builtin(self, project):
        agents = project / ".ollamacode" / "agents"
        agents.mkdir(parents=True)
        (agents / "explorer.yaml").write_text(
            "name: explorer\ndescription: custom\nsystem_prompt: mine\ntools: [read_file]\n"
        )
        registry = AgentRegistry(project)
        assert registry.get("explorer").description == "custom"
        assert registry.get("explorer").source == "project"

    def test_custom_command_with_argument_substitution(self, project):
        commands = project / ".ollamacode" / "commands"
        commands.mkdir(parents=True)
        (commands / "audit.md").write_text(
            "---\nname: audit\ndescription: audit deps\n---\nAudit $ARGUMENTS for CVEs.\n"
        )
        registry = CommandRegistry(project)
        assert registry.render("audit", "requests") == "Audit requests for CVEs."

    def test_command_without_placeholder_appends_arguments(self, project):
        commands = project / ".ollamacode" / "commands"
        commands.mkdir(parents=True)
        (commands / "x.md").write_text("---\nname: x\n---\nDo the thing.\n")
        assert CommandRegistry(project).render("x", "now") == "Do the thing.\n\nnow"

    def test_skills_activate_on_keyword_only(self, project):
        skill = project / ".ollamacode" / "skills" / "pytest-rules"
        skill.mkdir(parents=True)
        (skill / "skill.yaml").write_text("name: pytest-rules\nkeywords: [pytest, testing]\n")
        (skill / "SKILL.md").write_text("# Rules\nUse fixtures.\n")

        registry = SkillRegistry(project)
        assert registry.activate_for("rewrite the CSS") == []
        activated = registry.activate_for("add a pytest for this")
        assert [s.name for s in activated] == ["pytest-rules"]
        assert registry.activate_for("more pytest work") == [], "already active, not re-injected"

    def test_scaffold_creates_examples_once(self, project, config):
        created = scaffold_examples(project)
        assert created
        assert scaffold_examples(project) == []


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


class TestMCPConfig:
    def test_reads_mcp_json(self, project, config):
        from ollama_coder.mcpx import load_server_specs

        mcp_dir = project / ".ollamacode"
        mcp_dir.mkdir(exist_ok=True)
        (mcp_dir / "mcp.json").write_text(
            json.dumps({"mcpServers": {"fs": {"command": "npx", "args": ["-y", "server"]}}})
        )
        specs = load_server_specs(project, config)
        assert specs["fs"]["command"] == "npx"

    def test_disabled_servers_are_skipped(self, project, config):
        from ollama_coder.mcpx import load_server_specs

        mcp_dir = project / ".ollamacode"
        mcp_dir.mkdir(exist_ok=True)
        (mcp_dir / "mcp.json").write_text(
            json.dumps({"mcpServers": {"off": {"command": "x", "disabled": True}}})
        )
        assert load_server_specs(project, config) == {}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestReadOnlyMode:
    def _read_only_names(self, config, allow=()):
        """Mirror of HeadlessRunner's read-only filtering."""
        from ollama_coder.core.extensions import AgentRegistry
        from ollama_coder.tools import build_registry

        if allow:
            config.set("permissions.auto_allow", list(allow))
        tools = build_registry(config.project_dir, config, AgentRegistry(config.project_dir))
        keep = set(config.get("permissions.auto_allow", []) or [])
        for name in ("write_file", "edit_file", "multi_edit", "git_commit",
                     "git_run", "task", "bash"):
            if name not in keep:
                tools.unregister(name)
        return tools.names()

    def test_removes_every_mutating_tool_including_bash(self, config):
        """A shell can modify anything, so --read-only must drop it too."""
        names = self._read_only_names(config)
        for gone in ("write_file", "edit_file", "multi_edit", "bash", "git_commit", "git_run"):
            assert gone not in names
        for kept in ("read_file", "grep", "glob", "git_read"):
            assert kept in names

    def test_allow_flag_restores_a_tool(self, config):
        assert "bash" in self._read_only_names(config, allow=["bash"])


class TestCLI:
    def test_flags_map_onto_config_overrides(self):
        from ollama_coder.cli import build_parser, overrides_from_args

        args = build_parser().parse_args(
            ["--yolo", "--model", "m", "--num-ctx", "4096", "--no-mcp", "--host", "http://h:1"]
        )
        overrides = overrides_from_args(args)
        assert overrides["permissions"]["yolo"] is True
        assert overrides["model"] == "m"
        assert overrides["num_ctx"] == 4096
        assert overrides["mcp"]["enabled"] is False
        assert overrides["ollama"]["host"] == "http://h:1"

    def test_positional_prompt_is_accepted(self):
        from ollama_coder.cli import build_parser

        assert build_parser().parse_args(["fix the bug"]).prompt_positional == "fix the bug"

    def test_version_flag_exits(self):
        from ollama_coder.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0
