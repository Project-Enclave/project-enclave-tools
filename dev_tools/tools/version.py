"""Read or update a static project version in ``pyproject.toml``."""

from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Match, Optional, Tuple


class VersionError(Exception):
    """An expected project version operation failure."""


VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
PROJECT_SECTION_RE = re.compile(r"^\s*\[([^]]+)\]\s*(?:#.*)?$")
VERSION_LINE_RE = re.compile(
    r"^(\s*version\s*=\s*)(['\"])([^'\"]+)(\2\s*(?:#.*)?)$"
)


def _version_line(path: Path) -> Tuple[str, int, Match[str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as file_obj:
            lines = file_obj.readlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise VersionError(f"Could not read {path}: {exc}") from exc

    in_project = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        section = PROJECT_SECTION_RE.match(stripped)
        if section:
            in_project = section.group(1) == "project"
            continue
        if in_project:
            match = VERSION_LINE_RE.match(line.rstrip("\r\n"))
            if match:
                return "".join(lines), index, match
    raise VersionError(f"No static version was found in the [project] section of {path}.")


def _pyproject(directory: Optional[Path]) -> Path:
    root = Path.cwd() if directory is None else Path(directory).expanduser()
    try:
        root = root.resolve()
    except OSError as exc:
        raise VersionError(f"Could not resolve project directory: {exc}") from exc
    if not root.is_dir():
        raise VersionError(f"Not a project directory: {root}")
    return root / "pyproject.toml"


def current_version(directory: Optional[Path] = None) -> str:
    """Return the project's static ``[project].version`` value."""
    path = _pyproject(directory)
    content, _, match = _version_line(path)
    del content
    version = match.group(3)
    if not VERSION_RE.fullmatch(version):
        raise VersionError(f"Unsupported version format {version!r}; expected MAJOR.MINOR.PATCH.")
    return version


def _bumped(version: str, part: str) -> str:
    major, minor, patch = (int(component) for component in version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _write_version(path: Path, content: str, line_index: int, match: Match[str], version: str) -> None:
    lines = content.splitlines(keepends=True)
    line = lines[line_index]
    ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
    replacement = f"{match.group(1)}{match.group(2)}{version}{match.group(4)}{ending}"
    lines[line_index] = replacement

    try:
        original_mode = stat.S_IMODE(path.stat().st_mode)
        file_descriptor, temporary_name = tempfile.mkstemp(prefix=".pyproject-", dir=str(path.parent))
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="") as file_obj:
                file_obj.write("".join(lines))
            os.chmod(temporary_name, original_mode)
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
    except OSError as exc:
        raise VersionError(f"Could not update {path}: {exc}") from exc


def change_version(
    directory: Optional[Path] = None,
    bump: Optional[str] = None,
    set_to: Optional[str] = None,
) -> int:
    """Bump a semver component or set a static project version."""
    path = _pyproject(directory)
    content, line_index, match = _version_line(path)
    current = match.group(3)
    if not VERSION_RE.fullmatch(current):
        raise VersionError(f"Unsupported version format {current!r}; expected MAJOR.MINOR.PATCH.")
    if set_to is not None:
        if not VERSION_RE.fullmatch(set_to):
            raise VersionError("Version must use MAJOR.MINOR.PATCH, for example 1.2.3.")
        updated = set_to
    elif bump in ("major", "minor", "patch"):
        updated = _bumped(current, bump)
    else:
        raise VersionError("Choose a bump type (major, minor, patch) or a version to set.")

    if updated == current:
        print(f"Version is already {current}.")
        return 0
    _write_version(path, content, line_index, match, updated)
    print(f"Updated project version: {current} -> {updated}")
    return 0
