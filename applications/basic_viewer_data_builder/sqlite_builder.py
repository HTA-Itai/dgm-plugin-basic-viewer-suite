"""Role-independent, single-file dataset consumed directly by the desktop Viewer."""
from datetime import datetime, timezone
import csv
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import struct
import tempfile
import xml.etree.ElementTree as ET
import zlib

from builder import BuildConfig, read_assets, read_measurements, sha256

FORMAT = "plugin-viewer-sqlite"
VERSION = 4
IDENTITY = ("PNO", "SEQUENCENO", "XINDEX", "YINDEX", "XREL", "YREL")


def layout_info(path: Path) -> dict:
    text = path.read_text()
    fields = {
        "SHOT_ARRAY": r"array\s*:\s*0\s*:\s*(-?\d+)\s*,\s*(-?\d+)",
        "SHOT_SIZE_nm": r"pitch\s*:\s*0\s*:\s*(-?\d+)\s*,\s*(-?\d+)",
        "IN_SHOT_CHIP_ARRAY": r"array\s*:\s*1\s*:\s*(-?\d+)\s*,\s*(-?\d+)",
        "CHIP_SIZE_nm": r"chip_pitch\s*:\s*(-?\d+)\s*,\s*(-?\d+)",
        "CHIP_ORIGIN": r"map_origin\s*:\s*(-?\d+)\s*,\s*(-?\d+)",
        "ORIGIN_OFFSET_nm": r"map_offset\s*:\s*(-?\d+)\s*,\s*(-?\d+)",
    }
    info, mask = {}, {}
    for line in text.splitlines():
        if "~idw_info" not in line:
            continue
        for key, pattern in fields.items():
            match = re.search(pattern, line)
            if match:
                value = dict(zip(("X", "Y"), map(int, match.groups())))
                if key in info and info[key] != value:
                    raise ValueError(f"Inconsistent MSR {key}")
                info[key] = value
        match = re.search(r"chip_exist\s*:\s*(\d+)\s*:\s*([0-9]+)", line)
        if match:
            y, codes = int(match[1]), match[2]
            if y in mask:
                raise ValueError("Duplicate MSR chip_exist row")
            mask[y] = codes
    if set(info) != set(fields):
        raise ValueError("MSR is missing required wafer layout information")
    nx, ny = info["SHOT_ARRAY"]["X"], info["SHOT_ARRAY"]["Y"]
    if nx <= 0 or ny <= 0 or set(mask) != set(range(ny)) or any(len(row) != nx for row in mask.values()):
        raise ValueError("Incomplete MSR chip_exist mask")
    if info["IN_SHOT_CHIP_ARRAY"] != {"X": 1, "Y": 1}:
        raise ValueError("Only one chip per shot is supported")
    if any(info[key][axis] <= 0 for key in ("CHIP_SIZE_nm", "SHOT_SIZE_nm") for axis in ("X", "Y")):
        raise ValueError("MSR chip/shot pitch must be positive")
    return dict(info=info, cells=[dict(x=x, y=y, code=int(code))
                                 for y, row in sorted(mask.items()) for x, code in enumerate(row)],
                sha256=sha256(path))


def polygons(content: bytes, offset=(0, 0), design=False) -> list:
    result = []
    for node in ET.fromstring(content).iter():
        if node.tag.split("}")[-1] != "polygon" or design and node.get("data-design-type") != "0":
            continue
        points = [[float(v) for v in token.split(",")] for token in node.get("points", "").split()]
        if any(len(p) != 2 or any(not math.isfinite(v) for v in p) for p in points):
            raise ValueError("Invalid SVG polygon coordinates")
        if len(points) > 2:
            result.append([[x + offset[0], y + offset[1]] for x, y in points])
    return result


def layout_center(geometry: dict) -> tuple[float, float]:
    """Mean bbox center of the design patterns; All selected images aligns every image on this point."""
    centers = []
    for polygon in geometry["design"]:
        xs, ys = zip(*polygon)
        bounds = min(xs), min(ys), max(xs), max(ys)
        # A design-frame boundary enclosing the entire image is not a pattern.
        if bounds[0] <= 0 and bounds[1] <= 0 and bounds[2] >= geometry["width"] and bounds[3] >= geometry["height"]:
            continue
        centers.append(((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2))
    if not centers:
        raise ValueError("Missing design patterns for the layout center")
    return sum(x for x, _ in centers) / len(centers), sum(y for _, y in centers) / len(centers)


def point_in_polygon(x: float, y: float, polygon: list) -> bool:
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if (abs(cross) <= 1e-9 and min(x1, x2) - 1e-9 <= x <= max(x1, x2) + 1e-9
                and min(y1, y2) - 1e-9 <= y <= max(y1, y2) + 1e-9):
            return True
        if (y1 > y) != (y2 > y):
            crossing_x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < crossing_x:
                inside = not inside
        previous = current
    return inside


def contour_index(x: float, y: float, contours: list, bounds: list | None = None) -> int | None:
    matches = []
    if bounds is None:
        bounds = [(min(xv for xv, _ in polygon), min(yv for _, yv in polygon),
                   max(xv for xv, _ in polygon), max(yv for _, yv in polygon)) for polygon in contours]
    for index, (polygon, (xmin, ymin, xmax, ymax)) in enumerate(zip(contours, bounds, strict=True)):
        if xmin <= x <= xmax and ymin <= y <= ymax and point_in_polygon(x, y, polygon):
            matches.append(index)
    if len(matches) > 1:
        raise ValueError(f"Pattern center ({x}, {y}) belongs to multiple contours")
    return matches[0] if matches else None


DENSITY_BINS = 1024


def length_cells(polygon: list, geometry: dict, anchor: tuple[float, float], extent: float,
                 bins: int = DENSITY_BINS) -> dict[int, float]:
    """Exact contour length [nm] inside each density cell, origin at the design layout center."""
    scale = bins / (2 * extent)
    cells: dict[int, float] = {}
    previous = polygon[-1]
    for current in polygon:
        u0 = ((previous[0] - anchor[0]) * geometry["nm_x"] + extent) * scale
        v0 = (extent - (anchor[1] - previous[1]) * geometry["nm_y"]) * scale
        u1 = ((current[0] - anchor[0]) * geometry["nm_x"] + extent) * scale
        v1 = (extent - (anchor[1] - current[1]) * geometry["nm_y"]) * scale
        length = math.hypot((current[0] - previous[0]) * geometry["nm_x"],
                            (current[1] - previous[1]) * geometry["nm_y"])
        previous = current
        if length == 0:
            continue
        cuts = [0.0, 1.0]
        for a0, a1 in ((u0, u1), (v0, v1)):
            if a0 != a1:
                for line in range(math.floor(min(a0, a1)) + 1, math.ceil(max(a0, a1))):
                    cuts.append((line - a0) / (a1 - a0))
        cuts.sort()
        for t0, t1 in zip(cuts, cuts[1:]):
            if t1 <= t0:
                continue
            middle = (t0 + t1) / 2
            col, line = math.floor(u0 + (u1 - u0) * middle), math.floor(v0 + (v1 - v0) * middle)
            if 0 <= col < bins and 0 <= line < bins:
                cell = line * bins + col
                cells[cell] = cells.get(cell, 0.0) + (t1 - t0) * length
    return cells


def build_sqlite(source: Path, output: Path, config: BuildConfig, *, dataset_id: str, msr: Path) -> dict:
    source, output = source.resolve(), output.resolve()
    if not dataset_id.strip():
        raise ValueError("dataset_id is required")
    if output.is_relative_to(source) or output == msr.resolve():
        raise ValueError("Output must be outside source inputs")
    if output.exists():
        raise FileExistsError(output)
    if config.create_zip:
        raise ValueError("SQLite is already a single file; CreateZip must be false")
    csv_path = source / "report" / config.target_csv
    csv_hash = sha256(csv_path)
    additional = dict(config.additional_columns)
    for key in IDENTITY:
        additional[key] = {"type": "int64" if key in IDENTITY[:4] else "float64", "unit": ""}
    validated, _ = read_measurements(csv_path, BuildConfig(config.target_csv, additional))
    expected = {(r["IMAGENAME"], r["LayoutCoordX"], r["LayoutCoordY"]): r for r in validated.to_pylist()}
    layout = layout_info(msr)
    mask = {(c["x"], c["y"]) for c in layout["cells"] if c["code"] != 0}
    images = {}
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["SID"].strip():
                continue
            clean = {k: v for k, v in row.items() if k is not None}
            images.setdefault(clean["IMAGENAME"], []).append(clean)
    output.parent.mkdir(parents=True, exist_ok=True)
    mapped_patterns = 0
    with tempfile.TemporaryDirectory(prefix=".viewer-build-", dir=output.parent) as work:
        stage = Path(work) / "dataset.sqlite"
        with sqlite3.connect(stage) as db:
            db.executescript("""
                PRAGMA user_version=4;
                CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE images(id INTEGER PRIMARY KEY,name TEXT NOT NULL,pno INTEGER NOT NULL,
                    seq INTEGER NOT NULL,ms TEXT NOT NULL,chip_x INTEGER NOT NULL,chip_y INTEGER NOT NULL,
                    xrel REAL NOT NULL,yrel REAL NOT NULL,jpeg BLOB NOT NULL,geometry BLOB NOT NULL);
                CREATE UNIQUE INDEX pairing ON images(pno,seq,ms,chip_x,chip_y);
                CREATE TABLE patterns(image_id INTEGER,ordinal INTEGER,layout_x REAL,layout_y REAL,
                    px REAL,py REAL,dx REAL,dy REAL,magnitude REAL,direction REAL,
                    available INTEGER,valid INTEGER,contour_index INTEGER,raw TEXT,
                    PRIMARY KEY(image_id,ordinal));
                CREATE TABLE assets(image_id INTEGER,name TEXT,content BLOB,PRIMARY KEY(image_id,name));
                CREATE TABLE contour_data(image_id INTEGER PRIMARY KEY,width INTEGER,height INTEGER,
                    nm_x REAL,nm_y REAL,design_x REAL,design_y REAL,offsets BLOB,points BLOB,
                    density_offsets BLOB,density_cells BLOB,density_lengths BLOB);
            """)
            for name, rows in sorted(images.items()):
                first = rows[0]
                parsed = expected[(name, float(first["LayoutCoordX"]), float(first["LayoutCoordY"]))]
                if any(any(expected[(name, float(r["LayoutCoordX"]), float(r["LayoutCoordY"]))][k] != parsed[k]
                           for k in IDENTITY) for r in rows):
                    raise ValueError(f"Inconsistent acquisition metadata: {name}")
                if any(parsed[k] is None for k in IDENTITY):
                    raise ValueError(f"Missing acquisition metadata: {name}")
                if (parsed["XINDEX"], parsed["YINDEX"]) not in mask:
                    raise ValueError(f"Measured chip is outside MSR existence mask: {name}")
                ms = re.search(r"-(\d+MS)\.png$", name)
                if not ms:
                    raise ValueError(f"Missing image type suffix: {name}")
                archive_path = source / "viewer" / Path(name).with_suffix(".ovlx")
                with tempfile.TemporaryDirectory(dir=work) as assets_dir:
                    destination = Path(assets_dir) / "assets"
                    read_assets(archive_path, destination)
                    content = {p.name: p.read_bytes() for p in destination.iterdir()}
                condition = json.loads(content["cond.json"])
                image, shift = condition["image"], condition["matching_shift"]
                design = ET.fromstring(content["design.svg"])
                offset = [
                    int((image[f"actual_size_{axis}"] - float(design.get(size))) / 2 -
                        (shift[f"matching_shift_{axis}_nm"] - shift[f"manual_shift_{axis}_nm"]) /
                        image[f"nm_per_pixel_{axis}"])
                    for axis, size in (("x", "width"), ("y", "height"))
                ]
                contours = polygons(content["contour.svg"])
                contour_bounds = [
                    (min(x for x, _ in polygon), min(y for _, y in polygon),
                     max(x for x, _ in polygon), max(y for _, y in polygon))
                    for polygon in contours
                ]
                geometry = dict(width=image["actual_size_x"], height=image["actual_size_y"],
                                nm_x=image["nm_per_pixel_x"], nm_y=image["nm_per_pixel_y"],
                                contour=contours,
                                design=polygons(content["design.svg"], offset, True))
                identity = db.execute("INSERT INTO images VALUES(NULL,?,?,?,?,?,?,?,?,?,?)", (
                    name, *(parsed[k] for k in IDENTITY[:2]), ms[1],
                    *(parsed[k] for k in IDENTITY[2:]), content["image.jpeg"],
                    zlib.compress(json.dumps(geometry, allow_nan=False).encode(), 6),
                )).lastrowid
                offsets = [0]
                coordinates = []
                density_offsets = [0]
                density_cells = []
                density_lengths = []
                extent = max(geometry["width"] * geometry["nm_x"] / 2,
                             geometry["height"] * geometry["nm_y"] / 2)
                anchor_x, anchor_y = layout_center(geometry)
                for polygon in contours:
                    coordinates.extend(value for point in polygon for value in point)
                    offsets.append(len(coordinates) // 2)
                    for cell, length in sorted(length_cells(polygon, geometry, (anchor_x, anchor_y), extent).items()):
                        density_cells.append(cell)
                        density_lengths.append(length)
                    density_offsets.append(len(density_cells))
                db.execute("INSERT INTO contour_data VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                    identity, geometry["width"], geometry["height"], geometry["nm_x"], geometry["nm_y"],
                    anchor_x, anchor_y,
                    struct.pack(f"<{len(offsets)}I", *offsets),
                    struct.pack(f"<{len(coordinates)}d", *coordinates),
                    struct.pack(f"<{len(density_offsets)}I", *density_offsets),
                    struct.pack(f"<{len(density_cells)}I", *density_cells),
                    struct.pack(f"<{len(density_lengths)}f", *density_lengths),
                ))
                db.executemany("INSERT INTO assets VALUES(?,?,?)",
                               [(identity, member, content[member]) for member in ("design.svg", "contour.svg", "cond.json")])
                for ordinal, row in enumerate(rows):
                    value = expected[(name, float(row["LayoutCoordX"]), float(row["LayoutCoordY"]))]
                    x, y, px, py = [value[k] for k in ("LayoutCoordX", "LayoutCoordY", "PixelX", "PixelY")]
                    available = value["CenterX"] is not None and value["CenterY"] is not None
                    dx = value["CenterX"] - x if available else None
                    dy = value["CenterY"] - y if available else None
                    magnitude = math.hypot(dx, dy) if available else None
                    valid = available and row["DefectFlag"] == "Normal" and row["ContourSuccess"] == "Yes"
                    contour = contour_index(px, py, contours, contour_bounds)
                    if contour is not None:
                        mapped_patterns += 1
                    db.execute("INSERT INTO patterns VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                        identity, ordinal, x, y, px, py, dx, dy, magnitude, None,
                        available, valid, contour, json.dumps(row, ensure_ascii=False, allow_nan=False)))
            manifest = dict(format=FORMAT, format_version=VERSION, builder_version="4.0.0",
                            dataset_id=dataset_id, created_utc=datetime.now(timezone.utc).isoformat(),
                            csv_sha256=csv_hash, msr_sha256=layout["sha256"],
                            images=len(images), patterns=len(expected),
                            contour_mapped_patterns=mapped_patterns,
                            contour_unmapped_patterns=len(expected) - mapped_patterns,
                            contour_encoding="xy-f64le-offsets-u32le",
                            density_encoding="per-contour cells u32le + contour length nm f32le (parallel arrays); "
                                             "offsets u32le; 1024 x 1024 cells",
                            density_origin="design layout center (mean non-frame design bbox center)",
                            viewer_compatibility="SQLite dataset version 4",
                            coordinates=dict(dx="CenterX-LayoutCoordX [nm]", dy="CenterY-LayoutCoordY [nm]",
                                             screen_y="negative source dy; supported 1-chip-per-shot IDW profile"))
            for key, value in dict(manifest=manifest, layout=layout).items():
                db.execute("INSERT INTO metadata VALUES(?,?)", (key, json.dumps(value)))
            db.commit()
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Output SQLite integrity failed")
        if sha256(csv_path) != csv_hash or sha256(msr) != layout["sha256"]:
            raise ValueError("Source changed during build")
        # Exclusive publication avoids replacing a concurrent or existing result.
        os.link(stage, output)
    return dict(path=str(output), images=len(images), patterns=len(expected),
                contour_mapped_patterns=mapped_patterns, sha256=sha256(output))
