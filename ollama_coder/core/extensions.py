"""User-authored extensions loaded from the file system.

Three kinds, all discovered from `~/.ollamacode/` (global) and
`<project>/.ollamacode/` (project, which wins on name collisions):

    agents/<name>.yaml   -- subagent definitions
    commands/<name>.md   -- custom slash commands (YAML frontmatter + prompt)
    skills/<name>/       -- SKILL.md + skill.yaml, loaded on keyword match
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - declared as a hard dependency
    yaml = None  # type: ignore[assignment]


def _safe_yaml(text: str) -> dict[str, Any]:
    if yaml is None:
        return {}
    try:
        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Subagents
# ---------------------------------------------------------------------------

READ_ONLY_TOOLSET = ["read_file", "list_dir", "glob", "grep", "git_read", "think", "fetch_url"]
FULL_TOOLSET = READ_ONLY_TOOLSET + ["bash", "write_file", "edit_file", "multi_edit", "todo_write"]


@dataclass
class AgentDefinition:
    name: str
    description: str = ""
    system_prompt: str = ""
    tools: list[str] = field(default_factory=list)
    model: str = ""            # empty means "same model as the main agent"
    temperature: float = 0.3
    max_steps: int = 20
    source: str = "builtin"


BUILTIN_AGENTS: list[AgentDefinition] = [
    AgentDefinition(
        name="explorer",
        description="Searches the codebase and reports back findings. Read-only.",
        system_prompt=(
            "You are a codebase explorer. Locate what was asked for and report "
            "concisely: file paths with line numbers, the relevant snippets, and "
            "how the pieces connect. Do not modify anything. Do not speculate -- "
            "if you did not find something, say so plainly. Your entire reply is "
            "consumed by another agent, so lead with the findings, no preamble."
        ),
        tools=READ_ONLY_TOOLSET,
        temperature=0.2,
    ),
    AgentDefinition(
        name="reviewer",
        description="Reviews a diff or files for bugs and risk. Read-only.",
        system_prompt=(
            "You are a code reviewer. Read the code, then report only defects "
            "you can point at: the file and line, what breaks, and the input or "
            "state that triggers it. Rank by severity. Skip style opinions and "
            "praise. If the code is sound, say so in one line."
        ),
        tools=READ_ONLY_TOOLSET,
        temperature=0.2,
    ),
    AgentDefinition(
        name="tester",
        description="Writes and runs tests for existing code.",
        system_prompt=(
            "You write tests. First detect the project's test framework and copy "
            "its existing conventions exactly. Cover the real behaviour including "
            "edge cases and failure paths, not just the happy path. Run the tests "
            "you wrote and report the actual output."
        ),
        tools=FULL_TOOLSET,
        temperature=0.3,
    ),
]


class AgentRegistry:
    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.global_dir = Path.home() / ".ollamacode" / "agents"
        self.local_dir = self.project_dir / ".ollamacode" / "agents"
        self.agents: dict[str, AgentDefinition] = {}
        self.reload()

    def reload(self) -> None:
        self.agents = {a.name: a for a in BUILTIN_AGENTS}
        for directory, source in ((self.global_dir, "global"), (self.local_dir, "project")):
            if not directory.is_dir():
                continue
            for path in sorted(list(directory.glob("*.yaml")) + list(directory.glob("*.yml"))):
                data = _safe_yaml(_read(path))
                if not data:
                    continue
                name = str(data.get("name") or path.stem)
                self.agents[name] = AgentDefinition(
                    name=name,
                    description=str(data.get("description", "")),
                    system_prompt=str(data.get("system_prompt", "")),
                    tools=[str(t) for t in (data.get("tools") or data.get("allowed_tools") or [])],
                    model=str(data.get("model", "") or ""),
                    temperature=float(data.get("temperature", 0.3)),
                    max_steps=int(data.get("max_steps", 20)),
                    source=source,
                )

    def get(self, name: str) -> AgentDefinition | None:
        return self.agents.get(name)

    def names(self) -> list[str]:
        return sorted(self.agents)

    def describe(self) -> str:
        lines = []
        for name in self.names():
            agent = self.agents[name]
            tag = "" if agent.source == "builtin" else f" [{agent.source}]"
            lines.append(f"  {name:<14} {agent.description}{tag}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Custom slash commands
# ---------------------------------------------------------------------------


@dataclass
class CustomCommand:
    name: str            # without the leading slash
    description: str
    prompt: str
    source: str
    path: Path
    model: str = ""


class CommandRegistry:
    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.global_dir = Path.home() / ".ollamacode" / "commands"
        self.local_dir = self.project_dir / ".ollamacode" / "commands"
        self.commands: dict[str, CustomCommand] = {}
        self.reload()

    def reload(self) -> None:
        self.commands = {}
        for directory, source in ((self.global_dir, "global"), (self.local_dir, "project")):
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.md")):
                command = self._parse(path, source)
                if command:
                    self.commands[command.name] = command

    def _parse(self, path: Path, source: str) -> CustomCommand | None:
        text = _read(path)
        if not text.strip():
            return None
        metadata: dict[str, Any] = {}
        body = text
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        if match:
            metadata = _safe_yaml(match.group(1))
            body = text[match.end() :]
        name = str(metadata.get("name") or path.stem).lstrip("/")
        return CustomCommand(
            name=name,
            description=str(metadata.get("description") or f"custom command /{name}"),
            prompt=body.strip(),
            source=source,
            path=path,
            model=str(metadata.get("model", "") or ""),
        )

    def get(self, name: str) -> CustomCommand | None:
        return self.commands.get(name.lstrip("/"))

    def render(self, name: str, args: str) -> str | None:
        """Expand a command into a prompt. `$ARGUMENTS` is substituted."""
        command = self.get(name)
        if not command:
            return None
        prompt = command.prompt
        if "$ARGUMENTS" in prompt:
            return prompt.replace("$ARGUMENTS", args)
        return f"{prompt}\n\n{args}".strip() if args else prompt

    def names(self) -> list[str]:
        return sorted(self.commands)


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


@dataclass
class Skill:
    name: str
    description: str
    keywords: list[str]
    content: str
    source: str
    path: Path


class SkillRegistry:
    """Progressive disclosure: skill text enters the prompt only on a match."""

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.global_dir = Path.home() / ".ollamacode" / "skills"
        self.local_dir = self.project_dir / ".ollamacode" / "skills"
        self.skills: dict[str, Skill] = {}
        self.active: set[str] = set()
        self.reload()

    def reload(self) -> None:
        self.skills = {}
        for directory, source in ((self.global_dir, "global"), (self.local_dir, "project")):
            if not directory.is_dir():
                continue
            for skill_dir in sorted(directory.iterdir()):
                if not skill_dir.is_dir():
                    continue
                content = ""
                for filename in ("SKILL.md", "skill.md"):
                    candidate = skill_dir / filename
                    if candidate.exists():
                        content = _read(candidate)
                        break
                if not content.strip():
                    continue
                metadata: dict[str, Any] = {}
                for filename in ("skill.yaml", "skill.yml"):
                    candidate = skill_dir / filename
                    if candidate.exists():
                        metadata = _safe_yaml(_read(candidate))
                        break
                name = str(metadata.get("name") or skill_dir.name)
                self.skills[name] = Skill(
                    name=name,
                    description=str(metadata.get("description", "")),
                    keywords=[str(k).lower() for k in (metadata.get("keywords") or [skill_dir.name])],
                    content=content,
                    source=source,
                    path=skill_dir,
                )

    def match(self, text: str) -> list[Skill]:
        lowered = text.lower()
        found = []
        for skill in self.skills.values():
            if any(keyword and keyword in lowered for keyword in skill.keywords):
                found.append(skill)
        return found

    def activate_for(self, text: str) -> list[Skill]:
        """Activate any newly-matched skills; returns only the new ones."""
        newly = [s for s in self.match(text) if s.name not in self.active]
        for skill in newly:
            self.active.add(skill.name)
        return newly

    def activate(self, name: str) -> Skill | None:
        skill = self.skills.get(name)
        if skill:
            self.active.add(name)
        return skill

    def deactivate(self, name: str) -> None:
        self.active.discard(name)

    def names(self) -> list[str]:
        return sorted(self.skills)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def scaffold_examples(project_dir: Path) -> list[Path]:
    """Write starter agent/command/skill files. Never overwrites."""
    created: list[Path] = []
    root = Path.home() / ".ollamacode"

    agent = root / "agents" / "security-auditor.yaml"
    if not agent.exists():
        agent.parent.mkdir(parents=True, exist_ok=True)
        agent.write_text(
            "name: security-auditor\n"
            "description: Audits code for security problems. Read-only.\n"
            'model: ""   # empty = use the main model\n'
            "temperature: 0.2\n"
            "tools: [read_file, grep, glob, list_dir, git_read]\n"
            "system_prompt: |\n"
            "  You audit code for security defects: injection, authz gaps,\n"
            "  secret exposure, unsafe deserialisation, path traversal.\n"
            "  Report only issues you can point at with a file and line, each\n"
            "  with the concrete input that exploits it. No generic advice.\n"
        )
        created.append(agent)

    command = root / "commands" / "review.md"
    if not command.exists():
        command.parent.mkdir(parents=True, exist_ok=True)
        command.write_text(
            "---\n"
            "name: review\n"
            "description: Review uncommitted changes\n"
            "---\n"
            "Review my uncommitted changes.\n\n"
            "Read the diff with git_read, then report real defects only: the file,\n"
            "the line, what breaks and when. Rank by severity. $ARGUMENTS\n"
        )
        created.append(command)

    skill_dir = root / "skills" / "python-testing"
    if not skill_dir.exists():
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "skill.yaml").write_text(
            "name: python-testing\n"
            "description: pytest conventions for this machine\n"
            "keywords: [pytest, unittest, test, testing, coverage]\n"
        )
        (skill_dir / "SKILL.md").write_text(
            "# Python testing\n\n"
            "- pytest, not unittest. Fixtures over setUp.\n"
            "- Name tests for the behaviour: `test_retries_on_timeout`.\n"
            "- Prefer `tmp_path` over manual temp directories.\n"
            "- Run a single test with `pytest path::name -x -q`.\n"
        )
        created.append(skill_dir)

    return created
