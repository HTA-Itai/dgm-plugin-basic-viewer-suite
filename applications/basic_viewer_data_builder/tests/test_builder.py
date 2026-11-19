import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from PIL import Image
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from builder import BuildConfig, REQUIRED_COLUMNS, build_dataset


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        (self.source / "report").mkdir(parents=True)
        (self.source / "viewer").mkdir()
        self.output = self.root / "dataset"
        self.rows = [
            self.row("two.png", 20, center_x="", center_y=""),
            self.row("one.png", 10, center_x="11", center_y="9"),
            self.row("one.png", 11, center_x="0", center_y="0", flag="Necking"),
        ]
        self.write_csv()
        for name in ("one", "two"):
            self.write_ovlx(name)

    def tearDown(self):
        self.temporary.cleanup()

    def row(self, image, layout_x, *, center_x, center_y, flag="Normal"):
        return {
            "IMAGENAME": image, "SID": "", "ContourSuccess": "Yes", "DefectFlag": flag,
            "LayoutCoordX": str(layout_x), "LayoutCoordY": "10", "PixelX": "2", "PixelY": "3",
            "CenterX": center_x, "CenterY": center_y, "SizeX": "1.25", "Label": "日本語",
            "Count": "3", "Enabled": "true",
        }

    def write_csv(self, *, trailing=False):
        path = self.source / "report" / "result_Size_Area.csv"
        self.fields = REQUIRED_COLUMNS + ["SizeX", "Label", "Count", "Enabled"]
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(self.fields)
            for row in self.rows:
                writer.writerow([row[name] for name in self.fields] + ([""] if trailing else []))

    def write_ovlx(self, name, *, missing=None, condition=None, svg=None, design=None):
        image = io.BytesIO()
        Image.new("L", (16, 16)).save(image, format="JPEG")
        cond = condition or {
            "image": {"actual_size_x": 16, "actual_size_y": 16,
                      "nm_per_pixel_x": 0.659, "nm_per_pixel_y": 0.659},
            "matching_shift": {"matching_shift_x_nm": 1.5, "matching_shift_y_nm": -0.5,
                               "manual_shift_x_nm": 0.1, "manual_shift_y_nm": 0},
        }
        self.assets = {
            "image.jpeg": image.getvalue(),
            # The default matching shift moves this design pattern to source pixels 8..12.
            "design.svg": design or (b'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
                           b'<polygon data-design-type="0" points="18,15 22,15 22,19 18,19"/></svg>'),
            "contour.svg": svg or b'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"/>',
            "cond.json": json.dumps(cond).encode(),
        }
        with zipfile.ZipFile(self.source / "viewer" / f"{name}.ovlx", "w") as archive:
            for member, content in self.assets.items():
                if member != missing:
                    archive.writestr(member, content)

    def build(self, config=None, **kwargs):
        return build_dataset(self.source, self.output, config or BuildConfig(),
                             dataset_id="sample", **kwargs)

    def test_roundtrip_rowgroups_quality_assets_and_zip(self):
        self.rows.append({**self.rows[1], "SID": "1", "CenterX": "bad-not-selected"})
        self.write_csv(trailing=True)
        original_hash = hashlib.sha256((self.source / "report/result_Size_Area.csv").read_bytes()).hexdigest()
        config = BuildConfig.from_mapping({
            "AdditionalColumns": {
                "SizeX": {"type": "float64", "unit": "nm"},
                "Label": {"type": "string", "unit": ""},
                "Count": {"type": "int64", "unit": "count"},
                "Enabled": {"type": "bool", "unit": ""},
            },
        })
        result = self.build(config, create_zip=True)
        self.assertEqual(result["patterns"], 3)
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["statistics"]["excluded_sid_rows"], 1)
        self.assertEqual(manifest["statistics"]["empty_trailing_fields_normalized_rows"], 4)
        self.assertEqual(manifest["statistics"]["missing_center_rows"], 1)
        self.assertEqual(manifest["statistics"]["non_normal_rows"], 1)
        self.assertEqual(manifest["statistics"]["vector_valid_rows"], 1)
        self.assertEqual(manifest["source"]["csv_sha256"], original_hash)
        self.assertEqual(hashlib.sha256((self.source / "report/result_Size_Area.csv").read_bytes()).hexdigest(), original_hash)
        index = json.loads((self.output / "images.json").read_text())
        parquet = pq.ParquetFile(self.output / "measurements.parquet")
        self.assertEqual(parquet.num_row_groups, 2)
        self.assertEqual(parquet.read_row_group(0, columns=["CenterX"]).to_pydict(),
                         {"CenterX": [11.0, 0.0]})
        table = parquet.read()
        self.assertEqual(table["center_available"].to_pylist(), [True, True, False])
        self.assertEqual(table["vector_valid"].to_pylist(), [True, False, False])
        self.assertEqual(table["CenterX"].to_pylist(), [11.0, 0.0, None])
        self.assertEqual(table["Label"].to_pylist(), ["日本語"] * 3)
        self.assertEqual(table["Count"].to_pylist(), [3] * 3)
        self.assertEqual(table["Enabled"].to_pylist(), [True] * 3)
        self.assertEqual(table.schema.field("CenterX").metadata[b"unit"], b"nm")
        self.assertEqual(table.schema.field("PixelX").metadata[b"unit"], b"pixel")
        for image in index:
            for member, expected in self.assets.items():
                self.assertEqual((self.output / image["directory"] / member).read_bytes(), expected)
        with zipfile.ZipFile(self.output.with_suffix(".zip")) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn("dataset/manifest.json", archive.namelist())
            self.assertEqual(archive.read("dataset/manifest.json"), (self.output / "manifest.json").read_bytes())

    def test_duplicate_numeric_key_rejected_without_partial_output(self):
        self.rows.append({**self.rows[1], "LayoutCoordX": "10.000"})
        self.write_csv()
        with self.assertRaisesRegex(ValueError, "Duplicate pattern"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_missing_and_invalid_csv_data(self):
        for column, value in (("PixelX", ""), ("CenterX", "bad"), ("CenterX", "inf"),
                              ("LayoutCoordY", ""), ("IMAGENAME", "../one.png")):
            with self.subTest(column=column, value=value):
                original = self.rows[1].copy()
                self.rows[1][column] = value
                self.write_csv()
                with self.assertRaises(ValueError):
                    self.build()
                self.rows[1] = original
                self.assertFalse(self.output.exists())

    def test_partial_center_and_failed_contour_are_retained(self):
        self.rows[0]["CenterY"] = "10"
        self.rows[1]["ContourSuccess"] = "No"
        self.write_csv()
        self.build()
        table = pq.read_table(self.output / "measurements.parquet")
        self.assertEqual(table["vector_valid"].to_pylist(), [False, False, False])
        self.assertEqual(table["CenterY"].to_pylist()[-1], 10.0)

    def test_no_selected_rows_is_error(self):
        for row in self.rows:
            row["SID"] = "1"
        self.write_csv()
        with self.assertRaisesRegex(ValueError, "No patterns"):
            self.build()

    def test_missing_column_and_nonempty_trailing_fields(self):
        path = self.source / "report/result_Size_Area.csv"
        path.write_text("IMAGENAME,SID\none.png,\n")
        with self.assertRaisesRegex(ValueError, "missing required"):
            self.build()
        self.write_csv()
        with path.open("a") as stream:
            stream.write(",".join(["x"] * (len(self.fields) + 1)) + "\n")
        with self.assertRaisesRegex(ValueError, "unnamed trailing"):
            self.build()

    def test_missing_corrupt_or_incomplete_ovlx_leaves_no_output(self):
        for mode in ("missing", "corrupt", "member", "svg", "metadata"):
            with self.subTest(mode=mode):
                path = self.source / "viewer/two.ovlx"
                if mode == "missing":
                    path.unlink()
                elif mode == "corrupt":
                    path.write_bytes(b"not zip")
                elif mode == "member":
                    self.write_ovlx("two", missing="contour.svg")
                elif mode == "svg":
                    self.write_ovlx("two", svg=b"<invalid/>")
                else:
                    self.write_ovlx("two", condition={"image": {}, "matching_shift": {}})
                with self.assertRaises((ValueError, FileNotFoundError, zipfile.BadZipFile)):
                    self.build(create_zip=True)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.output.with_suffix(".zip").exists())
                self.assertFalse(list(self.root.glob(".dataset-*")))
                self.write_ovlx("two")

    def test_existing_output_and_nested_output_rejected(self):
        self.output.mkdir()
        marker = self.output / "keep"
        marker.write_text("untouched")
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual(marker.read_text(), "untouched")
        with self.assertRaisesRegex(ValueError, "non-nested"):
            build_dataset(self.source, self.source / "generated", BuildConfig(), dataset_id="sample")

    def test_configuration_rejects_bad_types_and_reserved_columns(self):
        for settings in (None, {"Unknown": 1}, {"CreateZip": "false"}, {"TargetCSV": "../x.csv"},
                         {"AdditionalColumns": ["x"]},
                         {"AdditionalColumns": {"CenterX": {"type": "string", "unit": ""}}},
                         {"AdditionalColumns": {"Other": {"type": "unknown", "unit": ""}}}):
            with self.subTest(settings=settings):
                with self.assertRaises(ValueError):
                    BuildConfig.from_mapping(settings)

    def test_missing_configured_column_is_error(self):
        config = BuildConfig.from_mapping({"AdditionalColumns": {"missing": {"type": "float64", "unit": "nm"}}})
        with self.assertRaisesRegex(ValueError, "missing required/configured"):
            self.build(config)

    def test_cli_and_plugin_trigger(self):
        app = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, str(app / "main.py"), "--source", str(self.source),
             "--output", str(self.output), "--zip", "--format", "parquet"],
            check=True, capture_output=True, text=True,
        )
        self.assertIn("Completed dataset", result.stderr)
        (self.root / "input").mkdir()
        shutil.copytree(self.source, self.root / "input" / "sample")
        (self.root / "config").mkdir()
        (self.root / "config/vector.yml").write_text("TargetCSV: result_Size_Area.csv\n")
        (self.root / "trigger.json").write_text(json.dumps({"TargetData": "sample", "Msr": "test-msr"}))
        subprocess.run([sys.executable, str(app / "main.py"), "trigger.json", "--format", "parquet"],
                       cwd=self.root, check=True, capture_output=True, text=True)
        manifest = json.loads((self.root / "output/dataset/manifest.json").read_text())
        self.assertEqual(manifest["dataset_id"], "test-msr")
        (self.root / "trigger.json").write_text(json.dumps({"TargetData": "../source"}))
        result = subprocess.run([sys.executable, str(app / "main.py"), "trigger.json"],
                                cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("inside input", result.stderr)


if __name__ == "__main__":
    unittest.main()
