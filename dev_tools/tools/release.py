"""Validate release metadata, build distributions, and stage release notes."""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from dev_tools.tools.signing import SigningError, sign_files
from dev_tools.tools.version import VersionError, current_version
from dev_tools.utils.keys import keys_exist


class ReleaseError(Exception):
    """An expected release preparation failure."""


def _read_release_notes(root: Path, version: str) -> str:
    changelog = root / "CHANGELOG.md"
    try:
        lines = changelog.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ReleaseError(f"Could not read {changelog}: {exc}") from exc

    heading = re.compile(
        r"^##\s+(?:\[?v?" + re.escape(version) + r"\]?)(?:\s*(?:-|—).*)?\s*$"
    )
    start = None
    for index, line in enumerate(lines):
        if heading.match(line.strip()):
            start = index
            break
    if start is None:
        raise ReleaseError(
            f"CHANGELOG.md needs a '## [{version}]' section before release."
        )

    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    section = [line.rstrip() for line in lines[start + 1 : end]]
    if not any(re.match(r"^\s*(?:[-*+] |\d+\. )\S", line) for line in section):
        raise ReleaseError(f"The {version} section in CHANGELOG.md has no release notes.")
    return f"# Release {version}\n\n" + "\n".join(lines[start:end]).strip() + "\n"


def _run(command: List[str], cwd: Path, description: str) -> None:
    try:
        result = subprocess.run(command, cwd=str(cwd), check=False)
    except OSError as exc:
        raise ReleaseError(f"{description}: {exc}") from exc
    if result.returncode:
        raise ReleaseError(f"{description} failed (exit code {result.returncode}).")


def prepare_release(
    directory: Optional[Path] = None,
    sign_distributions: bool = False,
    output_directory: Optional[Path] = None,
) -> int:
    """Build and validate release files without publishing or tagging them."""
    root = Path.cwd() if directory is None else Path(directory).expanduser()
    try:
        root = root.resolve()
    except OSError as exc:
        raise ReleaseError(f"Could not resolve project directory: {exc}") from exc
    if not root.is_dir():
        raise ReleaseError(f"Not a project directory: {root}")

    try:
        version = current_version(root)
    except VersionError as exc:
        raise ReleaseError(str(exc)) from exc
    notes = _read_release_notes(root, version)

    missing_modules = [name for name in ("build", "twine") if importlib.util.find_spec(name) is None]
    if missing_modules:
        raise ReleaseError(
            "Install release tools in this Python environment first: "
            "python -m pip install build twine (missing: {}).".format(", ".join(missing_modules))
        )
    if sign_distributions and not keys_exist():
        raise ReleaseError("Signing keys not found. Run 'tools sign --init' first.")

    with tempfile.TemporaryDirectory(prefix="dev-tools-release-") as temporary:
        staging = Path(temporary)
        build_output = staging / "build"
        build_output.mkdir()
        _run(
            [
                sys.executable,
                "-m",
                "build",
                "--sdist",
                "--wheel",
                "--outdir",
                str(build_output),
                str(root),
            ],
            root,
            "Building distributions",
        )
        distributions = sorted(
            [*build_output.glob("*.whl"), *build_output.glob("*.tar.gz")],
            key=lambda path: path.name,
        )
        if not distributions:
            raise ReleaseError("The build completed without producing a wheel or source archive.")
        _run(
            [sys.executable, "-m", "twine", "check", *[str(path) for path in distributions]],
            root,
            "Checking distribution metadata",
        )

        notes_name = f"release-notes-{version}.md"
        prepared = staging / "prepared"
        prepared.mkdir()
        try:
            for source in distributions:
                shutil.copy2(source, prepared / source.name)
            (prepared / notes_name).write_text(notes, encoding="utf-8")
            if sign_distributions:
                sign_files(
                    prepared,
                    ["*.whl", "*.tar.gz", notes_name],
                )
        except (OSError, SigningError) as exc:
            raise ReleaseError(f"Could not prepare release files: {exc}") from exc

        release_files = sorted(
            (path for path in prepared.iterdir() if path.is_file()),
            key=lambda path: path.name,
        )
        output_setting = Path("dist") if output_directory is None else Path(output_directory).expanduser()
        output = output_setting if output_setting.is_absolute() else root / output_setting
        targets = [output / path.name for path in release_files]
        collisions = [path.name for path in targets if path.exists()]
        if collisions:
            raise ReleaseError(
                "Refusing to overwrite existing dist file(s): " + ", ".join(collisions)
            )
        try:
            output.mkdir(parents=True, exist_ok=True)
            for source in release_files:
                shutil.copy2(source, output / source.name)
        except OSError as exc:
            raise ReleaseError(f"Could not stage release files in {output}: {exc}") from exc

    print(f"Release {version} prepared in {output}:")
    for path in targets:
        print(f"  {path.name}")
    if sign_distributions:
        print("Release distributions and notes are signed in manifest.json.")
    print("Nothing was published or tagged.")
    return 0
