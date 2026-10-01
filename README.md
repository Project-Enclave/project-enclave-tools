# Dev Tools

Dev Tools is a small command-line toolkit for contributors to [Project Enclave](https://github.com/project-enclave). It currently provides signed SHA256 manifests for release files.

## Installation

Install the current public release with `pipx`:

```bash
pipx install "git+https://github.com/Chinglen2080/dev-tools.git@v0.1.1"
```

OpenSSL must be installed and available on your `PATH` for key generation and signing.

## Signing release files

Generate a 2048-bit RSA key pair the first time you use the signing tool:

```bash
tools sign --init
```

The keys are stored in `~/.config/dev-tools/keys/`. Keep `private.pem` private; distribute `public.pem` to anyone who needs to verify your releases. If keys already exist, initialization asks before replacing them.

From the release directory, create or update the manifest and signature:

```bash
tools sign
```

The manifest contains SHA256 hashes for `.py`, `.json`, `.yaml`, `.yml`, `.toml`, `.txt`, and `.md` files. Hidden files and common generated directories are skipped. Commit `manifest.json` and `manifest.sig` with the release.

To sign or verify a different directory, pass it with `--directory` (or `-C`):

```bash
tools sign --directory ./release
tools sign --verify -C ./release
```

Verification checks the signature, each listed file's hash, missing files, and any new signable files that are absent from the manifest.

## License

This tool is provided under the GNU General Public License v3.0.
