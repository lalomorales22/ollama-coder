"""
Tests for Phase 2 Extensibility System

Tests cover:
- CommandManager: Custom slash commands
- SubagentManager: Agent definitions
- SkillManager: Progressive skill loading
"""

import pytest
from pathlib import Path
import tempfile

# Import modules - handle both installed and development modes
try:
    from ollama_coder.commands import CommandManager, Command
    from ollama_coder.subagent import SubagentManager, AgentDefinition
    from ollama_coder.skills import SkillManager, Skill
except ImportError:
    pytest.skip("OllamaCoder not installed", allow_module_level=True)


class TestCommandManager:
    """Tests for CommandManager custom slash commands"""
    
    def test_loads_command_from_file(self, tmp_path):
        """Should load command from markdown file"""
        commands_dir = tmp_path / ".ollamacode" / "commands"
        commands_dir.mkdir(parents=True)
        
        (commands_dir / "test-cmd.md").write_text("""---
name: /test-cmd
description: Test command
---
This is the test command prompt.
""")
        
        manager = CommandManager(tmp_path)
        cmd = manager.get_command("/test-cmd")
        
        assert cmd is not None
        assert cmd.name == "/test-cmd"
        assert cmd.description == "Test command"
        assert "test command prompt" in cmd.content
    
    def test_execute_command_returns_prompt(self, tmp_path):
        """execute_command should return prompt for AI"""
        commands_dir = tmp_path / ".ollamacode" / "commands"
        commands_dir.mkdir(parents=True)
        
        (commands_dir / "hello.md").write_text("""---
name: /hello
description: Say hello
auto_mode: true
---
Say hello to the world!
""")
        
        manager = CommandManager(tmp_path)
        result = manager.execute_command("/hello")
        
        assert "prompt" in result
        assert result["auto_mode"] == True
        assert "hello" in result["prompt"].lower()
    
    def test_list_commands(self, tmp_path):
        """Should list all available commands"""
        commands_dir = tmp_path / ".ollamacode" / "commands"
        commands_dir.mkdir(parents=True)
        
        (commands_dir / "cmd1.md").write_text("# Command 1")
        (commands_dir / "cmd2.md").write_text("# Command 2")
        
        manager = CommandManager(tmp_path)
        commands = manager.list_commands()
        
        assert len(commands) == 2
    
    def test_empty_when_no_commands(self, tmp_path):
        """Should return empty when no commands exist"""
        manager = CommandManager(tmp_path)
        commands = manager.list_commands()
        
        assert len(commands) == 0


class TestSubagentManager:
    """Tests for SubagentManager agent definitions"""
    
    def test_has_builtin_agents(self, tmp_path):
        """Should have built-in agents defined"""
        manager = SubagentManager(tmp_path)
        agents = manager.list_agents()
        
        # Should have at least the built-in agents
        agent_names = [a.name for a in agents]
        assert "code-reviewer" in agent_names
        assert "test-writer" in agent_names
    
    def test_loads_agent_from_yaml(self, tmp_path):
        """Should load agent from YAML file"""
        agents_dir = tmp_path / ".ollamacode" / "agents"
        agents_dir.mkdir(parents=True)
        
        (agents_dir / "custom-agent.yaml").write_text("""
name: custom-agent
model: llama3:8b
description: A custom agent
system_prompt: You are helpful
allowed_tools:
  - read_file
  - bash
""")
        
        manager = SubagentManager(tmp_path)
        agent = manager.get_agent("custom-agent")
        
        assert agent is not None
        assert agent.name == "custom-agent"
        assert agent.model == "llama3:8b"
        assert "read_file" in agent.allowed_tools


class TestSkillManager:
    """Tests for SkillManager progressive loading"""
    
    def test_loads_skill_from_folder(self, tmp_path):
        """Should load skill from folder structure"""
        skill_dir = tmp_path / ".ollamacode" / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        
        (skill_dir / "skill.yaml").write_text("""
name: my-skill
description: Test skill
keywords:
  - testing
  - skill
""")
        
        (skill_dir / "SKILL.md").write_text("""
# My Skill

This is expertise content.
""")
        
        manager = SkillManager(tmp_path)
        skill = manager._skills.get("my-skill")
        
        assert skill is not None
        assert skill.name == "my-skill"
        assert "testing" in skill.keywords
    
    def test_finds_matching_skills(self, tmp_path):
        """Should find skills by keyword matching"""
        skill_dir = tmp_path / ".ollamacode" / "skills" / "pytest-skill"
        skill_dir.mkdir(parents=True)
        
        (skill_dir / "skill.yaml").write_text("""
name: pytest-skill
keywords:
  - pytest
  - testing
""")
        (skill_dir / "SKILL.md").write_text("# Pytest expertise")
        
        manager = SkillManager(tmp_path)
        matches = manager.find_matching_skills("How do I run pytest tests?")
        
        assert len(matches) > 0
        assert any(s.name == "pytest-skill" for s in matches)
    
    def test_load_and_get_content(self, tmp_path):
        """Should load skill and return content"""
        skill_dir = tmp_path / ".ollamacode" / "skills" / "example"
        skill_dir.mkdir(parents=True)
        
        (skill_dir / "skill.yaml").write_text("name: example\nkeywords: [ex]")
        (skill_dir / "SKILL.md").write_text("# Example Content\n\nDetails here.")
        
        manager = SkillManager(tmp_path)
        content = manager.load_skill("example")
        
        assert content is not None
        assert "Example Content" in content
        assert "example" in manager._loaded_skills


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
