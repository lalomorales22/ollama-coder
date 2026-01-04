# OllamaCoder Handoff Document

**Date**: January 3, 2026  
**Current Version**: 0.2.2  
**Status**: PyPI working, Homebrew formula has dylib relocation issues

---

## 📍 Current State

### What Works ✅

1. **PyPI Installation** - Fully working
   ```bash
   pip install ollama-coder
   ollama-coder
   ```

2. **Features Implemented**:
   - Streaming responses with real-time output (`/streaming` to toggle)
   - Image/vision support for multimodal models (`/image <path> <message>`)
   - Context window management with auto-summarization (`/context` to view stats)
   - ThinkTool for structured reasoning
   - MultiEditTool for batch file edits
   - Enhanced agentic system prompt
   - 300 second timeout (5 minutes) for slow local models
   - Fixed 401 unauthorized error (empty headers filtering)

3. **Repositories**:
   - Main repo: https://github.com/lalomorales22/ollama-coder
   - Homebrew tap: https://github.com/lalomorales22/homebrew-ollama-coder

### What's Broken ❌

**Homebrew Installation** - Has dylib relocation error with pydantic-core

---

## 🔧 The Homebrew Problem

### Current Error

When running `brew install ollama-coder` after tapping:

```
==> /opt/homebrew/Cellar/ollama-coder/0.2.2/libexec/bin/pip install ollama-coder==0.2.2
Error: Failed changing dylib ID of /opt/homebrew/Cellar/ollama-coder/0.2.2/libexec/lib/python3.11/site-packages/pydantic_core/_pydantic_core.cpython-311-darwin.so
  from @rpath/pydantic_core._pydantic_core.cpython-311-darwin.so
    to /opt/homebrew/opt/ollama-coder/libexec/lib/python3.11/site-packages/pydantic_core/pydantic_core._pydantic_core.cpython-311-darwin.so
Error: Failed to fix install linkage
Updated load commands do not fit in the header of /opt/homebrew/Cellar/ollama-coder/0.2.2/libexec/lib/python3.11/site-packages/pydantic_core/_pydantic_core.cpython-311-darwin.so
```

### Root Cause

- `pydantic-core` is a Rust extension compiled with `maturin`
- The compiled `.so` file has limited header space for dylib path changes
- Homebrew tries to rewrite the dylib paths but the header is too small
- `skip_clean :all` didn't prevent the relocation attempt

### Current Formula (not working)

Location: `/Users/minibrain/Desktop/homebrew-ollama-coder/Formula/ollama-coder.rb`

```ruby
class OllamaCoder < Formula
  include Language::Python::Virtualenv

  desc "Agentic coding assistant for Ollama - like Claude Code, but local!"
  homepage "https://github.com/lalomorales22/ollama-coder"
  url "https://files.pythonhosted.org/packages/de/74/a4b183469ba5327305a9d3a6f730bdde449906605cce103d441210c14fb9/ollama_coder-0.2.2.tar.gz"
  sha256 "747c4aabd8b2d2fd8c342b5ba773e7a820b0b4c9c6de773703cd567e273adc76"
  license "MIT"

  depends_on "python@3.11"

  def install
    # Create virtualenv with pip included
    system "python3.11", "-m", "venv", libexec
    
    # Install ollama-coder with all dependencies
    system libexec/"bin/pip", "install", "--upgrade", "pip"
    system libexec/"bin/pip", "install", "ollama-coder==0.2.2"
    
    # Link the binary
    bin.install_symlink libexec/"bin/ollama-coder"
  end

  # Disable Homebrew's library relocation for this formula
  def post_install; end

  # Skip all library/binary analysis
  skip_clean :all

  test do
    assert_match "OllamaCoder", shell_output("#{bin}/ollama-coder --help")
  end
end
```

### Things We Tried

1. **Using virtualenv_install_with_resources with all deps listed** - Failed because pydantic-core requires Rust/maturin to build from source
2. **Using venv.pip_install** - Uses `--no-deps` so ollama wasn't installed
3. **Using system pip install** - Works but then dylib relocation fails
4. **skip_clean "libexec"** - Didn't prevent relocation
5. **skip_clean :all** - Still doesn't prevent relocation

### Potential Solutions to Try

1. **Disable relocation entirely** - Research Homebrew options like `bottle :unneeded` or `pour_bottle? only_if: :clt_installed` 

2. **Use pipx instead** - Create a formula that installs pipx and uses it:
   ```ruby
   def install
     system "pipx", "install", "ollama-coder==0.2.2"
   end
   ```

3. **Create a shell wrapper** - Instead of symlinking the venv binary, create a shell script:
   ```ruby
   def install
     system "python3.11", "-m", "venv", libexec
     system libexec/"bin/pip", "install", "ollama-coder==0.2.2"
     
     # Create wrapper script instead of symlink
     (bin/"ollama-coder").write <<~EOS
       #!/bin/bash
       exec "#{libexec}/bin/ollama-coder" "$@"
     EOS
   end
   ```

4. **Use bottle :unneeded** - Tell Homebrew not to process this formula:
   ```ruby
   bottle :unneeded
   ```

5. **Research other Python formulas with Rust deps** - Look at how `black`, `ruff`, or `uv` handle this in Homebrew

6. **Pin to older pydantic without Rust** - Use pydantic v1 which is pure Python (but would require code changes)

---

## 📁 Key Files

### Main Repository (`/Users/minibrain/Desktop/ollama-coder`)

| File | Purpose |
|------|---------|
| `ollama_coder/__init__.py` | Version: 0.2.2 |
| `ollama_coder/cli.py` | Main CLI (~2165 lines) - all features implemented |
| `pyproject.toml` | Build config, version 0.2.2 |
| `README.md` | Documentation (just updated) |

### Homebrew Tap (`/Users/minibrain/Desktop/homebrew-ollama-coder`)

| File | Purpose |
|------|---------|
| `Formula/ollama-coder.rb` | Homebrew formula (broken) |
| `README.md` | Installation instructions |

---

## 🎯 Goal

Get `brew install ollama-coder` working so users can:
```bash
brew tap lalomorales22/ollama-coder
brew install ollama-coder
ollama-coder
```

This avoids the `--break-system-packages` issue with pip on macOS.

---

## 🔗 Quick Links

- PyPI: https://pypi.org/project/ollama-coder/
- GitHub (main): https://github.com/lalomorales22/ollama-coder
- GitHub (tap): https://github.com/lalomorales22/homebrew-ollama-coder
- Homebrew Python formula docs: https://docs.brew.sh/Python-for-Formula-Authors

---

## 📝 Version History

| Version | Changes |
|---------|---------|
| 0.2.2 | Fix 401 unauthorized (filter empty headers) |
| 0.2.1 | Fix timeout for slow models (60s → 300s) |
| 0.2.0 | Streaming, vision, context management, ThinkTool, MultiEditTool |
| 0.1.3 | Module rename from ollama_code → ollama_coder |

---

## 🏁 Next Steps

1. Fix the Homebrew formula to avoid dylib relocation errors
2. Test on the second machine
3. Once working, commit the fix and push
4. Update both READMEs with working Homebrew instructions
