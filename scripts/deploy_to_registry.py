#!/usr/bin/env python3
"""Deploy suite applications from this repository into a Plugin Registry.

Only Git-tracked files are deployed. Each application is staged next to the
target and swapped in with directory renames, so the Registry never contains a
half-copied application. The previous copy is kept as a backup.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ".deployment.json"
WORK_DIR = ".deploy"
APPS = {
    # Plugin Application: the runner builds requirements/ and runs main.py with a trigger.
    "comparison_viewer_data_builder": ("main.py", "config", "requirements", "requirements.txt"),
    # Windows-local Viewer kept in the Registry next to its Builder; not run by the runner.
    "comparison_viewer": ("main.py", "engine.py", "contour_area.py", "requirements.txt", "web.html", "viewer.js", "viewer.css"),
}


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True,
                          capture_output=True, text=True).stdout


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_files(app: str) -> dict[str, Path]:
    prefix = f"applications/{app}/"
    names = [n for n in git("ls-files", "-z", "--", prefix).split("\0") if n]
    files = {}
    for name in names:
        path = ROOT / name
        if not path.is_file():
            raise SystemExit(f"Tracked file is missing from the working tree: {name}")
        files[name[len(prefix):]] = path
    if not files:
        raise SystemExit(f"No tracked files for {app}")
    return files


def validate(app: str, files: dict[str, Path]) -> None:
    for required in APPS[app]:
        if required not in files and not any(n.startswith(required + "/") for n in files):
            raise SystemExit(f"{app}: required entry is missing: {required}")
    for name, path in files.items():
        if name.endswith(".whl"):
            with path.open("rb") as stream:
                if stream.read(4) != b"PK\x03\x04":
                    raise SystemExit(f"{app}/{name} is not a wheel (Git LFS pointer?). Run: git lfs pull")


def current_files(target: Path) -> dict[str, str]:
    if not target.is_dir():
        return {}
    return {p.relative_to(target).as_posix(): sha256(p) for p in target.rglob("*")
            if p.is_file() and p.name != MANIFEST and "__pycache__" not in p.parts}


def deployed_commit(target: Path) -> str | None:
    try:
        return json.loads((target / MANIFEST).read_text())["commit"]
    except (OSError, ValueError, KeyError):
        return None


def plan(files: dict[str, Path], target: Path) -> dict[str, list[str]]:
    existing = current_files(target)
    wanted = {name: sha256(path) for name, path in files.items()}
    return dict(
        added=sorted(set(wanted) - set(existing)),
        changed=sorted(n for n in set(wanted) & set(existing) if wanted[n] != existing[n]),
        removed=sorted(set(existing) - set(wanted)),
    )


def stage(app: str, files: dict[str, Path], registry: Path, info: dict, stamp: str) -> Path:
    work = registry / WORK_DIR / app
    work.mkdir(parents=True, exist_ok=True)
    staging = work / f"staging-{stamp}"
    staging.mkdir()
    try:
        for name, path in files.items():
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        copied = {name: sha256(staging / name) for name in sorted(files)}
        expected = {name: sha256(path) for name, path in sorted(files.items())}
        if copied != expected:
            raise OSError(f"{app}: copied files differ from the source")
        manifest = dict(info, application=app, deployed_at=stamp, files=copied)
        (staging / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return staging


def swap(apps: dict[str, Path], registry: Path, stamp: str) -> dict[str, Path | None]:
    """Swap every staged app in; if any rename fails, restore all previous copies."""
    steps: list[tuple[str, Path | None, bool]] = []
    try:
        for app, staging in apps.items():
            target = registry / app
            backup = None
            if target.exists():
                backup = registry / WORK_DIR / app / f"backup-{stamp}"
                os.rename(target, backup)
            steps.append((app, backup, False))
            os.rename(staging, target)
            steps[-1] = (app, backup, True)
    except BaseException:
        for app, backup, installed in reversed(steps):
            if installed:
                os.rename(registry / app, apps[app])
            if backup is not None:
                os.rename(backup, registry / app)
        raise
    return {app: backup for app, backup, _ in steps}


def prune(app: str, registry: Path, keep: int) -> None:
    backups = sorted((registry / WORK_DIR / app).glob("backup-*"))
    for old in backups[:max(len(backups) - keep, 0)]:
        shutil.rmtree(old)


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--registry", type=Path, default=os.environ.get("PLUGIN_REGISTRY"),
                     help="Plugin Registry directory (default: $PLUGIN_REGISTRY)")
    cli.add_argument("--app", action="append", choices=sorted(APPS),
                     help="Application to deploy; repeatable (default: all)")
    cli.add_argument("--dry-run", action="store_true", help="Show the plan without writing")
    cli.add_argument("--allow-dirty", action="store_true",
                     help="Deploy uncommitted changes of tracked files (recorded as dirty)")
    cli.add_argument("--keep-backups", type=int, default=3)
    args = cli.parse_args()
    if args.registry is None:
        cli.error("--registry or PLUGIN_REGISTRY is required")
    registry = args.registry.resolve()
    if not registry.is_dir():
        cli.error(f"Registry directory does not exist: {registry}")
    if registry == ROOT or ROOT in registry.parents:
        cli.error("Registry must be outside this repository")

    dirty = bool(git("status", "--porcelain", "--untracked-files=no").strip())
    if dirty and not args.allow_dirty and not args.dry_run:
        cli.error("Working tree has uncommitted changes; commit them or use --allow-dirty")
    info = dict(
        repository=git("config", "--get", "remote.origin.url").strip() if git("remote").strip() else "",
        branch=git("rev-parse", "--abbrev-ref", "HEAD").strip(),
        commit=git("rev-parse", "HEAD").strip(),
        describe=git("describe", "--tags", "--always", "--dirty").strip(),
        dirty=dirty,
    )
    print(f"Source: {info['describe']} ({info['branch']}){' DIRTY' if dirty else ''}")
    print(f"Registry: {registry}")
    selected = {}
    for app in args.app or sorted(APPS):
        if (registry / app / "release.json").exists():
            cli.error(f"{app} is an installed release; use its release installer, not source deployment")
        files = source_files(app)
        validate(app, files)
        selected[app] = files
    pending = {}
    for app, files in selected.items():
        changes = plan(files, registry / app)
        summary = ", ".join(f"{k} {len(v)}" for k, v in changes.items())
        print(f"\n[{app}] {len(files)} files; {summary}")
        for kind, names in changes.items():
            for name in names:
                print(f"  {kind[0].upper()} {name}")
        if not any(changes.values()) and not dirty and deployed_commit(registry / app) == info["commit"]:
            print("  up to date; skipped")
        else:
            pending[app] = files
    if args.dry_run:
        print("\nDry run: nothing was written.")
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    staged = {}
    try:
        for app, files in pending.items():
            staged[app] = stage(app, files, registry, info, stamp)
        backups = swap(staged, registry, stamp)
    finally:
        for staging in staged.values():
            shutil.rmtree(staging, ignore_errors=True)
    for app, backup in backups.items():
        prune(app, registry, args.keep_backups)
        print(f"[{app}] deployed{f'; previous copy: {backup}' if backup else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
