"""Create and populate a project's local Python environment."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ImportError:  # Python 3.8–3.10
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]

from dev_tools.utils.config import (
    ConfigError,
    boolean_setting,
    load_project_config,
    path_setting,
    resolve_path,
    string_list,
    table,
)


class SetupError(Exception):
    """An expected project setup failure."""


def _project_python(root: Path, environment_directory: Path = Path(".venv")) -> Path:
    environment = resolve_path(root, environment_directory)
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def _resolve_python(command: Optional[str]) -> str:
    if command is None:
        return sys.executable
    candidate = Path(command).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    located = shutil.which(command)
    if located:
        return located
    raise SetupError(f"Python executable not found: {command}")


def _run(command: list[str], description: str, cwd: Path) -> None:
    try:
        result = subprocess.run(command, cwd=str(cwd), check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupError(f"{description}: {exc}") from exc
    if result.returncode:
        raise SetupError(f"{description} failed (exit code {result.returncode}).")


def _initialize_local_config(
    root: Path,
    template: Path = Path(".env.example"),
    local_config: Path = Path(".env"),
) -> None:
    template = resolve_path(root, template)
    local_config = resolve_path(root, local_config)
    if not template.is_file():
        print(f"No {template.name} template found; skipped local config.")
        return
    if local_config.exists():
        print(f"Keeping existing {local_config.name}; it was not overwritten.")
        return

    descriptor = None
    created = False
    try:
        descriptor = os.open(str(local_config), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        destination = os.fdopen(descriptor, "wb")
        descriptor = None
        with destination:
            with template.open("rb") as source:
                shutil.copyfileobj(source, destination)
    except FileExistsError:
        print(f"Keeping existing {local_config.name}; it was not overwritten.")
    except OSError as exc:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if created:
            try:
                local_config.unlink()
            except OSError:
                pass
        raise SetupError(f"Could not initialize local .env config: {exc}") from exc
    else:
        print(
            f"Created {local_config.name} from {template.name}; "
            "fill in local values before running the project."
        )


def _project_dependencies(root: Path) -> list[str]:
    """Return static runtime dependencies declared in pyproject.toml."""
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return []
    try:
        with pyproject.open("rb") as file_obj:
            project = tomllib.load(file_obj).get("project", {})
    except (OSError, ValueError) as exc:
        raise SetupError(f"Could not read project dependencies in {pyproject}: {exc}") from exc
    if not isinstance(project, dict):
        raise SetupError(f"The [project] table in {pyproject} is invalid.")
    dependencies = project.get("dependencies", [])
    if not isinstance(dependencies, list) or any(
        not isinstance(dependency, str) for dependency in dependencies
    ):
        raise SetupError(f"The project.dependencies value in {pyproject} must be an array of strings.")
    return dependencies


def setup_project(
    directory: Optional[Path] = None,
    python: Optional[str] = None,
    editable: Optional[bool] = None,
) -> int:
    """Create ``.venv`` if needed and install ``requirements.txt`` when present."""
    root = Path.cwd() if directory is None else Path(directory).expanduser()
    try:
        root = root.resolve()
    except OSError as exc:
        raise SetupError(f"Could not resolve project directory: {exc}") from exc
    if not root.is_dir():
        raise SetupError(f"Not a project directory: {root}")

    config = load_project_config(root)
    setup_config = table(config, "setup")
    environment_directory = path_setting(config, "environment-directory", ".venv")
    requirements_files = string_list(
        config, "requirements-files", ["requirements.txt", "requirements-dev.txt"]
    )
    env_template = path_setting(config, "env-template", ".env.example")
    local_config = path_setting(config, "local-config", ".env")
    environment_python = _project_python(root, environment_directory)
    python = python or setup_config.get("python")
    if python is not None:
        if not isinstance(python, str) or not python.strip():
            raise ConfigError("The 'python' setup setting must be an executable path or command name.")
        python_path = resolve_path(root, Path(python))
        if python_path.is_file():
            python = str(python_path)
    if editable is None:
        editable = boolean_setting(setup_config, "editable", False)

    environment_root = resolve_path(root, environment_directory)
    if environment_python.is_file():
        print(f"Using existing environment: {environment_python}")
    elif environment_root.exists():
        raise SetupError(
            f"Found {environment_root} but could not find its Python executable. "
            "Repair or remove that environment, then run setup again."
        )
    else:
        interpreter = _resolve_python(python)
        print(f"Creating environment with {interpreter}...")
        _run(
            [interpreter, "-m", "venv", str(environment_root)],
            "Creating the project environment",
            root,
        )
        if not environment_python.is_file():
            raise SetupError(f"Environment created, but Python was not found at {environment_python}.")

    _initialize_local_config(root, env_template, local_config)

    requirement_files = [
        resolve_path(root, Path(filename))
        for filename in requirements_files
        if resolve_path(root, Path(filename)).is_file()
    ]
    if requirement_files:
        for requirements in requirement_files:
            print(f"Installing {requirements.name}...")
            _run(
                [str(environment_python), "-m", "pip", "install", "-r", str(requirements)],
                f"Installing {requirements.name}",
                root,
            )
    else:
        print("No requirements files found.")

    project_dependencies = _project_dependencies(root)
    if project_dependencies and not editable:
        print(f"Installing {len(project_dependencies)} runtime dependency specification(s) from pyproject.toml...")
        _run(
            [str(environment_python), "-m", "pip", "install", *project_dependencies],
            "Installing project dependencies",
            root,
        )
    elif not requirement_files and not project_dependencies and not editable:
        print("No project dependencies found; skipped dependency installation.")

    if editable:
        if not (root / "pyproject.toml").is_file():
            raise SetupError("Cannot install the project itself: pyproject.toml was not found.")
        print("Installing the project in editable mode...")
        _run(
            [str(environment_python), "-m", "pip", "install", "--editable", str(root)],
            "Installing the project",
            root,
        )

    print(f"Project environment ready: {environment_python.parent.parent}")
    return 0
