"""Project fingerprinting: which libraries this project uses, at which versions.

This exists to solve a specific failure mode. A local model's training data is
frozen, so it writes `THREE.Geometry` (deleted in r125) or `new Vector3().sub()`
semantics from whatever era it learned. Telling it *"this project has three
0.180.0 installed"* is far more effective than any amount of prompt scolding:
it turns "recall the API" into "check the API", and gives it the exact version
string to search for.

Installed versions are preferred over declared ranges -- `^0.160.0` in
package.json tells you much less than the 0.180.0 actually sitting in
node_modules.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

# tomllib is stdlib from 3.11; tomli is the backport we depend on below that.
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

MAX_DEPS = 28
# Libraries whose public API churns enough that writing from memory is a bug.
FAST_MOVING = {
    "three", "react", "next", "vue", "svelte", "solid-js", "astro", "vite",
    "tailwindcss", "@tanstack/react-query", "zod", "drizzle-orm", "prisma",
    "expo", "react-native", "openai", "anthropic", "langchain", "pydantic",
    "fastapi", "sqlalchemy", "polars", "transformers", "torch", "numpy",
    "@react-three/fiber", "@react-three/drei", "gsap", "d3", "playwright",
}


@dataclass
class Dependency:
    name: str
    version: str
    ecosystem: str
    installed: bool = False   # True = read from the actual installed package

    def render(self) -> str:
        mark = "" if self.installed else " (declared)"
        return f"{self.name} {self.version}{mark}"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _node_dependencies(root: Path) -> list[Dependency]:
    manifest = root / "package.json"
    if not manifest.is_file():
        return []
    data = _read_json(manifest)

    declared: dict[str, str] = {}
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        block = data.get(section)
        if isinstance(block, dict):
            for name, spec in block.items():
                declared.setdefault(str(name), str(spec))

    found: list[Dependency] = []
    for name, spec in declared.items():
        installed = _read_json(root / "node_modules" / name / "package.json").get("version")
        if installed:
            found.append(Dependency(name, str(installed), "npm", installed=True))
        else:
            found.append(Dependency(name, spec.lstrip("^~"), "npm"))
    return found


def _python_dependencies(root: Path) -> list[Dependency]:
    declared: dict[str, str] = {}

    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and tomllib is not None:
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            data = {}
        for spec in data.get("project", {}).get("dependencies", []) or []:
            name, version = _split_requirement(str(spec))
            if name:
                declared.setdefault(name, version)
        poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
        if isinstance(poetry, dict):
            for name, spec in poetry.items():
                if str(name).lower() != "python":
                    declared.setdefault(str(name), str(spec) if isinstance(spec, str) else "")

    for filename in ("requirements.txt", "requirements/base.txt"):
        requirements = root / filename
        if requirements.is_file():
            try:
                for line in requirements.read_text(errors="replace").splitlines():
                    line = line.split("#")[0].strip()
                    if line and not line.startswith("-"):
                        name, version = _split_requirement(line)
                        if name:
                            declared.setdefault(name, version)
            except OSError:
                pass

    installed = _installed_python_versions(root)
    found: list[Dependency] = []
    for name, version in declared.items():
        actual = installed.get(name.lower().replace("_", "-"))
        if actual:
            found.append(Dependency(name, actual, "pypi", installed=True))
        else:
            found.append(Dependency(name, version or "unpinned", "pypi"))
    return found


def _installed_python_versions(root: Path) -> dict[str, str]:
    """Read versions out of a project-local virtualenv, if there is one."""
    versions: dict[str, str] = {}
    for venv in (".venv", "venv", "env"):
        site_packages = list((root / venv).glob("lib/python*/site-packages"))
        if not site_packages:
            continue
        for dist_info in site_packages[0].glob("*.dist-info"):
            stem = dist_info.name[: -len(".dist-info")]
            if "-" in stem:
                name, _, version = stem.rpartition("-")
                versions[name.lower().replace("_", "-")] = version
        break
    return versions


def _split_requirement(spec: str) -> tuple[str, str]:
    match = re.match(r"^\s*([A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*(.*)$", spec)
    if not match:
        return "", ""
    name = match.group(1)
    version = match.group(2).strip().lstrip("=<>~^!").split(",")[0].strip()
    return name, version


def _rust_dependencies(root: Path) -> list[Dependency]:
    manifest = root / "Cargo.toml"
    if not manifest.is_file() or tomllib is None:
        return []
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    found = []
    for name, spec in (data.get("dependencies") or {}).items():
        version = spec if isinstance(spec, str) else str(spec.get("version", ""))
        found.append(Dependency(str(name), version or "unpinned", "cargo"))
    return found


def _go_dependencies(root: Path) -> list[Dependency]:
    manifest = root / "go.mod"
    if not manifest.is_file():
        return []
    found = []
    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for match in re.finditer(r"^\s*([\w./-]+)\s+(v[\w.\-+]+)", text, re.M):
        module, version = match.groups()
        if module not in ("module", "go", "require"):
            found.append(Dependency(module.split("/")[-1], version, "go"))
    return found


def detect_dependencies(root: Path) -> list[Dependency]:
    """Direct dependencies of the project, installed versions where knowable."""
    found: list[Dependency] = []
    for detector in (_node_dependencies, _python_dependencies, _rust_dependencies, _go_dependencies):
        try:
            found.extend(detector(Path(root)))
        except Exception:
            continue

    # fast-moving and actually-installed packages first: those are the ones the
    # model is most likely to get wrong, and the ones a version helps most with
    def rank(dep: Dependency) -> tuple[int, int, str]:
        return (0 if dep.name in FAST_MOVING else 1, 0 if dep.installed else 1, dep.name)

    found.sort(key=rank)
    return found[:MAX_DEPS]


def describe_dependencies(root: Path) -> str:
    """The block that goes into the system prompt. Empty when nothing is found."""
    deps = detect_dependencies(root)
    if not deps:
        return ""

    volatile = [d for d in deps if d.name in FAST_MOVING]
    lines = [", ".join(d.render() for d in deps)]

    if volatile:
        names = ", ".join(f"{d.name} {d.version}" for d in volatile)
        lines.append(
            f"\nYour training data probably predates these: {names}. "
            "Do not write non-trivial code against them from memory. Check the "
            "installed source or typings under node_modules/ (or the site-packages "
            "equivalent), grep this project for existing usage, or search for that "
            "exact version's documentation. Guessing an API that was renamed or "
            "removed is the single most common way to waste the user's time."
        )
    return "\n".join(lines)
