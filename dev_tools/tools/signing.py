"""Create and verify signed SHA256 manifests for release files."""

from __future__ import annotations

import fnmatch
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
MANIFEST_FORMAT = "project-enclave-tools-manifest"
MANIFEST_VERSION = 2


def hash_file(path: Path) -> str:
    """Compute a SHA256 hash without loading the entire file into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _matches_pattern(relative_path: str, patterns: List[str]) -> bool:
    normalized_path = relative_path.replace("\\", "/")
    for pattern in patterns:
        normalized_pattern = pattern.replace("\\", "/")
        if fnmatch.fnmatchcase(normalized_path, normalized_pattern):
            return True
        if normalized_pattern.startswith("**/") and fnmatch.fnmatchcase(
            normalized_path, normalized_pattern[3:]
        ):
            return True
    return False


def _normalize_patterns(patterns: Optional[List[str]]) -> List[str]:
    normalized = []
    for pattern in patterns or []:
        if not pattern.strip():
            raise SigningError("Include and exclude patterns cannot be empty.")
        normalized.append(pattern.replace("\\", "/"))
    return sorted(set(normalized))


def discover_files(
    root: Path,
    include_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
) -> List[Path]:
    """Find signable regular files, returning paths relative to ``root`` sorted."""
    root = Path(root)
    include_patterns = include_patterns or []
    exclude_patterns = exclude_patterns or []
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
            if not candidate.is_file() or candidate.is_symlink():
                continue
            relative_path = candidate.relative_to(root).as_posix()
            if include_patterns:
                included = _matches_pattern(relative_path, include_patterns)
            else:
                included = candidate.suffix.lower() in SIGN_EXTENSIONS
            if included and not _matches_pattern(relative_path, exclude_patterns):
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


def fingerprint_public_key(public_key: Optional[Path] = None) -> str:
    """Return a SHA256 fingerprint of a PEM public key's DER encoding."""
    path = get_public_key() if public_key is None else Path(public_key).expanduser()
    if not path.is_file():
        raise SigningError(f"Public key not found: {path}")
    try:
        result = subprocess.run(
            ["openssl", "pkey", "-pubin", "-in", str(path), "-outform", "DER"],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SigningError("OpenSSL is required but was not found on PATH.") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise SigningError(f"Could not read public key: {detail or 'OpenSSL failed.'}")
    fingerprint = hashlib.sha256(result.stdout).hexdigest().upper()
    return "SHA256:" + ":".join(
        fingerprint[index : index + 2] for index in range(0, len(fingerprint), 2)
    )


def _release_directory(cwd: Optional[Path]) -> Path:
    directory = Path.cwd() if cwd is None else Path(cwd)
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        raise SigningError(f"Not a directory: {directory}")
    return directory


def sign_files(
    cwd: Optional[Path] = None,
    include_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
    dry_run: bool = False,
) -> None:
    """Sign selected files or preview the manifest without writing files."""
    cwd = _release_directory(cwd)
    include_patterns = _normalize_patterns(include_patterns)
    exclude_patterns = _normalize_patterns(exclude_patterns)
    print(f"\nScanning {cwd} for signable files...")
    files = discover_files(cwd, include_patterns, exclude_patterns)
    if not files:
        print("No signable files found.")
        return
    print(f"Found {len(files)} file(s) to {'include' if dry_run else 'sign'}:")
    for file_path in files:
        print(f"  {file_path.relative_to(cwd).as_posix()}")
    if dry_run:
        print("Dry run complete; no files were changed.")
        return

    if not keys_exist():
        raise SigningError("Signing keys not found. Run 'tools sign --init' first.")

    private_key = get_private_key()
    public_key = get_public_key()

    files_manifest: Dict[str, str] = {}
    for file_path in files:
        relative_path = file_path.relative_to(cwd).as_posix()
        try:
            files_manifest[relative_path] = hash_file(file_path)
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
        manifest = {
            "format": MANIFEST_FORMAT,
            "version": MANIFEST_VERSION,
            "selection": {
                "include": include_patterns,
                "exclude": exclude_patterns,
            },
            "files": files_manifest,
        }
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

    print(f"✓ Wrote manifest.json ({len(files_manifest)} files)")
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


def verify_files(
    cwd: Optional[Path] = None,
    public_key: Optional[Path] = None,
    include_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
) -> None:
    """Verify the manifest signature and every signable file in a directory."""
    cwd = _release_directory(cwd)
    manifest_path = cwd / "manifest.json"
    sig_path = cwd / "manifest.sig"

    if not manifest_path.is_file() or not sig_path.is_file():
        raise SigningError(f"manifest.json or manifest.sig not found in {cwd}")

    verification_key = get_public_key() if public_key is None else Path(public_key).expanduser()
    if not verification_key.is_file():
        raise SigningError(f"Public key not found: {verification_key}")

    print(f"Verifying signatures in {cwd}...")
    if not verify_manifest(manifest_path, sig_path, verification_key):
        raise SigningError("Manifest signature verification failed.")
    print("✓ Signature verified ✅")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SigningError(f"Could not read manifest.json: {exc}") from exc
    if not isinstance(manifest, dict):
        raise SigningError("manifest.json must contain a JSON object.")

    if manifest.get("format") == MANIFEST_FORMAT:
        if manifest.get("version") != MANIFEST_VERSION:
            raise SigningError(
                "Unsupported manifest version: {}".format(manifest.get("version"))
            )
        files_manifest = manifest.get("files")
        selection = manifest.get("selection")
        if not isinstance(files_manifest, dict) or not isinstance(selection, dict):
            raise SigningError("Manifest files and selection must be objects.")
        saved_include = selection.get("include")
        saved_exclude = selection.get("exclude")
        if (
            not isinstance(saved_include, list)
            or any(not isinstance(pattern, str) for pattern in saved_include)
            or not isinstance(saved_exclude, list)
            or any(not isinstance(pattern, str) for pattern in saved_exclude)
        ):
            raise SigningError("Manifest selection patterns must be arrays of strings.")
        effective_include = _normalize_patterns(saved_include)
        effective_exclude = _normalize_patterns(saved_exclude)
        if include_patterns is not None and _normalize_patterns(include_patterns) != effective_include:
            raise SigningError("Include patterns differ from those recorded in the manifest.")
        if exclude_patterns is not None and _normalize_patterns(exclude_patterns) != effective_exclude:
            raise SigningError("Exclude patterns differ from those recorded in the manifest.")
    else:
        # Version 1 manifests were a flat mapping of paths to hashes and did not
        # record custom selection patterns.
        files_manifest = manifest
        effective_include = _normalize_patterns(include_patterns)
        effective_exclude = _normalize_patterns(exclude_patterns)

    for file_path, expected_hash in files_manifest.items():
        if not isinstance(file_path, str) or not isinstance(expected_hash, str):
            raise SigningError("Manifest paths and hashes must be strings.")
        if not HASH_RE.fullmatch(expected_hash):
            raise SigningError(f"Invalid SHA256 hash in manifest for {file_path!r}.")

    print(f"\nChecking {len(files_manifest)} files...")
    all_match = True
    for relative_path, expected_hash in sorted(files_manifest.items()):
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

    listed_paths = set(files_manifest)
    for file_path in discover_files(cwd, effective_include, effective_exclude):
        relative_path = file_path.relative_to(cwd).as_posix()
        if relative_path not in listed_paths:
            print(f"  ✗ UNLISTED: {relative_path}")
            all_match = False

    if not all_match:
        raise SigningError("Some files are missing, changed, or absent from the signed manifest.")
    print("\n✓ All files verified ✅")
