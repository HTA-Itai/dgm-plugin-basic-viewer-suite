from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
import math
from pathlib import Path, PureWindowsPath
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image
import pyarrow as pa
import pyarrow.parquet as pq


FORMAT_VERSION = "1.0.0"
BUILDER_VERSION = "1.0.0"
KEY_COLUMNS = ["IMAGENAME", "LayoutCoordX", "LayoutCoordY"]
NUMERIC_COLUMNS = ["LayoutCoordX", "LayoutCoordY", "PixelX", "PixelY", "CenterX", "CenterY"]
TEXT_COLUMNS = ["IMAGENAME", "SID", "ContourSuccess", "DefectFlag"]
REQUIRED_COLUMNS = TEXT_COLUMNS + NUMERIC_COLUMNS
DERIVED_COLUMNS = ["image_id", "center_available", "quality_normal", "vector_valid"]
TYPE_MAP = {"float64": pa.float64(), "int64": pa.int64(), "string": pa.string(), "bool": pa.bool_()}
MEMBERS = ("image.jpeg", "design.svg", "contour.svg", "cond.json")


def relative_name(value: object, label: str, *, basename: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty relative path.")
    path = Path(value)
    if path.is_absolute() or PureWindowsPath(value).drive or "\\" in value or ".." in path.parts:
        raise ValueError(f"Unsafe {label}: {value!r}")
    if basename and (len(path.parts) != 1 or value in {".", ".."}):
        raise ValueError(f"{label} must be a filename: {value!r}")
    return value


@dataclass(frozen=True)
class BuildConfig:
    target_csv: str = "result_Size_Area.csv"
    additional_columns: dict[str, dict[str, str]] = field(default_factory=dict)
    create_zip: bool = False

    @classmethod
    def from_mapping(cls, settings: object) -> BuildConfig:
        if not isinstance(settings, dict):
            raise ValueError("Configuration must be a mapping.")
        unknown = set(settings) - {"TargetCSV", "AdditionalColumns", "CreateZip"}
        if unknown:
            raise ValueError(f"Unknown configuration keys: {sorted(unknown)}")
        target_csv = relative_name(settings.get("TargetCSV", "result_Size_Area.csv"), "TargetCSV", basename=True)
        additional = settings.get("AdditionalColumns", {})
        if not isinstance(additional, dict):
            raise ValueError("AdditionalColumns must be a mapping of column to {type, unit}.")
        for name, spec in additional.items():
            if not isinstance(name, str) or not name.strip() or name in REQUIRED_COLUMNS + DERIVED_COLUMNS:
                raise ValueError(f"Invalid or reserved additional column: {name!r}")
            if (not isinstance(spec, dict) or set(spec) != {"type", "unit"}
                    or not isinstance(spec["type"], str) or spec["type"] not in TYPE_MAP
                    or not isinstance(spec["unit"], str)):
                raise ValueError(f"{name}: specify type (float64/int64/string/bool) and unit.")
        create_zip = settings.get("CreateZip", False)
        if not isinstance(create_zip, bool):
            raise ValueError("CreateZip must be true or false.")
        return cls(target_csv, additional, create_zip)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def parse_value(raw: str, kind: str, column: str, line: int) -> object:
    if not raw.strip():
        return None
    if kind == "string":
        return raw
    if kind == "bool":
        if raw.strip().lower() not in {"true", "false"}:
            raise ValueError(f"CSV line {line}: {column} must be true/false.")
        return raw.strip().lower() == "true"
    try:
        value = int(raw) if kind == "int64" else float(raw)
    except ValueError as error:
        raise ValueError(f"CSV line {line}: invalid {kind} in {column}: {raw!r}") from error
    if kind == "float64" and not math.isfinite(value):
        raise ValueError(f"CSV line {line}: nonfinite value in {column}.")
    return value


def read_measurements(path: Path, config: BuildConfig) -> tuple[pa.Table, dict]:
    kinds = {name: "string" for name in TEXT_COLUMNS}
    kinds.update({name: "float64" for name in NUMERIC_COLUMNS})
    kinds.update({name: spec["type"] for name, spec in config.additional_columns.items()})
    values: dict[str, list] = {name: [] for name in kinds}
    seen = set()
    total = excluded = trailing = 0
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader, None)
        if not header:
            raise ValueError("CSV has no header.")
        if len(set(header)) != len(header) or any(not name for name in header):
            raise ValueError("CSV header contains duplicate or empty column names.")
        missing = set(kinds) - set(header)
        if missing:
            raise ValueError(f"CSV missing required/configured columns: {sorted(missing)}")
        positions = {name: header.index(name) for name in kinds}
        for row in reader:
            total += 1
            if len(row) < len(header):
                raise ValueError(f"CSV line {reader.line_num}: fewer fields than header.")
            if len(row) > len(header):
                if any(value.strip() for value in row[len(header):]):
                    raise ValueError(f"CSV line {reader.line_num}: nonempty unnamed trailing fields.")
                trailing += 1
            if row[positions["SID"]].strip():
                excluded += 1
                continue
            parsed = {name: parse_value(row[position], kinds[name], name, reader.line_num)
                      for name, position in positions.items()}
            for name in KEY_COLUMNS + ["PixelX", "PixelY", "ContourSuccess", "DefectFlag"]:
                if parsed[name] is None:
                    raise ValueError(f"CSV line {reader.line_num}: {name} is missing.")
            image = relative_name(parsed["IMAGENAME"], "IMAGENAME", basename=True)
            if Path(image).suffix.lower() != ".png":
                raise ValueError(f"IMAGENAME must end in .png: {image!r}")
            key = tuple(parsed[name] for name in KEY_COLUMNS)
            if key in seen:
                raise ValueError(f"Duplicate pattern key at CSV line {reader.line_num}: {key}")
            seen.add(key)
            for name in kinds:
                values[name].append(parsed[name])
    if not seen:
        raise ValueError("No patterns remain after selecting SID-empty rows.")
    fields = []
    for name, kind in kinds.items():
        if name in NUMERIC_COLUMNS:
            unit = "pixel" if name in {"PixelX", "PixelY"} else "nm"
        else:
            unit = config.additional_columns.get(name, {}).get("unit", "")
        fields.append(pa.field(name, TYPE_MAP[kind], metadata={b"unit": unit.encode("utf-8")}))
    table = pa.Table.from_pydict(values, schema=pa.schema(fields))
    table = table.sort_by([("IMAGENAME", "ascending"), ("LayoutCoordX", "ascending"), ("LayoutCoordY", "ascending")])
    return table, {
        "input_rows": total, "excluded_sid_rows": excluded, "selected_rows": table.num_rows,
        "empty_trailing_fields_normalized_rows": trailing,
        "dropped_columns": [name for name in header if name not in kinds],
    }


def read_assets(archive_path: Path, destination: Path) -> dict:
    source_hash = sha256(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        for member in MEMBERS:
            if names.count(member) != 1:
                raise ValueError(f"{archive_path.name}: missing or duplicate {member}.")
        content = {member: archive.read(member) for member in MEMBERS}
    condition = json.loads(content["cond.json"])
    if not isinstance(condition, dict):
        raise ValueError(f"{archive_path.name}: cond.json must be an object.")
    image = condition.get("image", {})
    shifts = condition.get("matching_shift", {})
    for group, keys in (
        (image, ["actual_size_x", "actual_size_y", "nm_per_pixel_x", "nm_per_pixel_y"]),
        (shifts, ["matching_shift_x_nm", "matching_shift_y_nm", "manual_shift_x_nm", "manual_shift_y_nm"]),
    ):
        if not isinstance(group, dict):
            raise ValueError(f"{archive_path.name}: invalid image/matching_shift metadata.")
        for name in keys:
            value = group.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{archive_path.name}: invalid {name}.")
            if name in image and value <= 0:
                raise ValueError(f"{archive_path.name}: {name} must be positive.")
    with Image.open(io.BytesIO(content["image.jpeg"])) as picture:
        if picture.format != "JPEG" or picture.size != (image["actual_size_x"], image["actual_size_y"]):
            raise ValueError(f"{archive_path.name}: JPEG format/dimensions disagree with cond.json.")
        picture.load()
    for member in ("design.svg", "contour.svg"):
        root = ET.fromstring(content[member])
        if root.tag.split("}")[-1] != "svg":
            raise ValueError(f"{archive_path.name}: {member} is not SVG.")
    if sha256(archive_path) != source_hash:
        raise ValueError(f"{archive_path.name}: source OVLX changed during extraction.")
    destination.mkdir()
    asset_info = {}
    for member, data in content.items():
        (destination / member).write_bytes(data)
        asset_info[member] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return {
        "size_pixels": [image["actual_size_x"], image["actual_size_y"]],
        "nm_per_pixel": [image["nm_per_pixel_x"], image["nm_per_pixel_y"]],
        "source_ovlx_sha256": source_hash,
        "assets": asset_info,
    }


def build_dataset(source: Path, output: Path, config: BuildConfig, *, dataset_id: str,
                  create_zip: bool = False) -> dict:
    started = time.perf_counter()
    config = BuildConfig.from_mapping({
        "TargetCSV": config.target_csv, "AdditionalColumns": config.additional_columns,
        "CreateZip": config.create_zip,
    })
    source, output = source.resolve(), output.resolve()
    if not isinstance(dataset_id, str) or not dataset_id.strip():
        raise ValueError("dataset_id must be a nonempty string.")
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("Source and output must be separate, non-nested directories.")
    zip_path = output.with_name(output.name + ".zip")
    if output.exists() or (create_zip and zip_path.exists()):
        raise FileExistsError("Output dataset/ZIP already exists; choose a new output path.")
    csv_path = source / "report" / config.target_csv
    source_hash = sha256(csv_path)
    table, statistics = read_measurements(csv_path, config)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as work:
        stage = Path(work) / "dataset"
        stage.mkdir()
        (stage / "images").mkdir()
        image_names = table["IMAGENAME"].to_pylist()
        unique_images = sorted(set(image_names))
        ids = {name: index for index, name in enumerate(unique_images)}
        available = [x is not None and y is not None
                     for x, y in zip(table["CenterX"].to_pylist(), table["CenterY"].to_pylist())]
        normal = [flag == "Normal" for flag in table["DefectFlag"].to_pylist()]
        contour = [flag == "Yes" for flag in table["ContourSuccess"].to_pylist()]
        table = table.append_column("image_id", pa.array([ids[name] for name in image_names], type=pa.int32()))
        table = table.append_column("center_available", pa.array(available))
        table = table.append_column("quality_normal", pa.array(normal))
        table = table.append_column("vector_valid", pa.array([
            center and quality and success for center, quality, success in zip(available, normal, contour)
        ]))
        images = []
        offset = 0
        counts = {}
        for name in image_names:
            counts[name] = counts.get(name, 0) + 1
        with pq.ParquetWriter(stage / "measurements.parquet", table.schema, compression="zstd") as writer:
            for image_id, name in enumerate(unique_images):
                count = counts[name]
                archive_path = source / "viewer" / Path(name).with_suffix(".ovlx")
                directory = f"images/{image_id:06d}"
                assets = read_assets(archive_path, stage / directory)
                writer.write_table(table.slice(offset, count), row_group_size=count)
                images.append({
                    "image_id": image_id, "IMAGENAME": name, "directory": directory,
                    "row_group": image_id, "row_offset": offset, "rows": count, **assets,
                })
                offset += count
                if (image_id + 1) % 100 == 0:
                    logging.info("Extracted %s/%s images", image_id + 1, len(unique_images))
        if sha256(csv_path) != source_hash:
            raise ValueError("Source CSV changed during build.")
        columns = []
        for column in table.schema:
            unit = (column.metadata or {}).get(b"unit", b"").decode("utf-8")
            columns.append({"name": column.name, "type": str(column.type), "unit": unit,
                            "nullable": column.nullable})
        write_json(stage / "images.json", images)
        statistics.update({
            "images": len(images), "missing_center_rows": available.count(False),
            "non_normal_rows": normal.count(False),
            "vector_valid_rows": sum(center and quality and success
                                     for center, quality, success in zip(available, normal, contour)),
        })
        manifest = {
            "format": "plugin-viewer-dataset", "format_version": FORMAT_VERSION,
            "builder_version": BUILDER_VERSION, "dataset_id": dataset_id,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "source": {"target_csv": config.target_csv, "csv_sha256": source_hash},
            "configuration": {"AdditionalColumns": config.additional_columns, "SID": "empty"},
            "statistics": statistics,
            "pattern_key": KEY_COLUMNS, "columns": columns,
            "measurements": {"path": "measurements.parquet", "compression": "zstd",
                             "row_groups": len(images), "sha256": sha256(stage / "measurements.parquet")},
            "image_index": {"path": "images.json", "sha256": sha256(stage / "images.json")},
            "coordinates": {
                "pixel": {"origin": "upper-left", "x": "right", "y": "down"},
                "layout": {"unit": "nm", "origin": "source-defined"},
                "center": {"unit": "nm", "coordinate_system": "same as LayoutCoordX/Y"},
                "displacement": {"x": "CenterX - LayoutCoordX", "y": "CenterY - LayoutCoordY"},
                "overlay": "raw OVLX SVG and cond.json preserved; no alignment or rasterization applied",
            },
            "quality": {
                "center_available": "both CenterX and CenterY are non-null (zero is not missing)",
                "quality_normal": "DefectFlag equals Normal",
                "vector_valid": "center_available and quality_normal and ContourSuccess equals Yes",
                "policy": "all SID-empty rows retained; missing centers are null, never zero-filled",
            },
            "build_seconds": time.perf_counter() - started,
        }
        write_json(stage / "manifest.json", manifest)
        if create_zip:
            staged_zip = Path(work) / "dataset.zip"
            with zipfile.ZipFile(staged_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
                for file in sorted(stage.rglob("*")):
                    if file.is_file():
                        archive.write(file, str(Path(output.name) / file.relative_to(stage)),
                                      compress_type=zipfile.ZIP_STORED
                                      if file.suffix in {".jpeg", ".parquet"} else zipfile.ZIP_DEFLATED)
            with zipfile.ZipFile(staged_zip) as archive:
                bad = archive.testzip()
                if bad:
                    raise ValueError(f"ZIP CRC check failed: {bad}")
        if output.exists() or (create_zip and zip_path.exists()):
            raise FileExistsError("Output appeared while building; refusing overwrite.")
        stage.rename(output)
        if create_zip:
            staged_zip.rename(zip_path)
    return {"path": str(output), "patterns": table.num_rows, "images": len(images)}
