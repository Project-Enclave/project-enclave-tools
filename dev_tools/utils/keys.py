"""Key management utilities for the signing tool."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


def get_keys_dir(create: bool = False) -> Path:
    """Return the key directory, creating it only when requested."""
    keys_dir = Path.home() / ".config" / "dev-tools" / "keys"
    if create:
        keys_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return keys_dir


def get_private_key() -> Path:
    """Get the path to the private key."""
    return get_keys_dir() / "private.pem"


def get_public_key() -> Path:
    """Get the path to the public key."""
    return get_keys_dir() / "public.pem"


def keys_exist() -> bool:
    """Check if both keys exist without creating the key directory."""
    return get_private_key().is_file() and get_public_key().is_file()


def _run_openssl(args: list[str], description: str) -> None:
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError("OpenSSL is required but was not found on PATH.") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "OpenSSL failed."
        raise RuntimeError(f"{description}: {detail}")


def generate_keys() -> None:
    """Generate an RSA key pair, preserving existing files on failure."""
    keys_dir = get_keys_dir(create=True)
    private_key = get_private_key()
    public_key = get_public_key()

    if private_key.exists() or public_key.exists():
        print("Keys already exist at:")
        print(f"  Private: {private_key}")
        print(f"  Public:  {public_key}")
        response = input("Overwrite? (y/n): ").strip().lower()
        if response != "y":
            print("Aborted.")
            return

    print(f"Generating RSA keypair in {keys_dir}...")
    with tempfile.TemporaryDirectory(prefix=".keygen-", dir=str(keys_dir)) as temp_dir:
        staged_private = Path(temp_dir) / "private.pem"
        staged_public = Path(temp_dir) / "public.pem"
        _run_openssl(
            ["openssl", "genrsa", "-out", str(staged_private), "2048"],
            "Could not generate private key",
        )
        _run_openssl(
            [
                "openssl", "rsa", "-in", str(staged_private), "-pubout",
                "-out", str(staged_public),
            ],
            "Could not generate public key",
        )
        staged_private.chmod(0o600)
        staged_public.chmod(0o644)
        os.replace(staged_private, private_key)
        os.replace(staged_public, public_key)

    print("✓ Keys generated successfully!")
    print(f"  Private key: {private_key}")
    print(f"  Public key:  {public_key}")
