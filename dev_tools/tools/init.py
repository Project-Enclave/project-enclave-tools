"""Scaffold a small, installable Python project without replacing files."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple


class ProjectInitError(Exception):
    """An expected project scaffolding failure."""


PYTHON_VERSION_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?$")


def _names(name: str) -> Tuple[str, str]:
    distribution = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    if not distribution:
        raise ProjectInitError("Project name must contain letters or numbers.")
    module = distribution.replace("-", "_")
    if module[0].isdigit():
        module = "project_" + module
    return distribution, module


def init_project(
    directory: Optional[Path] = None,
    name: Optional[str] = None,
    python_version: Optional[str] = None,
) -> int:
    """Create a basic src-layout Python project, preserving existing files."""
    root = Path.cwd() if directory is None else Path(directory).expanduser()
    try:
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ProjectInitError(f"Could not create project directory: {exc}") from exc
    if not root.is_dir():
        raise ProjectInitError(f"Not a directory: {root}")

    project_name = name or root.name
    distribution, module = _names(project_name)
    pin = python_version or "{}.{}.{}".format(
        sys.version_info.major, sys.version_info.minor, sys.version_info.micro
    )
    if not PYTHON_VERSION_RE.fullmatch(pin):
        raise ProjectInitError("Python version must look like 3.12 or 3.12.3.")

    files: Dict[Path, str] = {
        Path(".gitignore"): (
            "__pycache__/\n*.py[cod]\n*.egg-info/\n.venv/\nbuild/\ndist/\n"
            ".env\n.env.*\n!.env.example\n"
        ),
        Path(".python-version"): pin + "\n",
        Path("README.md"): (
            f"# {project_name}\n\n"
            "A Python project.\n\n"
            "## Development\n\n"
            "```bash\n"
            "tools setup --editable\n"
            "tools check\n"
            "# Activate .venv (see below), then run:\n"
            f"python -m {module}\n"
            "```\n\n"
            "Activate the environment with `.venv\\Scripts\\Activate.ps1` in "
            "Windows PowerShell or `source .venv/bin/activate` on macOS/Linux.\n"
        ),
        Path("requirements.txt"): "# Add runtime dependencies here.\n",
        Path("requirements-dev.txt"): "ruff\nmypy\npytest\nbuild\ntwine\n",
        Path(".env.example"): "# Add required local settings as NAME=value entries.\n",
        Path("CHANGELOG.md"): (
            "# Changelog\n\n"
            "## [Unreleased]\n\n"
            "- Add release notes here before bumping the project version.\n\n"
            "## [0.1.0]\n\n"
            "- Initial project scaffold.\n"
        ),
        Path("pyproject.toml"): (
            "[build-system]\n"
            "requires = [\"setuptools>=61\"]\n"
            "build-backend = \"setuptools.build_meta\"\n\n"
            "[project]\n"
            f"name = \"{distribution}\"\n"
            "version = \"0.1.0\"\n"
            f"description = \"{distribution} project\"\n"
            "readme = \"README.md\"\n"
            "requires-python = \">=3.8\"\n"
            "dependencies = [\"tomli>=2; python_version < '3.11'\"]\n\n"
            "[project.optional-dependencies]\n"
            "release = [\"build>=1\", \"twine>=5\"]\n\n"
            "[tool.setuptools.packages.find]\n"
            "where = [\"src\"]\n"
            "\n[tool.project-enclave-tools]\n"
            "output = \"text\"\n"
            "environment-directory = \".venv\"\n"
            "requirements-files = [\"requirements.txt\", \"requirements-dev.txt\"]\n"
            "env-template = \".env.example\"\n"
            "local-config = \".env\"\n"
            "\n[tool.project-enclave-tools.doctor]\n"
            "minimum-python = \"3.8\"\n"
            "\n[tool.project-enclave-tools.setup]\n"
            "editable = false\n"
            "\n[tool.project-enclave-tools.assets]\n"
            "directory = \"assets\"\n"
            "max-bytes = 10485760\n"
            "formats = [\"png\", \"jpg\", \"jpeg\", \"gif\", \"webp\", \"svg\"]\n"
            "\n[tool.project-enclave-tools.sign]\n"
            "include = []\n"
            "exclude = []\n"
            "\n[tool.project-enclave-tools.release]\n"
            "directory = \"dist\"\n"
            "sign = false\n"
            "\n[tool.project-enclave-tools.changelog]\n"
            "to-ref = \"HEAD\"\n"
            "\n[tool.project-enclave-tools.aliases]\n"
            "ci = [\"check\"]\n"
            "doctor-json = [\"doctor\", \"--json\"]\n"
            "\n[tool.project-enclave-tools.check]\n"
            "lint = [\"ruff\", \"check\", \"src\"]\n"
            "types = [\"mypy\", \"src\"]\n"
            "tests = [\"pytest\"]\n"
        ),
        Path("src") / module / "__init__.py": f'"""{distribution} package."""\n',
        Path("src") / module / "__main__.py": (
            "def main():\n"
            f"    print({('Hello from ' + project_name + '!')!r})\n\n"
            "\n"
            "if __name__ == \"__main__\":\n"
            "    main()\n"
        ),
        Path("tests") / "test_package.py": (
            f"from {module} import __doc__\n\n\n"
            "def test_package_has_documentation():\n"
            "    assert __doc__\n"
        ),
    }

    created = []
    kept = []
    for relative_path, content in files.items():
        target = root / relative_path
        if target.exists():
            kept.append(relative_path)
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise ProjectInitError(f"Could not write {target}: {exc}") from exc
        created.append(relative_path)

    print(f"Project: {root}")
    for relative_path in created:
        print(f"[created] {relative_path}")
    for relative_path in kept:
        print(f"[kept] {relative_path} (already exists)")
    print("\nNext: tools setup --editable")
    return 0
