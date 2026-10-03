#!/usr/bin/env python3
"""Command line interface for dev-tools."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Optional

from dev_tools.tools.assets import EXTENSION_FORMATS, AssetError, check_assets
from dev_tools.tools.changelog import ChangelogError, generate_changelog
from dev_tools.tools.check import CheckError, run_checks
from dev_tools.tools.doctor import doctor
from dev_tools.tools.init import ProjectInitError, init_project
from dev_tools.tools.release import ReleaseError, prepare_release
from dev_tools.tools.setup import SetupError, setup_project
from dev_tools.tools.signing import (
    SigningError,
    fingerprint_public_key,
    init_keys,
    sign_files,
    verify_files,
)
from dev_tools.tools.version import VersionError, change_version, current_version
from dev_tools.utils.config import (
    ConfigError,
    boolean_setting,
    integer_setting,
    load_project_config,
    optional_string_setting,
    output_format,
    path_setting,
    string_list,
    string_setting,
    table,
)

COMMAND_NAMES = {
    "sign",
    "doctor",
    "check",
    "setup",
    "init",
    "changelog",
    "version",
    "release",
    "assets",
}
ALIAS_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def _add_report_format(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--json", dest="report_format", action="store_const", const="json",
        help="Write a machine-readable JSON report to stdout",
    )
    group.add_argument(
        "--text", dest="report_format", action="store_const", const="text",
        help="Write the human-readable report (overrides the project config)",
    )


def _alias_config_root(arguments: List[str]) -> Path:
    """Find a project path passed to the alias's eventual command, if present."""
    for index, token in enumerate(arguments[1:], start=1):
        if token in ("-C", "--directory") and index + 1 < len(arguments):
            return Path(arguments[index + 1]).expanduser()
        if token.startswith("--directory="):
            return Path(token.split("=", 1)[1]).expanduser()
        if token.startswith("-C") and token != "-C":
            return Path(token[2:]).expanduser()
    return Path.cwd()


def expand_command_alias(arguments: List[str]) -> List[str]:
    """Expand one project-defined alias without invoking a shell."""
    if not arguments or arguments[0] in COMMAND_NAMES or arguments[0].startswith("-"):
        return list(arguments)

    aliases = table(load_project_config(_alias_config_root(arguments)), "aliases")
    if not aliases:
        return list(arguments)

    for name, expansion in aliases.items():
        if not isinstance(name, str) or not ALIAS_NAME_RE.fullmatch(name):
            raise ConfigError(f"Invalid command alias name: {name!r}.")
        if name in COMMAND_NAMES:
            raise ConfigError(f"Command alias '{name}' conflicts with a built-in command.")
        if (
            not isinstance(expansion, list)
            or not expansion
            or any(not isinstance(token, str) or not token.strip() for token in expansion)
        ):
            raise ConfigError(
                f"Command alias '{name}' must be a non-empty array of argument strings."
            )
        if expansion[0] not in COMMAND_NAMES:
            raise ConfigError(
                f"Command alias '{name}' must start with a built-in tools command."
            )

    alias = aliases.get(arguments[0])
    if alias is None:
        return list(arguments)
    return [*alias, *arguments[1:]]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Developer tools toolkit",
        prog="tools",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    sign_parser = commands.add_parser("sign", help="Sign or verify release files")
    action = sign_parser.add_mutually_exclusive_group()
    action.add_argument(
        "--init",
        action="store_true",
        help="Generate a signing key pair",
    )
    action.add_argument(
        "--verify",
        action="store_true",
        help="Verify the manifest and its files",
    )
    action.add_argument(
        "--fingerprint",
        action="store_true",
        help="Print the SHA256 fingerprint of a public key",
    )
    action.add_argument(
        "--dry-run",
        action="store_true",
        help="List the files that would be signed without writing files",
    )
    sign_parser.add_argument(
        "--public-key",
        type=Path,
        metavar="FILE",
        help="Public key for verification or fingerprinting",
    )
    sign_parser.add_argument(
        "--include",
        action="append",
        default=None,
        metavar="GLOB",
        help="Include files matching this pattern; may be repeated",
    )
    sign_parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        metavar="GLOB",
        help="Exclude files matching this pattern; may be repeated",
    )
    sign_parser.add_argument(
        "-C",
        "--directory",
        type=Path,
        default=Path.cwd(),
        metavar="DIR",
        help="Directory to sign or verify (default: current directory)",
    )
    doctor_parser = commands.add_parser(
        "doctor",
        help="Check tool prerequisites and a project's Python environment",
    )
    doctor_parser.add_argument(
        "-C",
        "--directory",
        type=Path,
        default=Path.cwd(),
        metavar="DIR",
        help="Project directory to check (default: current directory)",
    )
    _add_report_format(doctor_parser)
    check_parser = commands.add_parser(
        "check",
        help="Run configured lint, type-check, and test commands",
    )
    check_parser.add_argument(
        "-C",
        "--directory",
        type=Path,
        default=Path.cwd(),
        metavar="DIR",
        help="Project directory to check (default: current directory)",
    )
    _add_report_format(check_parser)
    check_parser.add_argument("--lint", action="store_true", help="Run only the lint command")
    check_parser.add_argument("--types", action="store_true", help="Run only the type-check command")
    check_parser.add_argument("--tests", action="store_true", help="Run only the test command")
    setup_parser = commands.add_parser(
        "setup",
        help="Prepare a project's .venv, dependencies, and local config",
    )
    setup_parser.add_argument(
        "-C",
        "--directory",
        type=Path,
        default=Path.cwd(),
        metavar="DIR",
        help="Project directory to set up (default: current directory)",
    )
    setup_parser.add_argument(
        "--python",
        metavar="EXECUTABLE",
        help="Python executable to create .venv with (default: this tool's Python)",
    )
    setup_mode = setup_parser.add_mutually_exclusive_group()
    setup_mode.add_argument(
        "--editable",
        dest="editable",
        action="store_true",
        help="Also install this project in editable mode (requires pyproject.toml)",
    )
    setup_mode.add_argument(
        "--no-editable",
        dest="editable",
        action="store_false",
        help="Do not install the project in editable mode",
    )
    setup_parser.set_defaults(editable=None)
    init_parser = commands.add_parser(
        "init",
        help="Scaffold a basic Python project without replacing existing files",
    )
    init_parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=Path.cwd(),
        metavar="DIR",
        help="Project directory to initialize (default: current directory)",
    )
    init_parser.add_argument("--name", help="Project name (default: directory name)")
    init_parser.add_argument(
        "--python-version",
        metavar="VERSION",
        help="Version to write to .python-version (default: current Python)",
    )
    changelog_parser = commands.add_parser(
        "changelog",
        help="Print grouped Markdown release notes from Git commits",
    )
    changelog_parser.add_argument(
        "-C",
        "--directory",
        type=Path,
        default=Path.cwd(),
        metavar="DIR",
        help="Git project directory (default: current directory)",
    )
    changelog_parser.add_argument(
        "--from",
        dest="from_ref",
        metavar="REF",
        help="Start after this tag or commit (default: nearest reachable tag)",
    )
    changelog_parser.add_argument(
        "--to",
        dest="to_ref",
        default=None,
        metavar="REF",
        help="End at this tag or commit (default: configured to-ref or HEAD)",
    )
    version_parser = commands.add_parser(
        "version",
        help="Show or update the static version in pyproject.toml",
    )
    version_action = version_parser.add_mutually_exclusive_group()
    version_action.add_argument(
        "--bump",
        choices=("major", "minor", "patch"),
        help="Increment a semantic version component",
    )
    version_action.add_argument(
        "--set",
        dest="set_version",
        metavar="VERSION",
        help="Set an explicit MAJOR.MINOR.PATCH version",
    )
    version_parser.add_argument(
        "-C",
        "--directory",
        type=Path,
        default=Path.cwd(),
        metavar="DIR",
        help="Project directory (default: current directory)",
    )
    release_parser = commands.add_parser(
        "release",
        help="Validate release notes and version, then build distributions",
    )
    release_parser.add_argument(
        "-C",
        "--directory",
        type=Path,
        default=Path.cwd(),
        metavar="DIR",
        help="Project directory to prepare (default: current directory)",
    )
    release_parser.add_argument(
        "--output",
        type=Path,
        metavar="DIR",
        help="Staging directory (default: configured directory or dist)",
    )
    release_sign = release_parser.add_mutually_exclusive_group()
    release_sign.add_argument(
        "--sign",
        dest="sign",
        action="store_true",
        help="Sign the staged distributions and release notes with the configured key",
    )
    release_sign.add_argument(
        "--no-sign",
        dest="sign",
        action="store_false",
        help="Do not sign the staged files (overrides the project config)",
    )
    release_parser.set_defaults(sign=None)
    assets_parser = commands.add_parser(
        "assets",
        help="Check image formats, dimensions, file sizes, and duplicates",
    )
    assets_parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=None,
        metavar="ASSET_DIR",
        help="Asset directory, relative to -C (default: assets)",
    )
    assets_parser.add_argument(
        "-C",
        "--directory",
        type=Path,
        default=Path.cwd(),
        metavar="DIR",
        help="Project directory (default: current directory)",
    )
    assets_parser.add_argument(
        "--max-bytes",
        type=int,
        default=None,
        metavar="N",
        help="Maximum allowed image file size (default: 10485760)",
    )
    assets_parser.add_argument(
        "--max-width",
        type=int,
        metavar="PIXELS",
        help="Maximum image width in pixels",
    )
    _add_report_format(assets_parser)
    assets_parser.add_argument(
        "--max-height",
        type=int,
        metavar="PIXELS",
        help="Maximum image height in pixels",
    )
    assets_parser.add_argument(
        "--formats",
        metavar="LIST",
        help="Comma-separated allowed formats (default: png,jpg,jpeg,gif,webp,svg)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        arguments = expand_command_alias(arguments)
    except ConfigError as exc:
        parser.exit(2, f"ERROR: {exc}\n")
    args = parser.parse_args(arguments)
    report_format = getattr(args, "report_format", None)

    try:
        if args.command == "sign":
            if args.public_key is not None and not (args.verify or args.fingerprint):
                parser.error("--public-key requires --verify or --fingerprint")
            if (args.include or args.exclude) and (args.init or args.fingerprint):
                parser.error("--include and --exclude apply only to signing, verification, or dry runs")
            if args.init:
                init_keys()
            elif args.fingerprint:
                print(fingerprint_public_key(args.public_key))
            elif args.verify:
                verify_files(
                    args.directory,
                    args.public_key,
                    args.include,
                    args.exclude,
                )
            elif args.dry_run:
                project_config = load_project_config(args.directory)
                sign_config = table(project_config, "sign")
                sign_files(
                    args.directory,
                    args.include if args.include is not None else string_list(sign_config, "include"),
                    args.exclude if args.exclude is not None else string_list(sign_config, "exclude"),
                    dry_run=True,
                )
            else:
                project_config = load_project_config(args.directory)
                sign_config = table(project_config, "sign")
                sign_files(
                    args.directory,
                    args.include if args.include is not None else string_list(sign_config, "include"),
                    args.exclude if args.exclude is not None else string_list(sign_config, "exclude"),
                )
        elif args.command == "doctor":
            report_format = output_format(args.directory, report_format)
            return doctor(args.directory, report_format)
        elif args.command == "check":
            selected = {
                name
                for name, enabled in (
                    ("lint", args.lint),
                    ("types", args.types),
                    ("tests", args.tests),
                )
                if enabled
            }
            report_format = output_format(args.directory, report_format)
            return run_checks(args.directory, selected or None, report_format)
        elif args.command == "setup":
            return setup_project(args.directory, args.python, args.editable)
        elif args.command == "init":
            return init_project(args.directory, args.name, args.python_version)
        elif args.command == "changelog":
            changelog_config = table(
                load_project_config(args.directory), "changelog"
            )
            from_ref = (
                args.from_ref
                if args.from_ref is not None
                else optional_string_setting(changelog_config, "from-ref")
            )
            to_ref = (
                args.to_ref
                if args.to_ref is not None
                else string_setting(changelog_config, "to-ref", "HEAD")
            )
            return generate_changelog(args.directory, from_ref, to_ref)
        elif args.command == "release":
            project_config = load_project_config(args.directory)
            release_config = table(project_config, "release")
            sign_release = (
                args.sign
                if args.sign is not None
                else boolean_setting(release_config, "sign", False)
            )
            output_directory = (
                args.output
                if args.output is not None
                else path_setting(release_config, "directory", "dist")
            )
            return prepare_release(args.directory, sign_release, output_directory)
        elif args.command == "assets":
            project_config = load_project_config(args.directory)
            report_format = output_format(args.directory, report_format)
            assets_config = table(project_config, "assets")
            configured_directory = path_setting(assets_config, "directory", "assets")
            selected_directory = args.path if args.path is not None else configured_directory
            asset_dir = (
                selected_directory
                if selected_directory.is_absolute()
                else args.directory / selected_directory
            )
            max_bytes = (
                args.max_bytes
                if args.max_bytes is not None
                else integer_setting(assets_config, "max-bytes", 10 * 1024 * 1024)
            )
            max_width = args.max_width if args.max_width is not None else assets_config.get("max-width")
            max_height = args.max_height if args.max_height is not None else assets_config.get("max-height")
            for key, value in (("max-width", max_width), ("max-height", max_height)):
                if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
                    raise ConfigError(f"The '{key}' setting must be a positive integer.")
            configured_formats = string_list(
                assets_config, "formats", ["png", "jpg", "jpeg", "gif", "webp", "svg"]
            )
            formats_value = args.formats.split(",") if args.formats else configured_formats
            allowed_formats = None
            if formats_value:
                allowed_formats = set()
                for value in formats_value:
                    extension = "." + value.strip().lower().lstrip(".")
                    if extension not in EXTENSION_FORMATS:
                        if args.formats:
                            parser.error(f"Unknown image format: {value}")
                        raise ConfigError(f"Unknown configured image format: {value}")
                    allowed_formats.add(EXTENSION_FORMATS[extension])
            return check_assets(
                asset_dir,
                max_bytes,
                allowed_formats,
                max_width,
                max_height,
                report_format,
            )
        elif args.command == "version":
            if args.bump or args.set_version:
                return change_version(args.directory, args.bump, args.set_version)
            print(current_version(args.directory))
    except (
        SigningError,
        SetupError,
        CheckError,
        AssetError,
        ReleaseError,
        ProjectInitError,
        ChangelogError,
        VersionError,
        OSError,
        ConfigError,
    ) as exc:
        if args.command in ("doctor", "check", "assets") and report_format == "json":
            print(
                json.dumps(
                    {
                        "command": args.command,
                        "report_version": 1,
                        "status": "error",
                        "error": str(exc),
                    },
                    sort_keys=True,
                )
            )
            return 1
        parser.exit(1, f"ERROR: {exc}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
