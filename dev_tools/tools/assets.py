"""Read-only checks for image formats, dimensions, file sizes, and duplicates."""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


class AssetError(Exception):
    """An expected asset inspection failure."""


DEFAULT_MAX_BYTES = 10 * 1024 * 1024
EXTENSION_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".gif": "GIF",
    ".webp": "WEBP",
    ".svg": "SVG",
    ".bmp": "BMP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
    ".avif": "AVIF",
    ".heic": "HEIC",
    ".heif": "HEIF",
    ".ico": "ICO",
}
DEFAULT_FORMATS = {"PNG", "JPEG", "GIF", "WEBP", "SVG"}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "dist", "build"}


def _detect_format(path: Path) -> Optional[str]:
    with path.open("rb") as file_obj:
        header = file_obj.read(512)
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "GIF"
    if header.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "WEBP"
    if header.startswith(b"\x00\x00\x01\x00"):
        return "ICO"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        brand = header[8:12].decode("ascii", errors="ignore").lower()
        if brand in {"avif", "avis"}:
            return "AVIF"
        if brand in {"heic", "heix", "hevc", "hevx"}:
            return "HEIC"
        if brand in {"mif1", "msf1"}:
            return "HEIF"
    if header.startswith(b"BM"):
        return "BMP"
    if header.startswith((b"II*\x00", b"MM\x00*")):
        return "TIFF"
    try:
        with path.open("rb") as file_obj:
            text = file_obj.read(262144).decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    if re.search(r"<svg(?:\s|>)", text, re.IGNORECASE):
        return "SVG"
    return None


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _svg_length(value: str) -> Optional[float]:
    match = re.fullmatch(r"\s*(\d+(?:\.\d*)?|\.\d+)\s*(?:px)?\s*", value, re.IGNORECASE)
    return float(match.group(1)) if match else None


def _tiff_dimensions(path: Path) -> Optional[Tuple[float, float]]:
    """Read width and height from a classic TIFF or BigTIFF first IFD."""
    with path.open("rb") as file_obj:
        header = file_obj.read(16)
        if len(header) < 8 or header[:2] not in (b"II", b"MM"):
            return None
        byte_order = "<" if header[:2] == b"II" else ">"
        magic = struct.unpack(byte_order + "H", header[2:4])[0]
        if magic == 42:
            ifd_offset = struct.unpack(byte_order + "I", header[4:8])[0]
            file_obj.seek(ifd_offset)
            count_bytes = file_obj.read(2)
            if len(count_bytes) != 2:
                return None
            entry_count = struct.unpack(byte_order + "H", count_bytes)[0]
            entry_size = 12
            value_size = 4
        elif magic == 43 and len(header) >= 16:
            offset_size, reserved = struct.unpack(byte_order + "HH", header[4:8])
            if offset_size != 8 or reserved != 0:
                return None
            ifd_offset = struct.unpack(byte_order + "Q", header[8:16])[0]
            file_obj.seek(ifd_offset)
            count_bytes = file_obj.read(8)
            if len(count_bytes) != 8:
                return None
            entry_count = struct.unpack(byte_order + "Q", count_bytes)[0]
            entry_size = 20
            value_size = 8
        else:
            return None

        dimensions: Dict[int, int] = {}
        for _ in range(min(entry_count, 4096)):
            entry = file_obj.read(entry_size)
            if len(entry) != entry_size:
                return None
            tag, value_type = struct.unpack(byte_order + "HH", entry[:4])
            if tag not in (256, 257):
                continue
            if magic == 42:
                value_count = struct.unpack(byte_order + "I", entry[4:8])[0]
                value_or_offset = entry[8:12]
            else:
                value_count = struct.unpack(byte_order + "Q", entry[4:12])[0]
                value_or_offset = entry[12:20]
            type_size = {3: 2, 4: 4, 16: 8}.get(value_type)
            if type_size is None or value_count < 1:
                continue
            if value_count == 1 and type_size <= value_size:
                raw_value = value_or_offset[:type_size]
            else:
                value_offset = int.from_bytes(value_or_offset, "little" if byte_order == "<" else "big")
                current_position = file_obj.tell()
                file_obj.seek(value_offset)
                raw_value = file_obj.read(type_size)
                file_obj.seek(current_position)
            if len(raw_value) != type_size:
                continue
            dimensions[tag] = int.from_bytes(raw_value, "little" if byte_order == "<" else "big")
        width, height = dimensions.get(256), dimensions.get(257)
        if width and height:
            return float(width), float(height)
    return None


def _isobmff_dimensions(path: Path) -> Optional[Tuple[float, float]]:
    """Read AVIF/HEIF dimensions from an ``ispe`` property box."""
    with path.open("rb") as file_obj:
        data = file_obj.read(16 * 1024 * 1024)

    containers = {b"meta", b"iprp", b"ipco", b"moov", b"trak", b"mdia", b"minf"}

    def find_ispe(start: int, end: int, depth: int = 0) -> Optional[Tuple[float, float]]:
        if depth > 8:
            return None
        offset = start
        while offset + 8 <= end:
            size32 = struct.unpack(">I", data[offset : offset + 4])[0]
            box_type = data[offset + 4 : offset + 8]
            header_size = 8
            if size32 == 1:
                if offset + 16 > end:
                    return None
                box_size = struct.unpack(">Q", data[offset + 8 : offset + 16])[0]
                header_size = 16
            elif size32 == 0:
                box_size = end - offset
            else:
                box_size = size32
            box_end = offset + box_size
            if box_size < header_size or box_end > end:
                return None
            payload_start = offset + header_size
            if box_type == b"ispe" and box_size >= header_size + 12:
                width, height = struct.unpack(">II", data[payload_start + 4 : payload_start + 12])
                if width and height:
                    return float(width), float(height)
            elif box_type in containers:
                child_start = payload_start + (4 if box_type == b"meta" else 0)
                result = find_ispe(child_start, box_end, depth + 1)
                if result is not None:
                    return result
            offset = box_end
        return None

    return find_ispe(0, len(data))


def _image_dimensions(path: Path, image_format: str) -> Optional[Tuple[float, float]]:
    """Read pixel dimensions for common image formats without decoding pixels."""
    if image_format == "TIFF":
        return _tiff_dimensions(path)
    if image_format in {"AVIF", "HEIF", "HEIC"}:
        return _isobmff_dimensions(path)
    with path.open("rb") as file_obj:
        header = file_obj.read(512)
        if image_format == "PNG" and len(header) >= 24 and header[12:16] == b"IHDR":
            return float(struct.unpack(">I", header[16:20])[0]), float(
                struct.unpack(">I", header[20:24])[0]
            )
        if image_format == "GIF" and len(header) >= 10:
            width, height = struct.unpack("<HH", header[6:10])
            return float(width), float(height)
        if image_format == "BMP" and len(header) >= 26:
            width, height = struct.unpack("<ii", header[18:26])
            return float(abs(width)), float(abs(height))
        if image_format == "ICO" and len(header) >= 8:
            width = header[6] or 256
            height = header[7] or 256
            return float(width), float(height)
        if image_format == "WEBP" and len(header) >= 30:
            chunk_type = header[12:16]
            if chunk_type == b"VP8X":
                width = int.from_bytes(header[24:27], "little") + 1
                height = int.from_bytes(header[27:30], "little") + 1
                return float(width), float(height)
            if chunk_type == b"VP8L" and len(header) >= 25 and header[20] == 0x2F:
                b1, b2, b3, b4 = header[21:25]
                width = 1 + (((b2 & 0x3F) << 8) | b1)
                height = 1 + (((b4 & 0x0F) << 10) | (b3 << 2) | ((b2 & 0xC0) >> 6))
                return float(width), float(height)
            if chunk_type == b"VP8 " and header[23:26] == b"\x9d\x01\x2a":
                width = struct.unpack("<H", header[26:28])[0] & 0x3FFF
                height = struct.unpack("<H", header[28:30])[0] & 0x3FFF
                return float(width), float(height)
        if image_format == "JPEG":
            file_obj.seek(0)
            if file_obj.read(2) != b"\xff\xd8":
                return None
            start_of_frame = {
                0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
            }
            while True:
                first_byte = file_obj.read(1)
                if not first_byte:
                    return None
                if first_byte != b"\xff":
                    continue
                marker_data = file_obj.read(1)
                while marker_data == b"\xff":
                    marker_data = file_obj.read(1)
                if not marker_data:
                    return None
                marker = marker_data[0]
                if marker == 0xD9:
                    return None
                if marker in {0x01, 0xD8} or 0xD0 <= marker <= 0xD7:
                    continue
                segment_length = file_obj.read(2)
                if len(segment_length) != 2:
                    return None
                length = struct.unpack(">H", segment_length)[0]
                if length < 2:
                    return None
                if marker in start_of_frame:
                    frame = file_obj.read(5)
                    if len(frame) != 5:
                        return None
                    height, width = struct.unpack(">HH", frame[1:5])
                    return float(width), float(height)
                file_obj.seek(length - 2, os.SEEK_CUR)
        if image_format == "SVG":
            file_obj.seek(0)
            header = file_obj.read(262144)
            try:
                text = header.decode("utf-8-sig")
            except UnicodeDecodeError:
                return None
            tag = re.search(r"<svg\b([^>]*)>", text, re.IGNORECASE | re.DOTALL)
            if not tag:
                return None
            attributes = tag.group(1)
            width_match = re.search(r"\bwidth\s*=\s*['\"]([^'\"]+)['\"]", attributes, re.I)
            height_match = re.search(r"\bheight\s*=\s*['\"]([^'\"]+)['\"]", attributes, re.I)
            width = _svg_length(width_match.group(1)) if width_match else None
            height = _svg_length(height_match.group(1)) if height_match else None
            if width is not None and height is not None:
                return width, height
            viewbox = re.search(
                r"\bviewBox\s*=\s*['\"]\s*[-+\d.eE]+[ ,]+[-+\d.eE]+[ ,]+([-+\d.eE]+)[ ,]+([-+\d.eE]+)\s*['\"]",
                attributes,
                re.I,
            )
            if viewbox:
                try:
                    return float(viewbox.group(1)), float(viewbox.group(2))
                except ValueError:
                    return None
    return None


def _assets(root: Path) -> List[Path]:
    paths = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            dirname
            for dirname in dirnames
            if dirname not in SKIP_DIRS
            and not dirname.startswith(".")
            and not (Path(directory) / dirname).is_symlink()
        )
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if (
                path.suffix.lower() in EXTENSION_FORMATS
                and path.is_file()
                and not path.is_symlink()
            ):
                paths.append(path)
    return paths


def check_assets(
    directory: Optional[Path] = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    allowed_formats: Optional[Set[str]] = None,
    max_width: Optional[int] = None,
    max_height: Optional[int] = None,
    report_format: str = "text",
) -> int:
    """Validate image dimensions, file sizes, allowed formats, and content duplicates."""
    if report_format not in ("text", "json"):
        raise AssetError("Report format must be 'text' or 'json'.")
    root = Path.cwd() / "assets" if directory is None else Path(directory).expanduser()
    try:
        root = root.resolve()
    except OSError as exc:
        raise AssetError(f"Could not resolve asset directory: {exc}") from exc
    if not root.is_dir():
        raise AssetError(f"Asset directory not found: {root}")
    if max_bytes <= 0:
        raise AssetError("Maximum file size must be greater than zero.")
    if max_width is not None and max_width <= 0:
        raise AssetError("Maximum image width must be greater than zero.")
    if max_height is not None and max_height <= 0:
        raise AssetError("Maximum image height must be greater than zero.")

    allowed = DEFAULT_FORMATS if allowed_formats is None else allowed_formats
    unknown_formats = allowed - set(EXTENSION_FORMATS.values())
    if unknown_formats:
        raise AssetError("Unknown image format(s): " + ", ".join(sorted(unknown_formats)))

    files = _assets(root)
    if not files:
        if report_format == "json":
            print(
                json.dumps(
                    {
                        "command": "assets",
                        "report_version": 1,
                        "directory": str(root),
                        "status": "ok",
                        "files": [],
                        "duplicates": [],
                        "summary": {"files": 0, "problems": 0},
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(f"No recognized image assets in {root}.")
        return 0

    failures = 0
    hashes: Dict[str, List[str]] = {}
    reports = []
    if report_format == "text":
        print(f"Checking {len(files)} image asset(s) in {root} (limit: {max_bytes} bytes each).")
    for path in files:
        relative = path.relative_to(root).as_posix()
        expected_format = EXTENSION_FORMATS[path.suffix.lower()]
        file_report: Dict[str, Any] = {"path": relative, "status": "ok", "issues": []}
        try:
            size = path.stat().st_size
            actual_format = _detect_format(path)
            dimensions = (
                _image_dimensions(path, actual_format) if actual_format is not None else None
            )
            digest = _file_hash(path)
        except OSError as exc:
            issue = f"could not read file: {exc}"
            file_report["status"] = "failed"
            file_report["issues"].append(issue)
            if report_format == "text":
                print(f"[FAIL] {relative}: {issue}")
            failures += 1
            reports.append(file_report)
            continue

        file_report["size_bytes"] = size
        file_report["format"] = actual_format
        if dimensions is not None:
            width, height = dimensions
            file_report["dimensions"] = {"width": width, "height": height}

        def report_issue(message: str) -> None:
            nonlocal failures
            failures += 1
            file_report["status"] = "failed"
            file_report["issues"].append(message)
            if report_format == "text":
                print(f"[FAIL] {relative}: {message}")

        if size > max_bytes:
            report_issue(f"{size} bytes exceeds the {max_bytes}-byte limit.")
        if actual_format is None:
            report_issue("File content is not a recognized image format.")
        elif actual_format != expected_format:
            report_issue(f"extension suggests {expected_format}, content is {actual_format}.")
        elif actual_format not in allowed:
            report_issue(f"{actual_format} is not in the allowed formats.")
        else:
            dimension_detail = ""
            if dimensions is not None:
                width, height = dimensions
                dimension_detail = f", {width:g} x {height:g} pixels"
            if report_format == "text":
                print(f"[OK] {relative}: {actual_format}{dimension_detail}, {size} bytes")

        if dimensions is None and (max_width is not None or max_height is not None):
            report_issue("Pixel dimensions could not be read for this format.")
        elif dimensions is not None:
            width, height = dimensions
            if max_width is not None and width > max_width:
                report_issue(f"width {width:g}px exceeds {max_width}px.")
            if max_height is not None and height > max_height:
                report_issue(f"height {height:g}px exceeds {max_height}px.")
        hashes.setdefault(digest, []).append(relative)
        reports.append(file_report)

    duplicate_groups = []
    for duplicate_paths in hashes.values():
        if len(duplicate_paths) > 1:
            duplicate_groups.append(duplicate_paths)
            for report in reports:
                if report["path"] in duplicate_paths:
                    report["status"] = "failed"
                    report["issues"].append("Duplicate image content.")
            if report_format == "text":
                print("[FAIL] Duplicate image content: " + ", ".join(duplicate_paths))
            failures += 1

    if report_format == "json":
        print(
            json.dumps(
                {
                    "command": "assets",
                    "report_version": 1,
                    "directory": str(root),
                    "status": "failed" if failures else "ok",
                    "files": reports,
                    "duplicates": duplicate_groups,
                    "summary": {"files": len(files), "problems": failures},
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(f"\n{failures} asset problem(s).")
    return 1 if failures else 0
