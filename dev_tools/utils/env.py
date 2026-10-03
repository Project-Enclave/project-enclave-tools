"""Inspect environment-file variable names without returning secret values."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Set

ENV_ASSIGNMENT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
PLACEHOLDERS = {"", "changeme", "change-me", "change_me", "todo", "example"}


def required_env_names(template: Path) -> Set[str]:
    """Return names declared in a checked-in ``.env.example`` template."""
    names = set()
    for line in template.read_text(encoding="utf-8").splitlines():
        match = ENV_ASSIGNMENT_RE.match(line)
        if match:
            names.add(match.group(1))
    return names


def configured_env_names(path: Path) -> Set[str]:
    """Return configured variable names without exposing their values."""
    configured = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = ENV_ASSIGNMENT_RE.match(line)
        if not match:
            continue
        name, raw_value = match.groups()
        if is_configured_value(raw_value):
            configured.add(name)
    return configured


def is_configured_value(raw_value: str) -> bool:
    """Check whether a value is non-empty and not an obvious template marker."""
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    normalized = value.casefold()
    if normalized in PLACEHOLDERS or normalized.startswith(
        ("your_", "your-", "replace_", "replace-")
    ):
        return False
    return bool(value)
