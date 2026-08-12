"""Command-line entry point.

With no prompt it launches the TUI; with `-p/--prompt` (or piped stdin) it runs
headless and exits with a meaningful status code.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from . import __version__

EPILOG = """
examples:
  ollama-coder                                  launch the TUI here
  ollama-coder --dir ~/code/app                 launch against another project
  ollama-coder -p "why does test_auth fail?"    one-shot, streams to stdout
  ollama-coder -p "fix the lint errors" --yolo  one-shot, no approval prompts
  git diff | ollama-coder -p "review this"      pipe context in
  ollama-coder -p "audit deps" --output json    machine-readable result
  ollama-coder --models                         list installed models

files:
  OLLAMA.md                     project instructions the agent must follow
  .ollamacode/settings.json     project configuration
  .ollamacode/mcp.json          MCP servers
  ~/.ollamacode/agents|commands|skills/   your extensions
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ollama-coder",
        description="OllamaCoder — an autonomous coding agent running on local Ollama models.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )
    parser.add_argument("prompt_positional", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("-p", "--prompt", help="run this prompt headlessly and exit")
    parser.add_argument("-m", "--model", help="model to use (default: configured, else first installed)")
    parser.add_argument("-d", "--dir", type=Path, default=Path.cwd(), help="project directory")
    parser.add_argument("-c", "--continue", dest="continue_session", action="store_true",
                        help="resume the most recent session for this project")
    parser.add_argument("-r", "--resume", metavar="ID", help="resume a specific session id")

    parser.add_argument("--yolo", action="store_true",
                        help="skip approval prompts (hard-blocked commands are still refused)")
    parser.add_argument("--read-only", action="store_true",
                        help="remove every tool that can modify anything, bash included "
                             "(re-enable one with --allow, e.g. --allow bash)")
    parser.add_argument("--allow", metavar="TOOL", action="append", default=[],
                        help="auto-approve a tool (repeatable)")
    parser.add_argument("--deny", metavar="TOOL", action="append", default=[],
                        help="forbid a tool entirely (repeatable)")
    parser.add_argument("--no-sandbox", action="store_true",
                        help="allow file tools outside the project directory")

    parser.add_argument("--output", choices=["text", "json"], default="text",
                        help="headless output format")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress tool chatter")
    parser.add_argument("--think", action="store_true", help="show the model's reasoning stream")
    parser.add_argument("--timeout", type=float, help="headless wall-clock limit in seconds")
    parser.add_argument("--max-steps", type=int, help="maximum model calls in one turn")
    parser.add_argument("--num-ctx", type=int, help="override the context window sent to Ollama")
    parser.add_argument("--host", help="Ollama host, e.g. http://192.168.1.10:11434")
    parser.add_argument("--no-mcp", action="store_true", help="skip MCP servers this run")

    parser.add_argument("--models", action="store_true", help="list installed models and exit")
    parser.add_argument("--doctor", action="store_true", help="check the environment and exit")
    parser.add_argument("--scaffold", action="store_true",
                        help="write example agent/command/skill files and exit")
    parser.add_argument("--import-skills", nargs="*", metavar="NAME",
                        help="import skills from Claude Code (all, or just the named ones)")
    parser.add_argument("-v", "--version", action="version", version=f"ollama-coder {__version__}")
    return parser


def overrides_from_args(args: argparse.Namespace) -> dict[str, Any]:
    overrides: dict[str, Any] = {}

    def put(path: str, value: Any) -> None:
        node = overrides
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    if args.model:
        put("model", args.model)
    if args.host:
        put("ollama.host", args.host)
    if args.yolo:
        put("permissions.yolo", True)
    if args.no_sandbox:
        put("sandbox.enabled", False)
    if args.max_steps:
        put("max_steps", args.max_steps)
    if args.num_ctx:
        put("num_ctx", args.num_ctx)
    if args.no_mcp:
        put("mcp.enabled", False)
    if args.think:
        put("ui.show_thinking", True)
    if args.deny:
        put("permissions.deny", list(args.deny))
    return overrides


async def _list_models(config: Any) -> int:
    from .core.llm import OllamaBackend

    backend = OllamaBackend(config)
    reachable, error = await backend.ping()
    if not reachable:
        print(error, file=sys.stderr)
        return 1
    models = await backend.list_models(refresh=True)
    if not models:
        print("No models installed. Try: ollama pull qwen3:8b")
        return 1
    current = config.get("model")
    width = max(len(m["name"]) for m in models)
    for model in models:
        info = await backend.info(model["name"])
        marks = "+".join(c for c in ("tools", "vision", "thinking") if c in info.capabilities)
        marker = "→" if model["name"] == current else " "
        print(f"{marker} {model['name']:<{width}}  {model['parameter_size']:>7}  "
              f"{info.context_length:>7,} ctx  {marks}")
    await backend.aclose()
    return 0


async def _doctor(config: Any) -> int:
    from .core.llm import OllamaBackend
    from .mcpx import MCPManager, load_server_specs

    ok = True
    print(f"ollama-coder {__version__}")
    print(f"python       {sys.version.split()[0]}")
    print(f"project      {config.project_dir}")

    backend = OllamaBackend(config)
    reachable, error = await backend.ping()
    if reachable:
        models = await backend.model_names()
        print(f"ollama       reachable · {len(models)} model(s)")
        model = config.get("model") or (models[0] if models else None)
        if model:
            info = await backend.info(model)
            effective = await backend.effective_num_ctx(model)
            print(f"model        {model} · {'+'.join(info.capabilities) or 'no capabilities'}")
            print(f"context      requesting {effective:,} of {info.context_length:,} advertised")
            if effective < info.context_length:
                print("             (raise with ollama.context_ceiling or --num-ctx; check")
                print("              `ollama ps` still says 100% GPU afterwards)")
            if not info.supports_tools:
                print("             ⚠ this model cannot call tools; pick another with --model")
                ok = False
    else:
        print(f"ollama       UNREACHABLE — {error}")
        ok = False

    context = "yes" if config.context.strip() else "none found"
    print(f"OLLAMA.md    {context}")

    specs = load_server_specs(config.project_dir, config)
    if specs:
        manager = MCPManager(config.project_dir, config)
        if not manager.available:
            print("mcp          configured but the `mcp` package is missing")
            ok = False
        else:
            for line in await manager.connect_all():
                print(f"mcp          {line}")
            await manager.aclose()
    else:
        print("mcp          no servers configured")

    for tool, label in (("rg", "ripgrep (faster grep)"), ("git", "git"), ("fd", "fd (optional)")):
        import shutil

        print(f"{tool:<12} {'found' if shutil.which(tool) else 'not installed — ' + label}")

    await backend.aclose()
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    project_dir = args.dir.expanduser()
    if not project_dir.is_dir():
        print(f"error: {project_dir} is not a directory", file=sys.stderr)
        return 1

    from .core.config import Config

    config = Config(project_dir=project_dir, overrides=overrides_from_args(args))

    for tool in args.allow:
        allow_list = config.get("permissions.auto_allow", []) or []
        if tool not in allow_list:
            config.set("permissions.auto_allow", allow_list + [tool])

    if args.models:
        return asyncio.run(_list_models(config))
    if args.doctor:
        return asyncio.run(_doctor(config))
    if args.import_skills is not None:
        from .core.extensions import import_claude_skills

        imported, skipped = import_claude_skills(only=args.import_skills or None)
        for name in imported:
            print(f"imported  {name}")
        for note in skipped:
            print(f"skipped   {note}")
        if imported:
            print(f"\n{len(imported)} skill(s) now in ~/.ollamacode/skills/")
            print("They load automatically when your message matches their keywords.")
            print("Edit keywords in <skill>/skill.yaml to tune when that happens.")
        elif not skipped:
            print("No Claude Code skills found in ~/.claude/skills or ~/.claude/plugins")
        return 0

    if args.scaffold:
        from .core.extensions import scaffold_examples

        created = scaffold_examples(project_dir)
        if created:
            for path in created:
                print(f"created {path}")
        else:
            print("example extensions already exist")
        return 0

    prompt = args.prompt or args.prompt_positional
    from .headless import read_stdin_context

    piped = read_stdin_context()
    if piped.strip():
        if prompt:
            prompt = f"{prompt}\n\n<piped-input>\n{piped.strip()}\n</piped-input>"
        else:
            prompt = f"Here is some input to work with:\n\n{piped.strip()}"

    if prompt:
        from .headless import HeadlessRunner

        if args.read_only:
            config.set("permissions.default", "allow")
        runner = HeadlessRunner(
            config,
            output_format=args.output,
            quiet=args.quiet,
            read_only=args.read_only,
            show_thinking=args.think,
        )
        try:
            return asyncio.run(runner.run(prompt, timeout=args.timeout))
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)
            return 2

    if not sys.stdout.isatty():
        print(
            "error: no prompt given and stdout is not a terminal.\n"
            "       use -p \"your prompt\" for non-interactive runs.",
            file=sys.stderr,
        )
        return 1

    from .tui.app import OllamaCoderApp

    resume = args.resume if args.resume else ("" if args.continue_session else None)
    app = OllamaCoderApp(config, resume=resume)
    app.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
