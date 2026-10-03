# Changelog

## [Unreleased]

## [0.1.2] - 2026-10-03

- Add project initialization, diagnostics, setup, check, changelog, version, release, and asset commands.
- Add image dimension, format, file-size, and duplicate checks across common formats.
- Add signing previews, custom file patterns, trusted public-key fingerprints, and selection-aware manifests while retaining legacy manifest verification.
- Add optional signatures for built distributions and release notes, plus pull-request and release manifest checks in GitHub Actions.
- Add versioned JSON reports for doctor, check, and assets, with project defaults centralized in pyproject.toml.
- Add project-defined command aliases in pyproject.toml, with safe argument expansion and built-in command validation.

## [0.1.1]

- Add SHA256 manifest signing and verification commands.
