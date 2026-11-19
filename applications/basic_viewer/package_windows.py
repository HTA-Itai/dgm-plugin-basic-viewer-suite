"""Build a Windows package with a frozen Python runtime and PySide6."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile


ROOT = Path(__file__).resolve().parent
APP_FILES = (
    "main.py",
    "engine.py",
    "requirements.txt",
    "web.html",
    "viewer.js",
    "viewer.css",
    "grouping_editor.html",
)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build(downloads: Path, output: Path) -> dict:
    downloads = downloads.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError(output)

    embed = downloads / "python-3.12.10-embed-amd64.zip"
    if not embed.is_file():
        raise FileNotFoundError(f"Missing {embed}")
    wheels = {
        "PySide6": downloads / "PySide6-6.8.3-cp39-abi3-win_amd64.whl",
        "PySide6-Essentials": downloads / "PySide6_Essentials-6.8.3-cp39-abi3-win_amd64.whl",
        "PySide6-Addons": downloads / "PySide6_Addons-6.8.3-cp39-abi3-win_amd64.whl",
        "shiboken6": downloads / "shiboken6-6.8.3-cp39-abi3-win_amd64.whl",
        "PyYAML": downloads / "PyYAML-6.0.2-cp312-cp312-win_amd64.whl",
    }
    missing = [str(path) for path in wheels.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing Windows wheels: " + ", ".join(missing))

    output.mkdir(parents=True)
    app = output / "app"
    runtime = output / "runtime"
    app.mkdir()
    runtime.mkdir()

    with zipfile.ZipFile(embed) as archive:
        archive.extractall(runtime)

    packages = runtime / "Lib/site-packages"
    packages.mkdir(parents=True)
    for wheel in wheels.values():
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(packages)

    (runtime / "python312._pth").write_text(
        "python312.zip\n.\nLib/site-packages\n../app\n",
        encoding="ascii",
    )
    for name in APP_FILES:
        shutil.copy2(ROOT / name, app / name)
    shutil.copy2(ROOT / "USER_GUIDE.md", output / "USER_GUIDE.md")
    shutil.copy2(ROOT / "start-viewer.cmd", output / "start-viewer.cmd")

    manifest = {
        "target": "Windows 10/11 x64",
        "python": "3.12.10 embed",
        "pyside": "6.8.3",
        "pyyaml": "6.0.2",
        "dataset_format": "basic-viewer-sqlite",
        "dataset_version": 1,
        "datasets_included": False,
        "runtime_artifacts": [
            {"name": path.name, "sha256": sha(path)}
            for path in [embed, *wheels.values()]
        ],
    }
    (output / "distribution.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )

    files = sorted(path for path in output.rglob("*") if path.is_file())
    hashes = {path.relative_to(output).as_posix(): sha(path) for path in files}
    (output / "SHA256SUMS.txt").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in hashes.items()),
        encoding="ascii",
    )

    archive_path = output.parent / f"{output.name}-windows.zip"
    with zipfile.ZipFile(archive_path, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output.parent))

    report = {
        "zip": str(archive_path),
        "zip_bytes": archive_path.stat().st_size,
        "extracted_bytes": sum(path.stat().st_size for path in files),
        "dataset_format": "basic-viewer-sqlite",
        "dataset_version": 1,
        "datasets_included": False,
    }
    (output.parent / f"{output.name}-package-report.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--downloads", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    print(json.dumps(build(args.downloads, args.output), indent=2))
