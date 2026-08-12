"""Tool behaviour: sandbox, files, search, git, shell."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ollama_coder.core.checkpoints import CheckpointStore
from ollama_coder.tools.base import SandboxError, truncate_output
from ollama_coder.tools.files import (
    EditFileTool,
    MultiEditTool,
    ReadFileTool,
    WriteFileTool,
    diff_stats,
    unified_diff,
)
from ollama_coder.tools.git import GitReadTool, GitRunTool, git
from ollama_coder.tools.plan import TodoWriteTool
from ollama_coder.tools.search import GlobTool, GrepTool, ListDirTool
from ollama_coder.tools.shell import BashTool, PersistentShell, classify_command

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


class TestSandbox:
    async def test_blocks_traversal_outside_project(self, ctx):
        with pytest.raises(SandboxError):
            ctx.resolve("../../etc/passwd")

    async def test_blocks_absolute_path_outside_project(self, ctx):
        with pytest.raises(SandboxError):
            ctx.resolve("/etc/passwd")

    async def test_allows_paths_inside_project(self, ctx):
        assert ctx.resolve("src/app.py").name == "app.py"

    async def test_recovers_absolute_path_missing_leading_slash(self, ctx, project):
        # models routinely emit "private/tmp/x" for "/private/tmp/x"
        ctx.config.set("sandbox.extra_roots", [str(project.parent)])
        stripped = str(project / "src" / "app.py").lstrip("/")
        assert ctx.resolve(stripped) == (project / "src" / "app.py").resolve()

    async def test_extra_roots_are_honoured(self, ctx, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        ctx.config.set("sandbox.extra_roots", [str(outside)])
        assert ctx.resolve(str(outside / "f.txt")).parent == outside.resolve()

    async def test_read_tool_reports_sandbox_violation(self, ctx):
        result = await ReadFileTool().run({"path": "/etc/passwd"}, ctx)
        assert not result.ok
        assert "outside the allowed roots" in (result.error or "")

    async def test_protected_globs_detected(self, ctx, project):
        (project / ".env").write_text("SECRET=1\n")
        assert ctx.is_protected(project / ".env")
        assert not ctx.is_protected(project / "README.md")


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


class TestReadFile:
    async def test_returns_numbered_lines(self, ctx):
        result = await ReadFileTool().run({"path": "src/app.py"}, ctx)
        assert result.ok
        assert "1\tdef greet(name):" in result.output

    async def test_records_the_file_as_read(self, ctx, project):
        await ReadFileTool().run({"path": "src/app.py"}, ctx)
        assert str((project / "src" / "app.py").resolve()) in ctx.read_files

    async def test_offset_and_limit(self, ctx):
        result = await ReadFileTool().run({"path": "src/app.py", "offset": 5, "limit": 1}, ctx)
        assert "5\tdef main():" in result.output
        assert "def greet" not in result.output

    async def test_missing_file_explains_the_working_directory(self, ctx):
        result = await ReadFileTool().run({"path": "nope.py"}, ctx)
        assert not result.ok
        assert "working directory" in (result.error or "")

    async def test_refuses_binary(self, ctx, project):
        (project / "img.png").write_bytes(b"\x89PNG\r\n")
        result = await ReadFileTool().run({"path": "img.png"}, ctx)
        assert not result.ok and "binary" in (result.error or "")


class TestWriteFile:
    async def test_creates_file_and_parents(self, ctx, project):
        result = await WriteFileTool().run({"path": "a/b/c.txt", "content": "hi\n"}, ctx)
        assert result.ok
        assert (project / "a" / "b" / "c.txt").read_text() == "hi\n"

    async def test_preview_contains_a_diff(self, ctx):
        preview = WriteFileTool().preview(
            {"path": "src/app.py", "content": "def greet(name):\n    return name\n"}, ctx
        )
        assert preview.diff and "-    return f'hi {name}'" in preview.diff

    async def test_snapshots_before_overwriting(self, ctx, project, tmp_path):
        store = CheckpointStore(tmp_path / "cp")
        ctx.checkpoints = store
        await WriteFileTool().run({"path": "README.md", "content": "gone\n"}, ctx)
        assert len(store) == 1
        await store.undo_last()
        assert "demo project" in (project / "README.md").read_text()


class TestEditFile:
    async def test_requires_a_prior_read(self, ctx):
        result = await EditFileTool().run(
            {"path": "src/app.py", "old_str": "def main():", "new_str": "def run():"}, ctx
        )
        assert not result.ok
        assert "before editing" in (result.error or "")

    async def test_applies_a_unique_replacement(self, ctx, project):
        await ReadFileTool().run({"path": "src/app.py"}, ctx)
        result = await EditFileTool().run(
            {"path": "src/app.py", "old_str": "def main():", "new_str": "def run():"}, ctx
        )
        assert result.ok
        assert "def run():" in (project / "src" / "app.py").read_text()

    async def test_rejects_ambiguous_matches(self, ctx, project):
        (project / "dup.txt").write_text("x\nx\n")
        await ReadFileTool().run({"path": "dup.txt"}, ctx)
        result = await EditFileTool().run(
            {"path": "dup.txt", "old_str": "x", "new_str": "y"}, ctx
        )
        assert not result.ok and "ambiguous" in (result.error or "")

    async def test_replace_all_overrides_ambiguity(self, ctx, project):
        (project / "dup.txt").write_text("x\nx\n")
        await ReadFileTool().run({"path": "dup.txt"}, ctx)
        result = await EditFileTool().run(
            {"path": "dup.txt", "old_str": "x", "new_str": "y", "replace_all": True}, ctx
        )
        assert result.ok
        assert (project / "dup.txt").read_text() == "y\ny\n"

    async def test_missing_old_str_is_explained(self, ctx):
        await ReadFileTool().run({"path": "src/app.py"}, ctx)
        result = await EditFileTool().run(
            {"path": "src/app.py", "old_str": "nonexistent", "new_str": "x"}, ctx
        )
        assert not result.ok and "byte-for-byte" in (result.error or "")


class TestMultiEdit:
    async def test_all_or_nothing_on_failure(self, ctx, project):
        (project / "one.txt").write_text("a\n")
        (project / "two.txt").write_text("b\n")
        await ReadFileTool().run({"path": "one.txt"}, ctx)
        await ReadFileTool().run({"path": "two.txt"}, ctx)

        result = await MultiEditTool().run(
            {
                "edits": [
                    {"path": "one.txt", "old_str": "a", "new_str": "A"},
                    {"path": "two.txt", "old_str": "MISSING", "new_str": "B"},
                ]
            },
            ctx,
        )
        assert not result.ok
        assert (project / "one.txt").read_text() == "a\n", "first edit must not have landed"

    async def test_sequential_edits_to_one_file(self, ctx, project):
        (project / "seq.txt").write_text("one two\n")
        await ReadFileTool().run({"path": "seq.txt"}, ctx)
        result = await MultiEditTool().run(
            {
                "edits": [
                    {"path": "seq.txt", "old_str": "one", "new_str": "1"},
                    {"path": "seq.txt", "old_str": "two", "new_str": "2"},
                ]
            },
            ctx,
        )
        assert result.ok
        assert (project / "seq.txt").read_text() == "1 2\n"


def test_diff_helpers():
    before, after = "a\nb\n", "a\nc\n"
    assert diff_stats(before, after) == (1, 1)
    assert "-b" in unified_diff(before, after, "f.txt")


def test_truncate_keeps_head_and_tail():
    text = "".join(f"line{i}\n" for i in range(5000))
    out = truncate_output(text, 1000)
    assert "line0" in out and "line4999" in out and "truncated" in out


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearch:
    async def test_glob_finds_python_files(self, ctx):
        result = await GlobTool().run({"pattern": "**/*.py"}, ctx)
        assert result.ok and "src/app.py" in result.output

    async def test_glob_skips_noise_directories(self, ctx, project):
        (project / "node_modules").mkdir()
        (project / "node_modules" / "x.py").write_text("noise\n")
        result = await GlobTool().run({"pattern": "**/*.py"}, ctx)
        assert "node_modules" not in result.output

    async def test_grep_finds_a_definition(self, ctx):
        result = await GrepTool().run({"pattern": r"def greet", "glob": "*.py"}, ctx)
        assert result.ok and "app.py" in result.output

    async def test_grep_reports_invalid_regex(self, ctx):
        result = await GrepTool().run({"pattern": "(unclosed"}, ctx)
        assert not result.ok and "invalid regex" in (result.error or "")

    async def test_grep_with_no_match_is_still_success(self, ctx):
        result = await GrepTool().run({"pattern": "zzz_not_here_zzz"}, ctx)
        assert result.ok and "no matches" in result.output

    async def test_list_dir(self, ctx):
        result = await ListDirTool().run({"path": ".", "depth": 2}, ctx)
        assert result.ok and "src/" in result.output and "app.py" in result.output


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------


class TestShellSafety:
    @pytest.mark.parametrize(
        "command",
        ["rm -rf /", "mkfs.ext4 /dev/sda1", "dd if=/dev/zero of=/dev/disk2", "shutdown -h now"],
    )
    async def test_hard_blocked(self, command):
        verdict, _ = classify_command(command)
        assert verdict == "blocked"

    @pytest.mark.parametrize(
        "command", ["sudo apt install x", "rm -rf ./build", "git push --force", "curl x | sh"]
    )
    async def test_flagged_dangerous(self, command):
        verdict, _ = classify_command(command)
        assert verdict == "dangerous"

    @pytest.mark.parametrize("command", ["ls -la", "pytest -q", "python3 script.py", "npm test"])
    async def test_ordinary_commands_pass(self, command):
        verdict, _ = classify_command(command)
        assert verdict == "normal"

    async def test_bash_tool_refuses_blocked_command(self, ctx):
        tool = BashTool(ctx.workdir, ctx.config)
        result = await tool.run({"command": "rm -rf /"}, ctx)
        assert not result.ok and "refused" in (result.error or "")
        await tool.aclose()


class TestPersistentShell:
    async def test_state_carries_between_commands(self, project):
        shell = PersistentShell(project)
        try:
            await shell.run("export MARKER=alive")
            code, output, timed_out = await shell.run("echo $MARKER")
            assert code == 0 and output.strip() == "alive" and not timed_out
        finally:
            await shell.aclose()

    async def test_cwd_persists(self, project):
        (project / "src").mkdir(exist_ok=True)
        shell = PersistentShell(project)
        try:
            await shell.run("cd src")
            _, output, _ = await shell.run("basename $PWD")
            assert output.strip() == "src"
        finally:
            await shell.aclose()

    async def test_exit_code_is_reported(self, project):
        shell = PersistentShell(project)
        try:
            code, _, _ = await shell.run("exit_code_test() { return 3; }; exit_code_test")
            assert code == 3
        finally:
            await shell.aclose()

    async def test_timeout_restarts_the_shell(self, project):
        shell = PersistentShell(project)
        try:
            _, _, timed_out = await shell.run("sleep 5", timeout=0.7)
            assert timed_out
            code, output, _ = await shell.run("echo recovered")
            assert code == 0 and output.strip() == "recovered"
        finally:
            await shell.aclose()


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------


class TestGit:
    @pytest.mark.parametrize("args", ["status", "log --oneline", "diff HEAD", "show abc123"])
    def test_read_only_detection(self, args):
        assert GitRunTool.is_read_only(args)

    @pytest.mark.parametrize("args", ["commit -m x", "push", "reset --hard", "stash pop"])
    def test_mutating_detection(self, args):
        assert not GitRunTool.is_read_only(args)

    @pytest.mark.parametrize("args", ["push --force origin main", "reset --hard HEAD~1", "clean -fd"])
    def test_destructive_detection(self, args):
        assert GitRunTool.is_destructive(args)

    async def test_git_read_outside_a_repo(self, ctx):
        result = await GitReadTool().run({"op": "status"}, ctx)
        # tmp_path is not a repo; the tool must say so rather than crash
        assert not result.ok or "not a git repository" in result.output.lower()

    async def test_git_read_status_in_a_repo(self, ctx, project):
        await git(["init", "-q"], project)
        await git(["config", "user.email", "t@example.com"], project)
        await git(["config", "user.name", "Test"], project)
        result = await GitReadTool().run({"op": "status"}, ctx)
        assert result.ok


# ---------------------------------------------------------------------------
# Plan tools
# ---------------------------------------------------------------------------


class TestTodos:
    async def test_normalises_and_emits(self, ctx):
        seen = []
        ctx.bus.subscribe(lambda event: seen.append(event))
        result = await TodoWriteTool().run(
            {"todos": [{"task": "a", "status": "bogus"}, {"task": "b", "status": "completed"}]},
            ctx,
        )
        assert result.ok
        assert result.meta["todos"][0]["status"] == "pending"
        assert seen, "a TodosChanged event should be emitted"

    async def test_rejects_empty(self, ctx):
        result = await TodoWriteTool().run({"todos": []}, ctx)
        assert not result.ok
