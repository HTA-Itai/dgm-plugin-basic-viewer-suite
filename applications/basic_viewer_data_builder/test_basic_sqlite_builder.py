import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
import zipfile

from basic_sqlite_builder import build_basic_sqlite


class BasicSQLiteBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / "source"
        (self.source / "report").mkdir(parents=True)
        (self.source / "viewer").mkdir()
        with (self.source / "report/result.csv").open("w", newline="") as stream:
            stream.write("IMAGENAME,PNO,SEQUENCENO,XINDEX,YINDEX,XREL,YREL,LayoutCoordX,LayoutCoordY,PixelX,PixelY,CenterX,CenterY,Area,DefectFlag,ContourSuccess,ReferenceArea,SID\n")
            stream.write("one-02MS.png,2,1,0,0,10,20,100,200,50,60,101,201,7000,Normal,Yes,,\n")
            stream.write("one-02MS.png,2,1,0,0,10,20,100,200,50,60,101,201,7000,Normal,Yes,,1\n")
        data = json.dumps({
            "image": {"actual_size_x": 16, "actual_size_y": 16},
            "matching_shift": {},
        }).encode()
        with zipfile.ZipFile(self.source / "viewer/one-02MS.ovlx", "w") as archive:
            archive.writestr("image.jpeg", b"jpeg")
            archive.writestr("design.svg", b"<svg/>")
            archive.writestr("contour.svg", b"<svg/>")
            archive.writestr("cond.json", data)

    def tearDown(self):
        self.temp.cleanup()

    def test_builder_excludes_sid_rows_and_writes_manifest(self):
        output = Path(self.temp.name) / "basic.sqlite"
        result = build_basic_sqlite(self.source, output, "test", "result.csv")
        self.assertEqual(result["patterns"], 1)
        self.assertEqual(result["images"], 1)
        db = sqlite3.connect(output)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM patterns").fetchone()[0], 1)
        manifest = json.loads(db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
        self.assertEqual(manifest["format"], "basic-viewer-sqlite")
        db.close()

    def test_output_must_not_exist(self):
        output = Path(self.temp.name) / "basic.sqlite"
        build_basic_sqlite(self.source, output, "test", "result.csv")
        with self.assertRaises(FileExistsError):
            build_basic_sqlite(self.source, output, "test", "result.csv")


if __name__ == "__main__":
    unittest.main()
