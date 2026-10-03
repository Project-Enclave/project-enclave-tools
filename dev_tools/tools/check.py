"""Run a project's configured lint, type, and test commands."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from dev_tools.utils.config import (
    load_project_config,
    path_setting,
    resolve_path,
    table,
)
from dev_tools.utils.config import (
    output_format as configured_output_format,
)


class CheckError(Exception):
    """An expected project check configuration failure."""


CHECK_NAMES = ("lint", "types", "tests")


def _read_commands(
    root: Path, project_config: Optional[Dict[str, Any]] = None
) -> Dict[str, List[str]]:
    project_config = load_project_config(root) if project_config is None else project_config
    check_config = table(project_config, "check")

    commands: Dict[str, List[str]] = {}
    for name in CHECK_NAMES:
        command = check_config.get(name, [])
        if not isinstance(command, list) or any(not isinstance(part, str) for part in command):
            raise CheckError(f"Check command '{name}' must be an array of strings.")
        if command and any(not part for part in command):
            raise CheckError(f"Check command '{name}' cannot contain empty arguments.")
        commands[name] = command
    unknown = sorted(set(check_config) - set(CHECK_NAMES))
    if unknown:
        raise CheckError("Unknown check command(s): " + ", ".join(unknown))
    if not any(commands.values()):
        raise CheckError(
            "No checks are configured. Add lint, types, or tests arrays under "
            "[tool.project-enclave-tools.check] in pyproject.toml."
        )
    return commands


def _venv_executable(
    root: Path, executable: str, environment_directory: Path = Path(".venv")
) -> str:
    environment = resolve_path(root, environment_directory)
    if os.name == "nt":
        candidate = environment / "Scripts" / executable
        if not candidate.suffix:
            candidate = candidate.with_suffix(".exe")
    else:
        candidate = environment / "bin" / executable
    return str(candidate) if candidate.is_file() else executable


def run_checks(
    directory: Optional[Path] = None,
    selected: Optional[Set[str]] = None,
    report_format: Optional[str] = None,
) -> int:
    """Run configured checks, using ``.venv`` executables when available."""
    root = Path.cwd() if directory is None else Path(directory).expanduser()
    try:
        root = root.resolve()
    except OSError as exc:
        raise CheckError(f"Could not resolve project directory: {exc}") from exc
    if not root.is_dir():
        raise CheckError(f"Not a project directory: {root}")

    report_format = configured_output_format(root, report_format)
    project_config = load_project_config(root)
    commands = _read_commands(root, project_config)
    environment_directory = path_setting(project_config, "environment-directory", ".venv")
    selected = set(CHECK_NAMES) if not selected else set(selected)
    unknown_selected = selected - set(CHECK_NAMES)
    if unknown_selected:
        raise CheckError("Unknown check category: " + ", ".join(sorted(unknown_selected)))

    failures = 0
    ran = 0
    results: List[Dict[str, Any]] = []
    for name in CHECK_NAMES:
        if name not in selected:
            continue
        command = commands[name]
        if not command:
            results.append({"name": name, "status": "skipped", "reason": "No command configured."})
            if report_format == "text":
                print(f"[SKIP] {name}: no command configured.")
            continue
        executable = _venv_executable(root, command[0], environment_directory)
        resolved_command = [executable, *command[1:]]
        if report_format == "text":
            print(f"\n[{name.upper()}] {' '.join(command)}")
        try:
            if report_format == "json":
                result = subprocess.run(
                    resolved_command,
                    cwd=str(root),
                    check=False,
                    stdout=sys.stderr,
                    stderr=sys.stderr,
                )
            else:
                result = subprocess.run(resolved_command, cwd=str(root), check=False)
        except OSError as exc:
            if report_format == "text":
                print(f"[FAIL] {name}: could not start command: {exc}")
            results.append(
                {
                    "name": name,
                    "executable": command[0],
                    "argument_count": len(command) - 1,
                    "status": "failed",
                    "error": str(exc),
                }
            )
            failures += 1
            continue
        ran += 1
        if result.returncode:
            if report_format == "text":
                print(f"[FAIL] {name}: exited with status {result.returncode}.")
            results.append(
                {
                    "name": name,
                    "executable": command[0],
                    "argument_count": len(command) - 1,
                    "status": "failed",
                    "returncode": result.returncode,
                }
            )
            failures += 1
        else:
            if report_format == "text":
                print(f"[OK] {name}")
            results.append(
                {
                    "name": name,
                    "executable": command[0],
                    "argument_count": len(command) - 1,
                    "status": "ok",
                    "returncode": 0,
                }
            )

    if ran == 0:
        raise CheckError("No selected checks are configured.")
    if report_format == "json":
        print(
            json.dumps(
                {
                    "command": "check",
                    "report_version": 1,
                    "project": str(root),
                    "status": "failed" if failures else "ok",
                    "results": results,
                    "summary": {"ran": ran, "failures": failures},
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(f"\n{failures} check problem(s).")
    return 1 if failures else 0
