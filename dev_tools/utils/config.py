"""Read project-specific Dev Tools settings from ``pyproject.toml``."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import tomllib
except ImportError:  # Python 3.8–3.10
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]


class ConfigError(Exception):
    """An invalid or unreadable Dev Tools configuration."""


def load_project_config(root: Path) -> Dict[str, Any]:
    """Return the ``tool.project-enclave-tools`` table, or an empty table."""
    pyproject = Path(root) / "pyproject.toml"
    if not pyproject.is_file():
        return {}
    try:
        with pyproject.open("rb") as file_obj:
            project = tomllib.load(file_obj)
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Could not read {pyproject}: {exc}") from exc

    tool = project.get("tool", {})
    if not isinstance(tool, dict):
        raise ConfigError("The [tool] value in pyproject.toml must be a TOML table.")
    config = tool.get("project-enclave-tools", {})
    if not isinstance(config, dict):
        raise ConfigError("[tool.project-enclave-tools] must be a TOML table.")
    return config


def table(config: Dict[str, Any], name: str) -> Dict[str, Any]:
    """Read a named configuration subtable with a consistent error."""
    value = config.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[tool.project-enclave-tools.{name}] must be a TOML table.")
    return value


def string_list(
    values: Dict[str, Any], key: str, default: Optional[List[str]] = None
) -> List[str]:
    """Read a TOML string array."""
    value = values.get(key, default or [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"The '{key}' setting must be an array of strings.")
    if any(not item.strip() for item in value):
        raise ConfigError(f"The '{key}' setting cannot contain empty strings.")
    return list(value)


def string_setting(
    values: Dict[str, Any], key: str, default: str
) -> str:
    """Read a non-empty string setting."""
    value = values.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"The '{key}' setting must be a non-empty string.")
    return value


def optional_string_setting(values: Dict[str, Any], key: str) -> Optional[str]:
    """Read an optional non-empty string setting."""
    value = values.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"The '{key}' setting must be a non-empty string when set.")
    return value


def path_setting(values: Dict[str, Any], key: str, default: str) -> Path:
    """Read a non-empty path value."""
    value = values.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"The '{key}' setting must be a non-empty path string.")
    return Path(value).expanduser()


def resolve_path(root: Path, value: Path) -> Path:
    """Resolve a path relative to a project unless it is already absolute."""
    expanded = Path(value).expanduser()
    return expanded if expanded.is_absolute() else Path(root) / expanded


def boolean_setting(values: Dict[str, Any], key: str, default: bool) -> bool:
    """Read a boolean setting."""
    value = values.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"The '{key}' setting must be true or false.")
    return value


def integer_setting(values: Dict[str, Any], key: str, default: int) -> int:
    """Read a positive integer setting."""
    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"The '{key}' setting must be a positive integer.")
    return value


def output_format(root: Path, override: Optional[str] = None) -> str:
    """Resolve the report format, with an explicit command-line value winning."""
    value = override if override is not None else load_project_config(root).get("output", "text")
    if not isinstance(value, str) or value not in ("text", "json"):
        raise ConfigError("The 'output' setting must be 'text' or 'json'.")
    return value
