from __future__ import annotations

import base64
import json
import math
import sqlite3
import statistics
from pathlib import Path


FORMAT = "basic-viewer-sqlite"
VERSION = 1


class Store:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.rule = None
        self._validate()

    def _connect(self):
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        return db

    def _validate(self):
        with self._connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version != VERSION:
                raise ValueError(f"Expected BasicViewer SQLite format version {VERSION}, got {version}")
            manifest_row = db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()
            if manifest_row is None:
                raise ValueError("Missing SQLite manifest")
            manifest = json.loads(manifest_row[0])
            if manifest.get("format") != FORMAT or manifest.get("format_version") != VERSION:
                raise ValueError("Unsupported BasicViewer SQLite format")

    def index(self):
        with self._connect() as db:
            manifest = json.loads(db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
            metrics = [dict(name=row[0], unit=row[1]) for row in db.execute(
                "SELECT name,unit FROM metrics ORDER BY name"
            )]
            images = [dict(
                id=row[0], name=row[1], pno=row[2], seq=row[3], ms=row[4],
                chip_x=row[5], chip_y=row[6], xrel=row[7], yrel=row[8],
            ) for row in db.execute(
                "SELECT id,name,pno,seq,ms,chip_x,chip_y,xrel,yrel FROM images ORDER BY id"
            )]
            pattern_count = db.execute("SELECT COUNT(*) FROM patterns").fetchone()[0]
            edge_count = db.execute("SELECT COUNT(*) FROM patterns WHERE edge_cut=1").fetchone()[0]
        return dict(
            manifest=manifest,
            metrics=metrics,
            images=images,
            pattern_count=pattern_count,
            edge_cut_count=edge_count,
            group_names=list(self.group_assignment().keys()),
        )

    def image(self, image_id: int):
        with self._connect() as db:
            image = db.execute(
                "SELECT id,name,pno,seq,ms,chip_x,chip_y,xrel,yrel,jpeg FROM images WHERE id=?",
                (image_id,),
            ).fetchone()
            if image is None:
                raise ValueError(f"Unknown image id: {image_id}")
            rows = db.execute(
                "SELECT ordinal,layout_x,layout_y,pixel_x,pixel_y,center_x,center_y,"
                "dx,dy,magnitude,available,valid,edge_cut,raw FROM patterns "
                "WHERE image_id=? ORDER BY ordinal",
                (image_id,),
            ).fetchall()
        patterns = []
        for row in rows:
            raw = json.loads(row["raw"])
            patterns.append(dict(
                ordinal=row["ordinal"], layout_x=row["layout_x"], layout_y=row["layout_y"],
                pixel_x=row["pixel_x"], pixel_y=row["pixel_y"], center_x=row["center_x"],
                center_y=row["center_y"], dx=row["dx"], dy=row["dy"], magnitude=row["magnitude"],
                available=bool(row["available"]), valid=bool(row["valid"]),
                edge_cut=bool(row["edge_cut"]), raw=raw,
            ))
        return dict(
            id=image["id"], name=image["name"], pno=image["pno"], seq=image["seq"],
            ms=image["ms"], chip_x=image["chip_x"], chip_y=image["chip_y"],
            xrel=image["xrel"], yrel=image["yrel"],
            jpeg_base64=base64.b64encode(image["jpeg"]).decode("ascii"),
            patterns=patterns,
        )

    def apply_rule(self, rule):
        if not isinstance(rule, dict):
            raise ValueError("Grouping rule must be an object")
        if rule.get("format") != "basic-viewer-grouping" or rule.get("version") != 2:
            raise ValueError("Unsupported grouping rule format")
        if not isinstance(rule.get("groups"), dict) or not rule["groups"]:
            raise ValueError("Grouping rule has no groups")
        assignments = self.resolve_rule(rule)
        self.rule = dict(rule=rule, assignments=assignments)
        return dict(group_names=list(assignments.keys()), groups={k: len(v) for k, v in assignments.items()})

    def apply_rule_file(self, path: Path):
        text = path.read_text(encoding="utf-8")
        try:
            rule = json.loads(text)
        except json.JSONDecodeError:
            try:
                import yaml
            except ImportError as error:
                raise ValueError("YAML rules require PyYAML") from error
            rule = yaml.safe_load(text)
        return self.apply_rule(rule)

    def resolve_rule(self, rule):
        coords = rule.get("coords", {})
        x_spec = coords.get("x", {})
        y_spec = coords.get("y", {})
        x_source = x_spec.get("source", "layout")
        y_source = y_spec.get("source", "layout")
        x_tolerance = float(x_spec.get("tolerance_nm", 30))
        y_tolerance = float(y_spec.get("tolerance_nm", y_spec.get("tolerance_px", 5)))

        with self._connect() as db:
            rows = db.execute(
                "SELECT p.image_id,p.ordinal,p.layout_x,p.layout_y,p.pixel_x,p.pixel_y,p.edge_cut,"
                "i.name,i.pno,i.seq,i.ms,i.chip_x,i.chip_y,p.raw "
                "FROM patterns p JOIN images i ON i.id=p.image_id "
                "WHERE p.edge_cut=0 ORDER BY p.image_id,p.ordinal"
            ).fetchall()

        by_image = {}
        for row in rows:
            by_image.setdefault(row["image_id"], []).append(row)

        ordinal_x = {}
        ordinal_y = {}
        for image_id, image_rows in by_image.items():
            distinct_x = sorted({r["layout_x"] for r in image_rows if r["layout_x"] is not None})
            distinct_y = sorted({r["layout_y"] for r in image_rows if r["layout_y"] is not None},
                                reverse=False)
            x_map = {value: i + 1 for i, value in enumerate(distinct_x)}
            y_map = {value: i + 1 for i, value in enumerate(distinct_y)}
            for row in image_rows:
                key = (row["image_id"], row["ordinal"])
                ordinal_x[key] = x_map.get(row["layout_x"])
                ordinal_y[key] = y_map.get(row["layout_y"])

        result = {name: set() for name in rule["groups"]}
        for name, spec in rule["groups"].items():
            selects = spec.get("select", [])
            for row in rows:
                key = (row["image_id"], row["ordinal"])
                for selector in selects:
                    points = selector.get("points")
                    if points is not None:
                        match = any(
                            abs(float(row["layout_x"]) - float(x)) <= x_tolerance
                            and abs(float(row["layout_y"]) - float(y)) <= y_tolerance
                            for x, y in points
                        ) if row["layout_x"] is not None and row["layout_y"] is not None else False
                    else:
                        x_match = True
                        y_match = True
                        if "x" in selector:
                            wanted = selector["x"]
                            actual = ordinal_x.get(key) if x_source == "ordinal" else row["layout_x"]
                            x_match = any(abs(actual - x) <= x_tolerance for x in wanted) if actual is not None else False
                        if "y" in selector:
                            wanted = selector["y"]
                            actual = ordinal_y.get(key) if y_source == "ordinal" else row["layout_y"]
                            y_match = any(abs(actual - y) <= y_tolerance for y in wanted) if actual is not None else False
                        match = x_match and y_match
                    if match:
                        result[name].add(key)
                        break
        return result

    def group_assignment(self):
        if self.rule is None:
            return {}
        return {name: sorted(keys) for name, keys in self.rule["assignments"].items()}

    def statistics(self, metric):
        with self._connect() as db:
            rows = db.execute(
                "SELECT p.image_id,p.ordinal,p.layout_x,p.layout_y,p.dx,p.dy,p.magnitude,p.raw,p.edge_cut "
                "FROM patterns p"
            ).fetchall()
        assignment = self.group_assignment()
        if not assignment:
            assignment = {"Ungrouped": [(r["image_id"], r["ordinal"]) for r in rows if not r["edge_cut"]]}
        member = {}
        for name, keys in assignment.items():
            member[name] = set(keys)

        values = {}
        for row in rows:
            if row["edge_cut"]:
                continue
            raw = json.loads(row["raw"])
            value = raw.get(metric)
            if value is None and metric in {"dx", "dy", "magnitude"}:
                value = dict(dx=row["dx"], dy=row["dy"], magnitude=row["magnitude"]).get(metric)
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            name = next((name for name, keys in member.items() if (row["image_id"], row["ordinal"]) in keys), "Ungrouped")
            values.setdefault(name, []).append(value)

        result = {}
        for name, nums in values.items():
            if not nums:
                continue
            result[name] = dict(
                n=len(nums),
                mean=sum(nums) / len(nums),
                median=statistics.median(nums),
                sd=statistics.pstdev(nums),
                minimum=min(nums),
                maximum=max(nums),
            )
        return dict(metric=metric, groups=result)
