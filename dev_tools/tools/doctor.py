"""Read-only environment checks for Python project checkouts."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import tomllib
except ImportError:  # Python 3.8–3.10
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]

from dev_tools.utils.config import (
    ConfigError,
    load_project_config,
    path_setting,
    resolve_path,
    string_list,
    table,
)
from dev_tools.utils.config import (
    output_format as configured_output_format,
)
from dev_tools.utils.env import (
    configured_env_names,
    is_configured_value,
    required_env_names,
)
from dev_tools.utils.keys import keys_exist

MIN_PROJECT_PYTHON = (3, 11)
REQUIREMENT_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
PYTHON_VERSION_RE = re.compile(r"Python\s+(\d+)\.(\d+)(?:\.(\d+))?")
PYTHON_REQUIREMENT_RE = re.compile(r"(?:>=|~=|==)\s*(\d+)\.(\d+)(?:\.\d+)?")


@dataclass
class Check:
    status: str
    name: str
    detail: str


def _python_version(executable: Path) -> Tuple[Optional[Tuple[int, int, int]], str]:
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)

    output = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        return None, output or "Python could not be started."
    match = PYTHON_VERSION_RE.search(output)
    if not match:
        return None, output or "Could not read the Python version."
    version = (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3) or 0),
    )
    return version, output


def _project_python(
    root: Path, environment_directory: Path = Path(".venv")
) -> Optional[Path]:
    environment = resolve_path(root, environment_directory)
    if os.name == "nt":
        candidate = environment / "Scripts" / "python.exe"
    else:
        candidate = environment / "bin" / "python"
    if candidate.is_file():
        return candidate

    # Also recognize an activated environment stored inside the project under
    # a different directory name.
    active_root = os.environ.get("VIRTUAL_ENV")
    if active_root:
        active_path = Path(active_root).expanduser().resolve()
        try:
            active_path.relative_to(root.resolve())
        except ValueError:
            return None
        executable = Path(sys.executable).resolve()
        return executable if executable.is_file() else None
    return None


def _read_requirements(path: Path) -> Tuple[List[str], int]:
    names: List[str] = []
    skipped = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = re.split(r"\s+#", raw_line, maxsplit=1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if ";" in line or line.startswith(("-", ".", "git+", "http:", "https:")):
            skipped += 1
            continue
        match = REQUIREMENT_NAME_RE.match(line)
        if not match:
            skipped += 1
            continue
        name = match.group(1)
        if name.lower().replace("_", "-") not in {
            current.lower().replace("_", "-") for current in names
        }:
            names.append(name)
    return names, skipped


def _installed_requirements(
    executable: Path, names: List[str]
) -> Tuple[Optional[Dict[str, Optional[str]]], str]:
    probe = (
        "import importlib.metadata as metadata, json, sys\n"
        "result = {}\n"
        "for name in sys.argv[1:]:\n"
        "    try:\n"
        "        result[name] = metadata.version(name)\n"
        "    except metadata.PackageNotFoundError:\n"
        "        result[name] = None\n"
        "print(json.dumps(result))\n"
    )
    try:
        result = subprocess.run(
            [str(executable), "-c", probe, "doctor", *names],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if result.returncode != 0:
        return None, (result.stderr or result.stdout).strip() or "Dependency check failed."
    try:
        return json.loads(result.stdout), ""
    except json.JSONDecodeError as exc:
        return None, f"Could not parse dependency check output: {exc}"


def _read_python_pin(path: Path) -> Optional[Tuple[int, ...]]:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    match = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?", value)
    if not match:
        return None
    return tuple(int(part) for part in match.groups() if part is not None)


def _project_minimum_python(
    root: Path, default: Tuple[int, int] = MIN_PROJECT_PYTHON
) -> Tuple[int, int]:
    pin = _read_python_pin(root / ".python-version")
    if pin:
        return pin[0], pin[1]

    try:
        with (root / "pyproject.toml").open("rb") as file_obj:
            project = tomllib.load(file_obj).get("project", {})
    except (OSError, ValueError):
        return default
    requires_python = project.get("requires-python") if isinstance(project, dict) else None
    if isinstance(requires_python, str):
        minimum = PYTHON_REQUIREMENT_RE.search(requires_python)
        if minimum:
            return int(minimum.group(1)), int(minimum.group(2))
    return default


def _project_dependencies(root: Path) -> Tuple[List[str], int, Optional[str]]:
    """Read required PEP 621 dependencies without evaluating secret or URL values."""
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return [], 0, None
    try:
        with pyproject.open("rb") as file_obj:
            project = tomllib.load(file_obj).get("project", {})
    except (OSError, ValueError) as exc:
        return [], 0, f"Could not read pyproject.toml: {exc}"
    if not isinstance(project, dict):
        return [], 0, "The [project] table in pyproject.toml is invalid."
    dependencies = project.get("dependencies", [])
    if not isinstance(dependencies, list) or any(not isinstance(item, str) for item in dependencies):
        return [], 0, "The project.dependencies value in pyproject.toml must be an array of strings."

    names: List[str] = []
    skipped = 0
    for dependency in dependencies:
        if ";" in dependency:
            skipped += 1
            continue
        match = REQUIREMENT_NAME_RE.match(dependency.strip())
        if not match:
            skipped += 1
            continue
        name = match.group(1)
        normalized = name.lower().replace("_", "-").replace(".", "-")
        if normalized not in {
            current.lower().replace("_", "-").replace(".", "-") for current in names
        }:
            names.append(name)
    return names, skipped, None


def _format_version(version: Tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def _tool_version(name: str, executable: Optional[str], arguments: List[str]) -> Check:
    if executable is None:
        details = {
            "Git": "Not found on PATH; repository and changelog commands need Git.",
            "OpenSSL": "Not found on PATH; signing commands need OpenSSL.",
            "Node.js": "Not found on PATH; this project has a package.json.",
            "npm": "Not found on PATH; this project has a package.json.",
        }
        return Check("WARN", name, details.get(name, "Not found on PATH."))
    try:
        result = subprocess.run(
            [executable, *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check("FAIL", name, f"Could not read version: {exc}")
    detail = (result.stdout or result.stderr).strip()
    if result.returncode:
        return Check("FAIL", name, detail or "Version command failed.")
    return Check("OK", name, f"{executable} ({detail or 'version unknown'})")


def doctor(directory: Optional[Path] = None, report_format: Optional[str] = None) -> int:
    """Report tool prerequisites and, when present, project venv health."""
    root = Path.cwd() if directory is None else Path(directory).expanduser()
    try:
        root = root.resolve()
    except OSError as exc:
        if report_format == "json":
            print(
                json.dumps(
                    {
                        "command": "doctor",
                        "report_version": 1,
                        "status": "error",
                        "error": f"Project directory: {exc}",
                    },
                    sort_keys=True,
                )
            )
        else:
            print(f"[FAIL] Project directory: {exc}")
        return 1
    if not root.is_dir():
        if report_format == "json":
            print(
                json.dumps(
                    {
                        "command": "doctor",
                        "report_version": 1,
                        "status": "error",
                        "error": f"Project directory: not a directory: {root}",
                    },
                    sort_keys=True,
                )
            )
        else:
            print(f"[FAIL] Project directory: not a directory: {root}")
        return 1

    config = load_project_config(root)
    report_format = configured_output_format(root, report_format)
    environment_directory = path_setting(config, "environment-directory", ".venv")
    requirements_files = string_list(
        config, "requirements-files", ["requirements.txt", "requirements-dev.txt"]
    )
    env_template = resolve_path(root, path_setting(config, "env-template", ".env.example"))
    local_config = resolve_path(root, path_setting(config, "local-config", ".env"))
    doctor_config = table(config, "doctor")
    minimum_python = doctor_config.get("minimum-python", "3.11")
    if not isinstance(minimum_python, str) or not re.fullmatch(r"\d+\.\d+", minimum_python):
        raise ConfigError("The 'minimum-python' setting must look like '3.11'.")
    minimum_major, minimum_minor = minimum_python.split(".")
    fallback_minimum = (int(minimum_major), int(minimum_minor))

    checks: List[Check] = []
    tool_version = tuple(sys.version_info[:3])
    if tool_version >= (3, 8, 0):
        checks.append(Check("OK", "Tools Python", _format_version(tool_version)))
    else:
        checks.append(Check("FAIL", "Tools Python", "Python 3.8 or newer is required."))

    checks.append(_tool_version("Git", shutil.which("git"), ["--version"]))
    checks.append(_tool_version("OpenSSL", shutil.which("openssl"), ["version"]))
    if (root / "package.json").is_file():
        checks.append(_tool_version("Node.js", shutil.which("node"), ["--version"]))
        checks.append(_tool_version("npm", shutil.which("npm"), ["--version"]))
    checks.append(
        Check("OK", "Signing keys", "Configured.")
        if keys_exist()
        else Check("INFO", "Signing keys", "Not configured; needed only for signing.")
    )

    if not env_template.is_file():
        checks.append(
            Check(
                "INFO",
                "Environment variables",
                f"No {env_template.name} template; skipped.",
            )
        )
    else:
        try:
            required_names = required_env_names(env_template)
            configured_names = set()
            env_file = local_config
            if env_file.is_file():
                configured_names.update(configured_env_names(env_file))
            configured_names.update(
                name
                for name in required_names
                if is_configured_value(os.environ.get(name, ""))
            )
            missing_names = sorted(required_names - configured_names)
        except (OSError, UnicodeDecodeError) as exc:
            checks.append(Check("FAIL", "Environment variables", f"Could not inspect config: {exc}"))
        else:
            if missing_names:
                checks.append(
                    Check(
                        "FAIL",
                        "Environment variables",
                        "Missing {}. Set these names in {} or the process environment; "
                        "values are never displayed.".format(
                            ", ".join(missing_names), local_config.name
                        ),
                    )
                )
            else:
                checks.append(
                    Check(
                        "OK",
                        "Environment variables",
                        f"All {len(required_names)} variables from {env_template.name} are configured.",
                    )
                )

    requirements_paths = [
        resolve_path(root, Path(name))
        for name in requirements_files
        if resolve_path(root, Path(name)).is_file()
    ]
    project_has_python_config = any(
        (root / name).is_file()
        for name in ("pyproject.toml", ".python-version", "requirements.txt", "requirements-dev.txt")
    )
    if not project_has_python_config:
        checks.append(Check("INFO", "Project environment", "No Python project files found; skipped."))
    else:
        project_python = _project_python(root, environment_directory)
        if project_python is None:
            detail = "{} Python not found. Prepare the environment with: tools setup".format(
                environment_directory
            )
            environment_root = resolve_path(root, environment_directory)
            if environment_root.exists():
                detail = "{} exists but its Python executable is missing; repair it or run tools setup.".format(
                    environment_directory
                )
            checks.append(
                Check(
                    "FAIL",
                    "Project environment",
                    detail,
                )
            )
        else:
            project_version, version_detail = _python_version(project_python)
            minimum_version = _project_minimum_python(root, fallback_minimum)
            if project_version is None:
                checks.append(Check("FAIL", "Project Python", version_detail))
            elif project_version[:2] < minimum_version:
                checks.append(
                    Check(
                        "FAIL",
                        "Project Python",
                        "Found {}; Python {}.{} or newer is required.".format(
                            _format_version(project_version), *minimum_version
                        ),
                    )
                )
            else:
                pin = _read_python_pin(root / ".python-version")
                detail = _format_version(project_version)
                if pin and project_version[: len(pin)] != pin:
                    checks.append(
                        Check(
                            "WARN",
                            "Project Python",
                            f"Found {detail}; .python-version recommends {_format_version(pin)}.",
                        )
                    )
                else:
                    checks.append(Check("OK", "Project Python", detail))

            requirement_names, skipped, metadata_error = _project_dependencies(root)
            if metadata_error:
                checks.append(Check("FAIL", "Project dependencies", metadata_error))
            requirements_read = True
            seen_names = set()
            for name in requirement_names:
                normalized = name.lower().replace("_", "-").replace(".", "-")
                if normalized not in seen_names:
                    seen_names.add(normalized)
            for requirements_path in requirements_paths:
                try:
                    names, skipped_count = _read_requirements(requirements_path)
                except (OSError, UnicodeDecodeError) as exc:
                    requirements_read = False
                    checks.append(
                        Check("FAIL", requirements_path.name, f"Could not read requirements: {exc}")
                    )
                    continue
                skipped += skipped_count
                for name in names:
                    normalized = name.lower().replace("_", "-").replace(".", "-")
                    if normalized not in seen_names:
                        seen_names.add(normalized)
                        requirement_names.append(name)

            if requirement_names and project_python is not None:
                installed, detail = _installed_requirements(project_python, requirement_names)
                if installed is None:
                    checks.append(Check("FAIL", "Project dependencies", detail))
                else:
                    missing = [name for name in requirement_names if not installed.get(name)]
                    if missing:
                        checks.append(
                            Check(
                                "FAIL",
                                "Project dependencies",
                                "Missing from .venv: " + ", ".join(missing),
                            )
                        )
                    else:
                        checks.append(
                            Check(
                                "OK",
                                "Project dependencies",
                                f"All {len(requirement_names)} listed packages are installed.",
                            )
                        )
            elif not requirement_names and requirements_read and not metadata_error:
                checks.append(
                    Check("INFO", "Project dependencies", "No package dependencies to check.")
                )

            if skipped:
                checks.append(
                    Check(
                        "WARN",
                        "Requirements syntax",
                        "Skipped {} option, URL, or marked requirement line(s) during "
                        "package checks.".format(skipped),
                    )
                )

            if project_python is not None:
                try:
                    consistency = subprocess.run(
                        [str(project_python), "-m", "pip", "check"],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=20,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    checks.append(Check("FAIL", "Dependency consistency", str(exc)))
                else:
                    if consistency.returncode == 0:
                        checks.append(Check("OK", "Dependency consistency", "pip check passed."))
                    else:
                        checks.append(
                            Check(
                                "FAIL",
                                "Dependency consistency",
                                (consistency.stdout or consistency.stderr).strip()
                                or "pip check failed.",
                            )
                        )

    failures = sum(check.status == "FAIL" for check in checks)
    warnings = sum(check.status == "WARN" for check in checks)
    if report_format == "json":
        print(
            json.dumps(
                {
                    "command": "doctor",
                    "report_version": 1,
                    "project": str(root),
                    "status": "failed" if failures else "ok",
                    "checks": [
                        {"status": check.status.lower(), "name": check.name, "detail": check.detail}
                        for check in checks
                    ],
                    "summary": {"checks": len(checks), "failures": failures, "warnings": warnings},
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(f"Project: {root}")
        for check in checks:
            print(f"[{check.status}] {check.name}: {check.detail}")
        print(f"\n{failures} problem(s), {warnings} warning(s).")
    return 1 if failures else 0
