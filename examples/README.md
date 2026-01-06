# OllamaCoder Examples

This directory contains example integrations for OllamaCoder.

## Git Hooks

### Pre-commit Hook
Reviews staged changes before commit.

```bash
# Install
cp examples/git-hooks/pre-commit .git/hooks/
chmod +x .git/hooks/pre-commit
```

## GitHub Actions

### PR Code Review
Automatically reviews pull requests.

```yaml
# Copy to your repo
cp examples/github-actions/ollamacode-review.yml .github/workflows/
```

## Headless Mode Usage

```bash
# Basic headless execution
ollama-coder --headless -p "fix lint errors"

# JSON output for parsing
ollama-coder --headless --output json -p "analyze this code"

# Safety limits
ollama-coder --headless --no-write --max-tools 10 -p "review changes"

# With timeout
ollama-coder --headless --timeout 120 -p "run tests"
```

### Exit Codes
- `0` - Success
- `1` - Failure/Error
- `2` - Needs human intervention (timeout, complex decision)
