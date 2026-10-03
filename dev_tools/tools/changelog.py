"""Generate Markdown release notes from Git commit subjects."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class ChangelogError(Exception):
    """An expected changelog generation failure."""


COMMIT_RE = re.compile(
    r"^(feat|fix|docs|test|perf|refactor|build|ci|chore|revert)"
    r"(?:\(([^)]*)\))?(!)?:\s*(.+)$",
    re.IGNORECASE,
)
CATEGORIES = (
    "Breaking Changes",
    "Features",
    "Fixes",
    "Performance",
    "Documentation",
    "Maintenance",
    "Other Changes",
)
TYPE_CATEGORY = {
    "feat": "Features",
    "fix": "Fixes",
    "perf": "Performance",
    "docs": "Documentation",
    "test": "Maintenance",
    "refactor": "Maintenance",
    "build": "Maintenance",
    "ci": "Maintenance",
    "chore": "Maintenance",
    "revert": "Other Changes",
}


def _git(root: Path, arguments: List[str]) -> str:
    if shutil.which("git") is None:
        raise ChangelogError("Git was not found on PATH.")
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
        raise ChangelogError(f"Git command failed: {exc}") from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "Git command failed."
        raise ChangelogError(detail)
    return result.stdout.strip()


def _category(subject: str) -> Tuple[str, str]:
    match = COMMIT_RE.match(subject)
    if not match:
        return "Other Changes", subject
    kind, scope, breaking, description = match.groups()
    if breaking:
        category = "Breaking Changes"
    else:
        category = TYPE_CATEGORY[kind.lower()]
    if scope:
        description = f"{scope}: {description}"
    return category, description


def generate_changelog(
    directory: Optional[Path] = None,
    from_ref: Optional[str] = None,
    to_ref: str = "HEAD",
) -> int:
    """Print Markdown notes for commits after a tag or an explicit Git ref."""
    root = Path.cwd() if directory is None else Path(directory).expanduser()
    try:
        root = root.resolve()
    except OSError as exc:
        raise ChangelogError(f"Could not resolve project directory: {exc}") from exc
    if not root.is_dir():
        raise ChangelogError(f"Not a directory: {root}")

    _git(root, ["rev-parse", "--show-toplevel"])
    start = from_ref
    if start is None:
        try:
            start = _git(root, ["describe", "--tags", "--abbrev=0"])
        except ChangelogError:
            # A repository without tags can still generate notes from its history.
            start = None
    revision_range = f"{start}..{to_ref}" if start else to_ref
    output = _git(root, ["log", "--no-merges", "--format=%s", revision_range])
    subjects = [line.strip() for line in output.splitlines() if line.strip()]
    grouped: Dict[str, List[str]] = {category: [] for category in CATEGORIES}
    for subject in subjects:
        category, description = _category(subject)
        grouped[category].append(description)

    print("# Changelog\n")
    print("## Unreleased\n")
    if not subjects:
        print("No commits found in the selected range.")
        return 0
    for category in CATEGORIES:
        entries = grouped[category]
        if not entries:
            continue
        print(f"### {category}\n")
        for entry in entries:
            print(f"- {entry}")
        print()
    return 0
