"""ASCII identity: the splash, the compact header mark, and the colour ramp.

Everything here is built from `rich.Text` with per-character styles so the
gradient survives resizing and both light and dark terminals.
"""

from __future__ import annotations

from rich.align import Align
from rich.console import Group, RenderableType
from rich.text import Text

# cyan -> electric blue -> violet -> magenta
RAMP: list[str] = [
    "#22d3ee", "#38bdf8", "#60a5fa", "#818cf8",
    "#a78bfa", "#c084fc", "#e879f9", "#f472b6",
]

LOGO = r"""
 ▄▄▄▄▄▄▄  ▄▄     ▄▄      ▄▄▄▄   ▄▄▄   ▄▄▄  ▄▄▄▄
██     ██ ██     ██     ██  ██  ████ ████ ██  ██
██     ██ ██     ██     ██████  ██ ███ ██ ██████
 ▀█████▀  ██████ ██████ ██  ██  ██  ▀  ██ ██  ██
   ▄▄▄▄  ▄▄▄▄▄  ▄▄▄▄▄  ▄▄▄▄▄  ▄▄▄▄▄
  ██     ██  ██ ██  ██ ██     ██  ██
  ██     ██  ██ ██  ██ ████   ██████
   ▀████ ▀████▀ ██████ ██████ ██  ██
"""

MARK = "◢◤ OLLAMACODER"

SCAN = "─" * 4 + "◆" + "─" * 4


def gradient(text: str, ramp: list[str] = RAMP, bold: bool = True) -> Text:
    """Colour a single line across the ramp, left to right."""
    result = Text()
    printable = [i for i, ch in enumerate(text) if ch != " "]
    if not printable:
        return Text(text)
    first, last = printable[0], printable[-1]
    span = max(1, last - first)
    for index, char in enumerate(text):
        position = min(1.0, max(0.0, (index - first) / span))
        colour = ramp[min(len(ramp) - 1, int(position * len(ramp)))]
        result.append(char, style=f"bold {colour}" if bold else colour)
    return result


def splash(version: str, model: str = "", workdir: str = "", extra: str = "") -> RenderableType:
    """Full-size startup art."""
    lines = [line for line in LOGO.strip("\n").splitlines()]
    width = max(len(line) for line in lines)

    body: list[RenderableType] = [Text("")]
    for row, line in enumerate(lines):
        # shift the ramp per row so the gradient reads as a diagonal sweep
        shifted = RAMP[row % len(RAMP) :] + RAMP[: row % len(RAMP)]
        body.append(Align.center(gradient(line.center(width), shifted)))

    tagline = Text()
    tagline.append("░▒▓ ", style="#22d3ee")
    tagline.append("autonomous coding agent", style="bold #e2e8f0")
    tagline.append(" · ", style="#475569")
    tagline.append("100% local", style="bold #4ade80")
    tagline.append(" · ", style="#475569")
    tagline.append(f"v{version}", style="#94a3b8")
    tagline.append(" ▓▒░", style="#f472b6")

    body.append(Text(""))
    body.append(Align.center(tagline))

    if model or workdir:
        meta = Text()
        if model:
            meta.append("model ", style="#475569")
            meta.append(model, style="bold #22d3ee")
        if model and workdir:
            meta.append("   ", style="")
        if workdir:
            meta.append("cwd ", style="#475569")
            meta.append(workdir, style="#a78bfa")
        body.append(Text(""))
        body.append(Align.center(meta))

    if extra:
        body.append(Text(""))
        body.append(Align.center(Text(extra, style="#64748b")))

    body.append(Text(""))
    return Group(*body)


def header_mark() -> Text:
    return gradient(MARK)


def rule(label: str = "", colour: str = "#1e293b") -> Text:
    line = Text()
    line.append("▁" * 2, style=colour)
    if label:
        line.append(f" {label} ", style="#64748b")
    return line


def context_gauge(used: int, window: int, width: int = 18) -> tuple[Text, float]:
    """A compact bar for the status line. Returns (renderable, fraction)."""
    if window <= 0:
        return Text("context ??", style="#475569"), 0.0
    fraction = min(1.0, used / window)
    filled = int(fraction * width)

    if fraction < 0.6:
        colour = "#4ade80"
    elif fraction < 0.85:
        colour = "#fbbf24"
    else:
        colour = "#f87171"

    bar = Text()
    bar.append("█" * filled, style=colour)
    bar.append("░" * (width - filled), style="#334155")
    bar.append(f" {fraction * 100:3.0f}%", style=colour)
    return bar, fraction


SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# Playful status verbs, cycled while the model is generating.
THINKING_WORDS = [
    "compiling thoughts", "reticulating splines", "reading the room",
    "consulting the weights", "spelunking", "untangling", "materialising",
    "chasing pointers", "warming the tensors", "pondering", "triangulating",
]
