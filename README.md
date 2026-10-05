# Dev Tools

Dev Tools is a command-line toolkit for contributors to [Project Enclave](https://github.com/project-enclave), with project setup and scaffolding, release notes, version management, and signing commands.

## Installation

Install the current public release with `pipx`:

```bash
pipx install project-enclave-tools
```

OpenSSL must be installed and available on your `PATH` for key generation and signing.

## Check a project setup

Run the doctor from a project directory to check common tools, the project's Python version, and dependencies from `requirements.txt`:

```bash
tools doctor
```

To check another checkout, pass its path with `--directory` (or `-C`):

```bash
tools doctor --directory ./Enclave-Messenger
```

The doctor reports tool and runtime versions, checks Python against `.python-version` or `[project].requires-python`, and checks dependencies from `requirements.txt` and `requirements-dev.txt`. If `.env.example` declares variables, it checks whether they have non-placeholder values in `.env` or the process environment. It reports variable names only and never prints their values. When neither Python setting exists, it expects Python 3.11 or newer. The command is read-only.

## Set up a project

Create a local `.venv`, install `requirements.txt` and `requirements-dev.txt` when present, install runtime dependencies from `[project].dependencies` in `pyproject.toml`, and copy `.env.example` to `.env` if no local config exists:

```bash
tools setup
```

Use `--python` to choose the interpreter used for a new environment, and `-C` to select a project directory. An existing `.venv` and `.env` are preserved. Add `--editable` to install the project itself from its `pyproject.toml` in editable mode.

Start a basic installable Python project in a new or existing directory:

```bash
tools init ./my-project --name my-project
```

The initializer creates a `src` package, test scaffold, `pyproject.toml`, `CHANGELOG.md`, `.env.example`, `.gitignore`, `.python-version`, and runtime/development requirements files. It keeps any files that already exist. Run `tools setup --editable` to create its environment, install dependencies, and install the project in editable mode.

Run configured lint, type, and test commands together:

```bash
tools check
```

The commands are arrays under `[tool.project-enclave-tools.check]` in `pyproject.toml`, with `lint`, `types`, and `tests` entries. Use `--lint`, `--types`, or `--tests` to run one category.

## Project configuration and JSON reports

Dev Tools reads project settings from the `[tool.project-enclave-tools]` tables in `pyproject.toml`. This keeps command defaults, asset rules, environment setup, signing selection, changelog refs, and release settings together. Command-line options override configured defaults.

```toml
[tool.project-enclave-tools]
output = "text" # default report format for doctor, check, and assets
environment-directory = ".venv"
requirements-files = ["requirements.txt", "requirements-dev.txt"]
env-template = ".env.example"
local-config = ".env"

[tool.project-enclave-tools.doctor]
minimum-python = "3.11" # fallback if the project declares no Python version

[tool.project-enclave-tools.setup]
python = "python3" # optional interpreter command
editable = false

[tool.project-enclave-tools.check]
lint = ["ruff", "check", "src"]
types = ["mypy", "src"]
tests = ["pytest"]

[tool.project-enclave-tools.assets]
directory = "assets"
max-bytes = 10485760
max-width = 2048 # optional
max-height = 2048 # optional
formats = ["png", "jpg", "jpeg", "gif", "webp", "svg"]

[tool.project-enclave-tools.sign]
include = [] # empty uses the built-in file type list
exclude = []

[tool.project-enclave-tools.release]
directory = "dist"
sign = false

[tool.project-enclave-tools.changelog]
# from-ref = "v1.2.0" # optional; otherwise use the nearest reachable tag
to-ref = "HEAD"

[tool.project-enclave-tools.aliases]
ci = ["check"]
doctor-json = ["doctor", "--json"]
```

The shared environment settings control which requirements files `doctor` checks and `setup` installs, along with the virtual environment and local config template paths. `setup.python` and `setup.editable` set defaults; `--python`, `--editable`, and `--no-editable` override them. Asset command options such as `--max-bytes`, `--max-width`, `--max-height`, and `--formats` override the asset table. `tools sign --include` and `--exclude` override the configured signing patterns. The release table sets the staging directory and whether `tools release` signs by default; `--output`, `--sign`, and `--no-sign` override those settings. The changelog table sets default commit refs, which `--from` and `--to` can override. Signing private keys stay in the user's key store and must never be put in `pyproject.toml`.

Define aliases in `[tool.project-enclave-tools.aliases]` as arrays of arguments. An alias expands to a built-in `tools` command, and arguments provided after the alias are appended. For example, `tools ci` runs the configured checks and `tools doctor-json -C ./my-project` runs the doctor with JSON output. Aliases come from the current project's `pyproject.toml` (or the project selected with `-C` / `--directory`); they cannot shadow built-in commands, chain to another alias, or run shell commands.

Use machine-readable reports in a CI job or editor integration:

```bash
tools doctor --json
tools check --json
tools assets --json
```

Set `output = "json"` to make JSON the project default. Use `--text` to request the human-readable report for one run. Each report has a top-level command, `report_version`, and status, structured per-check or per-file results, and a summary. The commands keep their normal success/failure exit codes. With `tools check --json`, the report goes to stdout and check command output goes to stderr; command output is not copied into the JSON report.

## Release notes and versions

Generate Markdown release notes from commits since the nearest reachable Git tag:

```bash
tools changelog
```

Commit subjects using Conventional Commit prefixes such as `feat:`, `fix:`, and `docs:` are grouped into sections. Use `--from REF` and `--to REF` to choose a different range; `[tool.project-enclave-tools.changelog]` can set defaults with `from-ref` and `to-ref`. Output goes to the terminal so it can be reviewed or redirected into a changelog file.

Show or update a static `MAJOR.MINOR.PATCH` version in `[project]` in `pyproject.toml`:

```bash
tools version
tools version --bump patch
tools version --bump minor
tools version --set 1.2.3
```

Version updates change only the version line in `pyproject.toml`.

Prepare a release after adding a non-empty section for the current version to `CHANGELOG.md`:

```bash
tools release
tools release --sign
```

This validates the static version and its changelog section, builds wheel and source distributions, checks their metadata, and stages release notes in `dist/`. Add `--sign` to create a signed manifest for the distributions and notes; this requires the configured signing keys. Verify the staged artifacts with `tools sign --verify -C dist`. It does not tag or publish the package. Install the optional release tools with `python -m pip install build twine` if needed.

Check image formats, pixel dimensions, maximum file size, and duplicate image content in `assets/`:

```bash
tools assets
tools assets ./frontend/images --max-bytes 5242880 --max-width 2048 --max-height 2048 --formats png,jpg,webp,svg
```

The default limit is 10 MiB per file, and the default allowed formats are PNG, JPEG, GIF, WebP, and SVG. Pixel dimensions are reported for PNG, JPEG, GIF, WebP, SVG, BMP, ICO, TIFF, AVIF, and HEIF/HEIC; `--max-width` and `--max-height` enforce dimension limits.

## Signing release files

Generate a 2048-bit RSA key pair the first time you use the signing tool:

```bash
tools sign --init
```

The keys are stored in `~/.config/dev-tools/keys/`. Keep `private.pem` private; distribute `public.pem` to anyone who needs to verify your releases. If keys already exist, initialization asks before replacing them.

Preview exactly which files would be included, without needing signing keys or writing files:

```bash
tools sign --dry-run
```

By default, signing includes `.py`, `.json`, `.yaml`, `.yml`, `.toml`, `.txt`, and `.md` files. Use repeatable `--include` and `--exclude` glob patterns to select other assets or archive types:

```bash
tools sign --include '**/*.js' --include '**/*.zip' --exclude '**/vendor/**'
```

The manifest records the include and exclude patterns, so verification automatically checks the same selection. Older flat manifests do not store patterns; pass the same custom patterns to `--verify` when checking those. Create or update the manifest and signature:

```bash
tools sign
```

The manifest contains SHA256 hashes for `.py`, `.json`, `.yaml`, `.yml`, `.toml`, `.txt`, and `.md` files. Hidden files and common generated directories are skipped. Commit `manifest.json` and `manifest.sig` with the release.

To sign or verify a different directory, pass it with `--directory` (or `-C`):

```bash
tools sign --directory ./release
tools sign --verify -C ./release
tools sign --verify --public-key ./trusted-public.pem -C ./release
tools sign --fingerprint --public-key ./trusted-public.pem
```

Verification checks the signature, each listed file's hash, missing files, and any new signable files that are absent from the manifest.

The fingerprint is SHA256 over the public key's DER encoding. The GitHub Actions workflow verifies the signed manifest on pull requests and before release publishing, using `.github/release-public.pem`. The current trusted fingerprint is `SHA256:C5:7E:67:5D:4C:B3:7A:47:AD:5F:1D:74:67:EF:3B:05:7C:0A:6C:08:2C:AA:80:5C:D7:DE:1C:41:12:AA:AE:5F`; compare it with `tools sign --fingerprint --public-key .github/release-public.pem` before trusting the key.

## License

This tool is provided under the GNU General Public License v3.0.

## Ai
Ai **HAS** been used in this project to help the user understand what this does, how to get it running, etc 
