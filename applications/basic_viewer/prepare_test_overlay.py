"""Create a visibly marked, synthetic chip-remapped copy; never alter source data."""
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from engine import Store, by_pid, connection


def common_pids(db, base_id: int, comparison_id: int) -> int:
    """Number of PIDs the Viewer would pair (engine.by_pid rules) between two images."""
    lookups = [by_pid(db.execute("SELECT json_extract(raw,'$.PID') pid FROM patterns WHERE image_id=?",
                                 (image_id,)).fetchall()) for image_id in (base_id, comparison_id)]
    return len(lookups[0].keys() & lookups[1].keys())


WARNING = ("TEST ONLY: Comparison chip coordinates are artificially remapped to Base. "
           "These are different measured wafer locations. Not a validated same-site comparison.")


def prepare(source: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    store = Store(source, allow_legacy=True)
    entries = store.index()["images"]
    by_key = {}
    mapping = {}
    reverse = {}
    evidence = defaultdict(int)
    for entry in entries:
        key = (entry["role"], entry["pno"], entry["seq"], entry["ms"])
        if key in by_key:
            raise ValueError(f"Ambiguous acquisition key: {key}")
        by_key[key] = entry
    for entry in entries:
        if entry["role"] != "comparison":
            continue
        base = by_key.get(("base", entry["pno"], entry["seq"], entry["ms"]))
        if base is None:
            continue
        original = (entry["chip_x"], entry["chip_y"])
        target = (base["chip_x"], base["chip_y"])
        if original in mapping and mapping[original] != target:
            raise ValueError(f"Inconsistent test chip mapping for {original}")
        if target in reverse and reverse[target] != original:
            raise ValueError(f"Non-bijective test chip mapping for {target}")
        mapping[original] = target
        reverse[target] = original
        evidence[original] += 1
    comparison = [entry for entry in entries if entry["role"] == "comparison"]
    if not comparison or any((entry["chip_x"], entry["chip_y"]) not in mapping for entry in comparison):
        raise ValueError("Every Comparison chip needs an unambiguous mapping")
    with connection(source) as db:
        metadata = {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM metadata")}
        if "test_chip_override" in metadata:
            raise ValueError("Input is already a synthetic chip-remapped dataset")
        for row in db.execute("SELECT raw FROM patterns"):
            raw = json.loads(row["raw"])
            if any(k in raw for k in ("TEST_ONLY_CHIP_OVERRIDE", "TEST_ORIGINAL_XINDEX", "TEST_ORIGINAL_YINDEX")):
                raise ValueError("Input already contains reserved test provenance fields")
    with source.open("rb") as stream:
        source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {
        "warning": WARNING,
        "source_sha256": source_hash,
        "basis": "PNO + SEQUENCENO + MS; consistent one-to-one chip mapping, test only",
        "mapping": [
            {"original_comparison_chip": list(original), "synthetic_chip": list(target),
             "shared_images": evidence[original]}
            for original, target in sorted(mapping.items())
        ],
        "comparison_images": len(comparison),
        "unchanged": ["Base data", "XREL/YREL", "LayoutCoord", "Pixel", "Center",
                      "vectors", "JPEG", "contour/design geometry"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with connection(source) as original, sqlite3.connect(output) as db:
        original.backup(db)
        with db:
            for entry in comparison:
                old = (entry["chip_x"], entry["chip_y"])
                target = mapping[old]
                db.execute("UPDATE images SET chip_x=?,chip_y=? WHERE id=?", (*target, entry["id"]))
                rows = db.execute("SELECT ordinal,raw FROM patterns WHERE image_id=?", (entry["id"],)).fetchall()
                for ordinal, content in rows:
                    raw = json.loads(content)
                    raw.update(TEST_ONLY_CHIP_OVERRIDE=True, TEST_ORIGINAL_XINDEX=raw.get("XINDEX", old[0]),
                               TEST_ORIGINAL_YINDEX=raw.get("YINDEX", old[1]),
                               XINDEX=str(target[0]), YINDEX=str(target[1]))
                    db.execute("UPDATE patterns SET raw=? WHERE image_id=? AND ordinal=?",
                               (json.dumps(raw, ensure_ascii=False), entry["id"], ordinal))
            db.execute("INSERT INTO metadata VALUES(?,?)", ("test_chip_override", json.dumps(report)))
    synthetic = Store(output, allow_legacy=True)
    results = {"paired_images": 0, "overlay_images": 0, "without_common_pid": 0, "unpaired_images": 0}
    preferred = None
    with connection(output) as db:
        for entry in db.execute("SELECT * FROM images WHERE role='base' ORDER BY seq,ms"):
            other = synthetic.pair(db, entry)
            if other is None:
                results["unpaired_images"] += 1
                continue
            results["paired_images"] += 1
            if common_pids(db, entry["id"], other["id"]):
                results["overlay_images"] += 1
                if preferred is None:
                    preferred = entry["id"]
            else:
                results["without_common_pid"] += 1
    if preferred is None:
        raise ValueError("Remapping produced no common-PID overlays")
    report.update(results, initial_image_id=preferred)
    with sqlite3.connect(output) as db:
        db.execute("UPDATE metadata SET value=? WHERE key='test_chip_override'", (json.dumps(report),))
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Synthetic database integrity check failed")
    return report


def _prepare_pair(base: Path, comparison: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    entries = Store(base, comparison).index()["images"]
    keyed, mapping, reverse = {}, {}, {}
    for entry in entries:
        key = (entry["role"], entry["pno"], entry["seq"], entry["ms"])
        if key in keyed:
            raise ValueError(f"Ambiguous acquisition key: {key}")
        keyed[key] = entry
    for entry in entries:
        if entry["role"] != "comparison":
            continue
        partner = keyed.get(("base", entry["pno"], entry["seq"], entry["ms"]))
        if partner is None:
            continue
        old, target = (entry["chip_x"], entry["chip_y"]), (partner["chip_x"], partner["chip_y"])
        if old in mapping and mapping[old] != target or target in reverse and reverse[target] != old:
            raise ValueError("Inconsistent/non-bijective test chip mapping")
        mapping[old], reverse[target] = target, old
    if any((e["chip_x"], e["chip_y"]) not in mapping for e in entries if e["role"] == "comparison"):
        raise ValueError("Every Comparison chip needs an unambiguous mapping")
    report = dict(warning=WARNING, source_sha256={}, mapping=[
        dict(original_comparison_chip=list(old), synthetic_chip=list(target)) for old, target in sorted(mapping.items())])
    for role, path in (("base", base), ("comparison", comparison)):
        with path.open("rb") as stream:
            report["source_sha256"][role] = hashlib.file_digest(stream, "sha256").hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    with connection(comparison) as source, sqlite3.connect(output) as db:
        if source.execute("SELECT 1 FROM metadata WHERE key='test_chip_override'").fetchone():
            raise ValueError("Input is already synthetic")
        source.backup(db)
        for identity, x, y in db.execute("SELECT id,chip_x,chip_y FROM images").fetchall():
            target = mapping[(x, y)]
            db.execute("UPDATE images SET chip_x=?,chip_y=? WHERE id=?", (*target, identity))
            for ordinal, content in db.execute("SELECT ordinal,raw FROM patterns WHERE image_id=?", (identity,)).fetchall():
                raw = json.loads(content)
                if any(key.startswith("TEST_") for key in raw):
                    raise ValueError("Reserved test provenance fields already present")
                raw.update(TEST_ONLY_CHIP_OVERRIDE=True, TEST_ORIGINAL_XINDEX=raw["XINDEX"],
                           TEST_ORIGINAL_YINDEX=raw["YINDEX"], XINDEX=str(target[0]), YINDEX=str(target[1]))
                db.execute("UPDATE patterns SET raw=? WHERE image_id=? AND ordinal=?",
                           (json.dumps(raw, ensure_ascii=False), identity, ordinal))
        db.execute("INSERT INTO metadata VALUES('test_chip_override',?)", (json.dumps(report),))
    store = Store(base, output)
    with connection(store.path) as db:
        for entry in db.execute("SELECT * FROM identities WHERE role='base' ORDER BY seq,ms"):
            partner = store.pair(db, entry)
            if partner is not None and common_pids(db, entry["id"], partner["id"]):
                report["initial_image_name"] = entry["name"]
                break
    if "initial_image_name" not in report:
        raise ValueError("Remapping produced no common-PID overlays")
    with sqlite3.connect(output) as db:
        db.execute("UPDATE metadata SET value=? WHERE key='test_chip_override'", (json.dumps(report),))
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Synthetic database integrity check failed")
    return report


def prepare_pair(base: Path, comparison: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".test-chip-copy-", dir=output.parent) as work:
        stage = Path(work) / "comparison.sqlite"
        report = _prepare_pair(base, comparison, stage)
        os.link(stage, output)
    return report


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("source", type=Path)
    cli.add_argument("output", type=Path)
    cli.add_argument("--base", type=Path, help="Single-dataset version 4 Base; source is Comparison. Omit only for legacy combined demos.")
    args = cli.parse_args()
    print(json.dumps(prepare_pair(args.base, args.source, args.output) if args.base else prepare(args.source, args.output), indent=2))
