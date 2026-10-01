#!/usr/bin/env python3
"""Command line interface for dev-tools."""

import argparse
from pathlib import Path

from dev_tools.tools.signing import SigningError, init_keys, sign_files, verify_files


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
    sign_parser.add_argument(
        "-C",
        "--directory",
        type=Path,
        default=Path.cwd(),
        metavar="DIR",
        help="Directory to sign or verify (default: current directory)",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "sign":
            if args.init:
                init_keys()
            elif args.verify:
                verify_files(args.directory)
            else:
                sign_files(args.directory)
    except (SigningError, OSError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
