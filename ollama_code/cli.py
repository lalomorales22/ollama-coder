#!/usr/bin/env python3
"""
OllamaCode - An agentic coding assistant for Ollama
Inspired by Claude Code's architecture with autonomous capabilities

Features:
- Agentic loop: gather context -> plan -> execute -> verify -> iterate
- Full tool system: bash, file operations, git, search
- Autonomous mode with multi-step execution
- Project and user-level configuration
- OLLAMA.md context files
- Permission management
- Persistent conversation history
- Error recovery and iteration
"""

import os
import sys
import json
import subprocess
import argparse
import readline
import shlex
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime
import re
import shutil
import glob
import traceback

try:
    from . import __version__
except Exception:
    __version__ = "0.0.0"

try:
    import ollama
except ImportError:
    print("Error: ollama package not found. Install with: pip install ollama")
    sys.exit(1)


# ============================================================================
# Configuration Management
# ============================================================================

class Config:
    """Manages configuration from multiple sources with hierarchical loading"""
    
    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.user_config_dir = Path.home() / ".ollamacode"
        self.project_config_dir = project_dir / ".ollamacode"
        
        # Create config directories if they don't exist
        self.user_config_dir.mkdir(exist_ok=True)
        self.project_config_dir.mkdir(exist_ok=True)
        
        self.config = self._load_config()
        self.context = self._load_context()

    def _default_config(self) -> Dict[str, Any]:
        """Return the default configuration"""
        return {
            "model": None,
            "ollama": {
                "host": "",
                "timeout_sec": 60,
                "headers": {},
                "api_key": ""
            },
            "max_tokens": 4096,
            "temperature": 0.7,
            "auto_mode": False,
            "max_iterations": 25,
            "max_tool_rounds": 8,
            "web_search": {
                "enabled": False,
                "provider": "custom",
                "endpoint": "",
                "api_key": "",
                "timeout_sec": 15,
                "max_results": 5
            },
            "permissions": {
                "allowed_tools": ["*"],
                "denied_tools": []
            }
        }

    def _deep_update(self, base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively merge updates into base configuration"""
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                base[key] = self._deep_update(base[key], value)
            else:
                base[key] = value
        return base

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from user and project settings"""
        config = self._default_config()

        # Load user-level config
        user_config_file = self.user_config_dir / "settings.json"
        if user_config_file.exists():
            with open(user_config_file) as f:
                user_config = json.load(f)
                self._deep_update(config, user_config)

        # Load project-level config (overrides user config)
        project_config_file = self.project_config_dir / "settings.json"
        if project_config_file.exists():
            with open(project_config_file) as f:
                project_config = json.load(f)
                self._deep_update(config, project_config)

        return config
    
    def _load_context(self) -> str:
        """Load context from OLLAMA.md files"""
        context_parts = []
        
        # Load user-level OLLAMA.md
        user_context_file = self.user_config_dir / "OLLAMA.md"
        if user_context_file.exists():
            context_parts.append("# User-level Context\n")
            context_parts.append(user_context_file.read_text())
        
        # Load project-level OLLAMA.md
        project_context_file = self.project_config_dir / "OLLAMA.md"
        if project_context_file.exists():
            context_parts.append("\n# Project-level Context\n")
            context_parts.append(project_context_file.read_text())
        
        return "\n".join(context_parts)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value"""
        return self.config.get(key, default)
    
    def set(self, key: str, value: Any):
        """Set configuration value"""
        self.config[key] = value
    
    def save_user_config(self):
        """Save current config to user settings"""
        config_file = self.user_config_dir / "settings.json"
        with open(config_file, 'w') as f:
            json.dump(self.config, f, indent=2)
    
    def save_project_config(self):
        """Save current config to project settings"""
        config_file = self.project_config_dir / "settings.json"
        with open(config_file, 'w') as f:
            json.dump(self.config, f, indent=2)


# ============================================================================
# Model Registry
# ============================================================================

class ModelRegistry:
    """Lists locally installed Ollama models"""

    def __init__(self, config: Config, client: ollama.Client):
        self.config = config
        self.client = client
        self._cache: Optional[List[Any]] = None

    def set_client(self, client: ollama.Client) -> None:
        self.client = client
        self._cache = None

    def list_models(self, refresh: bool = True) -> List[Any]:
        """Return model metadata for locally installed models"""
        if not refresh and self._cache is not None:
            return self._cache

        models: List[Any] = []
        try:
            response = self.client.list()
            if hasattr(response, "models"):
                models = list(response.models or [])
            elif hasattr(response, "model_dump"):
                models = response.model_dump().get("models", [])
            elif isinstance(response, dict):
                models = response.get("models", [])
        except Exception:
            models = self._list_models_cli()

        self._cache = models
        return models

    def _find_ollama_binary(self) -> Optional[str]:
        path = shutil.which("ollama")
        if path:
            return path
        for candidate in ("/usr/local/bin/ollama", "/opt/homebrew/bin/ollama"):
            if os.path.exists(candidate):
                return candidate
        return None

    def list_model_names(self, refresh: bool = True) -> List[str]:
        """Return model names only"""
        names = []
        for model in self.list_models(refresh=refresh):
            name = None
            if isinstance(model, dict):
                name = model.get("name") or model.get("model") or model.get("id")
            else:
                name = getattr(model, "name", None) or getattr(model, "model", None)
            if name:
                names.append(name)
        return names

    def _list_models_cli(self) -> List[Dict[str, Any]]:
        """Fallback to `ollama list` if Python API fails"""
        ollama_bin = self._find_ollama_binary()
        if not ollama_bin:
            return []

        try:
            env = os.environ.copy()
            host = (self.config.get("ollama", {}).get("host") or "").strip()
            if host:
                env["OLLAMA_HOST"] = host

            result = subprocess.run(
                [ollama_bin, "list"],
                capture_output=True,
                text=True,
                env=env,
                timeout=10
            )
            if result.returncode != 0:
                return []

            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if len(lines) <= 1:
                return []

            models = []
            for line in lines[1:]:
                parts = line.split()
                if not parts:
                    continue
                models.append({"name": parts[0]})
            return models
        except Exception:
            return []


# ============================================================================
# Tool System
# ============================================================================

class ToolResult:
    """Result from a tool execution"""
    def __init__(self, success: bool, output: str, error: Optional[str] = None):
        self.success = success
        self.output = output
        self.error = error
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error
        }


class Tool:
    """Base class for all tools"""
    name: str = ""
    description: str = ""
    
    def get_schema(self) -> Dict[str, Any]:
        """Return JSON schema for this tool"""
        raise NotImplementedError
    
    def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with given parameters"""
        raise NotImplementedError


class BashTool(Tool):
    """Execute bash commands"""
    name = "bash"
    description = "Execute bash commands in the terminal"
    
    def __init__(self, working_dir: Path):
        self.working_dir = working_dir
        self.session_env = os.environ.copy()
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Execute a bash command. Maintains session state between calls.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The bash command to execute"
                        }
                    },
                    "required": ["command"]
                }
            }
        }
    
    def execute(self, command: str) -> ToolResult:
        """Execute a bash command"""
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.working_dir,
                env=self.session_env,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR:\n{result.stderr}"
            
            return ToolResult(
                success=result.returncode == 0,
                output=output or "(no output)",
                error=None if result.returncode == 0 else f"Exit code: {result.returncode}"
            )
        except subprocess.TimeoutExpired:
            return ToolResult(False, "", "Command timed out after 30 seconds")
        except Exception as e:
            return ToolResult(False, "", str(e))


class ReadFileTool(Tool):
    """Read file contents"""
    name = "read_file"
    description = "Read the contents of a file"
    
    def __init__(self, working_dir: Path):
        self.working_dir = working_dir
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read the contents of a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path to the file to read (relative to project root)"
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "Optional: Start line number (1-indexed)"
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "Optional: End line number (inclusive, -1 for end of file)"
                        }
                    },
                    "required": ["path"]
                }
            }
        }
    
    def execute(self, path: str, start_line: Optional[int] = None, 
                end_line: Optional[int] = None) -> ToolResult:
        """Read a file"""
        try:
            file_path = self.working_dir / path
            if not file_path.exists():
                return ToolResult(False, "", f"File not found: {path}")
            
            if file_path.is_dir():
                return ToolResult(False, "", f"Path is a directory: {path}")
            
            with open(file_path, 'r') as f:
                lines = f.readlines()
            
            if start_line is not None or end_line is not None:
                start = (start_line - 1) if start_line else 0
                end = end_line if end_line and end_line != -1 else len(lines)
                lines = lines[start:end]
            
            content = ''.join(lines)
            line_count = len(lines)
            
            return ToolResult(
                True,
                f"File: {path}\nLines: {line_count}\n\n{content}"
            )
        except Exception as e:
            return ToolResult(False, "", str(e))


class WriteFileTool(Tool):
    """Write or create files"""
    name = "write_file"
    description = "Write content to a file (creates or overwrites)"
    
    def __init__(self, working_dir: Path):
        self.working_dir = working_dir
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write content to a file (creates new or overwrites existing)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path to the file to write"
                        },
                        "content": {
                            "type": "string",
                            "description": "Content to write to the file"
                        }
                    },
                    "required": ["path", "content"]
                }
            }
        }
    
    def execute(self, path: str, content: str) -> ToolResult:
        """Write to a file"""
        try:
            file_path = self.working_dir / path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(file_path, 'w') as f:
                f.write(content)
            
            line_count = len(content.splitlines())
            return ToolResult(
                True,
                f"Successfully wrote {line_count} lines to {path}"
            )
        except Exception as e:
            return ToolResult(False, "", str(e))


class EditFileTool(Tool):
    """Edit files using string replacement"""
    name = "edit_file"
    description = "Edit a file by replacing old content with new content"
    
    def __init__(self, working_dir: Path):
        self.working_dir = working_dir
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "Edit a file by replacing old_str with new_str",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path to the file to edit"
                        },
                        "old_str": {
                            "type": "string",
                            "description": "String to find and replace"
                        },
                        "new_str": {
                            "type": "string",
                            "description": "String to replace with"
                        }
                    },
                    "required": ["path", "old_str", "new_str"]
                }
            }
        }
    
    def execute(self, path: str, old_str: str, new_str: str) -> ToolResult:
        """Edit a file"""
        try:
            file_path = self.working_dir / path
            if not file_path.exists():
                return ToolResult(False, "", f"File not found: {path}")
            
            with open(file_path, 'r') as f:
                content = f.read()
            
            if old_str not in content:
                return ToolResult(False, "", f"String not found in file: {old_str}")
            
            # Count occurrences
            count = content.count(old_str)
            if count > 1:
                return ToolResult(
                    False, "",
                    f"String appears {count} times. Please be more specific."
                )
            
            new_content = content.replace(old_str, new_str)
            
            with open(file_path, 'w') as f:
                f.write(new_content)
            
            return ToolResult(
                True,
                f"Successfully edited {path}\nReplaced 1 occurrence"
            )
        except Exception as e:
            return ToolResult(False, "", str(e))


class ListDirectoryTool(Tool):
    """List directory contents"""
    name = "list_directory"
    description = "List contents of a directory"
    
    def __init__(self, working_dir: Path):
        self.working_dir = working_dir
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "List the contents of a directory",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path to directory (relative to project root, '.' for current)"
                        },
                        "recursive": {
                            "type": "boolean",
                            "description": "Whether to list recursively"
                        }
                    },
                    "required": ["path"]
                }
            }
        }
    
    def execute(self, path: str = ".", recursive: bool = False) -> ToolResult:
        """List directory contents"""
        try:
            dir_path = self.working_dir / path
            if not dir_path.exists():
                return ToolResult(False, "", f"Directory not found: {path}")
            
            if not dir_path.is_dir():
                return ToolResult(False, "", f"Path is not a directory: {path}")
            
            if recursive:
                items = []
                for root, dirs, files in os.walk(dir_path):
                    rel_root = Path(root).relative_to(dir_path)
                    for d in dirs:
                        items.append(f"  DIR: {rel_root / d}")
                    for f in files:
                        items.append(f"  FILE: {rel_root / f}")
                output = "\n".join(items)
            else:
                items = sorted(dir_path.iterdir(), key=lambda x: (not x.is_dir(), x.name))
                output = "\n".join([
                    f"  {'DIR' if item.is_dir() else 'FILE'}: {item.name}"
                    for item in items
                ])
            
            return ToolResult(True, f"Contents of {path}:\n{output}")
        except Exception as e:
            return ToolResult(False, "", str(e))


class SearchCodeTool(Tool):
    """Search for patterns in code"""
    name = "search_code"
    description = "Search for text patterns in files"
    
    def __init__(self, working_dir: Path):
        self.working_dir = working_dir
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "search_code",
                "description": "Search for a pattern in files using grep",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Pattern to search for"
                        },
                        "file_pattern": {
                            "type": "string",
                            "description": "File pattern to search in (e.g., '*.py', default: all files)"
                        }
                    },
                    "required": ["pattern"]
                }
            }
        }
    
    def execute(self, pattern: str, file_pattern: str = "*") -> ToolResult:
        """Search for pattern in files"""
        try:
            if shutil.which("rg"):
                cmd = ["rg", "--line-number", "--no-heading", "--color", "never"]
                if file_pattern and file_pattern != "*":
                    cmd.extend(["-g", file_pattern])
                cmd.extend(["--", pattern])
            else:
                cmd = ["grep", "-rn"]
                if file_pattern and file_pattern != "*":
                    cmd.append(f"--include={file_pattern}")
                cmd.extend(["--", pattern, "."])

            result = subprocess.run(
                cmd,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().splitlines()
                output = f"Found {len(lines)} matches:\n{result.stdout}"
                return ToolResult(True, output)
            if result.returncode == 1:
                return ToolResult(True, "No matches found")
            return ToolResult(False, "", result.stderr.strip() or f"Search failed (exit {result.returncode})")
        except Exception as e:
            return ToolResult(False, "", str(e))


class GitTool(Tool):
    """Run git commands"""
    name = "git"
    description = "Run git commands in the repository"

    def __init__(self, working_dir: Path):
        self.working_dir = working_dir

    def get_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "git",
                "description": "Run a git command (args only, e.g. 'status -sb')",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "args": {
                            "type": "string",
                            "description": "Git arguments, e.g. 'status -sb' or 'diff --stat'"
                        }
                    },
                    "required": ["args"]
                }
            }
        }

    def execute(self, args: str) -> ToolResult:
        """Execute a git command"""
        if not shutil.which("git"):
            return ToolResult(False, "", "git is not installed or not on PATH")

        try:
            cmd = ["git"] + shlex.split(args)
            result = subprocess.run(
                cmd,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=30
            )

            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR:\n{result.stderr}"

            return ToolResult(
                success=result.returncode == 0,
                output=output or "(no output)",
                error=None if result.returncode == 0 else f"Exit code: {result.returncode}"
            )
        except Exception as e:
            return ToolResult(False, "", str(e))


class WebSearchTool(Tool):
    """Search the web via a configured endpoint"""
    name = "web_search"
    description = "Search the web for up-to-date information"

    def __init__(self, config: Config):
        self.config = config

    def get_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web for up-to-date information",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query"
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Optional: maximum number of results"
                        }
                    },
                    "required": ["query"]
                }
            }
        }

    def execute(self, query: str, max_results: Optional[int] = None) -> ToolResult:
        """Execute a web search via configured provider"""
        cfg = self.config.get("web_search", {})
        if not cfg.get("enabled", False):
            return ToolResult(False, "", "Web search is disabled. Configure web_search in settings.json.")

        provider = cfg.get("provider", "custom")
        if provider != "custom":
            return ToolResult(False, "", f"Unsupported web search provider: {provider}")

        endpoint = (cfg.get("endpoint") or "").strip()
        if not endpoint:
            return ToolResult(False, "", "web_search.endpoint is not configured")

        timeout = cfg.get("timeout_sec", 15)
        limit = max_results or cfg.get("max_results", 5)

        params = {"q": query, "n": limit}
        query_string = urllib.parse.urlencode(params)
        joiner = "&" if "?" in endpoint else "?"
        url = f"{endpoint}{joiner}{query_string}"

        headers = {"User-Agent": "OllamaCode/1.0"}
        api_key = (cfg.get("api_key") or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        request = urllib.request.Request(url, headers=headers)

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
            return self._format_response(body, limit)
        except urllib.error.HTTPError as e:
            return ToolResult(False, "", f"HTTP error {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            return ToolResult(False, "", f"Network error: {e.reason}")
        except Exception as e:
            return ToolResult(False, "", str(e))

    def _format_response(self, body: str, limit: int) -> ToolResult:
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return ToolResult(True, body)

        results = data.get("results")
        if isinstance(results, list):
            trimmed = results[:limit]
            return ToolResult(True, json.dumps(trimmed, indent=2))

        return ToolResult(True, json.dumps(data, indent=2))


# ============================================================================
# Tool Manager
# ============================================================================

class ToolManager:
    """Manages all available tools and their execution"""
    
    def __init__(self, working_dir: Path, config: Config):
        self.working_dir = working_dir
        self.config = config
        self.tools: Dict[str, Tool] = {}
        self._register_tools()
    
    def _register_tools(self):
        """Register all available tools"""
        self.tools = {
            "bash": BashTool(self.working_dir),
            "read_file": ReadFileTool(self.working_dir),
            "write_file": WriteFileTool(self.working_dir),
            "edit_file": EditFileTool(self.working_dir),
            "list_directory": ListDirectoryTool(self.working_dir),
            "search_code": SearchCodeTool(self.working_dir),
            "git": GitTool(self.working_dir),
            "web_search": WebSearchTool(self.config),
        }
    
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Get schemas for all tools"""
        return [tool.get_schema() for tool in self.tools.values()]
    
    def execute_tool(self, tool_name: str, **kwargs) -> ToolResult:
        """Execute a tool by name"""
        if tool_name not in self.tools:
            return ToolResult(False, "", f"Unknown tool: {tool_name}")
        
        # Check permissions
        if not self._check_permission(tool_name):
            return ToolResult(False, "", f"Permission denied for tool: {tool_name}")
        
        return self.tools[tool_name].execute(**kwargs)
    
    def _check_permission(self, tool_name: str) -> bool:
        """Check if tool execution is permitted"""
        permissions = self.config.get("permissions", {})
        denied = permissions.get("denied_tools", [])
        allowed = permissions.get("allowed_tools", ["*"])
        
        # Check denied list first
        if tool_name in denied or "*" in denied:
            return False
        
        # Check allowed list
        if "*" in allowed or tool_name in allowed:
            return True
        
        return False


# ============================================================================
# Agentic Engine
# ============================================================================

class AgenticEngine:
    """The core agentic engine that orchestrates the autonomous loop"""
    
    def __init__(self, config: Config, tool_manager: ToolManager, client: ollama.Client):
        self.config = config
        self.tool_manager = tool_manager
        self.client = client
        self.messages: List[Dict[str, Any]] = []
        self.iteration_count = 0
        self.max_iterations = config.get("max_iterations", 10)
        self.max_tool_rounds = config.get("max_tool_rounds", 8)
        
        # Initialize with system context
        if config.context:
            self.messages.append({
                "role": "system",
                "content": self._build_system_prompt()
            })
    
    def _build_system_prompt(self) -> str:
        """Build the system prompt with context and capabilities"""
        prompt = """You are an agentic coding assistant running locally with Ollama. You help users with coding tasks by autonomously executing multi-step workflows.

Your capabilities:
- Execute bash commands to run code, tests, git operations, etc.
- Read, write, and edit files in the project
- Search through codebases
- List directory contents
- Run git commands
- Search the web for up-to-date information (when enabled)
- Make autonomous decisions to complete complex tasks

Your workflow:
1. Analyze the user's request
2. Break it down into steps
3. Execute tools to gather context
4. Plan your approach
5. Execute the plan using tools
6. Verify your work
7. Iterate if needed

Important guidelines:
- Always read files before editing them
- Verify changes after making them
- Run tests after code changes
- Run linters when relevant
- Use git to track changes
- Ask for clarification if needed
- Explain what you're doing as you work
- Be autonomous but safe - don't make destructive changes without being sure

"""
        
        if self.config.context:
            prompt += f"\n\n# Project Context\n{self.config.context}\n"
        
        return prompt

    def set_client(self, client: ollama.Client) -> None:
        self.client = client

    def _build_options(self) -> Dict[str, Any]:
        """Build Ollama options from config"""
        options: Dict[str, Any] = {}
        temperature = self.config.get("temperature")
        if temperature is not None:
            options["temperature"] = temperature
        max_tokens = self.config.get("max_tokens")
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        return options

    def _chat(self, include_tools: bool = True) -> Dict[str, Any]:
        """Call Ollama with current messages and options"""
        model = self.config.get("model")
        if not model:
            raise RuntimeError("No model configured. Set a model before chatting.")

        payload: Dict[str, Any] = {
            "model": model,
            "messages": self.messages
        }
        if include_tools:
            payload["tools"] = self.tool_manager.get_tool_schemas()

        options = self._build_options()
        if options:
            payload["options"] = options

        return self.client.chat(**payload)

    def _parse_tool_args(self, raw_args: Any) -> Dict[str, Any]:
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            try:
                return json.loads(raw_args)
            except json.JSONDecodeError:
                return {}
        return {}

    def _execute_tool_calls(self, tool_calls: List[Dict[str, Any]], verbose: bool = False) -> None:
        for tool_call in tool_calls:
            func = tool_call.get("function", {})
            name = func.get("name", "")
            args = self._parse_tool_args(func.get("arguments", {}))

            if verbose:
                print(f"🔧 Using tool: {name}")
                if args:
                    print(f"   Arguments: {json.dumps(args, indent=2)}")

            result = self.tool_manager.execute_tool(name, **args)

            if verbose:
                print(f"   Result: {'✅ Success' if result.success else '❌ Failed'}")
                if result.output:
                    print(f"   Output: {result.output[:200]}...")
                if result.error:
                    print(f"   Error: {result.error}")
                print()

            self.messages.append({
                "role": "tool",
                "name": name,
                "content": json.dumps(result.to_dict(), indent=2)
            })

    def _run_tool_rounds(self, verbose: bool = False) -> Dict[str, Any]:
        """Run tool calls until the model stops requesting tools or limit is reached"""
        rounds = 0
        last_content = ""

        while rounds < self.max_tool_rounds:
            response = self._chat(include_tools=True)
            assistant_message = response["message"]
            self.messages.append(assistant_message)

            if assistant_message.get("content"):
                last_content = assistant_message["content"]
                if verbose:
                    print(f"💭 Thinking: {assistant_message['content']}\n")

            tool_calls = assistant_message.get("tool_calls") or []
            if not tool_calls:
                return {"content": last_content, "completed": True}

            self._execute_tool_calls(tool_calls, verbose=verbose)
            rounds += 1

        return {"content": last_content, "completed": False}
    
    def chat(self, user_message: str, auto_mode: bool = False) -> str:
        """Process a user message and return response"""
        self.messages.append({
            "role": "user",
            "content": user_message
        })
        
        if auto_mode:
            return self._auto_mode_loop()
        else:
            return self._single_interaction()
    
    def _single_interaction(self) -> str:
        """Single interaction with the model"""
        try:
            result = self._run_tool_rounds(verbose=False)
            if not result["completed"]:
                return f"{result['content']}\n\n⚠️ Reached maximum tool rounds ({self.max_tool_rounds})"
            return result["content"]
            
        except Exception as e:
            error_msg = f"Error: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            return error_msg
    
    def _auto_mode_loop(self) -> str:
        """Autonomous mode - keeps working until task is complete"""
        print("\n🤖 Entering autonomous mode...")
        print(f"   Max iterations: {self.max_iterations}")
        print(f"   Max tool rounds: {self.max_tool_rounds}")
        print("   Press Ctrl+C to interrupt\n")
        
        responses = []
        self.iteration_count = 0
        
        try:
            while self.iteration_count < self.max_iterations:
                self.iteration_count += 1
                print(f"\n{'='*60}")
                print(f"Iteration {self.iteration_count}/{self.max_iterations}")
                print(f"{'='*60}\n")

                result = self._run_tool_rounds(verbose=True)
                if result["content"]:
                    responses.append(result["content"])

                if result["completed"]:
                    print("\n✅ Task appears to be complete (no more tool calls)")
                    break

                print(f"\n⚠️  Reached maximum tool rounds ({self.max_tool_rounds}) - continuing")
            
            if self.iteration_count >= self.max_iterations:
                print(f"\n⚠️  Reached maximum iterations ({self.max_iterations})")
            
            print(f"\n{'='*60}")
            print("Autonomous mode complete")
            print(f"{'='*60}\n")
            
            return "\n\n".join(responses)
            
        except KeyboardInterrupt:
            print("\n\n⚠️  Interrupted by user")
            return "\n\n".join(responses)
    
    def clear_history(self):
        """Clear conversation history"""
        system_message = self.messages[0] if self.messages and self.messages[0]['role'] == 'system' else None
        self.messages = []
        if system_message:
            self.messages.append(system_message)


# ============================================================================
# CLI Interface
# ============================================================================

class CLI:
    """Command-line interface for OllamaCode"""
    
    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.config = Config(project_dir)
        self.ollama_client = self._build_ollama_client()
        self.model_registry = ModelRegistry(self.config, self.ollama_client)
        self.tool_manager = ToolManager(project_dir, self.config)
        self.engine = AgenticEngine(self.config, self.tool_manager, self.ollama_client)
        
        # Setup readline for better input
        histfile = self.config.user_config_dir / "history"
        try:
            readline.read_history_file(histfile)
        except FileNotFoundError:
            pass
        self.histfile = histfile

    def _build_ollama_client(self) -> ollama.Client:
        ollama_cfg = self.config.get("ollama", {})
        host = (ollama_cfg.get("host") or "").strip() or os.environ.get("OLLAMA_HOST")
        timeout_sec = ollama_cfg.get("timeout_sec")
        headers = dict(ollama_cfg.get("headers") or {})
        api_key = (ollama_cfg.get("api_key") or "").strip()
        if api_key and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {api_key}"

        kwargs: Dict[str, Any] = {}
        if host:
            kwargs["host"] = host
        if timeout_sec:
            kwargs["timeout"] = timeout_sec
        if headers:
            kwargs["headers"] = headers

        return ollama.Client(**kwargs)

    def _refresh_ollama_client(self) -> None:
        self.ollama_client = self._build_ollama_client()
        self.model_registry.set_client(self.ollama_client)
        self.engine.set_client(self.ollama_client)

    def _list_models(self) -> List[str]:
        models = self.model_registry.list_model_names(refresh=True)
        return sorted(models)

    def _print_models(self) -> None:
        models = self._list_models()
        if not models:
            print("No local Ollama models found. Try: ollama pull <model>")
            return
        print("Available models:")
        for model in models:
            print(f"  - {model}")

    def _choose_model(self, persist: bool = True) -> Optional[str]:
        models = self._list_models()
        if not models:
            print("No local Ollama models found.")
            return self._prompt_manual_model(persist=persist)

        print("Available models:")
        for idx, model in enumerate(models, start=1):
            print(f"  {idx}) {model}")

        choice = input("Select model by number or name (blank to cancel): ").strip()
        if not choice:
            return None

        selected = None
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(models):
                selected = models[index - 1]
        elif choice in models:
            selected = choice

        if not selected:
            print("Invalid selection.")
            return None

        self.config.set("model", selected)
        if persist:
            save = input("Save as default in user config? [y/N]: ").strip().lower()
            if save in ("y", "yes"):
                self.config.save_user_config()
                print("✅ Saved to user config.")

        return selected

    def _resolve_model_choice(self, choice: str, models: List[str]) -> Optional[str]:
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(models):
                return models[index - 1]
            return None
        if choice in models:
            return choice
        return None

    def _prompt_model_on_start(self) -> None:
        current = self.config.get("model")
        models = self._list_models()

        if models:
            print("Available models:")
            for idx, model in enumerate(models, start=1):
                marker = " (current)" if model == current else ""
                print(f"  {idx}) {model}{marker}")

            prompt = "Select model by number or name"
            if current:
                prompt += f" (Enter to keep '{current}')"
            choice = input(f"{prompt}: ").strip()

            if not choice:
                if current:
                    return
                selected = models[0]
                self.config.set("model", selected)
                print(f"✅ Model set to {selected}")
                return

            selected = self._resolve_model_choice(choice, models)
            if not selected:
                print("Invalid selection.")
                self._prompt_manual_model(persist=True)
                return

            self.config.set("model", selected)
            save = input("Save as default in user config? [y/N]: ").strip().lower()
            if save in ("y", "yes"):
                self.config.save_user_config()
                print("✅ Saved to user config.")
            return

        if current:
            use_current = input(f"Use configured model '{current}'? [Y/n]: ").strip().lower()
            if use_current in ("n", "no"):
                self._prompt_manual_model(persist=True)
            return

        print("No models found from Ollama.")
        self._prompt_manual_model(persist=True)

    def _prompt_manual_model(self, persist: bool = True) -> str:
        while True:
            choice = input("Enter model name manually (or 'quit' to exit): ").strip()
            if not choice:
                print("A model is required to continue.")
                continue
            if choice.lower() in ("quit", "exit"):
                print("No model selected. Exiting.")
                sys.exit(1)

            self.config.set("model", choice)
            if persist:
                save = input("Save as default in user config? [y/N]: ").strip().lower()
                if save in ("y", "yes"):
                    self.config.save_user_config()
                    print("✅ Saved to user config.")

            return choice

    def ensure_model_available(self, interactive: bool) -> bool:
        current_model = self.config.get("model")
        models = self._list_models()

        if models:
            if not current_model or current_model not in models:
                if interactive:
                    selected = self._choose_model(persist=True)
                    return bool(selected)
                print(f"Error: configured model '{current_model}' is not installed.")
                print("Use --model, --choose-model, or /models to select an installed model.")
                return False
            return True

        if current_model:
            print("⚠️  Unable to list models. Proceeding with configured model.")
            return True

        if interactive:
            print("Unable to list models from Ollama.")
            selected = self._prompt_manual_model(persist=True)
            return bool(selected)

        print("Error: no local models found and no model configured.")
        return False
    
    def run(self, prompt: Optional[str] = None, auto_mode: bool = False):
        """Run the CLI"""
        print(r"")
        print(r"   ██████╗ ██╗     ██╗      █████╗ ███╗   ███╗ █████╗ ")
        print(r"  ██╔═══██╗██║     ██║     ██╔══██╗████╗ ████║██╔══██╗")
        print(r"  ██║   ██║██║     ██║     ███████║██╔████╔██║███████║")
        print(r"  ██║   ██║██║     ██║     ██╔══██║██║╚██╔╝██║██╔══██║")
        print(r"  ╚██████╔╝███████╗███████╗██║  ██║██║ ╚═╝ ██║██║  ██║")
        print(r"   ╚═════╝ ╚══════╝╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚═╝  ╚═╝")
        print(r"     //       //       //     //    //   //   //       ")
        print(r"")
        print(r"     ██████╗ ██████╗ ██████╗ ███████╗██████╗ ")
        print(r"    ██╔════╝██╔═══██╗██╔══██╗██╔════╝██╔══██╗")
        print(r"    ██║     ██║   ██║██║  ██║█████╗  ██████╔╝")
        print(r"    ██║     ██║   ██║██║  ██║██╔══╝  ██╔══██╗")
        print(r"    ╚██████╗╚██████╔╝██████╔╝███████╗██║  ██║")
        print(r"     ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝")
        print(r"       //      //      //      //     //     ")
        print()
        print()
        box_width = 63
        title = f"OllamaCoder v{__version__}"
        subtitle = "Agentic Coding Assistant for Ollama"
        print("╔" + ("═" * box_width) + "╗")
        print("║" + title.center(box_width) + "║")
        print("║" + subtitle.center(box_width) + "║")
        print("╚" + ("═" * box_width) + "╝")
        print()
        if prompt is None:
            self._prompt_model_on_start()

        print(f"📁 Working directory: {self.project_dir}")
        print(f"🤖 Model: {self.config.get('model')}")
        print(f"⚙️  Auto mode: {'enabled' if auto_mode else 'disabled'}")
        print()
        print("Commands:")
        print("  /auto       - Toggle autonomous mode")
        print("  /clear      - Clear conversation history")
        print("  /config     - Show configuration")
        print("  /model      - Show or set model")
        print("  /models     - List installed models")
        print("  /host       - Show or set Ollama host")
        print("  /help       - Show this help")
        print("  /quit       - Exit")
        print()
        
        # If prompt provided, execute and exit (headless mode)
        if prompt:
            print(f"User: {prompt}\n")
            response = self.engine.chat(prompt, auto_mode)
            print(f"\nAssistant: {response}\n")
            return
        
        # Interactive mode
        try:
            while True:
                try:
                    user_input = input("You: ").strip()
                    
                    if not user_input:
                        continue
                    
                    # Handle commands
                    if user_input.startswith('/'):
                        if user_input == '/quit' or user_input == '/exit':
                            print("Goodbye!")
                            break
                        elif user_input == '/clear':
                            self.engine.clear_history()
                            print("✅ History cleared")
                            continue
                        elif user_input == '/auto':
                            auto_mode = not auto_mode
                            print(f"✅ Auto mode {'enabled' if auto_mode else 'disabled'}")
                            continue
                        elif user_input == '/config':
                            print(json.dumps(self.config.config, indent=2))
                            continue
                        elif user_input == '/help':
                            print("\nCommands:")
                            print("  /auto   - Toggle autonomous mode")
                            print("  /clear  - Clear conversation history")
                            print("  /config - Show configuration")
                            print("  /model  - Show or set model")
                            print("  /models - List installed models")
                            print("  /host   - Show or set Ollama host")
                            print("  /help   - Show this help")
                            print("  /quit   - Exit\n")
                            continue
                        elif user_input == '/models':
                            self._print_models()
                            continue
                        elif user_input.startswith('/host'):
                            parts = user_input.split(maxsplit=1)
                            if len(parts) == 1:
                                host = (self.config.get("ollama", {}).get("host") or "").strip()
                                if not host:
                                    host = os.environ.get("OLLAMA_HOST", "")
                                print(f"Ollama host: {host or '(default)'}")
                                continue
                            arg = parts[1].strip()
                            if arg == "--clear":
                                ollama_cfg = self.config.config.setdefault("ollama", {})
                                ollama_cfg["host"] = ""
                                save = input("Save as default in user config? [y/N]: ").strip().lower()
                                if save in ("y", "yes"):
                                    self.config.save_user_config()
                                    print("✅ Saved to user config.")
                                self._refresh_ollama_client()
                                print("✅ Ollama host cleared.")
                                continue
                            ollama_cfg = self.config.config.setdefault("ollama", {})
                            ollama_cfg["host"] = arg
                            save = input("Save as default in user config? [y/N]: ").strip().lower()
                            if save in ("y", "yes"):
                                self.config.save_user_config()
                                print("✅ Saved to user config.")
                            self._refresh_ollama_client()
                            print(f"✅ Ollama host set to {arg}")
                            continue
                        elif user_input.startswith('/model'):
                            parts = user_input.split(maxsplit=1)
                            if len(parts) == 1:
                                print(f"Current model: {self.config.get('model')}")
                                continue
                            arg = parts[1].strip()
                            if arg == "--choose":
                                self._choose_model(persist=True)
                                continue
                            available = self._list_models()
                            if available and arg not in available:
                                print(f"Model not installed: {arg}")
                                print("Use /models to see installed models.")
                                continue
                            self.config.set("model", arg)
                            print(f"✅ Model set to {arg}")
                            continue
                        else:
                            print(f"Unknown command: {user_input}")
                            continue
                    
                    # Process message
                    print()
                    response = self.engine.chat(user_input, auto_mode)
                    print(f"\nAssistant: {response}\n")
                    
                except KeyboardInterrupt:
                    print("\n(Use /quit to exit)")
                    continue
                
        finally:
            # Save history
            readline.write_history_file(self.histfile)


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="OllamaCode - Agentic coding assistant for Ollama",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ollama-coder                           # Start interactive mode
  ollama-coder --auto                    # Start with auto mode enabled
  ollama-coder -p "fix the bug in app.py"  # Headless mode
  ollama-coder -p "refactor this code" --auto  # Headless + auto mode
  ollama-coder --model glm-4.7:cloud         # Use specific model
  ollama-coder --dir /path/to/project    # Work in specific directory
        """
    )
    
    parser.add_argument(
        '-p', '--prompt',
        help='Prompt to execute (headless mode)',
        type=str
    )
    
    parser.add_argument(
        '--auto',
        help='Enable autonomous mode',
        action='store_true'
    )
    
    parser.add_argument(
        '--model',
        help='Ollama model to use (default: configured or first installed)',
        type=str,
        default=None
    )

    parser.add_argument(
        '--list-models',
        help='List installed Ollama models and exit',
        action='store_true'
    )

    parser.add_argument(
        '--choose-model',
        help='Interactively choose a model before starting',
        action='store_true'
    )
    
    parser.add_argument(
        '--dir',
        help='Project directory (default: current directory)',
        type=Path,
        default=Path.cwd()
    )
    
    parser.add_argument(
        '--max-iterations',
        help='Maximum iterations in auto mode (default: 25)',
        type=int,
        default=None
    )
    
    args = parser.parse_args()
    
    # Validate project directory
    if not args.dir.exists():
        print(f"Error: Directory does not exist: {args.dir}")
        sys.exit(1)
    
    # Initialize and run
    cli = CLI(args.dir)

    if args.list_models:
        cli._print_models()
        return
    
    # Override config with CLI args
    if args.model:
        cli.config.set('model', args.model)
    if args.max_iterations is not None:
        cli.config.set('max_iterations', args.max_iterations)

    if args.choose_model:
        cli._choose_model(persist=True)

    if args.prompt and not cli.ensure_model_available(interactive=False):
        sys.exit(1)
    
    # Run
    cli.run(prompt=args.prompt, auto_mode=args.auto)


if __name__ == "__main__":
    main()
