#!/usr/bin/env python3
"""Run Builder, Viewer, Builder-to-Viewer integration and script tests.

The Builder needs its pinned wheels (pyarrow, Pillow, PyYAML) and the Viewer
tests may need PySide6, so each suite can use a different interpreter.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def interpreter(value: str) -> str:
    # Suites run with their own cwd, so path-like interpreters are anchored to the caller's cwd.
    # absolute() keeps venv symlinks intact, unlike resolve().
    if os.sep in value or (os.altsep and os.altsep in value):
        return str(Path(value).absolute())
    return value


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--builder-python", default=sys.executable)
    cli.add_argument("--viewer-python", default=sys.executable)
    args = cli.parse_args()
    builder, viewer = interpreter(args.builder_python), interpreter(args.viewer_python)
    suites = [
        ("builder", builder, ROOT / "applications/comparison_viewer_data_builder/tests", "test_*.py"),
        ("viewer", viewer, ROOT / "applications/comparison_viewer", "test_*.py"),
        ("integration", builder, ROOT / "tests/integration", "test_*.py"),
        ("scripts", sys.executable, ROOT / "tests/scripts", "test_*.py"),
    ]
    failed = []
    for name, python, directory, pattern in suites:
        print(f"== {name} ({python})", flush=True)
        result = subprocess.run([python, "-m", "unittest", "discover", "-s", str(directory), "-p", pattern],
                                cwd=directory)
        if result.returncode:
            failed.append(name)
    print("FAILED: " + ", ".join(failed) if failed else "All suites passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
