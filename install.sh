#!/usr/bin/env bash
#
# OllamaCoder installer.
#
#   curl -fsSL https://raw.githubusercontent.com/lalomorales22/ollama-coder/main/install.sh | bash
#   ./install.sh                    # from a clone
#   ./install.sh --yes              # no questions asked
#   ./install.sh --uninstall
#
# Installs into its own virtualenv at ~/.ollamacode/venv and links the launcher
# into ~/.local/bin, so it never fights with your system Python or a PEP 668
# "externally managed environment". Safe to re-run: it upgrades in place.

set -euo pipefail

REPO_URL="https://github.com/lalomorales22/ollama-coder"
GIT_URL="https://github.com/lalomorales22/ollama-coder.git"
MIN_PY_MINOR=10                       # we require 3.10+
VENV_DIR="${OLLAMACODER_HOME:-$HOME/.ollamacode}/venv"
BIN_DIR="${OLLAMACODER_BIN:-$HOME/.local/bin}"
LAUNCHER="$BIN_DIR/ollama-coder"

ASSUME_YES=0
SOURCE=""                             # local | git | pypi  (auto-detected)
WANT_MODEL=""
SKIP_MODEL=0
DO_UNINSTALL=0
DEV_MODE=0
PIP_EXTRA_ARGS=""

# ---------------------------------------------------------------- output ----

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_RESET=$'\033[0m'; C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'
    C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'
    C_MAGENTA=$'\033[35m'
else
    C_RESET=""; C_DIM=""; C_BOLD=""; C_CYAN=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_MAGENTA=""
fi

step()  { printf '\n%s▸ %s%s\n' "$C_CYAN$C_BOLD" "$*" "$C_RESET"; }
info()  { printf '  %s\n' "$*"; }
ok()    { printf '  %s✓%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn()  { printf '  %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
die()   { printf '\n%s✗ %s%s\n\n' "$C_RED$C_BOLD" "$*" "$C_RESET" >&2; exit 1; }

banner() {
    printf '%s' "$C_CYAN"
    cat <<'ART'

 ▄▄▄▄▄▄▄  ▄▄     ▄▄      ▄▄▄▄   ▄▄▄   ▄▄▄  ▄▄▄▄
██     ██ ██     ██     ██  ██  ████ ████ ██  ██
██     ██ ██     ██     ██████  ██ ███ ██ ██████
 ▀█████▀  ██████ ██████ ██  ██  ██  ▀  ██ ██  ██
   ▄▄▄▄  ▄▄▄▄▄  ▄▄▄▄▄  ▄▄▄▄▄  ▄▄▄▄▄
  ██     ██  ██ ██  ██ ██     ██  ██
  ██     ██  ██ ██  ██ ████   ██████
   ▀████ ▀████▀ ██████ ██████ ██  ██
ART
    printf '%s' "$C_RESET"
    printf '%s  autonomous coding agent · 100%% local%s\n' "$C_DIM" "$C_RESET"
}

ask() {
    # ask "question" [default_yes]  -> 0 for yes, 1 for no
    local prompt="$1" default="${2:-y}" reply
    if [ "$ASSUME_YES" = 1 ]; then return 0; fi
    if [ ! -t 0 ]; then                       # piped from curl: no stdin to read
        [ "$default" = "y" ] && return 0 || return 1
    fi
    if [ "$default" = "y" ]; then
        printf '  %s?%s %s [Y/n] ' "$C_MAGENTA" "$C_RESET" "$prompt"
    else
        printf '  %s?%s %s [y/N] ' "$C_MAGENTA" "$C_RESET" "$prompt"
    fi
    read -r reply </dev/tty || reply=""
    reply="$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')"
    [ -z "$reply" ] && reply="$default"
    [ "$reply" = "y" ] || [ "$reply" = "yes" ]
}

usage() {
    cat <<EOF
${C_BOLD}OllamaCoder installer${C_RESET}

  ./install.sh [options]

Options:
  -y, --yes            accept every prompt (for CI / unattended installs)
      --from SOURCE    local | git | pypi   (default: local when run in a clone,
                                             otherwise git)
      --model NAME     pull this model instead of the suggested one
      --no-model       skip the model download entirely
      --dev            editable install, so the command tracks your edits
      --uninstall      remove the venv and launcher (your sessions are kept)
  -h, --help           this message

Environment:
  OLLAMACODER_HOME     install root, default ~/.ollamacode
  OLLAMACODER_BIN      where to link the launcher, default ~/.local/bin
EOF
}

# ------------------------------------------------------------------ args ----

while [ $# -gt 0 ]; do
    case "$1" in
        -y|--yes)     ASSUME_YES=1 ;;
        --from)       SOURCE="${2:-}"; shift ;;
        --from=*)     SOURCE="${1#*=}" ;;
        --model)      WANT_MODEL="${2:-}"; shift ;;
        --model=*)    WANT_MODEL="${1#*=}" ;;
        --no-model)   SKIP_MODEL=1 ;;
        --dev)        DEV_MODE=1 ;;
        --uninstall)  DO_UNINSTALL=1 ;;
        -h|--help)    usage; exit 0 ;;
        *)            die "unknown option: $1  (try --help)" ;;
    esac
    shift
done

# ------------------------------------------------------------- uninstall ----

if [ "$DO_UNINSTALL" = 1 ]; then
    banner
    step "Uninstalling"
    [ -e "$LAUNCHER" ] && { rm -f "$LAUNCHER"; ok "removed $LAUNCHER"; }
    if [ -d "$VENV_DIR" ]; then rm -rf "$VENV_DIR"; ok "removed $VENV_DIR"; fi
    info ""
    info "Your sessions, settings and extensions are still in"
    info "  ${OLLAMACODER_HOME:-$HOME/.ollamacode}"
    info "Delete that directory too if you want a clean slate."
    printf '\n'
    exit 0
fi

# ---------------------------------------------------------------- python ----

find_python() {
    local candidate version minor
    for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        version="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" || continue
        case "$version" in
            3.*) minor="${version#3.}" ;;
            *)   continue ;;
        esac
        if [ "$minor" -ge "$MIN_PY_MINOR" ] 2>/dev/null; then
            # a venv needs the stdlib venv module present (Debian splits it out)
            "$candidate" -c 'import venv' >/dev/null 2>&1 || continue
            printf '%s' "$candidate"
            return 0
        fi
    done
    return 1
}

python_help() {
    case "$(uname -s)" in
        Darwin) echo "  brew install python@3.12" ;;
        Linux)  echo "  sudo apt install python3 python3-venv    # Debian/Ubuntu"
                echo "  sudo dnf install python3                 # Fedora" ;;
        *)      echo "  https://www.python.org/downloads/" ;;
    esac
}

# ---------------------------------------------------------------- ollama ----

ollama_running() { curl -fsS --max-time 3 http://localhost:11434/api/version >/dev/null 2>&1; }

install_ollama() {
    case "$(uname -s)" in
        Linux)
            info "running the official Ollama installer…"
            curl -fsSL https://ollama.com/install.sh | sh
            ;;
        Darwin)
            if command -v brew >/dev/null 2>&1; then
                info "installing Ollama with Homebrew…"
                brew install --quiet ollama
            else
                warn "Ollama needs to be installed manually on macOS without Homebrew."
                info "Download it from https://ollama.com/download, then re-run this script."
                return 1
            fi
            ;;
        *)
            warn "unsupported platform for automatic install: $(uname -s)"
            info "Get Ollama from https://ollama.com/download"
            return 1
            ;;
    esac
}

start_ollama() {
    command -v ollama >/dev/null 2>&1 || return 1
    if [ "$(uname -s)" = "Darwin" ] && [ -d "/Applications/Ollama.app" ]; then
        open -ga Ollama 2>/dev/null || true
    else
        nohup ollama serve >/tmp/ollama-serve.log 2>&1 &
    fi
    local i
    for i in $(seq 1 20); do
        ollama_running && return 0
        sleep 1
    done
    return 1
}

suggest_model() {
    # pick by installed RAM; tool calling gets unreliable below ~4B
    local gb=0
    case "$(uname -s)" in
        Darwin) gb=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824 )) ;;
        Linux)  gb=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0) / 1048576 )) ;;
    esac
    if   [ "$gb" -ge 32 ]; then printf 'qwen3:14b'
    elif [ "$gb" -ge 16 ]; then printf 'qwen3:8b'
    else                        printf 'qwen3:4b'
    fi
}

has_tool_model() {
    # any installed model that can actually call tools
    "$VENV_DIR/bin/ollama-coder" --models 2>/dev/null | grep -q 'tools'
}

# ------------------------------------------------------------------ main ----

banner

# --- 1. python ---------------------------------------------------------------
step "Checking Python"
PYTHON="$(find_python)" || {
    printf '\n'
    warn "Python 3.$MIN_PY_MINOR or newer is required and none was found."
    python_help
    die "install Python, then re-run this script"
}
ok "$("$PYTHON" -V 2>&1) at $(command -v "$PYTHON")"

# --- 2. where are we installing from? ---------------------------------------
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [ -z "$SOURCE" ]; then
    if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
        SOURCE="local"
    else
        SOURCE="git"
    fi
fi

case "$SOURCE" in
    local)
        [ -f "$SCRIPT_DIR/pyproject.toml" ] || die "--from local needs to run inside a clone"
        if [ "$DEV_MODE" = 1 ]; then
            # editable: the installed command tracks your edits to the checkout
            PIP_EXTRA_ARGS="--editable"
            PACKAGE="$SCRIPT_DIR[all,dev]"
            SOURCE_LABEL="this checkout, editable ($SCRIPT_DIR)"
        else
            PACKAGE="$SCRIPT_DIR[all]"
            SOURCE_LABEL="this checkout ($SCRIPT_DIR)"
        fi
        ;;
    git)
        command -v git >/dev/null 2>&1 || die "git is required for --from git"
        PACKAGE="ollama-coder[all] @ git+$GIT_URL"
        SOURCE_LABEL="$REPO_URL (main)"
        ;;
    pypi)
        PACKAGE="ollama-coder[all]"
        SOURCE_LABEL="PyPI"
        ;;
    *)
        die "--from must be one of: local, git, pypi"
        ;;
esac

# --- 3. install --------------------------------------------------------------
step "Installing OllamaCoder"
info "source: $SOURCE_LABEL"
info "target: $VENV_DIR"

mkdir -p "$(dirname "$VENV_DIR")" "$BIN_DIR"

if [ -x "$VENV_DIR/bin/python" ]; then
    info "reusing the existing virtualenv"
else
    "$PYTHON" -m venv "$VENV_DIR" || die "could not create a virtualenv at $VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
if ! "$VENV_DIR/bin/python" -m pip install --quiet --upgrade $PIP_EXTRA_ARGS "$PACKAGE"; then
    printf '\n'
    die "installation failed — re-run with the output visible:
    $VENV_DIR/bin/python -m pip install --upgrade $PIP_EXTRA_ARGS '$PACKAGE'"
fi

VERSION="$("$VENV_DIR/bin/ollama-coder" --version 2>/dev/null | awk '{print $2}')"
[ -n "$VERSION" ] || die "installed, but the launcher did not run — please open an issue"
ok "ollama-coder $VERSION"

# --- 4. link -----------------------------------------------------------------
step "Linking the launcher"
ln -sf "$VENV_DIR/bin/ollama-coder" "$LAUNCHER"
ok "$LAUNCHER"

PATH_OK=0
case ":$PATH:" in *":$BIN_DIR:"*) PATH_OK=1 ;; esac
if [ "$PATH_OK" = 0 ]; then
    case "$(basename "${SHELL:-sh}")" in
        zsh)  RC="$HOME/.zshrc" ;;
        bash) [ -f "$HOME/.bash_profile" ] && RC="$HOME/.bash_profile" || RC="$HOME/.bashrc" ;;
        fish) RC="$HOME/.config/fish/config.fish" ;;
        *)    RC="" ;;
    esac
    LINE="export PATH=\"$BIN_DIR:\$PATH\""
    [ "$(basename "${SHELL:-sh}")" = "fish" ] && LINE="fish_add_path $BIN_DIR"

    warn "$BIN_DIR is not on your PATH"
    if [ -n "$RC" ] && [ -f "$RC" ] && grep -qF "$LINE" "$RC" 2>/dev/null; then
        # already added by a previous run; the shell just has not been reloaded
        ok "$(basename "$RC") already sets it — open a new terminal to pick it up"
    elif [ -n "$RC" ] && ask "add it to $(basename "$RC")?"; then
        mkdir -p "$(dirname "$RC")"
        printf '\n# added by the ollama-coder installer\n%s\n' "$LINE" >> "$RC"
        ok "updated $RC — run 'source $RC' or open a new terminal"
    else
        info "add this to your shell profile yourself:"
        printf '\n    %s\n' "$LINE"
    fi
fi

# --- 5. ollama ---------------------------------------------------------------
step "Checking Ollama"
if ! command -v ollama >/dev/null 2>&1; then
    warn "Ollama is not installed — it is what actually runs the models"
    if ask "install it now?"; then
        install_ollama || warn "automatic install did not complete"
    else
        info "get it later from https://ollama.com/download"
    fi
fi

if command -v ollama >/dev/null 2>&1; then
    if ollama_running; then
        ok "Ollama is running"
    else
        info "Ollama is installed but not running — starting it…"
        if start_ollama; then ok "Ollama is running"; else warn "could not start it; run 'ollama serve' in another terminal"; fi
    fi
fi

# --- 6. model ----------------------------------------------------------------
if [ "$SKIP_MODEL" = 0 ] && ollama_running; then
    step "Checking for a usable model"
    if [ -z "$WANT_MODEL" ] && has_tool_model; then
        ok "you already have a model that supports tool calling"
    else
        MODEL="${WANT_MODEL:-$(suggest_model)}"
        if [ -z "$WANT_MODEL" ]; then
            warn "no installed model supports tool calling"
            info "without one, the agent cannot read or edit files"
        fi
        info "suggested for this machine: ${C_BOLD}$MODEL${C_RESET}"
        if ask "download $MODEL now? (a few GB)"; then
            ollama pull "$MODEL" && ok "$MODEL is ready"
        else
            info "pull one later with:  ollama pull $MODEL"
        fi
    fi
fi

# --- 7. done -----------------------------------------------------------------
step "Verifying the install"
"$VENV_DIR/bin/ollama-coder" --doctor || true

cat <<EOF

${C_GREEN}${C_BOLD}Installed.${C_RESET}

  ${C_BOLD}cd${C_RESET} into a project and run ${C_CYAN}${C_BOLD}ollama-coder${C_RESET}

  ${C_DIM}ollama-coder${C_RESET}                        start the terminal UI
  ${C_DIM}ollama-coder -p "fix the tests"${C_RESET}      one-shot, prints to stdout
  ${C_DIM}ollama-coder --models${C_RESET}                which of your models can call tools
  ${C_DIM}ollama-coder --doctor${C_RESET}                re-check the environment

  Press ${C_BOLD}F1${C_RESET} inside the app for every command and key.
  Docs: $REPO_URL

EOF

if [ "$PATH_OK" = 0 ]; then
    warn "remember: open a new terminal (or source your shell profile) before 'ollama-coder' resolves"
    printf '\n'
fi
