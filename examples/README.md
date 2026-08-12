# Examples

Integrations that use OllamaCoder's headless mode.

| | |
|---|---|
| [`git-hooks/pre-commit`](git-hooks/pre-commit) | reviews staged changes and blocks the commit on a real defect |
| [`github-actions/ollamacode-review.yml`](github-actions/ollamacode-review.yml) | reviews pull requests with a model running on the runner |

Both run with `--read-only`, which unregisters every mutating tool including
`bash`, so the tree cannot change regardless of what the model decides to do.

## Install the pre-commit hook

```bash
cp examples/git-hooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

Bypass it for one commit with `SKIP_REVIEW=1 git commit`, and pick the model
with `REVIEW_MODEL=qwen3:14b git commit`.

## Rolling your own

The pieces that matter for scripting:

```bash
ollama-coder -p "<prompt>" --output json --read-only --timeout 300
```

* stdout is JSON: `{ok, response, error, tools, duration_ms, exit_code}`
* stderr carries progress; `--quiet` silences it
* exit `0` success · `1` error · `2` a human was needed (approval or timeout)
* piped stdin is appended to the prompt as context
