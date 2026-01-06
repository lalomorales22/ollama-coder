# OllamaCoder Tests

This directory contains tests for OllamaCoder.

## Running Tests

```bash
# Install pytest
pip install pytest

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_hooks.py -v

# Run with coverage (optional)
pip install pytest-cov
pytest tests/ --cov=ollama_coder --cov-report=term-missing
```

## Test Files

- `test_hooks.py` - Tests for the hooks system (BashSafetyParser, HookManager)
