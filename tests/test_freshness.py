"""Keeping the model current: dependency detection, skills, web search.

A local model's training data is frozen, so it writes APIs that were removed
releases ago. These are the three mechanisms that counter that.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ollama_coder.core.extensions import (
    derive_keywords,
    discover_claude_skills,
    import_claude_skills,
    parse_skill_dir,
)
from ollama_coder.core.project import (
    describe_dependencies,
    detect_dependencies,
)
from ollama_coder.tools.web import _parse_ddg, unwrap_ddg_url


# ---------------------------------------------------------------------------
# Dependency detection
# ---------------------------------------------------------------------------


class TestNodeDependencies:
    def test_prefers_the_installed_version_over_the_declared_range(self, tmp_path: Path):
        """`^0.160.0` says far less than the 0.185.1 actually on disk."""
        (tmp_path / "package.json").write_text(
            json.dumps({"dependencies": {"three": "^0.160.0"}})
        )
        installed = tmp_path / "node_modules" / "three"
        installed.mkdir(parents=True)
        (installed / "package.json").write_text(json.dumps({"version": "0.185.1"}))

        deps = detect_dependencies(tmp_path)
        assert len(deps) == 1
        assert deps[0].version == "0.185.1"
        assert deps[0].installed is True

    def test_falls_back_to_the_declared_range(self, tmp_path: Path):
        (tmp_path / "package.json").write_text(
            json.dumps({"dependencies": {"three": "^0.160.0"}})
        )
        dep = detect_dependencies(tmp_path)[0]
        assert dep.version == "0.160.0" and dep.installed is False
        assert "declared" in dep.render()

    def test_dev_dependencies_are_included(self, tmp_path: Path):
        (tmp_path / "package.json").write_text(
            json.dumps({"devDependencies": {"vite": "6.0.0"}})
        )
        assert [d.name for d in detect_dependencies(tmp_path)] == ["vite"]

    def test_fast_moving_libraries_sort_first(self, tmp_path: Path):
        (tmp_path / "package.json").write_text(
            json.dumps({"dependencies": {"aaa-stable": "1.0.0", "three": "0.185.1"}})
        )
        assert detect_dependencies(tmp_path)[0].name == "three"


class TestPythonDependencies:
    def test_reads_pyproject(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "x"\ndependencies = ["httpx>=0.27", "pydantic==2.9.0"]\n'
        )
        names = {d.name for d in detect_dependencies(tmp_path)}
        assert names == {"httpx", "pydantic"}

    def test_reads_requirements_txt(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("# comment\nfastapi==0.115.0\n-e .\n")
        assert [d.name for d in detect_dependencies(tmp_path)] == ["fastapi"]

    def test_prefers_versions_from_a_local_venv(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "x"\ndependencies = ["pydantic>=2.0"]\n'
        )
        site = tmp_path / ".venv" / "lib" / "python3.12" / "site-packages"
        site.mkdir(parents=True)
        (site / "pydantic-2.11.7.dist-info").mkdir()

        dep = detect_dependencies(tmp_path)[0]
        assert dep.version == "2.11.7" and dep.installed is True


class TestDependencyDescription:
    def test_empty_for_a_project_with_no_manifest(self, tmp_path: Path):
        assert describe_dependencies(tmp_path) == ""

    def test_warns_about_fast_moving_libraries(self, tmp_path: Path):
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"three": "0.185.1"}}))
        text = describe_dependencies(tmp_path)
        assert "three 0.185.1" in text
        assert "training data probably predates" in text

    def test_no_warning_when_nothing_is_volatile(self, tmp_path: Path):
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"leftpad": "1.0.0"}}))
        text = describe_dependencies(tmp_path)
        assert "leftpad 1.0.0" in text
        assert "training data" not in text

    def test_survives_a_malformed_manifest(self, tmp_path: Path):
        (tmp_path / "package.json").write_text("{ not json")
        assert describe_dependencies(tmp_path) == ""


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


class TestSkillFormats:
    def test_reads_claude_frontmatter_format(self, tmp_path: Path):
        """Claude Code skills are SKILL.md with frontmatter and no skill.yaml."""
        skill = tmp_path / "frontend-design"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: frontend-design\ndescription: Guidance for visual design in React apps.\n---\n\n# Design\n\nBody text.\n"
        )
        parsed = parse_skill_dir(skill, "claude")
        assert parsed is not None
        assert parsed.name == "frontend-design"
        assert parsed.keywords, "must derive keywords when none are declared"
        assert not parsed.content.startswith("---"), "frontmatter should be stripped"
        assert "Body text." in parsed.content

    def test_explicit_keywords_win_over_derived(self, tmp_path: Path):
        skill = tmp_path / "s"
        skill.mkdir()
        (skill / "SKILL.md").write_text("---\nname: s\ndescription: A thing.\n---\nbody\n")
        (skill / "skill.yaml").write_text("keywords:\n  - alpha\n  - beta\n")
        assert parse_skill_dir(skill, "x").keywords == ["alpha", "beta"]

    def test_skill_without_content_is_ignored(self, tmp_path: Path):
        skill = tmp_path / "empty"
        skill.mkdir()
        (skill / "skill.yaml").write_text("name: empty\n")
        assert parse_skill_dir(skill, "x") is None


class TestKeywordDerivation:
    def test_keeps_domain_terms(self):
        keywords = derive_keywords(
            "build-mcp-server",
            "Use this when the user asks to create an MCP server for Claude.",
        )
        assert "mcp" in keywords
        assert "build-mcp-server" in keywords

    @pytest.mark.parametrize("noise", ["use", "user", "asks", "create", "the", "when", "want"])
    def test_drops_boilerplate_verbs(self, noise):
        """These appear in nearly every description; matching on them would
        inject the skill into nearly every message."""
        keywords = derive_keywords(
            "some-skill", "Use this when the user asks or wants to create something."
        )
        assert noise not in keywords

    def test_keeps_dotted_and_versioned_tokens(self):
        keywords = derive_keywords("threejs", "Helps with three.js and WebGL scenes.")
        assert "three.js" in keywords

    def test_bounded_length(self):
        keywords = derive_keywords("x", " ".join(f"Word{i}" for i in range(60)))
        assert len(keywords) <= 10


class TestSkillImport:
    def test_import_writes_keywords_for_claude_skills(self, tmp_path: Path):
        source_root = tmp_path / "claude" / "skills"
        skill = source_root / "math-olympiad"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: math-olympiad\ndescription: Solve IMO and Putnam problems.\n---\nbody\n"
        )

        target = tmp_path / "ollamacode" / "skills"
        imported, skipped = import_claude_skills(
            target_dir=target, roots=[source_root]
        )
        assert imported == ["math-olympiad"]
        assert (target / "math-olympiad" / "SKILL.md").is_file()

        generated = (target / "math-olympiad" / "skill.yaml").read_text()
        assert "keywords:" in generated and "imo" in generated.lower()

    def test_second_import_skips_instead_of_clobbering(self, tmp_path: Path):
        source_root = tmp_path / "skills"
        skill = source_root / "s"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\nbody\n")
        target = tmp_path / "out"

        import_claude_skills(target_dir=target, roots=[source_root])
        # a user's edits to keywords must survive a re-run
        (target / "s" / "skill.yaml").write_text("keywords:\n  - mine\n")
        imported, skipped = import_claude_skills(target_dir=target, roots=[source_root])

        assert imported == []
        assert "already imported" in skipped[0]
        assert "mine" in (target / "s" / "skill.yaml").read_text()

    def test_only_filter(self, tmp_path: Path):
        source_root = tmp_path / "skills"
        for name in ("keep", "drop"):
            skill = source_root / name
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nbody\n")
        imported, _ = import_claude_skills(
            target_dir=tmp_path / "out", roots=[source_root], only=["keep"]
        )
        assert imported == ["keep"]

    def test_discovery_handles_missing_directories(self):
        assert discover_claude_skills(roots=[Path("/nonexistent/path")]) == []


class TestBundledSkills:
    # `config` redirects Path.home(), so the user's own imported skills in the
    # real ~/.ollamacode do not leak into this assertion
    def test_threejs_skill_ships_and_matches(self, project: Path, config):
        from ollama_coder.core.extensions import SkillRegistry

        registry = SkillRegistry(project)
        assert "threejs" in registry.names()
        assert [s.name for s in registry.match("add orbitcontrols to the three.js scene")] == ["threejs"]
        assert registry.match("rename this python variable") == []


class TestSkillRanking:
    def _registry(self, root: Path):
        from ollama_coder.core.extensions import SkillRegistry

        return SkillRegistry(root)

    def _write(self, root: Path, name: str, keywords: list[str]) -> None:
        skill = root / ".ollamacode" / "skills" / name
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(f"# {name}\nbody\n")
        (skill / "skill.yaml").write_text(
            f"name: {name}\nkeywords:\n" + "".join(f"  - {k}\n" for k in keywords)
        )

    def test_more_specific_keyword_ranks_first(self, project: Path, config):
        self._write(project, "generic", ["mcp"])
        self._write(project, "specific", ["mcp server"])
        ranked = [s.name for s in self._registry(project).match("build an mcp server")]
        assert ranked[0] == "specific"

    def test_activation_is_capped(self, project: Path, config):
        for index in range(5):
            self._write(project, f"skill{index}", ["widget"])
        activated = self._registry(project).activate_for("fix the widget", limit=2)
        assert len(activated) == 2, "injecting every match would swamp the context"

    def test_already_active_skills_are_not_reinjected(self, project: Path, config):
        self._write(project, "one", ["widget"])
        registry = self._registry(project)
        assert [s.name for s in registry.activate_for("widget")] == ["one"]
        assert registry.activate_for("widget again") == []


# ---------------------------------------------------------------------------
# Web search
# ---------------------------------------------------------------------------


class TestDuckDuckGo:
    def test_unwraps_redirect_urls(self):
        """DDG wraps results in //duckduckgo.com/l/?uddg=... — unusable as-is,
        because fetch_url requires a scheme."""
        wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fthreejs.org%2Fdocs%2F&rut=abc123"
        assert unwrap_ddg_url(wrapped) == "https://threejs.org/docs/"

    def test_passes_through_a_plain_url(self):
        assert unwrap_ddg_url("https://example.com/a?b=c") == "https://example.com/a?b=c"

    def test_adds_a_scheme_to_protocol_relative_urls(self):
        assert unwrap_ddg_url("//example.com/x").startswith("https://")

    def test_parses_the_html_result_page(self):
        html = (
            '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fthreejs.org%2F">'
            "three.js</a><div class=\"result__snippet\">JavaScript 3D library</a>"
        )
        results = _parse_ddg(html, limit=5)
        assert len(results) == 1
        assert results[0]["url"] == "https://threejs.org/"
        assert results[0]["title"] == "three.js"

    def test_discards_results_without_a_usable_url(self):
        html = '<a class="result__a" href="javascript:void(0)">x</a><div class="result__snippet">y</a>'
        assert _parse_ddg(html, limit=5) == []


class TestRequirementParsing:
    def test_environment_markers_are_stripped(self, tmp_path: Path):
        """'tomli>=2.0; python_version < "3.11"' must not leak the marker
        into the version string shown to the model."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "x"\n'
            'dependencies = [\'tomli>=2.0; python_version < "3.11"\', "httpx[http2]>=0.27"]\n'
        )
        deps = {d.name: d.version for d in detect_dependencies(tmp_path)}
        assert deps["tomli"] == "2.0"
        assert "python_version" not in describe_dependencies(tmp_path)
        assert deps["httpx"] == "0.27", "extras must not confuse the version"
