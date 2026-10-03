from __future__ import annotations

import hashlib
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from dev_tools.cli import main
from dev_tools.tools.assets import _detect_format, _image_dimensions, check_assets
from dev_tools.tools.check import run_checks
from dev_tools.tools.doctor import (
    _project_dependencies as _doctor_project_dependencies,
)
from dev_tools.tools.doctor import (
    _project_minimum_python,
    doctor,
)
from dev_tools.tools.init import init_project
from dev_tools.tools.release import _read_release_notes, prepare_release
from dev_tools.tools.setup import (
    _initialize_local_config,
    setup_project,
)
from dev_tools.tools.setup import (
    _project_dependencies as _setup_project_dependencies,
)
from dev_tools.tools.setup import (
    _project_python as _setup_python,
)
from dev_tools.tools.signing import (
    SigningError,
    discover_files,
    fingerprint_public_key,
    sign_files,
    sign_manifest,
    verify_files,
)
from dev_tools.tools.version import change_version, current_version
from dev_tools.utils.config import load_project_config
from dev_tools.utils.env import configured_env_names, required_env_names

PNG_CONTENT = (
    b"\x89PNG\r\n\x1a\n"
    + b"\x00\x00\x00\rIHDR"
    + struct.pack(">II", 2, 3)
    + b"\x08\x06\x00\x00\x00\x00\x00\x00\x00"
)


def _isobmff_image(brand: bytes, width: int, height: int) -> bytes:
    ispe = struct.pack(">I4s4sII", 20, b"ispe", b"\x00" * 4, width, height)
    ipco = struct.pack(">I4s", 8 + len(ispe), b"ipco") + ispe
    iprp = struct.pack(">I4s", 8 + len(ipco), b"iprp") + ipco
    meta = struct.pack(">I4s", 12 + len(iprp), b"meta") + b"\x00" * 4 + iprp
    ftyp = struct.pack(">I4s4s4s", 16, b"ftyp", brand, b"\x00" * 4)
    return ftyp + meta


def test_assets_reports_duplicate_images(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "one.png").write_bytes(PNG_CONTENT)
    (assets / "copy.png").write_bytes(PNG_CONTENT)

    assert check_assets(assets) == 1
    assert "Duplicate image content" in capsys.readouterr().out


def test_assets_detects_extension_mismatch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "image.jpg").write_bytes(PNG_CONTENT)

    assert check_assets(assets) == 1
    assert "extension suggests JPEG, content is PNG" in capsys.readouterr().out


def test_assets_checks_pixel_dimensions(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "wide.png").write_bytes(PNG_CONTENT)

    assert check_assets(assets, max_width=1, max_height=3) == 1
    output = capsys.readouterr().out
    assert "2 x 3 pixels" in output
    assert "width 2px exceeds 1px" in output


def test_assets_json_report_has_structured_file_and_duplicate_results(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "one.png").write_bytes(PNG_CONTENT)
    (assets / "copy.png").write_bytes(PNG_CONTENT)

    assert check_assets(assets, report_format="json") == 1
    report = json.loads(capsys.readouterr().out)
    assert report["command"] == "assets"
    assert report["status"] == "failed"
    assert report["duplicates"] == [["copy.png", "one.png"]]
    assert report["files"][0]["dimensions"] == {"width": 2.0, "height": 3.0}


def test_assets_cli_uses_configured_defaults_and_cli_output_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    image_dir = tmp_path / "design-assets"
    image_dir.mkdir()
    (image_dir / "logo.png").write_bytes(PNG_CONTENT)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.project-enclave-tools]\noutput = "json"\n\n'
        '[tool.project-enclave-tools.assets]\n'
        'directory = "design-assets"\nmax-bytes = 1024\nformats = ["png"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["tools", "assets", "-C", str(tmp_path)])

    assert main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["command"] == "assets"
    assert report["files"][0]["path"] == "logo.png"

    monkeypatch.setattr(
        sys, "argv", ["tools", "assets", "-C", str(tmp_path), "--text"]
    )
    assert main() == 0
    assert "Checking 1 image asset" in capsys.readouterr().out


def test_command_alias_expands_config_and_appends_cli_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.project-enclave-tools.aliases]\nci = ["check", "--lint"]\n',
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(
        "dev_tools.cli.run_checks",
        lambda directory, selected, report_format: calls.append(
            (directory, selected, report_format)
        ) or 0,
    )

    assert main(["ci", "--types", "-C", str(tmp_path)]) == 0
    assert calls == [(tmp_path, {"lint", "types"}, "text")]


def test_command_alias_rejects_shell_commands(tmp_path: Path) -> None:
    marker = tmp_path / "should-not-exist"
    (tmp_path / "pyproject.toml").write_text(
        "[tool.project-enclave-tools.aliases]\n"
        'unsafe = ["sh", "-c", "touch {}"]\n'.format(marker),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as error:
        main(["unsafe", "-C", str(tmp_path)])
    assert error.value.code == 2
    assert not marker.exists()


def test_assets_reads_tiff_pixel_dimensions(tmp_path: Path) -> None:
    width_entry = struct.pack("<HHI4s", 256, 4, 1, struct.pack("<I", 640))
    height_entry = struct.pack("<HHI4s", 257, 4, 1, struct.pack("<I", 480))
    image = tmp_path / "image.tif"
    image.write_bytes(b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 2) + width_entry + height_entry + b"\x00" * 4)

    assert _detect_format(image) == "TIFF"
    assert _image_dimensions(image, "TIFF") == (640.0, 480.0)


@pytest.mark.parametrize(
    ("extension", "brand", "image_format"),
    (("avif", b"avif", "AVIF"), ("heic", b"heic", "HEIC"), ("heif", b"mif1", "HEIF")),
)
def test_assets_reads_isobmff_pixel_dimensions(
    tmp_path: Path, extension: str, brand: bytes, image_format: str
) -> None:
    image = tmp_path / f"image.{extension}"
    image.write_bytes(_isobmff_image(brand, 1920, 1080))

    assert _detect_format(image) == image_format
    assert _image_dimensions(image, image_format) == (1920.0, 1080.0)


def test_doctor_never_prints_environment_values(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / ".env.example").write_text(
        "SERVICE_TOKEN=\nMISSING_SETTING=\n", encoding="utf-8"
    )
    (tmp_path / ".env").write_text("SERVICE_TOKEN=very-secret-value\n", encoding="utf-8")

    doctor(tmp_path)
    output = capsys.readouterr().out
    assert "MISSING_SETTING" in output
    assert "very-secret-value" not in output


def test_doctor_json_uses_configured_output_without_secret_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.project-enclave-tools]\noutput = "json"\n', encoding="utf-8"
    )
    (tmp_path / ".env.example").write_text("SERVICE_TOKEN=\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SERVICE_TOKEN=secret-not-for-reports\n", encoding="utf-8")

    doctor(tmp_path)
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["command"] == "doctor"
    assert "secret-not-for-reports" not in output
    assert report["summary"]["checks"] == len(report["checks"])


def test_env_helpers_return_names_not_values(tmp_path: Path) -> None:
    template = tmp_path / ".env.example"
    local = tmp_path / ".env"
    template.write_text("API_TOKEN=\nOTHER=value\n", encoding="utf-8")
    local.write_text("API_TOKEN=real-secret\nOTHER=your_value\n", encoding="utf-8")

    assert required_env_names(template) == {"API_TOKEN", "OTHER"}
    assert configured_env_names(local) == {"API_TOKEN"}


def test_setup_copies_template_without_overwriting_local_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".env.example").write_text("API_TOKEN=\n", encoding="utf-8")
    _initialize_local_config(tmp_path)
    local = tmp_path / ".env"
    assert local.read_text(encoding="utf-8") == "API_TOKEN=\n"
    local.write_text("API_TOKEN=local-secret\n", encoding="utf-8")

    _initialize_local_config(tmp_path)
    assert local.read_text(encoding="utf-8") == "API_TOKEN=local-secret\n"
    assert "local-secret" not in capsys.readouterr().out


def test_setup_creates_virtual_environment_and_copies_template(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".env.example").write_text("API_KEY=replace_me\n", encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text("# Empty setup fixture.\n", encoding="utf-8")

    assert setup_project(tmp_path, python=sys.executable) == 0
    assert _setup_python(tmp_path).is_file()
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "API_KEY=replace_me\n"
    assert "Created .env" in capsys.readouterr().out


def test_setup_uses_configured_paths_and_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.project-enclave-tools]\n"
        'environment-directory = ".environment"\n'
        'requirements-files = ["deps/locked.txt"]\n'
        'env-template = "config/local.env.example"\n'
        'local-config = "config/local.env"\n',
        encoding="utf-8",
    )
    env_python = tmp_path / ".environment" / "bin" / "python"
    env_python.parent.mkdir(parents=True)
    env_python.write_text("", encoding="utf-8")
    (tmp_path / "deps").mkdir()
    (tmp_path / "deps" / "locked.txt").write_text("# fixture\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "local.env.example").write_text("TOKEN=\n", encoding="utf-8")
    commands = []
    monkeypatch.setattr(
        "dev_tools.tools.setup._run",
        lambda command, description, cwd: commands.append(command),
    )

    assert setup_project(tmp_path) == 0
    assert (tmp_path / "config" / "local.env").read_text(encoding="utf-8") == "TOKEN=\n"
    assert any(str(tmp_path / "deps" / "locked.txt") in command for command in commands)


def test_project_scaffold_keeps_existing_files(tmp_path: Path) -> None:
    project = tmp_path / "example"
    project.mkdir()
    readme = project / "README.md"
    readme.write_text("keep\n", encoding="utf-8")

    assert init_project(project, "example-app") == 0
    assert readme.read_text(encoding="utf-8") == "keep\n"
    assert (project / "src" / "example_app" / "__main__.py").is_file()
    config = load_project_config(project)
    assert config["assets"]["directory"] == "assets"
    assert config["release"]["directory"] == "dist"
    assert config["aliases"]["ci"] == ["check"]


def test_check_runs_configured_commands(tmp_path: Path) -> None:
    command = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])
    pyproject = (
        "[tool.project-enclave-tools.check]\n"
        f"lint = {command}\n"
        f"types = {command}\n"
        f"tests = {command}\n"
    )
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")

    assert run_checks(tmp_path) == 0


def test_check_returns_failure_for_failed_command(tmp_path: Path) -> None:
    success = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])
    failure = json.dumps([sys.executable, "-c", "raise SystemExit(4)"])
    (tmp_path / "pyproject.toml").write_text(
        "[tool.project-enclave-tools.check]\n"
        f"lint = {success}\n"
        f"types = {success}\n"
        f"tests = {failure}\n",
        encoding="utf-8",
    )

    assert run_checks(tmp_path, {"tests"}) == 1


def test_check_json_is_parseable_and_keeps_command_output_on_stderr(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    script = tmp_path / "emit-check-output.py"
    script.write_text("print('check-output-marker')\n", encoding="utf-8")
    command = json.dumps([sys.executable, str(script)])
    (tmp_path / "pyproject.toml").write_text(
        "[tool.project-enclave-tools.check]\ntests = {}\n".format(command),
        encoding="utf-8",
    )

    assert run_checks(tmp_path, {"tests"}, "json") == 0
    captured = capfd.readouterr()
    report = json.loads(captured.out)
    assert report["results"][0]["name"] == "tests"
    assert report["results"][0]["status"] == "ok"
    assert "check-output-marker" in captured.err
    assert "check-output-marker" not in captured.out


def test_project_minimum_uses_declared_python_requirement(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.9,<4"\n', encoding="utf-8"
    )
    assert _project_minimum_python(tmp_path) == (3, 9)
    (tmp_path / ".python-version").write_text("3.8.12\n", encoding="utf-8")
    assert _project_minimum_python(tmp_path) == (3, 8)


def test_doctor_reads_pyproject_runtime_dependencies(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["requests>=2", "urllib3; python_version < \'3.11\'"]\n',
        encoding="utf-8",
    )

    names, skipped, error = _doctor_project_dependencies(tmp_path)
    assert names == ["requests"]
    assert skipped == 1
    assert error is None


def test_setup_installs_pyproject_runtime_dependencies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\ndependencies = ["requests>=2"]\n', encoding="utf-8")
    assert _setup_project_dependencies(tmp_path) == ["requests>=2"]

    fake_python = tmp_path / ".venv" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        "dev_tools.tools.setup._project_python",
        lambda _, environment_directory=Path(".venv"): fake_python,
    )
    monkeypatch.setattr(
        "dev_tools.tools.setup._run",
        lambda command, description, cwd: calls.append((command, description, cwd)),
    )

    assert setup_project(tmp_path) == 0
    assert any(call[0][-1] == "requests>=2" for call in calls)


def test_custom_sign_patterns_include_assets_and_exclude_private_files(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("app", encoding="utf-8")
    (tmp_path / "assets" / "bundle.zip").write_bytes(b"archive")
    (tmp_path / "assets" / "private.zip").write_bytes(b"private")

    selected = discover_files(tmp_path, ["**/*.zip"], ["**/private.zip"])
    assert [path.relative_to(tmp_path).as_posix() for path in selected] == ["assets/bundle.zip"]


def test_sign_dry_run_does_not_write_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "app.js").write_text("console.log('hello')", encoding="utf-8")

    sign_files(tmp_path, ["*.js"], dry_run=True)
    output = capsys.readouterr().out
    assert "app.js" in output
    assert "no files were changed" in output
    assert not (tmp_path / "manifest.json").exists()
    assert not (tmp_path / "manifest.sig").exists()


def test_manifest_records_custom_selection_for_later_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shutil.which("openssl") is None:
        pytest.skip("OpenSSL is unavailable")
    private_key = tmp_path / "private.pem"
    public_key = tmp_path / "public.pem"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(private_key)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["openssl", "pkey", "-in", str(private_key), "-pubout", "-out", str(public_key)],
        check=True,
        capture_output=True,
    )
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("app", encoding="utf-8")
    (assets / "archive.zip").write_bytes(b"archive")
    (assets / "private.zip").write_bytes(b"private")

    monkeypatch.setattr("dev_tools.tools.signing.keys_exist", lambda: True)
    monkeypatch.setattr("dev_tools.tools.signing.get_private_key", lambda: private_key)
    monkeypatch.setattr("dev_tools.tools.signing.get_public_key", lambda: public_key)
    include = ["**/*.js", "**/*.zip"]
    exclude = ["**/private.zip"]
    sign_files(tmp_path, include, exclude)

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "project-enclave-tools-manifest"
    assert manifest["selection"] == {"include": sorted(include), "exclude": exclude}
    verify_files(tmp_path, public_key)
    with pytest.raises(SigningError, match="Include patterns differ"):
        verify_files(tmp_path, public_key, ["**/*.js"], exclude)


def test_public_key_fingerprint_is_stable(tmp_path: Path) -> None:
    if shutil.which("openssl") is None:
        pytest.skip("OpenSSL is unavailable")
    private_key = tmp_path / "private.pem"
    public_key = tmp_path / "public.pem"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(private_key)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["openssl", "pkey", "-in", str(private_key), "-pubout", "-out", str(public_key)],
        check=True,
        capture_output=True,
    )

    fingerprint = fingerprint_public_key(public_key)
    assert fingerprint.startswith("SHA256:")
    assert fingerprint == fingerprint_public_key(public_key)

    (tmp_path / "payload.txt").write_text("release payload\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    signature = tmp_path / "manifest.sig"
    manifest.write_text(
        json.dumps({"payload.txt": hashlib.sha256(b"release payload\n").hexdigest()}),
        encoding="utf-8",
    )
    sign_manifest(manifest, signature, private_key)
    verify_files(tmp_path, public_key)


def test_release_notes_require_a_versioned_section(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## [1.2.3]\n\n- Add an item.\n\n## [Unreleased]\n",
        encoding="utf-8",
    )

    notes = _read_release_notes(tmp_path, "1.2.3")
    assert notes.startswith("# Release 1.2.3")
    assert "Add an item" in notes


def test_release_can_sign_staged_distributions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if shutil.which("openssl") is None:
        pytest.skip("OpenSSL is unavailable")
    private_key = tmp_path / "private.pem"
    public_key = tmp_path / "public.pem"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(private_key)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["openssl", "pkey", "-in", str(private_key), "-pubout", "-out", str(public_key)],
        check=True,
        capture_output=True,
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample-release"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [1.2.3]\n\n- Release test.\n", encoding="utf-8"
    )

    def fake_run(command: list[str], cwd: Path, description: str) -> None:
        if "build" in command:
            output = Path(command[command.index("--outdir") + 1])
            (output / "sample-1.2.3-py3-none-any.whl").write_bytes(b"wheel")
            (output / "sample-1.2.3.tar.gz").write_bytes(b"sdist")

    monkeypatch.setattr("dev_tools.tools.release.importlib.util.find_spec", lambda _: object())
    monkeypatch.setattr("dev_tools.tools.release._run", fake_run)
    monkeypatch.setattr("dev_tools.tools.release.keys_exist", lambda: True)
    monkeypatch.setattr("dev_tools.tools.signing.keys_exist", lambda: True)
    monkeypatch.setattr("dev_tools.tools.signing.get_private_key", lambda: private_key)
    monkeypatch.setattr("dev_tools.tools.signing.get_public_key", lambda: public_key)

    assert prepare_release(tmp_path, sign_distributions=True) == 0
    distribution_dir = tmp_path / "dist"
    verify_files(distribution_dir, public_key)
    manifest = json.loads((distribution_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["selection"]["include"] == [
        "*.tar.gz",
        "*.whl",
        "release-notes-1.2.3.md",
    ]
    assert len(manifest["files"]) == 3
    capsys.readouterr()


def test_version_bump_changes_static_project_version(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "sample"\nversion = "0.1.9" # keep comment\n',
        encoding="utf-8",
    )

    assert change_version(tmp_path, bump="patch") == 0
    assert current_version(tmp_path) == "0.1.10"
    assert '# keep comment' in pyproject.read_text(encoding="utf-8")
