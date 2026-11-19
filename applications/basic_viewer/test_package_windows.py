import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import package_windows


ASSETS = Path(__import__("os").environ.get(
    "BASIC_VIEWER_WINDOWS_ASSETS",
    "/mnt/c/Users/hitai/Downloads/basic-viewer-build-assets",
))


class WindowsPackageTests(unittest.TestCase):
    def test_build_package(self):
        if not ASSETS.is_dir():
            self.skipTest("Windows package assets are not mounted")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "basic-viewer-windows"
            report = package_windows.build(ASSETS, output)
            self.assertTrue((output / "runtime/python.exe").is_file())
            self.assertTrue((output / "start-viewer.cmd").is_file())
            self.assertTrue((output / "app/main.py").is_file())
            self.assertTrue((output / "USER_GUIDE.md").is_file())
            self.assertTrue((output / "SHA256SUMS.txt").is_file())
            manifest = json.loads((output / "distribution.json").read_text())
            self.assertFalse(manifest["datasets_included"])
            archive = Path(report["zip"])
            with zipfile.ZipFile(archive) as zf:
                self.assertIsNone(zf.testzip())
                self.assertIn("basic-viewer-windows/start-viewer.cmd", zf.namelist())


if __name__ == "__main__":
    unittest.main()
