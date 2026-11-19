#!/usr/bin/env python3
"""Verify the release archive and main tag, then publish the frozen bytes with gh."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "applications/comparison_viewer_data_builder"))
from release_bundle import APP, VIEWER_ZIP, hash_mapping, relative_path, sha256, validate_identity
from build_release import git


def publish(archive_path, *, execute=False):
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        for name in names:
            relative_path(name)
        if len(set(name.casefold() for name in names)) != len(names):
            raise ValueError("Duplicate release ZIP members.")
        raw = archive.read("release.json")
        manifest = json.loads(raw)
        validate_identity(manifest)
        hashes = hash_mapping(manifest.get("files"))
        if set(names) != set(hashes) | {"release.json", "SHA256SUMS.txt"}:
            raise ValueError("Release ZIP inventory differs from its manifest.")
        for name, expected in hashes.items():
            with archive.open(name) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    raise ValueError(f"Release ZIP checksum mismatch: {name}")
        if hashes.get(f"Registry/{APP}/{VIEWER_ZIP}") != manifest.get("viewer_sha256"):
            raise ValueError("Frozen Viewer differs from the release manifest.")
        tag = "v" + manifest["version"]
        if git(ROOT, "rev-parse", f"{tag}^{{tree}}") != manifest["source_tree"]:
            raise ValueError("Release tag source tree differs from the tested artifact.")
        subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", tag, "origin/main"], check=True)
        checksum = archive_path.with_suffix(".zip.sha256")
        expected_checksum = f"{sha256(archive_path)}  {archive_path.name}\n"
        if checksum.read_text(encoding="ascii") != expected_checksum:
            raise ValueError("Release archive sidecar checksum differs.")
        print(f"Verified {tag}: tag is on origin/main and matches the frozen artifact.")
        if not execute:
            print("Dry run: pass --publish to create the GitHub Release.")
            return
        with tempfile.TemporaryDirectory(prefix="viewer-release-publish-") as temporary:
            viewer = Path(temporary) / f"comparison-viewer-v{manifest['version']}-windows.zip"
            with archive.open(f"Registry/{APP}/{VIEWER_ZIP}") as source, viewer.open("xb") as target:
                shutil.copyfileobj(source, target)
            if sha256(viewer) != manifest["viewer_sha256"]:
                raise ValueError("Frozen Viewer differs from the release manifest.")
            command = ["gh", "release", "create", tag, "--verify-tag", "--generate-notes",
                       "--title", f"Comparison Viewer Suite {tag}"]
            if "-" in manifest["version"].partition("+")[0]:
                command.append("--prerelease")
            subprocess.run([*command, str(archive_path), str(checksum), str(viewer)], cwd=ROOT, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    publish(args.archive.resolve(), execute=args.publish)
