"""Developer packager; collect Qt PE dependencies rather than all Qt modules."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent
MODULES = {"QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtPrintSupport",
           "QtWebChannel", "QtWebEngineCore", "QtWebEngineWidgets"}
APP_FILES = ("main.py", "engine.py", "contour_area.py", "requirements.txt", "web.html", "viewer.js", "viewer.css")
SOURCE_FILES = (*APP_FILES, "USER_GUIDE.md", "start-viewer.cmd", "package_windows.py", "build_user_guide_pdf.py")


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_hashes(root=ROOT):
    names = set(SOURCE_FILES)
    for name in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", (root / "USER_GUIDE.md").read_text(encoding="utf-8")):
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError(f"Guide image must remain inside the Viewer source: {name}")
        names.add(path.relative_to(root.resolve()).as_posix())
    return {name: sha(root / name) for name in sorted(names)}


def dependencies(path):
    text = subprocess.run(["objdump", "-p", str(path)], check=True,
                          capture_output=True, text=True).stdout
    return re.findall(r"DLL Name:\s*(\S+)", text, re.IGNORECASE)


def build(downloads, output, guide_python, release_version=None):
    app = output / "app"
    app.mkdir(parents=True, exist_ok=False)
    runtime = output / "runtime"
    runtime.mkdir(exist_ok=False)
    original = downloads / "python-3.12.10-embed-amd64.zip"
    with zipfile.ZipFile(original) as archive:
        assert archive.testzip() is None
        archive.extractall(runtime)
    staging = output / "_wheel_staging"
    staging.mkdir(exist_ok=False)
    packages = runtime / "Lib/site-packages"
    qt = packages / "PySide6"
    wheels = sorted(downloads.glob("*6.8.3*win_amd64.whl"))
    if len(wheels) != 4:
        raise ValueError("Expected the four pinned Windows Qt wheels")
    for package in ("numpy", "shapely"):
        pins = re.findall(rf"^{package}==([0-9.]+)$", (ROOT / "requirements.txt").read_text(), re.MULTILINE)
        if len(pins) != 1:
            raise ValueError(f"Expected one pinned {package} requirement")
        wheel = downloads / f"{package}-{pins[0]}-cp312-cp312-win_amd64.whl"
        if not wheel.is_file():
            raise FileNotFoundError(f"Download the pinned Windows wheel first: {wheel.name}")
        wheels.append(wheel)
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            assert archive.testzip() is None
            archive.extractall(staging)
    selected = set()
    for path in staging.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(staging).parts
        keep = (
            ".dist-info" in parts[0] or parts[0] in {"shiboken6", "numpy", "numpy.libs", "shapely", "shapely.libs"}
            or parts[0] == "PySide6" and (
                len(parts) == 2 and path.suffix == ".py"
                or path.stem in MODULES and path.suffix == ".pyd"
                or path.name == "QtWebEngineProcess.exe"
                or "resources" in parts and ".debug." not in path.name
                or "qtwebengine_locales" in parts and path.name in {"ja.pak", "en-US.pak"}
                or "plugins" in parts and parts[-2] in {"imageformats", "iconengines", "styles", "tls"}
                or path.name == "qwindows.dll"
                or len(parts) == 2 and path.suffix == ".dll" and not path.name.startswith("Qt6")
            )
        )
        if keep:
            selected.add(path)
    binaries = {p.name.lower(): p for p in staging.rglob("*")
                if p.is_file() and p.suffix.lower() in {".dll", ".pyd"}}
    system_dependencies = set()
    queue = [p for p in selected if p.suffix.lower() in {".exe", ".dll", ".pyd"}]
    visited = set()
    while queue:
        path = queue.pop()
        if path in visited:
            continue
        visited.add(path)
        for name in dependencies(path):
            candidate = binaries.get(name.lower())
            if candidate is not None:
                if candidate not in selected:
                    selected.add(candidate)
                    queue.append(candidate)
            elif (runtime / name).is_file() or name.lower() in {p.name.lower() for p in runtime.iterdir()}:
                continue
            else:
                if name.lower().startswith(("qt6", "pyside", "shiboken", "msvcp", "vcruntime", "geos", "libscipy_openblas")):
                    raise ValueError(f"Unresolved bundled dependency: {path.name} → {name}")
                system_dependencies.add(name)
    for path in selected:
        target = packages / path.relative_to(staging)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    # Staging contains only the named wheel extracts created above, never user data.
    shutil.rmtree(staging)
    (runtime / "python312._pth").write_text("python312.zip\n.\nLib/site-packages\n../app\n", encoding="ascii")
    for name in APP_FILES:
        shutil.copy2(ROOT / name, app / name)
    subprocess.run([str(guide_python), str(ROOT / "build_user_guide_pdf.py"),
                    "--output", str(output / "USER_GUIDE.pdf")], check=True)
    shutil.copy2(ROOT / "start-viewer.cmd", output / "start-viewer.cmd")
    launcher = output / "start-viewer.cmd"
    launcher.write_bytes(launcher.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    for name in ("QtWebEngineProcess.exe", "plugins/platforms/qwindows.dll",
                 "resources/icudtl.dat", "resources/qtwebengine_resources.pak"):
        assert (qt / name).is_file(), name
    manifest = dict(
        target="Windows 10/11 x64", windows_execution_tested=False,
        packages=[dict(name=p.name, sha256=sha(p)) for p in [original, *wheels]],
        binding_modules=sorted(MODULES),
        collected_binaries=len(visited), system_imports=sorted(system_dependencies),
        dataset_format="plugin-viewer-sqlite", dataset_version=4, datasets_included=False,
        release_version=release_version,
        source_files=source_hashes(),
        note="PE dependency closure is a static check, not a Windows runtime test.",
    )
    (output / "distribution.json").write_text(json.dumps(manifest, indent=2)+"\n")
    files = sorted(p for p in output.rglob("*") if p.is_file())
    hashes = {p.relative_to(output).as_posix(): sha(p) for p in files}
    (output / "SHA256SUMS.txt").write_text("".join(f"{h}  {n}\n" for n, h in hashes.items()))
    archive_path = output.parent / (output.name + "-windows.zip")
    with zipfile.ZipFile(archive_path, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in [*files, output / "SHA256SUMS.txt"]:
            archive.write(path, path.relative_to(output.parent))
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        for name, expected in hashes.items():
            with archive.open(output.name + "/" + name) as stream:
                assert hashlib.file_digest(stream, "sha256").hexdigest() == expected, name
    report = dict(zip_bytes=archive_path.stat().st_size, extracted_files=len(files)+1,
                  uncompressed_bytes=sum(p.stat().st_size for p in files),
                  runtime_files=sum(p.is_file() for p in runtime.rglob("*")),
                  runtime_bytes=sum(p.stat().st_size for p in runtime.rglob("*") if p.is_file()),
                  sha256=sha(archive_path))
    print(json.dumps(report, indent=2))
    (output.parent / (output.name + "-package-report.json")).write_text(json.dumps(report, indent=2)+"\n")


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--downloads", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--guide-python", type=Path, required=True,
                     help="Python with PySide6 on this machine; renders USER_GUIDE.md to USER_GUIDE.pdf")
    cli.add_argument("--release-version", help="Suite version, e.g. 1.0.0 (required for release packaging)")
    args = cli.parse_args()
    build(args.downloads, args.output, args.guide_python, args.release_version)
