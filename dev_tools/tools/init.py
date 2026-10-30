"""Scaffold a small, installable Python project without replacing files."""

from __future__ import annotations

import re
import shutil
import subprocess
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
    """Create a src-layout project, preserve existing files, and initialize Git."""
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
            "__pycache__/\n*.py[cod]\n*.egg-info/\n.venv/\nvenv/\nenv/\n"
            ".pytest_cache/\n.mypy_cache/\n.ruff_cache/\n.coverage\nhtmlcov/\n"
            "build/\ndist/\n.env\n.env.*\n!.env.example\n.DS_Store\nThumbs.db\n"
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
        Path("requirements-dev.txt"): "ruff\nmypy\npytest\nbuild\ntwine\nbandit\n",
        Path(".env.example"): "# Add required local settings as NAME=value entries.\n",
        Path(".github") / "workflows" / "ci.yml": """name: CI and release

on:
  push:
    branches: [main]
    tags: ['v*']
  pull_request:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version-file: .python-version
          cache: pip
          cache-dependency-path: requirements-dev.txt
      - name: Install dependencies
        run: python -m pip install -r requirements.txt -r requirements-dev.txt
      - name: Install project
        run: python -m pip install --editable .
      - name: Lint
        run: ruff check src tests
      - name: Type check
        run: mypy src
      - name: Security scan
        run: bandit -r src
      - name: Test
        run: pytest

  build:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version-file: .python-version
          cache: pip
          cache-dependency-path: requirements-dev.txt
      - name: Install build tools
        run: python -m pip install -r requirements-dev.txt
      - name: Build and validate distributions
        run: |
          python -m build
          python -m twine check dist/*
      - uses: actions/upload-artifact@v4
        with:
          name: python-distributions
          path: dist/

  release:
    if: startsWith(github.ref, 'refs/tags/v')
    needs: build
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: python-distributions
          path: dist/
      - name: Create GitHub release
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh release create "$GITHUB_REF_NAME" dist/* --generate-notes
""",
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
    _initialize_git(root)
    print("\nNext: tools setup --editable")
    return 0


def _initialize_git(root: Path) -> None:
    """Initialize a main-branch repository without staging or committing files."""
    git = shutil.which("git")
    if git is None:
        raise ProjectInitError("Git was not found. Install Git, then run tools init again.")

    if (root / ".git").exists():
        print("[kept] .git (already exists)")
        return

    result = subprocess.run(
        [git, "-C", str(root), "init", "--initial-branch=main"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # Older Git versions do not support --initial-branch.
        result = subprocess.run(
            [git, "-C", str(root), "init"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            result = subprocess.run(
                [git, "-C", str(root), "symbolic-ref", "HEAD", "refs/heads/main"],
                capture_output=True,
                text=True,
                check=False,
            )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ProjectInitError(f"Could not initialize Git repository: {detail}")
    print("[created] .git (branch: main)")


def create_github_repository(
    directory: Path, name: Optional[str] = None, visibility: str = "private"
) -> int:
    """Create a GitHub repository and origin with gh, leaving commits to the user."""
    root = Path(directory).expanduser().resolve()
    if visibility not in ("private", "public"):
        raise ProjectInitError("GitHub visibility must be 'private' or 'public'.")

    gh = shutil.which("gh")
    if gh is None:
        print("[skipped] GitHub setup (install and authenticate gh to create a remote)")
        return 0

    auth = subprocess.run(
        [gh, "auth", "status"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if auth.returncode != 0:
        print("[skipped] GitHub setup (run 'gh auth login', then rerun tools init)")
        return 0

    git = shutil.which("git")
    if git is None:
        raise ProjectInitError("Git was not found. Install Git before creating a GitHub repo.")
    origin = subprocess.run(
        [git, "-C", str(root), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    if origin.returncode == 0:
        print(f"[kept] origin ({origin.stdout.strip()})")
        return 0

    project_name = name or root.name
    distribution, _ = _names(project_name)
    result = subprocess.run(
        [
            gh,
            "repo",
            "create",
            distribution,
            f"--{visibility}",
            "--source",
            str(root),
            "--remote",
            "origin",
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ProjectInitError(f"Could not create GitHub repository: {detail}")

    output = result.stdout.strip()
    print(output or f"[created] GitHub repository {distribution} ({visibility})")
    print("No files were committed or pushed. Review the project, then commit and push it.")
    return 0
