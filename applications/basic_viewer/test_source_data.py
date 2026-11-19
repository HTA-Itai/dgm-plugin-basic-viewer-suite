"""Read-only, all-row regression against the two original source CSVs and JPEGs."""
import argparse
import csv
import json
import math
import os
from pathlib import Path
import zipfile
from engine import Store, connection


def verify(database, source, comparison=None):
    with connection(Store(database, comparison).path) as db:
        meta = {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM metadata")}
        total, assets = 0, 0
        valid_counts = {}
        for role, dataset in meta["datasets"].items():
            path = next((source / "extensions" / dataset).glob("*/report/result_Size_Area.csv"))
            expected = {}
            with path.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    if not row["SID"].strip():
                        expected.setdefault(row["IMAGENAME"], []).append(
                            {k: v for k, v in row.items() if k is not None})
            images = db.execute("SELECT * FROM images WHERE role=?", (role,)).fetchall()
            assert len(images) == len(expected) == 1290
            valid_counts[role] = 0
            mask = {(c["x"], c["y"]) for c in meta["layouts"][role]["cells"] if c["code"] != 0}
            for image in images:
                rows = db.execute("SELECT * FROM patterns WHERE image_id=? ORDER BY ordinal", (image["id"],)).fetchall()
                assert len(rows) == len(expected[image["name"]])
                assert (image["chip_x"], image["chip_y"]) in mask
                for actual, original in zip(rows, expected[image["name"]]):
                    assert json.loads(actual["raw"]) == original
                    for target, key in [("layout_x", "LayoutCoordX"), ("layout_y", "LayoutCoordY"),
                                        ("px", "PixelX"), ("py", "PixelY")]:
                        assert actual[target] == float(original[key])
                    available = bool(original["CenterX"].strip() and original["CenterY"].strip())
                    valid = available and original["DefectFlag"] == "Normal" and original["ContourSuccess"] == "Yes"
                    assert bool(actual["available"]) == available and bool(actual["valid"]) == valid
                    valid_counts[role] += valid
                    if available:
                        dx = float(original["CenterX"]) - float(original["LayoutCoordX"])
                        dy = float(original["CenterY"]) - float(original["LayoutCoordY"])
                        assert (actual["dx"], actual["dy"]) == (dx, dy)
                        assert actual["magnitude"] == math.hypot(dx, dy)
                    else:
                        assert actual["dx"] is None and actual["dy"] is None
                    assert image["pno"] == int(original["PNO"])
                    assert image["seq"] == int(original["SEQUENCENO"])
                    assert image["chip_x"] == int(original["XINDEX"])
                    assert image["chip_y"] == int(original["YINDEX"])
                    assert (image["xrel"], image["yrel"]) == (float(original["XREL"]), float(original["YREL"]))
                    total += 1
                with zipfile.ZipFile(path.parent.parent / "viewer" / image["name"].replace(".png", ".ovlx")) as archive:
                    assert image["jpeg"] == archive.read("image.jpeg")
                    assets += 1
                    if comparison is not None:
                        schema = "base_data" if role == "base" else "comparison_data"
                        local = db.execute("SELECT local_id FROM identities WHERE id=?", (image["id"],)).fetchone()[0]
                        for member in ("design.svg", "contour.svg", "cond.json"):
                            assert db.execute(f"SELECT content FROM {schema}.assets WHERE image_id=? AND name=?",
                                              (local, member)).fetchone()[0] == archive.read(member)
        assert total == 51600 and assets == 2580
        assert valid_counts["base"] == 22246
        if comparison is None:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        else:
            for schema in ("base_data", "comparison_data"):
                assert db.execute(f"PRAGMA {schema}.integrity_check").fetchone()[0] == "ok"
        print("PASS: all 51600 raw/numeric/quality/identity rows, 2580 byte-identical JPEGs,")
        print("MSR mask contains all measured chips, SQLite integrity; valid counts", valid_counts)


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("database", type=Path)
    cli.add_argument("--comparison", type=Path)
    cli.add_argument("--source", type=Path,
                     help="Raw data directory; defaults to $PLUGIN_ROOT/Input_nfs/GT2000/2D_Micron")
    args = cli.parse_args()
    if args.source is None:
        if "PLUGIN_ROOT" not in os.environ:
            cli.error("--source or PLUGIN_ROOT is required")
        args.source = Path(os.environ["PLUGIN_ROOT"]) / "Input_nfs/GT2000/2D_Micron"
    verify(args.database, args.source, args.comparison)
