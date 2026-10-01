"""Create and verify signed SHA256 manifests for release files."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Dict, List, Optional

from dev_tools.utils.keys import (
    generate_keys,
    get_private_key,
    get_public_key,
    keys_exist,
)


class SigningError(Exception):
    """An expected signing or verification failure."""


# Directories to exclude from scanning.
EXCLUDE_DIRS = {
    ".git", ".venv", "venv", ".env", "__pycache__",
    ".mypy_cache", ".pytest_cache", "node_modules",
    "dist", "build", ".tox",
}

# File extensions to sign.
SIGN_EXTENSIONS = {".py", ".json", ".yaml", ".yml", ".toml", ".txt", ".md"}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def hash_file(path: Path) -> str:
    """Compute a SHA256 hash without loading the entire file into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_files(root: Path) -> List[Path]:
    """Find signable regular files, returning paths relative to ``root`` sorted."""
    root = Path(root)
    found: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            dirname
            for dirname in dirnames
            if dirname not in EXCLUDE_DIRS
            and not dirname.startswith(".")
            and not dirname.endswith(".egg-info")
            and not (Path(dirpath) / dirname).is_symlink()
        )

        for filename in sorted(filenames):
            if filename in ("manifest.json", "manifest.sig") or filename.startswith("."):
                continue
            candidate = Path(dirpath) / filename
            if (
                candidate.suffix.lower() in SIGN_EXTENSIONS
                and candidate.is_file()
                and not candidate.is_symlink()
            ):
                found.append(candidate)

    return sorted(found, key=lambda item: item.relative_to(root).as_posix())


def _run_openssl(args: List[str], operation: str) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise SigningError("OpenSSL is required but was not found on PATH.") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "OpenSSL failed."
        raise SigningError(f"{operation} failed: {detail}")
    return result


def sign_manifest(manifest_path: Path, sig_path: Path, private_key: Path) -> None:
    """Sign a manifest using the configured private key."""
    _run_openssl(
        [
            "openssl", "dgst", "-sha256", "-sign", str(private_key),
            "-out", str(sig_path), str(manifest_path),
        ],
        "Signing the manifest",
    )


def verify_manifest(manifest_path: Path, sig_path: Path, public_key: Path) -> bool:
    """Return whether the signature matches the manifest and public key."""
    try:
        result = subprocess.run(
            [
                "openssl", "dgst", "-sha256", "-verify", str(public_key),
                "-signature", str(sig_path), str(manifest_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SigningError("OpenSSL is required but was not found on PATH.") from exc
    return result.returncode == 0


def init_keys() -> None:
    """Initialize signing keys (first-time setup)."""
    try:
        generate_keys()
    except RuntimeError as exc:
        raise SigningError(str(exc)) from exc


def _release_directory(cwd: Optional[Path]) -> Path:
    directory = Path.cwd() if cwd is None else Path(cwd)
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        raise SigningError(f"Not a directory: {directory}")
    return directory


def sign_files(cwd: Optional[Path] = None) -> None:
    """Sign all signable files in a directory and write its manifest."""
    cwd = _release_directory(cwd)

    if not keys_exist():
        raise SigningError("Signing keys not found. Run 'tools sign --init' first.")

    private_key = get_private_key()
    public_key = get_public_key()
    print(f"\nScanning {cwd} for signable files...")
    files = discover_files(cwd)
    if not files:
        print("No signable files found.")
        return
    print(f"Found {len(files)} file(s) to sign.\n")

    manifest: Dict[str, str] = {}
    for file_path in files:
        relative_path = file_path.relative_to(cwd).as_posix()
        try:
            manifest[relative_path] = hash_file(file_path)
            print(f"  ✓ {relative_path}")
        except OSError as exc:
            raise SigningError(f"Could not read {relative_path}: {exc}") from exc

    manifest_path = cwd / "manifest.json"
    sig_path = cwd / "manifest.sig"

    # Stage both artifacts first so a failed signing operation leaves the old
    # release files untouched.
    with tempfile.TemporaryDirectory(prefix=".dev-tools-", dir=str(cwd)) as temp_dir:
        staged_manifest = Path(temp_dir) / "manifest.json"
        staged_signature = Path(temp_dir) / "manifest.sig"
        staged_manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        sign_manifest(staged_manifest, staged_signature, private_key)

        print("\nVerifying signature...")
        if not verify_manifest(staged_manifest, staged_signature, public_key):
            raise SigningError("Signature verification failed; release files were not updated.")

        os.replace(staged_manifest, manifest_path)
        os.replace(staged_signature, sig_path)

    print(f"✓ Wrote manifest.json ({len(manifest)} files)")
    print("✓ Signed manifest.sig")
    print("✓ Signature verified ✅\n")
    print("Release ready — commit manifest.json and manifest.sig")


def _manifest_file(root: Path, relative_path: str) -> Path:
    """Resolve a canonical manifest path and ensure it stays inside the release."""
    posix_path = PurePosixPath(relative_path)
    windows_path = PureWindowsPath(relative_path)
    if (
        not relative_path
        or "\\" in relative_path
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or posix_path.as_posix() != relative_path
        or not posix_path.parts
        or any(part in (".", "..") for part in posix_path.parts)
    ):
        raise SigningError(f"Invalid path in manifest: {relative_path!r}")

    candidate = root.joinpath(*posix_path.parts)
    resolved_root = root.resolve()
    try:
        resolved_candidate = candidate.resolve()
    except OSError as exc:
        raise SigningError(f"Could not resolve manifest path: {relative_path}") from exc
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise SigningError(f"Manifest path escapes the release directory: {relative_path}") from exc
    return candidate


def verify_files(cwd: Optional[Path] = None) -> None:
    """Verify the manifest signature and every signable file in a directory."""
    cwd = _release_directory(cwd)
    manifest_path = cwd / "manifest.json"
    sig_path = cwd / "manifest.sig"

    if not manifest_path.is_file() or not sig_path.is_file():
        raise SigningError(f"manifest.json or manifest.sig not found in {cwd}")

    public_key = get_public_key()
    if not public_key.is_file():
        raise SigningError("Public key not found. Run 'tools sign --init' first.")

    print(f"Verifying signatures in {cwd}...")
    if not verify_manifest(manifest_path, sig_path, public_key):
        raise SigningError("Manifest signature verification failed.")
    print("✓ Signature verified ✅")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SigningError(f"Could not read manifest.json: {exc}") from exc
    if not isinstance(manifest, dict):
        raise SigningError("manifest.json must contain an object mapping file paths to SHA256 hashes.")

    for file_path, expected_hash in manifest.items():
        if not isinstance(file_path, str) or not isinstance(expected_hash, str):
            raise SigningError("Manifest paths and hashes must be strings.")
        if not HASH_RE.fullmatch(expected_hash):
            raise SigningError(f"Invalid SHA256 hash in manifest for {file_path!r}.")

    print(f"\nChecking {len(manifest)} files...")
    all_match = True
    for relative_path, expected_hash in sorted(manifest.items()):
        file_path = _manifest_file(cwd, relative_path)
        if not file_path.is_file():
            print(f"  ✗ Missing: {relative_path}")
            all_match = False
            continue
        try:
            actual_hash = hash_file(file_path)
        except OSError as exc:
            print(f"  ✗ Could not read {relative_path}: {exc}")
            all_match = False
            continue
        if actual_hash == expected_hash:
            print(f"  ✓ {relative_path}")
        else:
            print(f"  ✗ MODIFIED: {relative_path}")
            all_match = False

    listed_paths = set(manifest)
    for file_path in discover_files(cwd):
        relative_path = file_path.relative_to(cwd).as_posix()
        if relative_path not in listed_paths:
            print(f"  ✗ UNLISTED: {relative_path}")
            all_match = False

    if not all_match:
        raise SigningError("Some files are missing, changed, or absent from the signed manifest.")
    print("\n✓ All files verified ✅")
