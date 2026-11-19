"""Read-only local data access and comparison/statistics contracts."""
from __future__ import annotations

import array
import base64
from collections import Counter
from dataclasses import dataclass
from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3
import statistics
import struct
import sys
from threading import Lock
import zlib

METRICS = ("dx", "dy", "magnitude")
PPE_COMPONENTS = ("x", "y", "magnitude")
DENSITY_BINS = 1024
_AREA_PARALLEL_PATTERNS = 4096
_AREA_POOL_LOCK = Lock()
PATTERN_COLUMNS = ("SELECT ordinal,px,py,dx,dy,magnitude,available,valid,"
                   "json_extract(raw,'$.PID') pid FROM patterns WHERE image_id=? ORDER BY ordinal")


@dataclass(frozen=True)
class DatasetPair:
    """Base and Comparison files; comparison is None for a Base-only session."""
    base: Path
    comparison: Path | None


def _area_chunk(path: DatasetPair, pairs: list, extent: float, roi: list | None) -> dict:
    store = Store(path.base, path.comparison)
    with connection(path) as db:
        contexts = [store._area_context(db, pair) for pair in pairs]
        result = store._area_sum(db, contexts, extent, roi)
    result["grids"] = {kind: zlib.compress(grid.astype("<f8", copy=False).tobytes(), 3)
                       for kind, grid in result["grids"].items()}
    return result


def validate_dataset(path: Path) -> dict:
    with connection(path) as db:
        if db.execute("PRAGMA user_version").fetchone()[0] != 4:
            raise ValueError(f"{path.name}: expected a single-dataset Viewer SQLite version 4; rebuild with the current Builder")
        metadata = {key: json.loads(value) for key, value in db.execute("SELECT key,value FROM metadata")}
        manifest = metadata.get("manifest", {})
        if manifest.get("format") != "plugin-viewer-sqlite" or manifest.get("format_version") != 4:
            raise ValueError(f"{path.name}: unsupported Viewer dataset format")
        if not isinstance(manifest.get("dataset_id"), str) or not manifest["dataset_id"].strip():
            raise ValueError(f"{path.name}: missing dataset identity")
        if not isinstance(manifest.get("csv_sha256"), str) or not manifest["csv_sha256"].strip():
            raise ValueError(f"{path.name}: missing source CSV hash")
        for table, columns in {
            "images": {"id", "name", "pno", "seq", "ms", "chip_x", "chip_y", "xrel", "yrel", "jpeg", "geometry"},
            "patterns": {"image_id", "ordinal", "layout_x", "layout_y", "px", "py", "dx", "dy",
                         "magnitude", "direction", "available", "valid", "contour_index", "raw"},
            "contour_data": {"image_id", "width", "height", "nm_x", "nm_y", "design_x", "design_y",
                             "offsets", "points", "density_offsets", "density_cells", "density_lengths"},
        }.items():
            existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            if not columns <= existing:
                raise ValueError(f"{path.name}: incomplete {table} schema; rebuild with the current Builder")
        if not isinstance(metadata.get("layout", {}).get("cells"), list) or not metadata["layout"].get("info"):
            raise ValueError(f"{path.name}: missing wafer layout")
        if db.execute("SELECT COUNT(*) FROM images").fetchone()[0] != manifest.get("images"):
            raise ValueError(f"{path.name}: image count differs from manifest")
        if db.execute("SELECT COUNT(*) FROM patterns").fetchone()[0] != manifest.get("patterns"):
            raise ValueError(f"{path.name}: measurement count differs from manifest")
        if db.execute("SELECT COUNT(*) FROM contour_data").fetchone()[0] != manifest.get("images"):
            raise ValueError(f"{path.name}: contour frame count differs from manifest")
        if db.execute("SELECT 1 FROM images GROUP BY pno,seq,ms,chip_x,chip_y HAVING COUNT(*)>1 LIMIT 1").fetchone():
            raise ValueError(f"{path.name}: ambiguous acquisition keys")
        if db.execute("SELECT 1 FROM patterns p LEFT JOIN images i ON i.id=p.image_id WHERE i.id IS NULL LIMIT 1").fetchone():
            raise ValueError(f"{path.name}: orphan pattern rows")
        return metadata


ROLE_SOURCES = {
    "images": "SELECT m.id,m.role,m.name,m.pno,m.seq,m.ms,m.chip_x,m.chip_y,m.xrel,m.yrel,i.jpeg,i.geometry "
              "FROM identities m JOIN {role}_data.images i ON i.id=m.local_id WHERE m.role='{role}'",
    "patterns": "SELECT m.id image_id,p.ordinal,p.layout_x,p.layout_y,p.px,p.py,p.dx,p.dy,p.magnitude,p.direction,"
                "p.available,p.valid,p.contour_index,p.raw "
                "FROM identities m JOIN {role}_data.patterns p ON p.image_id=m.local_id WHERE m.role='{role}'",
    "contour_data": "SELECT m.id image_id,c.width,c.height,c.nm_x,c.nm_y,c.design_x,c.design_y,c.offsets,c.points,"
                    "c.density_offsets,c.density_cells,c.density_lengths "
                    "FROM identities m JOIN {role}_data.contour_data c ON c.image_id=m.local_id WHERE m.role='{role}'",
}


@contextmanager
def connection(path: Path | DatasetPair):
    db = sqlite3.connect(":memory:" if isinstance(path, DatasetPair) else path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        if isinstance(path, DatasetPair):
            # A Base-only session attaches one file; every query still sees role-tagged images.
            files = {"base": path.base, "comparison": path.comparison}
            files = {role: file for role, file in files.items() if file is not None}
            for role, file in files.items():
                db.execute(f"ATTACH DATABASE ? AS {role}_data", (file.resolve().as_uri() + "?mode=ro",))
            db.execute("""CREATE TEMP TABLE identities(
                id INTEGER PRIMARY KEY,local_id INTEGER,role TEXT,name TEXT,pno INTEGER,seq INTEGER,
                ms TEXT,chip_x INTEGER,chip_y INTEGER,xrel REAL,yrel REAL)""")
            for role in files:
                db.execute(f"INSERT INTO identities SELECT NULL,id,'{role}',name,pno,seq,ms,chip_x,chip_y,xrel,yrel "
                           f"FROM {role}_data.images ORDER BY id")
            db.execute("CREATE INDEX temp.pairing ON identities(role,pno,seq,ms,chip_x,chip_y)")
            db.execute("CREATE INDEX temp.source_identity ON identities(role,local_id)")
            for view, source in ROLE_SOURCES.items():
                db.execute(f"CREATE TEMP VIEW {view} AS " +
                           " UNION ALL ".join(source.format(role=role) for role in files))
            db.execute("CREATE TEMP TABLE metadata(key TEXT PRIMARY KEY,value TEXT)")
            metadatas = {
                role: {r["key"]: json.loads(r["value"]) for r in db.execute(f"SELECT key,value FROM {role}_data.metadata")}
                for role in files
            }
            metadata = dict(
                datasets={role: meta["manifest"]["dataset_id"] for role, meta in metadatas.items()},
                layouts={role: meta["layout"] for role, meta in metadatas.items()},
                source_csv={role: meta["manifest"]["csv_sha256"] for role, meta in metadatas.items()},
                files={role: str(file.resolve()) for role, file in files.items()},
                single=path.comparison is None,
            )
            overrides = {role: meta["test_chip_override"] for role, meta in metadatas.items() if "test_chip_override" in meta}
            if overrides:
                metadata["test_chip_override"] = dict(
                    warning=("TEST ONLY: the Base dataset has synthetic chip coordinates. Not suitable for process analysis."
                             if path.comparison is None else
                             "TEST ONLY: at least one selected dataset has synthetic chip coordinates. Not a validated same-site comparison."),
                    datasets=overrides,
                )
                preferred = overrides.get("comparison", {}).get("initial_image_name")
                if preferred:
                    row = db.execute("SELECT id FROM identities WHERE role='base' AND name=?", (preferred,)).fetchone()
                    if row is not None:
                        metadata["test_chip_override"]["initial_image_id"] = row["id"]
            db.executemany("INSERT INTO metadata VALUES(?,?)", [(k, json.dumps(v)) for k, v in metadata.items()])
        yield db
    finally:
        db.close()


def inside(x: float, y: float, rect: list[float] | None) -> bool:
    return rect is None or rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]


def little_endian_array(code: str, blob: bytes) -> array.array:
    """Read a little-endian uint32 ("I") or float32 ("f") BLOB as a C-backed array."""
    values = array.array(code)
    if values.itemsize != 4:
        raise RuntimeError(f"array type {code!r} is not 4 bytes on this platform")
    values.frombytes(blob)
    if sys.byteorder != "little":
        values.byteswap()
    return values


def length_cells(polygon: list, geometry: dict, anchor: tuple[float, float], extent: float,
                 bins: int = DENSITY_BINS) -> dict[int, float]:
    """Exact contour length [nm] inside each density cell; must match the Builder's length_cells."""
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


def summarize(values: list[float], angular: bool = False) -> dict:
    if not values:
        return dict(n=0, mean=None, median=None, sigma=None, minimum=None, maximum=None)
    if angular:
        sine = statistics.mean(math.sin(math.radians(v)) for v in values)
        cosine = statistics.mean(math.cos(math.radians(v)) for v in values)
        radius = min(1.0, math.hypot(sine, cosine))
        mean = math.degrees(math.atan2(sine, cosine)) if radius > 1e-12 else None
        sigma = math.degrees(math.sqrt(-2 * math.log(radius))) if radius > 1e-12 else None
        return dict(n=len(values), mean=mean, median=None, sigma=sigma, minimum=None, maximum=None)
    return dict(n=len(values), mean=statistics.mean(values), median=statistics.median(values),
                sigma=statistics.pstdev(values), minimum=min(values), maximum=max(values))


def pid_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def pid_order(pid: str) -> tuple:
    return (0, int(pid), pid) if pid.isdigit() else (1, 0, pid)


def by_pid(rows: list) -> dict:
    """PID -> row for one image. Missing or repeated PIDs cannot identify a pattern and are left out."""
    seen: dict = {}
    repeated = set()
    for row in rows:
        pid = pid_text(row["pid"])
        if pid is None:
            continue
        if pid in seen:
            repeated.add(pid)
        seen[pid] = row
    return {pid: row for pid, row in seen.items() if pid not in repeated}


def image_alignment(patterns: dict) -> tuple:
    lookups = {role: by_pid(rows) for role, rows in patterns.items()}
    common = lookups["base"].keys() & lookups["comparison"].keys()
    shift = [statistics.mean(lookups["base"][pid][axis] - lookups["comparison"][pid][axis]
                             for pid in common) for axis in ("px", "py")] if common else [0.0, 0.0]
    return lookups, shift


def polygon_contains(x: float, y: float, polygon: list) -> bool:
    previous = polygon[-1]
    contained = False
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if (abs(cross) <= 1e-9 and min(x1, x2) - 1e-9 <= x <= max(x1, x2) + 1e-9
                and min(y1, y2) - 1e-9 <= y <= max(y1, y2) + 1e-9):
            return True
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            contained = not contained
        previous = current
    return contained


def design_center(geometry: dict, x: float, y: float) -> tuple[float, float]:
    candidates = []
    for polygon in geometry["design"]:
        xs, ys = zip(*polygon)
        bounds = min(xs), min(ys), max(xs), max(ys)
        # A design-frame boundary enclosing the entire image is not a pattern.
        if bounds[0] <= 0 and bounds[1] <= 0 and bounds[2] >= geometry["width"] and bounds[3] >= geometry["height"]:
            continue
        if inside(x, y, bounds) and polygon_contains(x, y, polygon):
            area = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])
            candidates.append((area, ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)))
    candidates.sort()
    if not candidates or len(candidates) > 1 and math.isclose(candidates[0][0], candidates[1][0]):
        raise ValueError(f"No unambiguous design pattern at source pixel ({x}, {y})")
    return candidates[0][1]


def ppe_summary(values: dict) -> dict:
    result = {"n": len(values["x"])}
    for component in PPE_COMPONENTS:
        data = values[component]
        mean = statistics.mean(data) if data else None
        sigma = statistics.pstdev(data) if data else None
        result.update({f"{component}_mean": mean, f"{component}_sd": sigma})
    return result


class Store:
    def __init__(self, path: Path, comparison: Path | None = None, *, allow_legacy: bool = False):
        if comparison is None and allow_legacy:
            with connection(path) as db:
                if db.execute("PRAGMA user_version").fetchone()[0] == 1:
                    # The combined demo format is retained only for regression and test tools.
                    self.path = path
                    return
        validate_dataset(path)
        if comparison is not None:
            validate_dataset(comparison)
        self.path = DatasetPair(path, comparison)
        # Build the view once so an invalid dataset fails before replacing the open session.
        with connection(self.path):
            pass

    @property
    def single(self) -> bool:
        """True for a Base-only session: no Comparison dataset is open."""
        return isinstance(self.path, DatasetPair) and self.path.comparison is None

    def index(self) -> dict:
        with connection(self.path) as db:
            meta = {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM metadata")}
            records = db.execute(
                "SELECT id,role,name,pno,seq,ms,chip_x,chip_y,xrel,yrel FROM images ORDER BY name"
            ).fetchall()
            images = []
            for record in records:
                entry = dict(record)
                other = self.pair(db, record)
                entry["pair_id"] = other["id"] if other is not None else None
                images.append(entry)
        return {"metadata": meta, "images": images}

    @staticmethod
    def pair(db: sqlite3.Connection, entry: sqlite3.Row) -> sqlite3.Row | None:
        matches = db.execute(
            "SELECT id,role,name,pno,seq,ms,chip_x,chip_y,xrel,yrel FROM images WHERE role = ? AND pno=? AND seq=? AND ms=? "
            "AND chip_x=? AND chip_y=?",
            ("comparison" if entry["role"] == "base" else "base",
             *(entry[k] for k in ("pno", "seq", "ms", "chip_x", "chip_y"))),
        ).fetchall()
        if len(matches) > 1:
            raise ValueError("Ambiguous same-chip image correspondence")
        return matches[0] if matches else None

    def image(self, identity: int) -> dict:
        with connection(self.path) as db:
            entry = db.execute("SELECT * FROM images WHERE id=?", (identity,)).fetchone()
            if entry is None or entry["role"] != "base":
                raise ValueError("Select an existing Base image")
            other = self.pair(db, entry)
            entries = {"base": entry}
            if other is not None:
                other = db.execute("SELECT * FROM images WHERE id=?", (other["id"],)).fetchone()
                entries["comparison"] = other
            result = {"name": entry["name"], "id": identity, "rows": {}, "layers": {}, "reference_frames": {},
                      "vectors": {}, "matched": 0, "shift": None,
                      "notice": "" if self.single else
                      "Comparison: no same-chip acquisition. Other chips are not substituted."}
            patterns = {}
            for role, item in entries.items():
                records = [dict(r) for r in db.execute(
                    "SELECT *,json_extract(raw,'$.PID') pid FROM patterns WHERE image_id=? ORDER BY ordinal",
                    (item["id"],))]
                patterns[role] = records
                result["rows"][role] = [dict(json.loads(r["raw"]), dx=r["dx"], dy=r["dy"],
                                             magnitude=r["magnitude"],
                                             vector_valid=bool(r["valid"])) for r in records]
            shift = [0.0, 0.0]
            result["labels"] = [{"pid": pid_text(r["pid"]), "x": r["px"], "y": r["py"],
                                 "contour_index": r.get("contour_index")}
                                for r in patterns["base"] if pid_text(r["pid"]) is not None]
            # Contour targets for This image ROI; PID is optional, so these do not depend on it.
            result["contour_patterns"] = [{"pid": pid_text(r["pid"]), "x": r["px"], "y": r["py"]}
                                          for r in patterns["base"] if r.get("contour_index") is not None]
            if other is not None:
                # The two acquisitions can use different Layout coordinate systems, so patterns pair by PID.
                lookups, shift = image_alignment(patterns)
                common = lookups["base"].keys() & lookups["comparison"].keys()
                if not common:
                    result["notice"] = "Comparison: same chip, but no common pattern IDs (PID)."
                else:
                    result.update(matched=len(common), shift=shift, notice="")
            for role, item in entries.items():
                geometry = json.loads(zlib.decompress(item["geometry"]))
                result["reference_frames"][role] = {key: geometry[key] for key in ("width", "height", "nm_x", "nm_y")}
                # Legacy demo DBs have no contour frames; version 4 pairs supply the density origin.
                if isinstance(self.path, DatasetPair):
                    frame = db.execute("SELECT design_x,design_y FROM contour_data WHERE image_id=?",
                                       (item["id"],)).fetchone()
                    if frame is None:
                        raise ValueError(f"Missing contour frame for {item['name']}; rebuild with the current Builder")
                    result["reference_frames"][role]["design_center"] = [frame["design_x"], frame["design_y"]]
                if role == "base":
                    result.update(width=geometry["width"], height=geometry["height"],
                                  design=geometry["design"],
                                  image="data:image/jpeg;base64," + base64.b64encode(item["jpeg"]).decode())
                else:
                    result["comparison_design"] = geometry["design"]
                allowed = role == "base" or result["matched"] > 0
                offset = [0, 0] if role == "base" else shift
                result["layers"][role] = (
                    [[[x + offset[0], y + offset[1]] for x, y in points]
                     for points in geometry["contour"]] if allowed else [])
                result["vectors"][role] = [
                    {"start": [r["px"] + offset[0], r["py"] + offset[1]],
                     "delta": [r["dx"] / geometry["nm_x"], -r["dy"] / geometry["nm_y"]],
                     "dx": r["dx"], "dy": r["dy"]}
                    for r in patterns[role] if r["valid"]
                ] if allowed else []
            result["vectors"].setdefault("comparison", [])
            result["layers"].setdefault("comparison", [])
            result["rows"].setdefault("comparison", [])
            return result

    def comparison_image(self, identity: int) -> dict:
        """JPEG of the Comparison image paired with a Base image; loaded only when the Viewer shows it."""
        with connection(self.path) as db:
            entry = db.execute("SELECT * FROM images WHERE id=?", (identity,)).fetchone()
            if entry is None or entry["role"] != "base":
                raise ValueError("Select an existing Base image")
            other = self.pair(db, entry)
            if other is None:
                return {"id": identity, "name": None, "image": None}
            item = db.execute("SELECT name,jpeg FROM images WHERE id=?", (other["id"],)).fetchone()
            return {"id": identity, "name": item["name"],
                    "image": "data:image/jpeg;base64," + base64.b64encode(item["jpeg"]).decode()}

    def cohort(self, db: sqlite3.Connection, filters: dict) -> tuple[list, dict]:
        pno = int(filters["pno"])
        ms = filters.get("ms", "")
        chips = {tuple(map(int, v)) for v in filters.get("chips", [])}
        chip_rect = self.rect(filters.get("chipRect"))
        counts = {role: dict(selected=0, unpaired=0, counterpart_outside=0) for role in ("base", "comparison")}
        types = {row[0] for row in db.execute("SELECT DISTINCT ms FROM images WHERE pno=?", (pno,))}
        if ms and ms not in types:
            raise ValueError(f"Unknown image type: {ms}")
        entries = db.execute(
            "SELECT id,role,name,pno,seq,ms,chip_x,chip_y,xrel,yrel FROM images WHERE pno=?",
            (pno,),
        ).fetchall()
        selected = {
            entry["id"]: entry for entry in entries
            if (not ms or entry["ms"] == ms)
            and (not chips or (entry["chip_x"], entry["chip_y"]) in chips)
            and inside(entry["xrel"], entry["yrel"], chip_rect)
        }
        cohort = []
        if self.single:
            # Base-only: every selected image is analysed; there is nothing to pair.
            for entry in selected.values():
                counts["base"]["selected"] += 1
                cohort.append((entry,))
            return cohort, counts
        for entry in selected.values():
            counts[entry["role"]]["selected"] += 1
            other = self.pair(db, entry)
            if other is None:
                counts[entry["role"]]["unpaired"] += 1
            elif other["id"] not in selected:
                counts[entry["role"]]["counterpart_outside"] += 1
            elif entry["role"] == "base":
                cohort.append((entry, other))
        return cohort, counts

    @staticmethod
    def ppe_pairs(rows: dict, roi: list[float] | None):
        """Yield (pid, base row, comparison row, valid) for patterns with the same PID in one image pair.

        The ROI is drawn on the Base image, so the Base pattern position decides membership.
        """
        lookups = {role: by_pid(rows[role]) for role in ("base", "comparison")}
        for pid in lookups["base"].keys() & lookups["comparison"].keys():
            base, comparison = lookups["base"][pid], lookups["comparison"][pid]
            if inside(base["px"], base["py"], roi):
                yield pid, base, comparison, bool(base["valid"] and comparison["valid"])

    def statistics(self, filters: dict) -> dict:
        roi = self.rect(filters.get("roi"))
        groups = {}
        image_maps = []
        ppe = {}
        by_pid_values = {}
        unmatched = dict(base=0, comparison=0)
        with connection(self.path) as db:
            cohort, counts = self.cohort(db, filters)
            for pair in cohort:
                rows = {}
                for item in pair:
                    rows[item["role"]] = db.execute(PATTERN_COLUMNS, (item["id"],)).fetchall()
                    chip = (item["chip_x"], item["chip_y"])
                    group = groups.setdefault((item["role"], *chip), {
                        "values": {metric: [] for metric in METRICS}, "missing": 0, "excluded": 0,
                    })
                    values = {metric: [] for metric in METRICS}
                    missing, excluded = 0, 0
                    for row in rows[item["role"]]:
                        if not inside(row["px"], row["py"], roi):
                            continue
                        if not row["available"]:
                            missing += 1
                        elif not row["valid"]:
                            excluded += 1
                        else:
                            for metric in METRICS:
                                values[metric].append(row[metric])
                                group["values"][metric].append(row[metric])
                    group["missing"] += missing
                    group["excluded"] += excluded
                    image_maps.append(dict(id=item["id"], role=item["role"], n=len(values["dx"]),
                                           missing=missing, excluded=excluded,
                                           **{metric: summarize(values[metric])["median"] for metric in METRICS}))
                    for pid, row in by_pid(rows[item["role"]]).items():
                        if not inside(row["px"], row["py"], roi):
                            continue
                        entry = by_pid_values.setdefault((item["role"], pid),
                                                         dict(excluded=0, **{c: [] for c in PPE_COMPONENTS}))
                        if not row["valid"]:
                            entry["excluded"] += 1
                            continue
                        for component, metric in zip(PPE_COMPONENTS, METRICS):
                            entry[component].append(row[metric])
                if len(pair) < 2:
                    continue
                lookups = {role: by_pid(rows[role]).keys() for role in rows}
                for role, other in (("base", "comparison"), ("comparison", "base")):
                    unmatched[role] += len(lookups[role] - lookups[other])
                for pid, base, comparison, valid in self.ppe_pairs(rows, roi):
                    entry = ppe.setdefault(pid, dict(excluded=0, **{c: [] for c in PPE_COMPONENTS}))
                    if not valid:
                        entry["excluded"] += 1
                        continue
                    x, y = base["dx"] - comparison["dx"], base["dy"] - comparison["dy"]
                    entry["x"].append(x)
                    entry["y"].append(y)
                    entry["magnitude"].append(math.hypot(x, y))
        offset_pids = sorted({pid for _, pid in by_pid_values}, key=pid_order)
        offset_table = [dict(role=role, pid=pid, **ppe_summary(by_pid_values[role, pid]),
                             excluded=by_pid_values[role, pid]["excluded"])
                        for role in ("base", "comparison") for pid in offset_pids if (role, pid) in by_pid_values]
        tables = []
        graph = {}
        for role in ("base", "comparison"):
            subset = [v for k, v in groups.items() if k[0] == role]
            graph[role] = {}
            for metric in METRICS:
                values = [value for group in subset for value in group["values"][metric]]
                graph[role][metric] = values
                tables.append(dict(role=role, metric=metric,
                                   **summarize(values),
                                   missing=sum(v["missing"] for v in subset),
                                   excluded=sum(v["excluded"] for v in subset)))
        maps = [dict(role=role, x=x, y=y, n=len(group["values"]["dx"]),
                     **{metric: summarize(group["values"][metric])["median"] for metric in METRICS})
                for (role, x, y), group in groups.items()]
        pids = sorted(ppe, key=pid_order)
        combined = {c: [v for pid in pids for v in ppe[pid][c]] for c in PPE_COMPONENTS}
        ppe_table = [dict(pid="All", **ppe_summary(combined), excluded=sum(v["excluded"] for v in ppe.values()))]
        ppe_table += [dict(pid=pid, **ppe_summary(ppe[pid]), excluded=ppe[pid]["excluded"]) for pid in pids]
        if self.single:
            note = ("Base only (Comp not loaded): maps, statistics and graphs use every selected Base image. "
                    "With Comp loaded, only matched image pairs are used, so N can differ. ")
        else:
            note = ("Maps, statistics and graphs use the same matched image pairs. Both images must "
                    "pass chip/position filters; source-pixel ROI and quality checks apply independently "
                    "to their patterns. Not a common-pattern difference analysis. ")
        return dict(filters=filters, images=len(cohort), paired_images=0 if self.single else len(cohort),
                    single=self.single, counts=counts,
                    table=tables, graph=graph, maps=maps, image_maps=image_maps,
                    offset_by_pid=dict(table=offset_table, pids=offset_pids),
                    ppe=dict(table=ppe_table, pids=pids, unmatched=unmatched,
                             values={pid: {c: ppe[pid][c] for c in PPE_COMPONENTS} for pid in pids}),
                    note=note + "SD: population SD. "
                         "Arrows: median Offset X/Y; color: median per-pattern |Offset|. Missing values are not zero.")

    def ppe_values(self, filters: dict) -> list[dict]:
        """One row per same-PID pattern pair in the selection, for CSV export."""
        if self.single:
            raise ValueError("PPE requires both Base and Comp")
        roi = self.rect(filters.get("roi"))
        records = []
        with connection(self.path) as db:
            cohort, _ = self.cohort(db, filters)
            for base_image, comparison_image in sorted(cohort, key=lambda p: p[0]["name"]):
                rows = {image["role"]: db.execute(PATTERN_COLUMNS, (image["id"],)).fetchall()
                        for image in (base_image, comparison_image)}
                for pid, base, comparison, valid in sorted(self.ppe_pairs(rows, roi), key=lambda v: pid_order(v[0])):
                    x = base["dx"] - comparison["dx"] if valid else None
                    y = base["dy"] - comparison["dy"] if valid else None
                    records.append(dict(
                        chip_x=base_image["chip_x"], chip_y=base_image["chip_y"], image_type=base_image["ms"],
                        sequence=base_image["seq"], base_image=base_image["name"],
                        comparison_image=comparison_image["name"], pid=pid,
                        base_offset_x=base["dx"], base_offset_y=base["dy"], base_valid=bool(base["valid"]),
                        comparison_offset_x=comparison["dx"], comparison_offset_y=comparison["dy"],
                        comparison_valid=bool(comparison["valid"]),
                        ppe_x=x, ppe_y=y, ppe_magnitude=math.hypot(x, y) if valid else None))
        return records

    def _area_context(self, db, pair, image_center=False):
        rows, frames = {}, {}
        for role, item in zip(("base", "comparison"), pair):
            rows[role] = db.execute(
                "SELECT contour_index,px,py,json_extract(raw,'$.PID') pid FROM patterns "
                "WHERE image_id=? ORDER BY ordinal", (item["id"],)).fetchall()
            frame = self._contour_row(db, item["id"], "width,height,nm_x,nm_y,design_x,design_y")
            if frame is None:
                raise ValueError(f"Missing contour frame for {item['name']}")
            frames[role] = dict(frame)
        lookups, shift = image_alignment(rows)
        base, comp = frames["base"], frames["comparison"]
        if image_center:
            base.update(design_x=base["width"] / 2, design_y=base["height"] / 2)
        if any(not math.isclose(base[k], comp[k], rel_tol=1e-9) for k in ("nm_x", "nm_y")):
            raise ValueError("Area difference requires equal Base/Comp pixel calibration; Image alignment does not rescale contours")
        extent = 1.0
        for role, frame in frames.items():
            sx, sy = shift if role == "comparison" else (0, 0)
            extent = max(extent, *(abs(x) * base["nm_x"] for x in
                                  (sx - base["design_x"], sx + frame["width"] - base["design_x"])),
                         *(abs(y) * base["nm_y"] for y in
                           (base["design_y"] - sy, base["design_y"] - sy - frame["height"])))
        return dict(pair=pair, rows=rows, frames=frames, lookups=lookups, shift=shift, extent=extent)

    def _area_regions(self, db, context, roi, pids=None):
        import numpy as np
        import shapely
        from contour_area import Region

        rows, lookups, shift = (context[k] for k in ("rows", "lookups", "shift"))
        frames = {role: self._contour_frame(db, item["id"])
                  for role, item in zip(("base", "comparison"), context["pair"])}
        counts = {role: Counter(row["contour_index"] for row in records if row["contour_index"] is not None)
                  for role, records in rows.items()}
        base = context["frames"]["base"]
        patterns, issues, candidates = [], [], []
        polygons = {role: [] for role in frames}
        for row in rows["base"]:
            if not inside(row["px"], row["py"], roi):
                continue
            pid = pid_text(row["pid"])
            if pids is not None and pid not in pids:
                continue
            reason = None
            other = lookups["comparison"].get(pid)
            if pid is None:
                reason = "missing PID"
            elif pid not in lookups["base"]:
                reason = "ambiguous PID"
            elif other is None:
                reason = "missing or ambiguous Comp PID"
            elif row["contour_index"] is None or other["contour_index"] is None:
                reason = "missing contour"
            elif counts["base"][row["contour_index"]] != 1 or counts["comparison"][other["contour_index"]] != 1:
                reason = "ambiguous contour mapping"
            if reason:
                issues.append(dict(pid=pid, reason=reason))
                continue
            parts = {}
            for role, pattern in (("base", row), ("comparison", other)):
                frame = frames[role]
                index = pattern["contour_index"]
                if not 0 <= index < len(frame["offsets"]) - 1:
                    raise ValueError("Invalid contour mapping")
                start, end = frame["offsets"][index:index + 2]
                points = np.frombuffer(frame["points"][start * 16:end * 16], dtype="<f8").reshape(-1, 2)
                if (not np.isfinite(points).all() or (points < 0).any()
                        or (points > [frame["width"], frame["height"]]).any()):
                    reason = "Contour extends outside its declared image frame"
                    break
                parts[role] = points
            if reason:
                issues.append(dict(pid=pid, reason=reason))
                continue
            for role, points in parts.items():
                polygons[role].append(points)
            candidates.append(row)
        for role, parts in polygons.items():
            lengths = np.asarray([len(points) for points in parts])
            valid = lengths >= 3
            shapes = np.full(len(parts), shapely.Polygon(), dtype=object)
            if valid.any():
                coordinates = np.concatenate([points for points in parts if len(points) >= 3])
                sx, sy = shift if role == "comparison" else (0, 0)
                coordinates = (coordinates + [sx - base["design_x"], sy - base["design_y"]]) * [base["nm_x"], -base["nm_y"]]
                rings = shapely.linearrings(coordinates, indices=np.repeat(np.arange(valid.sum()), lengths[valid]))
                shapes[valid] = shapely.polygons(rings)
            polygons[role] = shapes
        b, c = (polygons[role] for role in ("base", "comparison"))
        usable = shapely.is_valid(b) & shapely.is_valid(c) & (shapely.area(b) > 0) & (shapely.area(c) > 0)
        inward, outward = shapely.difference(b[usable], c[usable]), shapely.difference(c[usable], b[usable])
        balance = shapely.area(b[usable]) - shapely.area(c[usable])
        if not np.allclose(shapely.area(inward) - shapely.area(outward), balance, rtol=1e-8, atol=1e-7):
            raise ArithmeticError("Contour subtraction failed area conservation")
        results = iter(zip(inward, outward))
        for row, valid in zip(candidates, usable):
            if not valid:
                issues.append(dict(pid=pid_text(row["pid"]), reason="Contour must enclose one simple non-zero region"))
                continue
            inner, outer = next(results)
            patterns.append(dict(pid=pid_text(row["pid"]), px=row["px"], py=row["py"],
                                 inward=Region(inner), outward=Region(outer)))
        return patterns, issues

    def _area_sum(self, db, contexts, extent, roi, pid=None):
        from contour_area import area_grid

        geometries = {kind: [] for kind in ("inward", "outward")}
        used = compared = 0
        exclusions = Counter()
        totals = dict(inward=0.0, outward=0.0)
        for context in contexts:
            patterns, issues = self._area_regions(db, context, roi, [pid] if pid is not None else None)
            exclusions.update(item["reason"] for item in issues)
            used += bool(patterns)
            compared += len(patterns)
            for pattern in patterns:
                for kind, shapes in geometries.items():
                    shape = pattern[kind]
                    totals[kind] += shape.area
                    shapes.append(shape.geometry)
        grids = {kind: area_grid(shapes, extent, DENSITY_BINS) for kind, shapes in geometries.items()}
        bounds = None
        if pid is not None:
            import shapely

            shapes = [shape for shape in geometries["inward"] + geometries["outward"] if not shape.is_empty]
            bounds = shapely.total_bounds(shapes).tolist() if shapes else [-1, -1, 1, 1]
            if not all(math.isfinite(value) for value in bounds):
                bounds = [-1, -1, 1, 1]
        return dict(grids=grids, totals=totals, pairs=used, compared=compared,
                    excluded=dict(exclusions), bounds=bounds)

    def _area_parallel_sum(self, path: DatasetPair, contexts, extent, roi):
        from concurrent.futures import ProcessPoolExecutor
        from multiprocessing import get_context
        import numpy as np

        pairs = [[dict(item) for item in context["pair"]] for context in contexts]
        middle = len(pairs) // 2
        # Spawn avoids forking Qt threads; the lock bounds overlapping requests to two children.
        with _AREA_POOL_LOCK, ProcessPoolExecutor(max_workers=2, mp_context=get_context("spawn")) as pool:
            jobs = [pool.submit(_area_chunk, path, chunk, extent, roi)
                    for chunk in (pairs[:middle], pairs[middle:])]
            results = [job.result() for job in jobs]
        grids = {kind: np.zeros((DENSITY_BINS, DENSITY_BINS), dtype=np.float64)
                 for kind in ("inward", "outward")}
        exclusions = Counter()
        for result in results:
            exclusions.update(result["excluded"])
            for kind, grid in grids.items():
                grid += np.frombuffer(zlib.decompress(result["grids"][kind]), dtype="<f8").reshape(grid.shape)
        return dict(grids=grids, totals={kind: math.fsum(result["totals"][kind] for result in results) for kind in grids},
                    pairs=sum(result["pairs"] for result in results),
                    compared=sum(result["compared"] for result in results), excluded=dict(exclusions), bounds=None)

    def area_difference(self, params: dict) -> dict:
        if self.single:
            raise ValueError("Area difference requires both Base and Comp")
        if not isinstance(self.path, DatasetPair):
            raise ValueError("Area difference requires Builder SQLite version 4")
        import numpy as np

        scope = params.get("scope")
        with connection(self.path) as db:
            if scope == "image":
                entry = db.execute("SELECT id,role,name,geometry,pno,seq,ms,chip_x,chip_y FROM images WHERE id=?",
                                   (int(params["id"]),)).fetchone()
                if entry is None or entry["role"] != "base":
                    raise ValueError("Select an existing Base image")
                other = self.pair(db, entry)
                if other is None:
                    raise ValueError("Area difference requires a matching Comp image")
                context = self._area_context(db, (entry, other))
                centered = params.get("measure") == "area"
                roi = self.rect(params.get("filters", {}).get("roi")) if centered else None
                pids = params.get("pids") if centered else None
                if pids is not None and (not isinstance(pids, list) or
                                        any(not isinstance(pid, str) or not pid.strip() for pid in pids)):
                    raise ValueError("Selected PIDs must be a list of non-empty strings")
                patterns, issues = self._area_regions(db, context, roi if pids is None else None, pids)
                frame = context["frames"]["base"]
                design = json.loads(zlib.decompress(entry["geometry"])) if centered else None
                result = []
                bounds = [math.inf, math.inf, -math.inf, -math.inf]
                for pattern in patterns:
                    item = dict(pid=pattern["pid"])
                    anchor = design_center(design, pattern["px"], pattern["py"]) if centered else None
                    for kind in ("inward", "outward"):
                        shape = pattern[kind]
                        rings = [[[(x - (anchor[0] - frame["design_x"]) * frame["nm_x"]) if centered else
                                   x / frame["nm_x"] + frame["design_x"],
                                   (y - (frame["design_y"] - anchor[1]) * frame["nm_y"]) if centered else
                                   frame["design_y"] - y / frame["nm_y"]]
                                  for x, y in ring] for ring in shape.rings]
                        for ring in rings:
                            for x, y in ring:
                                bounds = [min(bounds[0], x), min(bounds[1], y), max(bounds[2], x), max(bounds[3], y)]
                        item[kind] = dict(area_nm2=shape.area, rings=rings)
                    result.append(item)
                return dict(scope=scope, id=entry["id"], patterns=result, issues=issues, shift=context["shift"],
                            measure="area", compared=len(patterns), pairs=int(bool(patterns)),
                            bounds=bounds if math.isfinite(bounds[0]) else [-1, -1, 1, 1])
            if scope not in ("selection", "pid"):
                raise ValueError("Area difference supports image, pid or selection scope")
            pid = pid_text(params.get("pid")) if scope == "pid" else None
            if scope == "pid" and pid is None:
                raise ValueError("Select a PID for Area Diff")
            filters = dict(params.get("filters", {}))
            roi = self.rect(filters.get("roi"))
            cohort, _ = self.cohort(db, filters)
            contexts = [self._area_context(db, pair, image_center=scope == "pid") for pair in cohort]
            extent = max((context["extent"] for context in contexts), default=1.0) + 1e-8
            candidates = sum(inside(row["px"], row["py"], roi) for context in contexts for row in context["rows"]["base"])
            if scope == "selection" and len(contexts) > 1 and candidates >= _AREA_PARALLEL_PATTERNS:
                summary = self._area_parallel_sum(self.path, contexts, extent, roi)
            else:
                summary = self._area_sum(db, contexts, extent, roi, pid)
            grids, totals, used = (summary[key] for key in ("grids", "totals", "pairs"))
            if used:
                grids = {kind: grid / used for kind, grid in grids.items()}
                totals = {kind: value / used for kind, value in totals.items()}
            for kind, grid in grids.items():
                if not math.isclose(float(grid.sum()), totals[kind], rel_tol=1e-8, abs_tol=1e-7):
                    raise ArithmeticError("Area grid failed conservation after aggregation")
            maximum = float(np.max(grids["inward"] + grids["outward"])) or 1.0
            return dict(scope=scope, measure="area", bins=DENSITY_BINS, extent_nm=extent,
                        pid=pid, bounds=summary["bounds"],
                        pairs=used, selected_pairs=len(cohort), compared=summary["compared"],
                        excluded=summary["excluded"], mean_area_nm2=totals, max_area=maximum,
                        packed={kind: base64.b64encode(zlib.compress(
                            grid.astype("<f8", copy=False).tobytes(), 3)).decode("ascii") for kind, grid in grids.items()})

    def contour_overlay(self, params: dict) -> dict:
        filters = dict(params.get("filters", {}))
        pno = int(filters.get("pno", 0))
        if pno <= 0:
            raise ValueError("A positive PNo is required for Contour overlay")
        roi = self.rect(filters.get("roi"))
        scope = params.get("scope")
        if scope == "image":
            pids = params.get("pids")
            if pids is not None and (not isinstance(pids, list) or
                                     any(not isinstance(pid, str) or not pid.strip() for pid in pids)):
                raise ValueError("Selected PIDs must be a list of non-empty strings")
            return self._image_contours(int(params["id"]), roi, pids)
        if scope == "pid":
            pid = pid_text(params.get("pid"))
            if pid is None:
                raise ValueError("Select a PID for Contour overlay")
            return self._pid_contours(filters, pid, roi)
        if scope == "selection":
            bins = params.get("bins", DENSITY_BINS)
            if not isinstance(bins, int) or isinstance(bins, bool) or bins != DENSITY_BINS:
                raise ValueError(f"Density resolution must be {DENSITY_BINS}")
            return self._selection_density(filters, roi)
        raise ValueError("Unknown Contour overlay scope")

    def _image_contours(self, identity: int, roi: list[float] | None, pids: list[str] | None = None) -> dict:
        with connection(self.path) as db:
            entry = db.execute(
                "SELECT id,role,name,pno,seq,ms,chip_x,chip_y,xrel,yrel FROM images WHERE id=?", (identity,)
            ).fetchone()
            if entry is None or entry["role"] != "base":
                raise ValueError("Select an existing Base image")
            paired = self.pair(db, entry)
            items = {"base": entry}
            if paired is not None:
                items["comparison"] = paired
            contours = {}
            bounds = [math.inf, math.inf, -math.inf, -math.inf]
            for role, item in items.items():
                geometry = self._contour_frame(db, item["id"])
                design = json.loads(zlib.decompress(db.execute(
                    "SELECT geometry FROM images WHERE id=?", (item["id"],)).fetchone()[0]))
                rows = db.execute(
                    "SELECT contour_index,px,py,dx,dy,available,json_extract(raw,'$.PID') pid "
                    "FROM patterns WHERE image_id=? ORDER BY ordinal", (item["id"],)
                ).fetchall()
                selected = []
                lookup = by_pid(rows)
                for row in rows:
                    pid = pid_text(row["pid"])
                    if pids is not None:
                        if pid not in pids:
                            continue
                        if pid not in lookup:
                            raise ValueError(f"Selected PID {pid} is not unique in {role}")
                    elif not inside(row["px"], row["py"], roi):
                        continue
                    if row["contour_index"] is None:
                        continue
                    cx, cy = design_center(design, row["px"], row["py"])
                    points = [[(x - cx) * geometry["nm_x"], (cy - y) * geometry["nm_y"]]
                              for x, y in self._contour_points(geometry, row["contour_index"])]
                    for x, y in points:
                        bounds[0], bounds[1] = min(bounds[0], x), min(bounds[1], y)
                        bounds[2], bounds[3] = max(bounds[2], x), max(bounds[3], y)
                    selected.append(dict(
                        pid=pid, px=row["px"], py=row["py"], design_center=[cx, cy],
                        available=bool(row["available"]), dx=row["dx"], dy=row["dy"],
                        points=points,
                    ))
                contours[role] = selected
            for role in ("base", "comparison"):
                contours.setdefault(role, [])
            return dict(scope="image", id=identity,
                        bounds=bounds if any(contours.values()) else [-1, -1, 1, 1], contours=contours,
                        pairs=1 if paired is not None or self.single else 0)

    @staticmethod
    def _contour_row(db: sqlite3.Connection, identity: int, columns: str):
        source = db.execute("SELECT role,local_id FROM identities WHERE id=?", (identity,)).fetchone()
        if source is None:
            return None
        role = source["role"]
        if role not in ("base", "comparison"):
            raise ValueError("Invalid contour dataset role")
        # A UNION view coroutine otherwise reads unrequested large density BLOBs too.
        return db.execute(f"SELECT {columns} FROM {role}_data.contour_data WHERE image_id=?",
                          (source["local_id"],)).fetchone()

    @staticmethod
    def _contour_frame(db: sqlite3.Connection, identity: int, density: bool = False) -> dict:
        columns = "width,height,nm_x,nm_y,design_x,design_y,offsets,points"
        if density:
            columns += ",density_offsets,density_cells,density_lengths"
        row = Store._contour_row(db, identity, columns)
        if row is None:
            raise ValueError(f"Missing contour frame for image {identity}; rebuild with the current Builder")
        frame = dict(row)
        if len(frame["offsets"]) < 4 or len(frame["offsets"]) % 4 or len(frame["points"]) % 16:
            raise ValueError(f"Invalid compact contour data for image {identity}")
        frame["offsets"] = tuple(value for value, in struct.iter_unpack("<I", frame["offsets"]))
        offsets = frame["offsets"]
        if (offsets[0] != 0 or offsets[-1] * 16 != len(frame["points"])
                or any(a > b for a, b in zip(offsets, offsets[1:]))
                or frame["width"] <= 0 or frame["height"] <= 0
                or not math.isfinite(frame["nm_x"]) or not math.isfinite(frame["nm_y"])
                or frame["nm_x"] <= 0 or frame["nm_y"] <= 0
                or not math.isfinite(frame["design_x"]) or not math.isfinite(frame["design_y"])):
            raise ValueError(f"Invalid compact contour frame for image {identity}")
        frame["points"] = memoryview(frame["points"])
        if density:
            blobs = [frame[key] for key in ("density_offsets", "density_cells", "density_lengths")]
            if (any(blob is None or len(blob) % 4 for blob in blobs)
                    or len(blobs[0]) != len(offsets) * 4 or len(blobs[1]) != len(blobs[2])):
                raise ValueError(f"Invalid contour density for image {identity}")
            starts, cells, lengths = (little_endian_array(code, blob) for code, blob in zip("IIf", blobs))
            # Bulk checks keep the per-record loop free of validation; cells beyond the grid raise IndexError there.
            if (starts[0] != 0 or starts[-1] != len(cells) or any(a > b for a, b in zip(starts, starts[1:]))
                    or (lengths and (min(lengths) < 0 or not math.isfinite(sum(lengths))))):
                raise ValueError(f"Invalid contour density for image {identity}")
            frame["density_offsets"], frame["density_cells"], frame["density_lengths"] = starts, cells, lengths
        return frame

    @staticmethod
    def _contour_points(frame: dict, index: int):
        offsets = frame["offsets"]
        if not 0 <= index < len(offsets) - 1:
            raise ValueError("Invalid contour mapping")
        return struct.iter_unpack("<dd", frame["points"][offsets[index] * 16:offsets[index + 1] * 16])

    @staticmethod
    def _to_center_nm(points: list, geometry: dict) -> list[list[float]]:
        return [[(x - geometry["width"] / 2) * geometry["nm_x"],
                 (geometry["height"] / 2 - y) * geometry["nm_y"]]
                for x, y in points]

    def _pid_contours(self, filters: dict, pid: str, roi: list[float] | None) -> dict:
        with connection(self.path) as db:
            cohort, _ = self.cohort(db, filters)
            coordinates = {"base": [], "comparison": []}
            lengths = {"base": [], "comparison": []}
            bounds = [math.inf, math.inf, -math.inf, -math.inf]
            for pair in cohort:
                rows = {}
                items = dict(zip(("base", "comparison"), pair))
                for role, item in items.items():
                    rows[role] = db.execute(
                        "SELECT contour_index,px,py,dx,dy,available,json_extract(raw,'$.PID') pid "
                        "FROM patterns WHERE image_id=? ORDER BY ordinal", (item["id"],)
                    ).fetchall()
                lookups = {role: by_pid(records) for role, records in rows.items()}
                base = lookups["base"].get(pid)
                comparison = lookups.get("comparison", {}).get(pid)
                if base is None or not self.single and comparison is None or not inside(base["px"], base["py"], roi):
                    continue
                for role, item in items.items():
                    row = lookups[role].get(pid)
                    if row is None or row["contour_index"] is None:
                        continue
                    geometry = self._contour_frame(db, item["id"])
                    shape = self._to_center_nm(self._contour_points(geometry, row["contour_index"]), geometry)
                    lengths[role].append(len(shape))
                    for x, y in shape:
                        coordinates[role].extend((x, y))
                        bounds[0], bounds[1] = min(bounds[0], x), min(bounds[1], y)
                        bounds[2], bounds[3] = max(bounds[2], x), max(bounds[3], y)
            packed = {
                role: dict(lengths=lengths[role], points=base64.b64encode(
                    zlib.compress(struct.pack(f"<{len(values)}d", *values), 3)).decode("ascii"))
                for role, values in coordinates.items()
            }
            return dict(scope="pid", pid=pid, packed=packed,
                        bounds=bounds if any(lengths.values()) else [-1, -1, 1, 1], pairs=len(cohort))

    def _selection_density(self, filters: dict, roi: list[float] | None) -> dict:
        bins = DENSITY_BINS
        with connection(self.path) as db:
            cohort, _ = self.cohort(db, filters)
            geometries = {}
            extent = 0.0
            for pair in cohort:
                for item in pair:
                    geometry = geometries.get(item["id"])
                    if geometry is None:
                        geometry = self._contour_frame(db, item["id"], density=True)
                        geometries[item["id"]] = geometry
                    extent = max(extent, geometry["width"] * geometry["nm_x"] / 2,
                                 geometry["height"] * geometry["nm_y"] / 2)
            extent = extent or 1.0
            grids = {role: [0.0] * (bins * bins) for role in ("base", "comparison")}
            mapped = {role: 0 for role in ("base", "comparison")}
            # Finish one role before the next: alternating two 1M-cell grids per image doubles the time.
            for role, position in (("base", 0), ("comparison", 1)):
                for pair in cohort:
                    if position >= len(pair):
                        continue
                    item = pair[position]
                    geometry = geometries[item["id"]]
                    # Stored cells use this image's own extent; a larger common frame needs exact recomputation.
                    cached_density = max(geometry["width"] * geometry["nm_x"] / 2,
                                         geometry["height"] * geometry["nm_y"] / 2) == extent
                    rows = db.execute(
                        "SELECT contour_index,px,py FROM patterns WHERE image_id=? ORDER BY ordinal",
                        (item["id"],),
                    ).fetchall()
                    grid = grids[role]
                    selected = []
                    for row in rows:
                        if not inside(row["px"], row["py"], roi) or row["contour_index"] is None:
                            continue
                        index = row["contour_index"]
                        if not 0 <= index < len(geometry["offsets"]) - 1:
                            raise ValueError(f"Invalid contour mapping in {item['name']}")
                        mapped[role] += 1
                        selected.append(index)
                    if cached_density:
                        starts = geometry["density_offsets"]
                        cells, lengths = geometry["density_cells"], geometry["density_lengths"]
                        try:
                            if sorted(selected) == list(range(len(starts) - 1)):
                                # Every contour once (no ROI): one pass over the whole image.
                                for cell, length in zip(cells, lengths):
                                    grid[cell] += length
                                continue
                            for index in selected:
                                start, end = starts[index], starts[index + 1]
                                for cell, length in zip(cells[start:end], lengths[start:end]):
                                    grid[cell] += length
                        except IndexError:
                            raise ValueError(f"Invalid contour density in {item['name']}") from None
                        continue
                    for index in selected:
                        points = list(self._contour_points(geometry, index))
                        if not points:
                            continue
                        cells = length_cells(points, geometry, (geometry["design_x"], geometry["design_y"]), extent, bins)
                        for cell, length in cells.items():
                            grid[cell] += length
            pair_count = len(cohort)
            if pair_count:
                grids = {role: [value / pair_count for value in grid] for role, grid in grids.items()}
            density = sorted(value for grid in grids.values() for value in grid if value > 0)
            totals = sorted(base + comparison for base, comparison in zip(
                grids["base"], grids["comparison"], strict=True) if base + comparison > 0)
            maximum = density[min(len(density) - 1, int((len(density) - 1) * .995))] if density else 1.0
            maximum_total = totals[min(len(totals) - 1, int((len(totals) - 1) * .995))] if totals else 1.0
            result = dict(scope="selection", bins=bins, extent_nm=extent, pairs=pair_count,
                        mapped=mapped, max_density=maximum, max_total=maximum_total,
                        base=grids["base"], comparison=grids["comparison"])
            result["packed"] = {
                role: base64.b64encode(zlib.compress(struct.pack(f"<{len(values)}d", *values), 3)).decode("ascii")
                for role, values in grids.items()
            }
            del result["base"], result["comparison"]
            return result

    @staticmethod
    def rect(value: object) -> list[float] | None:
        if value is None:
            return None
        if not isinstance(value, list) or len(value) != 4:
            raise ValueError("Rectangle needs four finite coordinates")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
            raise ValueError("Invalid rectangle coordinates")
        if value[0] > value[2] or value[1] > value[3]:
            raise ValueError("Rectangle bounds are reversed")
        return value
