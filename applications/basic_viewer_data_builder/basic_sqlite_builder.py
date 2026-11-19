from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


FORMAT = "basic-viewer-sqlite"
VERSION = 1
NUMERIC_HINTS = ("x", "y", "area", "size", "diameter", "gap", "angle", "ratio", "epe")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_float(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        result = float(text)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def safe_int(value):
    result = safe_float(value)
    if result is None:
        return None
    if not result.is_integer():
        return None
    return int(result)


def numeric_field(name: str, value) -> float | int | None:
    return safe_float(value)


def read_csv(source: Path, target_csv: str) -> tuple[list[dict], dict]:
    csv_path = source / "report" / target_csv
    if not csv_path.is_file():
        raise ValueError(f"CSV not found: {csv_path}")
    required = {"IMAGENAME", "PNO", "SEQUENCENO", "LayoutCoordX", "LayoutCoordY", "PixelX", "PixelY"}
    all_rows = []
    header = []
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        header = reader.fieldnames or []
        missing = sorted(required - set(header))
        if missing:
            raise ValueError(f"Missing required CSV columns: {missing}")
        for row in reader:
            all_rows.append({key: value for key, value in row.items() if key is not None})
    if not all_rows:
        raise ValueError("The selected CSV contains no rows.")
    return all_rows, {"csv_path": str(csv_path), "csv_sha256": sha256(csv_path), "header": header}


def read_ovlx(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        expected = {"image.jpeg", "design.svg", "contour.svg", "cond.json"}
        missing = expected - names
        if missing:
            raise ValueError(f"{path.name}: missing OVLX members: {sorted(missing)}")
        jpeg = archive.read("image.jpeg")
        cond = json.loads(archive.read("cond.json").decode("utf-8"))
        return {
            "jpeg": jpeg,
            "design": archive.read("design.svg"),
            "contour": archive.read("contour.svg"),
            "cond": json.dumps(cond, ensure_ascii=False, sort_keys=True),
        }


def image_ms(name: str) -> str:
    import re
    match = re.search(r"-([0-9]+MS)\.png$", name)
    return match.group(1) if match else ""


def edge_cut_key(rows: list[dict]) -> bool:
    design_column = "ReferenceArea"
    if any("LayoutArea" in row for row in rows):
        design_column = "LayoutArea"
    design_present = any(safe_float(row.get(design_column)) is not None for row in rows)
    normal = all(str(row.get("DefectFlag", "")).strip() == "Normal" for row in rows)
    return normal and not design_present


def build_basic_sqlite(source: Path, output: Path, dataset_id: str, target_csv: str = "result.csv") -> dict:
    source = source.resolve()
    output = output.resolve()
    if not source.is_dir():
        raise ValueError(f"Source directory not found: {source}")
    if output.exists():
        raise FileExistsError(output)
    if output.is_relative_to(source):
        raise ValueError("Output must be outside the source directory")

    all_rows, csv_info = read_csv(source, target_csv)
    grouped = {}
    for row in all_rows:
        key = (
            str(row.get("IMAGENAME", "")).strip(),
            safe_float(row.get("LayoutCoordX")),
            safe_float(row.get("LayoutCoordY")),
        )
        if not key[0]:
            continue
        grouped.setdefault(key, []).append(row)

    selected = []
    for key, rows in grouped.items():
        base_rows = [row for row in rows if not str(row.get("SID", "")).strip()]
        if not base_rows:
            continue
        row = base_rows[0]
        row_copy = dict(row)
        row_copy["_edge_cut"] = edge_cut_key(rows)
        selected.append(row_copy)

    if not selected:
        raise ValueError("No SID-empty pattern rows remain.")

    header = csv_info["header"]
    numeric_metrics = []
    for name in header:
        if name in {"IMAGENAME", "SID", "CID", "Lot", "WaferID", "DefectFlag", "ContourSuccess", "CategoricalValue", "LayerType"}:
            continue
        values = [safe_float(row.get(name)) for row in selected[:1000]]
        if values and sum(v is not None for v in values) >= max(1, len(values) // 2):
            numeric_metrics.append(name)
    numeric_metrics = list(dict.fromkeys(numeric_metrics))

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".basic-viewer-build-", dir=output.parent) as work:
        stage = Path(work) / "dataset.sqlite"
        with sqlite3.connect(stage) as db:
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
                CREATE INDEX patterns_layout ON patterns(layout_x,layout_y);
                CREATE TABLE assets(image_id INTEGER NOT NULL,name TEXT NOT NULL,content BLOB NOT NULL,
                    PRIMARY KEY(image_id,name));
                CREATE TABLE metrics(name TEXT PRIMARY KEY,unit TEXT NOT NULL);
            """)

            images = {}
            for row in selected:
                name = str(row["IMAGENAME"])
                images.setdefault(name, []).append(row)

            pattern_count = 0
            edge_count = 0
            for name in sorted(images):
                rows = images[name]
                first = rows[0]
                pno = safe_int(first.get("PNO"))
                seq = safe_int(first.get("SEQUENCENO"))
                chip_x = safe_int(first.get("XINDEX"))
                chip_y = safe_int(first.get("YINDEX"))
                xrel = safe_float(first.get("XREL"))
                yrel = safe_float(first.get("YREL"))
                if None in (pno, seq, chip_x, chip_y, xrel, yrel):
                    raise ValueError(f"Missing acquisition metadata: {name}")
                ms = image_ms(name)
                ovlx = source / "viewer" / name.replace(".png", ".ovlx")
                if not ovlx.is_file():
                    ovlx = source / "viewer" / Path(name).with_suffix(".ovlx")
                if not ovlx.is_file():
                    raise ValueError(f"Missing OVLX asset: {ovlx}")
                assets = read_ovlx(ovlx)
                image_id = db.execute(
                    "INSERT INTO images VALUES(NULL,?,?,?,?,?,?,?,?,?)",
                    (name, pno, seq, ms, chip_x, chip_y, xrel, yrel, assets["jpeg"]),
                ).lastrowid
                db.executemany(
                    "INSERT INTO assets VALUES(?,?,?)",
                    [(image_id, "design.svg", assets["design"]),
                     (image_id, "contour.svg", assets["contour"]),
                     (image_id, "cond.json", assets["cond"].encode("utf-8"))],
                )
                for ordinal, row in enumerate(rows):
                    lx = safe_float(row.get("LayoutCoordX"))
                    ly = safe_float(row.get("LayoutCoordY"))
                    px = safe_float(row.get("PixelX"))
                    py = safe_float(row.get("PixelY"))
                    cx = safe_float(row.get("CenterX"))
                    cy = safe_float(row.get("CenterY"))
                    available = cx is not None and cy is not None
                    dx = cx - lx if available and lx is not None else None
                    dy = cy - ly if available and ly is not None else None
                    magnitude = math.hypot(dx, dy) if dx is not None and dy is not None else None
                    valid = available and str(row.get("DefectFlag", "")).strip() == "Normal" and str(row.get("ContourSuccess", "")).strip() == "Yes"
                    edge_cut = bool(row.pop("_edge_cut", False))
                    if edge_cut:
                        edge_count += 1
                    raw_json = json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)
                    db.execute(
                        "INSERT INTO patterns VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (image_id, ordinal, lx, ly, px, py, cx, cy, dx, dy, magnitude,
                         int(available), int(valid), int(edge_cut), raw_json),
                    )
                    pattern_count += 1

            for name in numeric_metrics:
                unit = "nm" if name.lower().startswith(("layout", "center", "pixel", "epe", "gap", "size")) else ""
                db.execute("INSERT INTO metrics VALUES(?,?)", (name, unit))
            manifest = {
                "format": FORMAT,
                "format_version": VERSION,
                "builder_version": "1.0.0",
                "dataset_id": dataset_id,
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "csv_sha256": csv_info["csv_sha256"],
                "images": len(images),
                "patterns": pattern_count,
                "edge_cut_patterns": edge_count,
                "metrics": numeric_metrics,
            }
            db.execute("INSERT INTO metadata VALUES(?,?)", (
                "manifest", json.dumps(manifest, ensure_ascii=False, sort_keys=True)
            ))
            db.execute("INSERT INTO metadata VALUES(?,?)", (
                "source", json.dumps({"csv": csv_info["csv_path"]}, ensure_ascii=False)
            ))
            db.commit()
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Output SQLite integrity check failed")
        os.link(stage, output)
    return {
        "path": str(output),
        "images": len(images),
        "patterns": pattern_count,
        "edge_cut_patterns": edge_count,
        "metrics": numeric_metrics,
        "sha256": sha256(output),
    }
