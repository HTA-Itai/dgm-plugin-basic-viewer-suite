import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


class UserGuidePdfTest(unittest.TestCase):
    def test_pdf_built_from_current_guide(self):
        try:
            from PySide6.QtPdf import QPdfDocument
            from PySide6.QtGui import QGuiApplication
        except ImportError:
            self.skipTest("PySide6 is not installed")
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "USER_GUIDE.pdf"
            subprocess.run([sys.executable, str(ROOT / "build_user_guide_pdf.py"), "--output", str(target)],
                           check=True)
            app = QGuiApplication.instance() or QGuiApplication(["test"])
            document = QPdfDocument()
            document.load(str(target))
            digest = hashlib.sha256((ROOT / "USER_GUIDE.md").read_bytes()).hexdigest()
            self.assertEqual(document.metaData(QPdfDocument.MetaDataField.Creator), "USER_GUIDE.md sha256 " + digest)
            self.assertEqual(document.metaData(QPdfDocument.MetaDataField.Title), "Comparison Viewer - User Guide")
            self.assertGreaterEqual(document.pageCount(), 3)
            text = document.getAllText(0).text()
            self.assertIn("Comparison Viewer User Guide", text)
            document.close()
            del app

    def test_missing_guide_image_fails(self):
        try:
            import PySide6  # noqa: F401
        except ImportError:
            self.skipTest("PySide6 is not installed")
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "guide.md"
            source.write_text("# Guide\n\n![missing](missing.png)\n", encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "build_user_guide_pdf.py"), "--source", str(source),
                                     "--output", str(Path(folder) / "guide.pdf")], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("USER_GUIDE image not found", result.stderr)


if __name__ == "__main__":
    unittest.main()
