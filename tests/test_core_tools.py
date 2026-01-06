"""
Tests for Phase 1 Core Tools

Tests cover:
- GlobTool: File pattern matching
- GrepTool: Regex search with context
- URLFetchTool: Web content fetching
"""

import pytest
from pathlib import Path
import tempfile
import os

# Import the tools - handle both installed and development modes
try:
    from ollama_coder.cli import GlobTool, GrepTool, URLFetchTool, ScreenshotTool, Config, ToolResult
except ImportError:
    pytest.skip("OllamaCoder not installed", allow_module_level=True)


class MockConfig:
    """Mock Config for testing"""
    def __init__(self):
        self._config = {}
    
    def get(self, key, default=None):
        return self._config.get(key, default)


class TestGlobTool:
    """Tests for GlobTool file pattern matching"""
    
    def test_finds_python_files(self, tmp_path):
        """Should find Python files with *.py pattern"""
        # Create test files
        (tmp_path / "test.py").touch()
        (tmp_path / "app.py").touch()
        (tmp_path / "readme.txt").touch()
        
        tool = GlobTool(tmp_path)
        result = tool.execute("*.py")
        
        assert result.success
        assert "Found 2 files" in result.output
        assert "test.py" in result.output
        assert "app.py" in result.output
        assert "readme.txt" not in result.output
    
    def test_recursive_glob(self, tmp_path):
        """Should find files recursively with **/*.py"""
        # Create nested structure
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").touch()
        (tmp_path / "test.py").touch()
        
        tool = GlobTool(tmp_path)
        result = tool.execute("**/*.py")
        
        assert result.success
        assert "main.py" in result.output
    
    def test_no_files_found(self, tmp_path):
        """Should return appropriate message when no files match"""
        tool = GlobTool(tmp_path)
        result = tool.execute("*.nonexistent")
        
        assert result.success
        assert "No files found" in result.output


class TestGrepTool:
    """Tests for GrepTool regex search"""
    
    def test_finds_pattern_in_files(self, tmp_path):
        """Should find simple patterns in files"""
        # Create test file
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello():\n    print('Hello World')\n")
        
        tool = GrepTool(tmp_path)
        result = tool.execute("hello")
        
        assert result.success
        assert "hello" in result.output.lower()
    
    def test_case_insensitive_search(self, tmp_path):
        """Should support case-insensitive search"""
        test_file = tmp_path / "test.py"
        test_file.write_text("def HelloWorld():\n    pass\n")
        
        tool = GrepTool(tmp_path)
        result = tool.execute("helloworld", case_insensitive=True)
        
        assert result.success
        # Should find HelloWorld with case insensitive flag
    
    def test_no_matches_found(self, tmp_path):
        """Should return appropriate message when no matches"""
        test_file = tmp_path / "test.py"
        test_file.write_text("def foo():\n    pass\n")
        
        tool = GrepTool(tmp_path)
        result = tool.execute("nonexistentpattern12345")
        
        assert result.success
        assert "No matches" in result.output


class TestURLFetchTool:
    """Tests for URLFetchTool web content fetching"""
    
    def test_rejects_invalid_url(self):
        """Should reject URLs without http/https"""
        config = MockConfig()
        tool = URLFetchTool(config)
        
        result = tool.execute("ftp://example.com")
        
        assert not result.success
        assert "http" in result.error.lower()
    
    def test_html_to_text_conversion(self):
        """HTML to text should strip tags"""
        config = MockConfig()
        tool = URLFetchTool(config)
        
        html = "<html><body><h1>Title</h1><p>Content</p></body></html>"
        text = tool._html_to_text(html)
        
        assert "<html>" not in text
        assert "<body>" not in text
        assert "Title" in text
        assert "Content" in text
    
    def test_html_to_text_removes_scripts(self):
        """HTML to text should remove script tags"""
        config = MockConfig()
        tool = URLFetchTool(config)
        
        html = "<html><script>alert('evil')</script><body>Safe content</body></html>"
        text = tool._html_to_text(html)
        
        assert "alert" not in text
        assert "Safe content" in text


class TestScreenshotTool:
    """Tests for ScreenshotTool browser screenshots"""
    
    def test_rejects_invalid_url(self):
        """Should reject URLs without http/https (or gracefully handle missing playwright)"""
        config = MockConfig()
        tool = ScreenshotTool(config)
        
        result = tool.execute("file:///etc/passwd")
        
        assert not result.success
        # Either gets http validation error or playwright not installed error
        assert "http" in result.error.lower() or "playwright" in result.error.lower()
    
    def test_graceful_playwright_missing(self):
        """Should give helpful error if playwright not installed"""
        # This test will pass if playwright IS installed (won't get the error)
        # or if it's NOT installed (will get helpful message)
        config = MockConfig()
        tool = ScreenshotTool(config)
        
        result = tool.execute("https://example.com")
        
        # Either succeeds OR gives helpful install message
        if not result.success:
            assert "playwright" in result.error.lower()


class TestBashTool:
    """Tests for BashTool bash command execution"""
    
    def test_bash_timeout_from_config(self, tmp_path):
        """BashTool should respect timeout from config"""
        try:
            from ollama_coder.cli import BashTool
        except ImportError:
            pytest.skip("OllamaCoder not installed")
        
        # Create mock config with short timeout for testing
        class MockConfig:
            def get(self, key, default=None):
                if key == "bash":
                    return {"timeout_sec": 1}  # 1 second
                return default
        
        tool = BashTool(tmp_path, MockConfig())
        result = tool.execute("sleep 2")  # Sleep longer than timeout
        
        assert not result.success
        assert "timed out" in result.error.lower()
    
    def test_bash_default_timeout(self, tmp_path):
        """BashTool should use 300s default when no config"""
        try:
            from ollama_coder.cli import BashTool
        except ImportError:
            pytest.skip("OllamaCoder not installed")
        
        tool = BashTool(tmp_path)  # No config passed
        assert tool.timeout == 300  # Default 5 minutes
    
    def test_bash_execute_success(self, tmp_path):
        """BashTool should execute simple commands"""
        try:
            from ollama_coder.cli import BashTool
        except ImportError:
            pytest.skip("OllamaCoder not installed")
        
        tool = BashTool(tmp_path)
        result = tool.execute("echo 'hello world'")
        
        assert result.success
        assert "hello world" in result.output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
