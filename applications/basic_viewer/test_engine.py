import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from engine import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "basic.sqlite"
        db = sqlite3.connect(self.path)
        db.executescript("""
            PRAGMA user_version=1;
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE images(id INTEGER PRIMARY KEY,name TEXT NOT NULL,pno INTEGER NOT NULL,
                seq INTEGER NOT NULL,ms TEXT NOT NULL,chip_x INTEGER NOT NULL,chip_y INTEGER NOT NULL,
                xrel REAL NOT NULL,yrel REAL NOT NULL,jpeg BLOB NOT NULL);
            CREATE TABLE patterns(image_id INTEGER NOT NULL,ordinal INTEGER NOT NULL,
                layout_x REAL,layout_y REAL,pixel_x REAL,pixel_y REAL,
                center_x REAL,center_y REAL,dx REAL,dy REAL,magnitude REAL,
                available INTEGER NOT NULL,valid INTEGER NOT NULL,edge_cut INTEGER NOT NULL,
                raw TEXT NOT NULL,PRIMARY KEY(image_id,ordinal));
            CREATE TABLE metrics(name TEXT PRIMARY KEY,unit TEXT NOT NULL);
        """)
        manifest = {
            "format": "basic-viewer-sqlite",
            "format_version": 1,
            "dataset_id": "test",
            "metrics": ["Area"],
            "images": 1,
            "patterns": 4,
        }
        db.execute("INSERT INTO metadata VALUES(?,?)", ("manifest", json.dumps(manifest)))
        db.execute("INSERT INTO images VALUES(1,'one-02MS.png',2,1,'02MS',0,0,10,20,?)", (b"jpeg",))
        db.execute("INSERT INTO metrics VALUES('Area','')")
        for ordinal, (lx, ly, area) in enumerate(((100, 100, 7000), (200, 100, 7100), (100, 200, 7200), (200, 200, 7300))):
            db.execute(
                "INSERT INTO patterns VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ordinal, lx, ly, 50, 50, lx, ly, 0, 0, 0, 1, 1, 0, json.dumps({"Area": area})),
            )
        db.commit()
        db.close()
        self.store = Store(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_index_and_statistics(self):
        index = self.store.index()
        self.assertEqual(index["pattern_count"], 4)
        self.assertEqual(index["metrics"][0]["name"], "Area")
        stats = self.store.statistics("Area")
        self.assertEqual(stats["groups"]["Ungrouped"]["n"], 4)

    def test_apply_layout_group_rule(self):
        rule = {
            "format": "basic-viewer-grouping",
            "version": 2,
            "coords": {"x": {"source": "layout", "tolerance_nm": 1}, "y": {"source": "layout", "tolerance_nm": 1}},
            "groups": {"Left": {"select": [{"x": [100], "y": [100, 200]}]}},
        }
        result = self.store.apply_rule(rule)
        self.assertEqual(result["groups"]["Left"], 2)
        stats = self.store.statistics("Area")
        self.assertEqual(stats["groups"]["Left"]["n"], 2)


if __name__ == "__main__":
    unittest.main()
