#!/usr/bin/env python3
"""Assemble an offline Suite release around an already frozen Windows Viewer."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "applications/comparison_viewer_data_builder"))
sys.path.insert(0, str(ROOT / "applications/comparison_viewer"))

import deploy_to_registry as deploy
import package_windows
from release_bundle import (
    APP, DATASET_FORMAT, DATASET_VERSION, VIEWER_ZIP, file_hashes, sha256,
    valid_version, validate_app, verify_tree, verify_viewer, viewer_contents, write_json,
)


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def build(root: Path, viewer: Path, version: str, output: Path) -> Path:
    if not valid_version(version):
        raise ValueError("Version must follow SemVer, for example 1.0.0 or 1.0.0-rc.1.")
    if git(root, "status", "--porcelain"):
        raise ValueError("Commit or remove working-tree changes before building a release.")
    archive_path = output / f"comparison-viewer-suite-v{version}-pluginunit.zip"
    if archive_path.exists() or archive_path.with_suffix(".zip.sha256").exists():
        raise FileExistsError(archive_path)
    viewer_info = verify_viewer(viewer, version=version)
    wanted = package_windows.source_hashes(root / "applications/comparison_viewer")
    tracked = set(git(root, "ls-files", "-z", "--", "applications/comparison_viewer/").split("\0"))
    missing = sorted(name for name in wanted if f"applications/comparison_viewer/{name}" not in tracked)
    if missing:
        raise ValueError(f"Viewer source files are not tracked by Git: {', '.join(missing)}")
    if viewer_info.get("source_files") != wanted:
        raise ValueError("Frozen Viewer was not built from this checkout's Viewer sources/guide.")
    with zipfile.ZipFile(viewer) as archive:
        _, _, viewer_hashes = viewer_contents(archive)
        if any(viewer_hashes["app/" + name] != wanted[name] for name in package_windows.APP_FILES):
            raise ValueError("Frozen Viewer application bytes differ from this checkout.")
    identity = dict(schema_version=1, version=version, source_commit=git(root, "rev-parse", "HEAD"),
                    source_tree=git(root, "rev-parse", "HEAD^{tree}"),
                    dataset_format=DATASET_FORMAT, dataset_version=DATASET_VERSION,
                    viewer_sha256=sha256(viewer))
    prefix = f"applications/{APP}/"
    names = git(root, "ls-files", "-z", "--", prefix).split("\0")
    files = {name[len(prefix):]: root / name for name in names if name and
             not name[len(prefix):].startswith(("tests/", "documents/"))}
    deploy.validate(APP, files)
    for name, source in files.items():
        if name.endswith(".whl"):
            with zipfile.ZipFile(source) as wheel:
                if wheel.testzip() is not None:
                    raise ValueError(f"Corrupt offline wheel: {name}")
    for required in ("release_bundle.py", "sqlite_builder.py", "builder.py", ".dockerignore"):
        if required not in files:
            raise ValueError(f"Builder release is missing {required}")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".release-", dir=output) as temporary:
        stage = Path(temporary)
        app = stage / "Registry" / APP
        for name, source in files.items():
            target = app / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        (app / "payload").mkdir()
        shutil.copyfile(viewer, app / VIEWER_ZIP)
        write_json(app / "release.json", dict(identity, files=file_hashes(app)))
        validate_app(app)
        for source, target in (
            ("scripts/install_release.py", "install.py"),
            ("scripts/deploy_to_registry.py", "deploy_to_registry.py"),
            ("documents/release.md", "documents/release.md"),
            ("documents/deployment.md", "documents/deployment.md"),
            ("documents/workflow-examples/comparison-viewer.yml", "documents/workflow-examples/comparison-viewer.yml"),
        ):
            destination = stage / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / source, destination)
        write_json(stage / "release.json", dict(identity, files=file_hashes(stage)))
        checksums = file_hashes(stage)
        (stage / "SHA256SUMS.txt").write_text(
            "".join(f"{digest}  {name}\n" for name, digest in checksums.items()), encoding="utf-8")
        verify_tree(stage, checksums, exclude=("SHA256SUMS.txt",))
        temporary_zip = stage / "release.zip"
        with zipfile.ZipFile(temporary_zip, "x", zipfile.ZIP_DEFLATED) as archive:
            for name in (*checksums, "SHA256SUMS.txt"):
                archive.write(stage / name, name, compress_type=(
                    zipfile.ZIP_STORED if name.endswith((".whl", ".zip")) else zipfile.ZIP_DEFLATED))
        with zipfile.ZipFile(temporary_zip) as archive:
            bad = archive.testzip()
            if bad is not None:
                raise ValueError(f"Corrupt release ZIP member: {bad}")
        temporary_zip.rename(archive_path)
    archive_path.with_suffix(".zip.sha256").write_text(
        f"{sha256(archive_path)}  {archive_path.name}\n", encoding="ascii")
    print(archive_path)
    return archive_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--viewer-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(ROOT, args.viewer_zip.resolve(), args.version, args.output.resolve())


if __name__ == "__main__":
    main()
