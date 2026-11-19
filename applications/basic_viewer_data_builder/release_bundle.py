"""Offline release and frozen Viewer integrity contracts (standard library only)."""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import zipfile

APP = "comparison_viewer_data_builder"
DATASET_FORMAT = "plugin-viewer-sqlite"
DATASET_VERSION = 4
VIEWER_NAME = "comparison-viewer"
VIEWER_ZIP = "payload/comparison-viewer-windows.zip"
VIEWER_REQUIRED = {
    "start-viewer.cmd", "USER_GUIDE.pdf", "distribution.json", "runtime/python.exe",
    "app/main.py", "app/engine.py", "app/contour_area.py", "app/web.html",
    "app/viewer.js", "app/viewer.css", "app/requirements.txt",
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def relative_path(name: str) -> str:
    if (not isinstance(name, str) or not name or "\\" in name
            or PurePosixPath(name).is_absolute()
            or PurePosixPath(name).as_posix() != name):
        raise ValueError(f"Unsafe release path: {name!r}")
    for part in name.split("/"):
        if (part in {"", ".", ".."} or part.endswith((" ", "."))
                or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
                or PureWindowsPath(part).is_reserved()):
            raise ValueError(f"Unsafe release path: {name!r}")
    return name


def file_hashes(root: Path, *, exclude: tuple[str, ...] = ()) -> dict[str, str]:
    result = {}
    for base, directories, filenames in os.walk(root):
        for name in list(directories) + filenames:
            path = Path(base) / name
            relative = path.relative_to(root).as_posix()
            if name == "__pycache__" or relative in exclude:
                if name in directories:
                    directories.remove(name)
                continue
            if path.is_symlink():
                raise ValueError(f"Symlinks are not allowed in a release: {path}")
            if name in filenames:
                result[relative_path(relative)] = sha256(path)
    return dict(sorted(result.items()))


@contextmanager
def exclusive_lock(path: Path):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        yield
    finally:
        os.close(descriptor)
        path.unlink()


def hash_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise ValueError("A nonempty file hash mapping is required.")
    result = {}
    folded = set()
    for name, digest in value.items():
        relative_path(name)
        if name.casefold() in folded or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError(f"Invalid or duplicate file hash: {name}")
        folded.add(name.casefold())
        result[name] = digest
    return result


def verify_tree(root: Path, expected: object, *, exclude: tuple[str, ...] = ()) -> None:
    wanted = hash_mapping(expected)
    actual = file_hashes(root, exclude=exclude)
    if actual != wanted:
        changed = sorted(name for name in set(actual) | set(wanted) if actual.get(name) != wanted.get(name))
        raise ValueError(f"Release files differ in {root}: {', '.join(changed)}")


def valid_version(value: object) -> bool:
    if not isinstance(value, str):
        return False
    match = re.fullmatch(
        r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
        r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
        r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?", value,
    )
    if not match:
        return False
    prerelease = match.group(1)
    return not prerelease or not any(
        part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease.split(".")
    )


def validate_identity(manifest: dict) -> None:
    if (manifest.get("schema_version") != 1
            or not valid_version(manifest.get("version"))
            or manifest.get("dataset_format") != DATASET_FORMAT
            or manifest.get("dataset_version") != DATASET_VERSION
            or not re.fullmatch(r"[a-f0-9]{40,64}", str(manifest.get("source_commit", "")))
            or not re.fullmatch(r"[a-f0-9]{40,64}", str(manifest.get("source_tree", "")))):
        raise ValueError("Invalid release identity or incompatible SQLite format/version.")


def viewer_contents(archive: zipfile.ZipFile) -> tuple[dict, dict[str, zipfile.ZipInfo], dict[str, str]]:
    entries = {}
    folded = set()
    roots = set()
    for info in archive.infolist():
        name = relative_path(info.filename.rstrip("/"))
        mode = stat.S_IFMT(info.external_attr >> 16)
        if mode not in {0, stat.S_IFREG, stat.S_IFDIR} or info.flag_bits & 1:
            raise ValueError(f"Unsupported Viewer ZIP entry: {name}")
        if name.casefold() in folded:
            raise ValueError(f"Duplicate Viewer ZIP entry: {name}")
        folded.add(name.casefold())
        parts = name.split("/")
        roots.add(parts[0])
        if not info.is_dir():
            if len(parts) < 2:
                raise ValueError("Viewer ZIP must contain one top-level folder.")
            entries["/".join(parts[1:])] = info
    if len(roots) != 1 or "SHA256SUMS.txt" not in entries:
        raise ValueError("Viewer ZIP must contain one folder and SHA256SUMS.txt.")
    hashes = {}
    for line in archive.read(entries["SHA256SUMS.txt"]).decode("utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or name in hashes:
            raise ValueError("Invalid Viewer SHA256SUMS.txt.")
        hashes[name] = digest
    hashes = hash_mapping(hashes)
    if set(entries) != set(hashes) | {"SHA256SUMS.txt"} or not VIEWER_REQUIRED <= set(hashes):
        raise ValueError("Viewer ZIP inventory is incomplete or contains unlisted files.")
    manifest = json.loads(archive.read(entries["distribution.json"]))
    if (not isinstance(manifest, dict) or manifest.get("dataset_format") != DATASET_FORMAT
            or manifest.get("dataset_version") != DATASET_VERSION
            or manifest.get("datasets_included") is not False):
        raise ValueError("Viewer distribution has an incompatible dataset contract.")
    return manifest, entries, hashes


def verify_viewer(path: Path, *, version: str | None = None, destination: Path | None = None) -> dict:
    with zipfile.ZipFile(path) as archive:
        manifest, entries, hashes = viewer_contents(archive)
        if version is not None and manifest.get("release_version") != version:
            raise ValueError("Frozen Viewer release version differs from the Suite release.")
        for name, info in entries.items():
            digest = hashlib.sha256()
            target = destination / name if destination is not None else None
            if target is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source:
                if target is None:
                    digest = hashlib.file_digest(source, "sha256")
                else:
                    with target.open("xb") as output:
                        while block := source.read(1024 * 1024):
                            output.write(block)
                            digest.update(block)
            if name in hashes and digest.hexdigest() != hashes[name]:
                raise ValueError(f"Frozen Viewer file checksum mismatch: {name}")
    return manifest


def validate_app(root: Path) -> dict:
    manifest = read_json(root / "release.json")
    validate_identity(manifest)
    verify_tree(root, manifest.get("files"), exclude=("release.json", ".deployment.json", "input", "output"))
    files = hash_mapping(manifest["files"])
    if files.get(VIEWER_ZIP) != manifest.get("viewer_sha256"):
        raise ValueError("Frozen Viewer archive hash differs from the release inventory.")
    return manifest
