#!/usr/bin/env python3
"""Install an extracted offline release; run its install.py, not this source file."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "Registry/comparison_viewer_data_builder"))
import deploy_to_registry as deploy
from release_bundle import APP, VIEWER_ZIP, exclusive_lock, read_json, validate_app, validate_identity, verify_tree, verify_viewer


def validate_release(root: Path) -> dict:
    manifest = read_json(root / "release.json")
    validate_identity(manifest)
    verify_tree(root, manifest.get("files"), exclude=("release.json", "SHA256SUMS.txt"))
    app = validate_app(root / "Registry" / APP)
    if {key: app.get(key) for key in manifest if key != "files"} != {
            key: value for key, value in manifest.items() if key != "files"}:
        raise ValueError("Package and Builder release identities differ.")
    verify_viewer(root / "Registry" / APP / VIEWER_ZIP, version=app["version"])
    return manifest


def install(root: Path, registry: Path, *, dry_run: bool = False, quiesced: bool = False,
            replace_modified: bool = False, rollback: str | None = None) -> dict:
    root, registry = root.resolve(), registry.resolve()
    if not registry.is_dir() or registry == root or root in registry.parents or registry in root.parents:
        raise ValueError("Registry must be an existing directory outside the extracted release.")
    work = registry / deploy.WORK_DIR / APP
    target = registry / APP
    if target.is_symlink() or work.is_symlink() or (registry / deploy.WORK_DIR).is_symlink():
        raise ValueError("Application and deployment work directories must not be symlinks.")
    if rollback:
        if not re.fullmatch(r"backup-[0-9TZ]+-[a-f0-9]{12}", rollback):
            raise ValueError("Use a backup directory name, not a path.")
        source = work / rollback
        if source.is_symlink():
            raise ValueError("Backup must not be a symlink.")
        manifest = validate_app(source)
        verify_viewer(source / VIEWER_ZIP, version=manifest["version"])
    else:
        manifest = validate_release(root)
        source = root / "Registry" / APP
    source_manifest = read_json(source / "release.json")
    files = {name: source / name for name in (*source_manifest["files"], "release.json")}
    changes = deploy.plan(files, target)
    print(json.dumps(dict(version=manifest["version"], changes=changes), indent=2))
    if dry_run:
        return changes
    if not quiesced:
        raise ValueError("Stop new workflows, wait for running workflows, then pass --quiesced.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:12]
    work.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(work / ".install.lock"):
        if target.exists() and not replace_modified:
            if not (target / "release.json").is_file():
                raise ValueError("Existing source deployment: use --replace-modified after reviewing its configuration.")
            validate_app(target)
        staging = deploy.stage(APP, files, registry, dict(
            version=manifest["version"], source_commit=manifest["source_commit"],
            viewer_sha256=manifest["viewer_sha256"], rollback_from=rollback,
        ), stamp)
        try:
            for name in ("input", "output"):
                (staging / name).mkdir()
            validate_app(staging)
            backups = deploy.swap({APP: staging}, registry, stamp)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    print(f"Installed {manifest['version']}; previous application: {backups[APP]}")
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiesced", action="store_true", help="Confirm workflows have been stopped/drained")
    parser.add_argument("--replace-modified", action="store_true",
                        help="Back up and replace a source deployment or locally modified release")
    parser.add_argument("--rollback", help="Restore a named backup, retaining the current version as another backup")
    args = parser.parse_args()
    install(ROOT, args.registry, dry_run=args.dry_run, quiesced=args.quiesced,
            replace_modified=args.replace_modified, rollback=args.rollback)


if __name__ == "__main__":
    main()
