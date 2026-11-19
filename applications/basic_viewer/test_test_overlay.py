import json
import sqlite3
import unittest

from engine import Store
from prepare_test_overlay import prepare
import test_engine


class TestChipOverride(unittest.TestCase):
    def setUp(self):
        self.fixture = test_engine.EngineTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.source = self.fixture.path
        self.output = self.source.parent / "synthetic.sqlite"
        with sqlite3.connect(self.source) as db:
            db.execute("DELETE FROM images WHERE id=3")
            db.execute("DELETE FROM patterns WHERE image_id=3")
            db.execute("UPDATE images SET chip_x=6,chip_y=4 WHERE role='comparison'")
        self.before = self.source.read_bytes()

    def test_isolated_remap_and_positive_overlay(self):
        self.assertEqual(Store(self.source, allow_legacy=True).image(1)["vectors"]["comparison"], [])
        report = prepare(self.source, self.output)
        self.assertEqual(self.source.read_bytes(), self.before)
        self.assertEqual(report["mapping"][0]["original_comparison_chip"], [6, 4])
        self.assertEqual(report["mapping"][0]["synthetic_chip"], [8, 7])
        self.assertEqual(report["overlay_images"], 1)
        image = Store(self.output, allow_legacy=True).image(1)
        self.assertEqual(image["matched"], 4)
        self.assertEqual(len(image["vectors"]["comparison"]), 2)
        self.assertTrue(image["rows"]["comparison"][0]["TEST_ONLY_CHIP_OVERRIDE"])
        self.assertEqual(image["rows"]["comparison"][0]["TEST_ORIGINAL_XINDEX"], 6)
        self.assertEqual(image["rows"]["comparison"][0]["XINDEX"], "8")
        self.assertIn("TEST ONLY", Store(self.output, allow_legacy=True).index()["metadata"]["test_chip_override"]["warning"])

    def set_pids(self, role, pid):
        with sqlite3.connect(self.source) as db:
            for image_id, ordinal, raw in db.execute(
                    "SELECT p.image_id,p.ordinal,p.raw FROM patterns p JOIN images i ON i.id=p.image_id "
                    "WHERE i.role=?", (role,)).fetchall():
                db.execute("UPDATE patterns SET raw=json_set(raw,'$.PID',?) WHERE image_id=? AND ordinal=?",
                           (pid(json.loads(raw)["PID"]), image_id, ordinal))

    def test_overlay_detection_normalizes_pid_like_viewer(self):
        self.set_pids("comparison", lambda pid: f" {pid} ")
        self.assertEqual(prepare(self.source, self.output)["overlay_images"], 1)

    def test_empty_or_repeated_pids_are_not_overlays(self):
        for pid in (lambda _: "", lambda _: "7"):
            with self.subTest(pid=pid("1")):
                self.set_pids("base", pid)
                self.set_pids("comparison", pid)
                with self.assertRaisesRegex(ValueError, "common-PID"):
                    prepare(self.source, self.output)
                self.output.unlink(missing_ok=True)

    def test_existing_destination_rejected(self):
        self.output.touch()
        with self.assertRaises(FileExistsError):
            prepare(self.source, self.output)

    def test_ambiguous_acquisition_rejected(self):
        with sqlite3.connect(self.source) as db:
            db.execute("INSERT INTO images SELECT 4,role,name,pno,seq,ms,9,7,xrel,yrel,jpeg,geometry FROM images WHERE id=1")
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            prepare(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_repeat_remapping_rejected(self):
        prepare(self.source, self.output)
        with self.assertRaisesRegex(ValueError, "already"):
            prepare(self.output, self.source.parent / "repeat.sqlite")


if __name__ == "__main__":
    unittest.main()
