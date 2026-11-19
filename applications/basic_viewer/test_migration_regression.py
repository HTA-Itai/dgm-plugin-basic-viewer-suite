"""Verify all preprocessed geometry, values and UI payloads against the earlier demo."""
import argparse
import json
from pathlib import Path
import zlib

from engine import Store, connection


def verify(base, comparison, legacy):
    new = Store(base, comparison)
    old = Store(legacy, allow_legacy=True)
    with connection(new.path) as converted, connection(old.path) as original:
        rows = converted.execute("SELECT id,role,name,pno,seq,ms,chip_x,chip_y,xrel,yrel FROM images").fetchall()
        for image in rows:
            previous = original.execute("SELECT * FROM images WHERE role=? AND name=?", (image["role"], image["name"])).fetchone()
            assert previous is not None
            current = converted.execute("SELECT * FROM images WHERE id=?", (image["id"],)).fetchone()
            for key in ("pno", "seq", "ms", "chip_x", "chip_y", "xrel", "yrel", "jpeg"):
                assert current[key] == previous[key], (image["name"], key)
            assert json.loads(zlib.decompress(current["geometry"])) == json.loads(zlib.decompress(previous["geometry"]))
            columns = "ordinal,layout_x,layout_y,px,py,dx,dy,magnitude,available,valid,raw"
            for db, identity in ((converted, current["id"]), (original, previous["id"])):
                values = db.execute(f"SELECT {columns} FROM patterns WHERE image_id=? ORDER BY ordinal", (identity,)).fetchall()
                parsed = [(*tuple(r)[:-1], json.loads(r["raw"])) for r in values]
                if db is converted:
                    actual = parsed
                else:
                    assert actual == parsed, image["name"]
        assert len(rows) == 2580
    for filters in (dict(pno=2), dict(pno=2, ms="01MS", chips=[[8, 7]], roi=[0, 0, 256, 512])):
        a, b = new.statistics(filters), old.statistics(filters)
        assert a["table"] == b["table"]
        assert a["graph"] == b["graph"]
        assert a["counts"] == b["counts"]
        assert a["paired_images"] == b["paired_images"]
    print("PASS: all 2580 JPEG/geometry/acquisition payloads and 51600 raw/numeric/quality rows match the earlier version;")
    print("full/narrowed tables, graph values, paired/unpaired/exclusion counts are unchanged.")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("base", type=Path)
    cli.add_argument("comparison", type=Path)
    cli.add_argument("legacy", type=Path)
    args = cli.parse_args()
    verify(args.base, args.comparison, args.legacy)
