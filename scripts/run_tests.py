#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def interpreter(value: str) -> str:
    return str(Path(value).absolute()) if os.sep in value or (os.altsep and os.altsep in value) else value


def main() -> int:
    cli = argparse.ArgumentParser()
    cli.add_argument("--builder-python", default=sys.executable)
    cli.add_argument("--viewer-python", default=sys.executable)
    args = cli.parse_args()
    suites = [
        ("builder", interpreter(args.builder_python), ROOT / "applications/basic_viewer_data_builder", "test_*.py"),
        ("viewer", interpreter(args.viewer_python), ROOT / "applications/basic_viewer", "test_*.py"),
        ("integration", interpreter(args.builder_python), ROOT / "tests/integration", "test_*.py"),
    ]
    failed = []
    for name, python, directory, pattern in suites:
        print(f"== {name} ({python})", flush=True)
        result = subprocess.run(
            [python, "-m", "unittest", "discover", "-s", str(directory), "-p", pattern],
            cwd=directory,
        )
        if result.returncode:
            failed.append(name)
    print("FAILED: " + ", ".join(failed) if failed else "All suites passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
