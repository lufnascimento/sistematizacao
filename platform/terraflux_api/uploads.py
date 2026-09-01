from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import uuid
import zipfile
from pathlib import Path
from typing import BinaryIO

from fastapi import UploadFile

from .catalog import ASSET_TYPES


SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class UploadValidationError(ValueError):
    pass


def safe_filename(original: str | None) -> str:
    raw = (original or "upload.bin").replace("\\", "/").split("/")[-1]
    cleaned = SAFE_NAME.sub("_", raw).strip(" ._")
    if not cleaned:
        cleaned = "upload.bin"
    stem, suffix = os.path.splitext(cleaned)
    if stem.upper() in WINDOWS_RESERVED:
        stem = f"file_{stem}"
    return f"{stem[:120]}{suffix[:16].lower()}"


def validate_role_extension(role: str, filename: str) -> str:
    definition = ASSET_TYPES.get(role)
    if definition is None:
        raise UploadValidationError(f"unknown asset role: {role}")
    extension = Path(filename).suffix.lower()
    if extension not in definition["extensions"]:
        allowed = ", ".join(definition["extensions"])
        raise UploadValidationError(f"extension {extension or '(none)'} is not allowed for {role}; use {allowed}")
    return extension


def _validate_magic(path: Path, extension: str) -> list[str]:
    warnings: list[str] = []
    with path.open("rb") as stream:
        head = stream.read(16)
    if extension in {".las", ".laz"} and head[:4] != b"LASF":
        raise UploadValidationError("LAS/LAZ signature is invalid")
    if extension in {".tif", ".tiff"} and head[:4] not in {b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"}:
        raise UploadValidationError("TIFF signature is invalid")
    if extension == ".pdf" and not head.startswith(b"%PDF-"):
        raise UploadValidationError("PDF signature is invalid")
    if extension == ".shp" and (len(head) < 4 or struct.unpack(">I", head[:4])[0] != 9994):
        raise UploadValidationError("Shapefile signature is invalid")
    if extension in {".json", ".geojson"}:
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UploadValidationError(f"JSON is invalid: {exc}") from exc
        if extension == ".geojson" and not isinstance(parsed, dict):
            raise UploadValidationError("GeoJSON root must be an object")
        if extension == ".geojson" and parsed.get("type") not in {
            "FeatureCollection", "Feature", "Polygon", "MultiPolygon", "LineString", "MultiLineString", "Point", "MultiPoint"
        }:
            raise UploadValidationError("GeoJSON type is missing or unsupported")
    if extension == ".zip":
        _validate_zip(path)
    if extension in {".dbf", ".shx", ".prj", ".cpg"}:
        warnings.append("SHAPEFILE_SIDECAR_REQUIRES_MATCHING_COMPONENTS")
    return warnings


def _validate_zip(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if not members or len(members) > 2000:
                raise UploadValidationError("ZIP must contain between 1 and 2000 members")
            total = 0
            has_supported_geodata = False
            shapefile_parts: dict[str, set[str]] = {}
            for member in members:
                normalized = member.filename.replace("\\", "/")
                parts = Path(normalized).parts
                if normalized.startswith("/") or ".." in parts or re.match(r"^[A-Za-z]:", normalized):
                    raise UploadValidationError("ZIP contains an unsafe path")
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise UploadValidationError("ZIP symbolic links are not accepted")
                total += member.file_size
                if total > 20 * 1024**3:
                    raise UploadValidationError("ZIP expanded size exceeds 20 GiB")
                if member.file_size > 100 * 1024**2 and member.compress_size > 0 and member.file_size / member.compress_size > 500:
                    raise UploadValidationError("ZIP compression ratio is unsafe")
                member_path = Path(normalized)
                extension = member_path.suffix.lower()
                if extension in {".gpkg", ".geojson", ".json"}:
                    has_supported_geodata = True
                if extension in {".shp", ".shx", ".dbf"}:
                    shapefile_parts.setdefault(str(member_path.with_suffix("")).casefold(), set()).add(extension)
            if any({".shp", ".shx", ".dbf"} <= extensions for extensions in shapefile_parts.values()):
                has_supported_geodata = True
            if not has_supported_geodata:
                raise UploadValidationError("ZIP must contain GeoPackage/GeoJSON/JSON or a complete SHP+SHX+DBF set")
    except zipfile.BadZipFile as exc:
        raise UploadValidationError("ZIP is invalid") from exc


async def store_upload(
    upload: UploadFile,
    destination_dir: Path,
    role: str,
    max_bytes: int,
) -> dict:
    filename = safe_filename(upload.filename)
    extension = validate_role_extension(role, filename)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{uuid.uuid4().hex}_{filename}"
    temporary = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("xb") as target:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise UploadValidationError(f"file exceeds configured limit of {max_bytes} bytes")
                digest.update(chunk)
                target.write(chunk)
        if size == 0:
            raise UploadValidationError("empty files are not accepted")
        warnings = _validate_magic(temporary, extension)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return {
        "original_filename": upload.filename or filename,
        "stored_filename": destination.name,
        "extension": extension,
        "size_bytes": size,
        "sha256": digest.hexdigest(),
        "path": str(destination),
        "warnings": warnings,
    }
