# Project Enclave Tools Handover

Updated: 2026-10-03

## Repository and current state

- Checkout: `/home/tombi/dev-tools`
- Repository: [Project-Enclave/project-enclave-tools](https://github.com/Project-Enclave/project-enclave-tools)
- The requested feature work is implemented in the working tree. It has not been committed, pushed, or published.
- PyPI reports `0.1.1` as the latest published version (checked 2026-10-03). The refreshed `0.1.2` candidate is under `dist/0.1.2-aliases-final/`; it has not been published.

## Chat context

- The user previously asked to improve, test, and publish this project. They clarified that the GitHub repository owner is `Project-Enclave` and shared a pending PyPI publisher entry for this repo, workflow `publish.yml`, and environment `pypi`.
- The user asked to verify feature ideas before adding them, then approved the requested setup, doctor, check, release, assets, signing, and GitHub Actions features.
- This handoff is for continuing this chat after the user reaches their monthly usage limit. Do not repeat the connection troubleshooting already discussed; resume from the current local changes and publishing status.

## Implemented

- `tools doctor` checks tool/runtime versions, Python project setup, declared dependencies, and required environment variable names. It does not print environment values.
- `tools setup` creates `.venv`, installs requirements files and static runtime dependencies from `pyproject.toml`, and creates `.env` from `.env.example` without overwriting an existing local config.
- `tools check` runs the configured lint, type, and test commands. The current project config uses Ruff, mypy, and pytest.
- `tools release` validates the version and matching changelog section, builds wheel and source distributions, checks metadata with Twine, and stages release notes in `dist/`. It does not publish or tag.
- `tools assets` checks recognized image formats, file size, pixel dimensions for common formats, and duplicate file contents. It parses TIFF, AVIF, HEIF, and HEIC dimensions too. Width and height limits are available with `--max-width` and `--max-height`.
- `tools sign` supports `--dry-run`, repeatable `--include` and `--exclude` patterns, custom verification keys with `--public-key`, and `--fingerprint`.
- `tools doctor`, `tools check`, and `tools assets` can emit versioned JSON reports with `--json`; use `--text` to override a JSON project default.
- Project defaults live in `pyproject.toml` under `[tool.project-enclave-tools]` tables for environment/setup, check commands, assets, signing patterns, release output, and changelog refs. CLI options override those defaults. Private signing keys remain in the user's key store.
- Project command aliases live in `[tool.project-enclave-tools.aliases]` as argument arrays. They expand to one built-in command, append user arguments, cannot shadow built-ins, and never run through a shell.
- `.github/workflows/publish.yml` runs checks and manifest verification on pull requests and before release publishing. It checks the release key fingerprint before verifying the manifest.

## Additional features built during this continuation

The original approved checklist and all three follow-ups requested during this continuation are complete:

- [x] Add pixel-dimension parsing for TIFF, AVIF, HEIF, and HEIC. Header-level fixture tests pass.
- [x] Store custom `--include` and `--exclude` patterns in version 2 manifests. Verification reuses the recorded patterns automatically and still supports legacy flat manifests.
- [x] Add `tools release --sign` to sign the staged wheel, source archive, and release notes together.
- [x] Add `--json` / `--text` reports for `doctor`, `check`, and `assets`, including versioned JSON summaries and structured per-item results.
- [x] Centralize project defaults in `pyproject.toml`: output format, environment and requirements paths, setup behavior, checks, asset policy, signing patterns, release staging/signing, and changelog refs. CLI arguments override configured defaults.
- [x] Add configurable command aliases and document their syntax and safety rules.

## Verification completed

- `tools check`: passed with Ruff, mypy, and 31 tests. Alias smoke checks `tools doctor-json -C .` and `tools ci --lint` also passed.
- `tools release --sign --output dist/0.1.2-aliases-final` built the refreshed wheel, source archive, and notes after the alias changelog update; Twine metadata checks passed.
- The refreshed bundle's signature verifies against all three files in `dist/0.1.2-aliases-final/`. Older candidate folders predate alias support; use only `dist/0.1.2-aliases-final/` for review.
- TIFF and AVIF/HEIF/HEIC dimensions have synthetic-header regression coverage; manifest v2 selection persistence and legacy verification are tested.
- The source manifest and signature are refreshed after the final handover edit and verify against `.github/release-public.pem`.
- Release public key fingerprint: `SHA256:C5:7E:67:5D:4C:B3:7A:47:AD:5F:1D:74:67:EF:3B:05:7C:0A:6C:08:2C:AA:80:5C:D7:DE:1C:41:12:AA:AE:5F`.

Version 2 manifests store the original file selection. Verification still accepts legacy flat manifests. `manifest.json` and `manifest.sig` must be refreshed after edits to signable files; the default manifest selection includes this handover file.

## Remaining before a real release

1. Review the working tree and commit the intended files.
2. Confirm the PyPI Trusted Publisher configuration is active. The pending publisher details previously shared were repository `Project-Enclave/project-enclave-tools`, workflow `publish.yml`, and environment `pypi`.
3. Once the working tree is reviewed and committed and the Trusted Publisher is confirmed active, publishing a GitHub Release triggers the existing workflow's PyPI publishing job.
4. For a later release, increment the version, update `pyproject.toml` and `CHANGELOG.md`, and prepare a fresh signed candidate.

Keep the signing private key private. Only `.github/release-public.pem` and its fingerprint belong in the repository.

## Current continuation

- [x] Bump the local package version to `0.1.2` and add its dated changelog section.
- [x] Run full checks and build/sign the refreshed `0.1.2` release artifacts under `dist/0.1.2-aliases-final/`.
- [x] Refresh and verify the source manifest after the handover update.
- [ ] Leave publication pending until the PyPI Trusted Publisher entry is active and the user requests publication.

## Follow-up ideas

- [ ] Consider project templates for `tools init`, dependency auditing, documentation link checks, and safe cleanup in later work.
