from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path, PureWindowsPath
import shutil
import tempfile

import yaml

from builder import BuildConfig, build_dataset, relative_name
from sqlite_builder import build_sqlite
from release_bundle import VIEWER_NAME, VIEWER_ZIP, exclusive_lock, sha256, validate_app, verify_viewer, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Build one dataset for the local comparison viewer.")
    parser.add_argument("trigger", nargs="?", type=Path)
    parser.add_argument("--source", type=Path, help="Directory containing report/ and viewer/.")
    parser.add_argument("--output", type=Path, help="New SQLite file (or legacy directory); must not exist.")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--dataset-id", help="Stable source dataset/MSR identifier for standalone use.")
    parser.add_argument("--zip", action="store_true", help="Also write <output>.zip.")
    parser.add_argument("--format", choices=("sqlite", "parquet"), default="sqlite",
                        help="Default: one role-independent Viewer SQLite file. parquet is the legacy directory format.")
    parser.add_argument("--msr", type=Path, help="Source MSR supplying the wafer existence mask and layout.")
    parser.add_argument("--data-only", action="store_true",
                        help="Development only: allow an unpackaged source trigger without a Viewer.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.trigger:
        if args.source or args.output or args.dataset_id:
            parser.error("Use a trigger OR --source/--output/--dataset-id, not both.")
        trigger = json.loads(args.trigger.read_text(encoding="utf-8"))
        if not isinstance(trigger, dict):
            raise ValueError("Trigger must be an object.")
        target = trigger.get("TargetData")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("Trigger TargetData is required.")
        source = (Path("input") / target).resolve()
        if not source.is_relative_to(Path("input").resolve()):
            raise ValueError("TargetData must remain inside input/.")
        dataset_id = trigger.get("Msr") or target
        if args.format == "sqlite":
            msr_id = trigger.get("Msr")
            if msr_id is None or msr_id == "":
                logging.warning("Trigger Msr is missing; using output/dataset.sqlite.")
                output = Path("output/dataset.sqlite")
            else:
                name = relative_name(msr_id, "Trigger Msr", basename=True)
                filename = f"{name}.sqlite"
                if (any(ord(char) < 32 or char in '<>:"/\\|?*' for char in name)
                        or name.endswith((" ", "."))
                        or PureWindowsPath(filename).is_reserved()):
                    raise ValueError(f"Trigger Msr cannot be used as a Windows filename: {name!r}")
                output = Path("output") / filename
        else:
            output = Path("output/dataset")
        msr = args.msr
        if args.format == "sqlite":
            msr_name = trigger.get("SourceMsr", f"msr/{dataset_id}")
            if msr is None:
                if not isinstance(msr_name, str) or not msr_name.strip():
                    raise ValueError("SourceMsr must be a nonempty input-relative path.")
                msr = Path("input") / msr_name
            if msr is None or not msr.resolve().is_relative_to(Path("input").resolve()):
                raise ValueError("SQLite build requires SourceMsr (or --msr) inside input/.")
        target_config = trigger.get("TargetConfig") or "vector.yml"
        if not isinstance(target_config, str):
            raise ValueError("TargetConfig must be a filename.")
        config_path = args.config or Path("config") / target_config
        if not config_path.resolve().is_relative_to(Path("config").resolve()):
            raise ValueError("TargetConfig must remain inside config/.")
    else:
        if not args.source or not args.output:
            parser.error("--source and --output are required without a trigger.")
        source, output = args.source, args.output
        config_path = args.config or Path(__file__).parent / "config" / "vector.yml"
        dataset_id = args.dataset_id or source.resolve().name
        msr = args.msr

    settings = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config = BuildConfig.from_mapping(settings)
    release_dir = Path(__file__).resolve().parent
    if args.data_only and (release_dir / "release.json").exists():
        parser.error("--data-only is not allowed in an installed release.")
    if args.trigger and args.format != "sqlite" and (release_dir / "release.json").exists():
        parser.error("Installed releases require SQLite with the frozen Viewer; parquet is development only.")
    if args.format == "sqlite":
        if msr is None:
            parser.error("--msr is required for SQLite wafer layout data.")
        if args.zip:
            parser.error("--zip is only for the legacy parquet format; SQLite is already one file.")
        if args.trigger and not args.data_only:
            release = validate_app(release_dir)
            output.parent.mkdir(parents=True, exist_ok=True)
            destinations = (output, output.parent / VIEWER_NAME, output.parent / "result.json")
            for destination in destinations:
                if destination.exists():
                    raise FileExistsError(destination)
            with exclusive_lock(output.parent / ".viewer-result.lock"), \
                    tempfile.TemporaryDirectory(prefix=".viewer-result-", dir=output.parent) as temporary:
                for destination in destinations:
                    if destination.exists():
                        raise FileExistsError(destination)
                stage = Path(temporary)
                verify_viewer(release_dir / VIEWER_ZIP, version=release["version"], destination=stage / VIEWER_NAME)
                result = build_sqlite(source, stage / output.name, config, dataset_id=dataset_id, msr=msr)
                write_json(stage / "result.json", dict(
                    schema_version=1, status="complete", version=release["version"],
                    source_commit=release["source_commit"], dataset=output.name,
                    dataset_sha256=sha256(stage / output.name),
                    viewer=VIEWER_NAME, viewer_sha256=release["viewer_sha256"],
                    patterns=result["patterns"], images=result["images"],
                ))
                published = []
                try:
                    # Publish the completion manifest last, never a partial success marker.
                    for destination in destinations:
                        shutil.move(str(stage / destination.name), destination)
                        published.append(destination)
                except BaseException:
                    for destination in reversed(published):
                        if destination.is_dir():
                            shutil.rmtree(destination)
                        else:
                            destination.unlink()
                    raise
                result["path"] = str(output)
        else:
            if args.trigger:
                logging.warning("Development data-only trigger: no frozen Viewer is included.")
            result = build_sqlite(source, output, config, dataset_id=dataset_id, msr=msr)
    else:
        result = build_dataset(source, output, config, dataset_id=dataset_id, create_zip=args.zip or config.create_zip)
    logging.info("Completed dataset: %s (%s patterns, %s images)",
                 result["path"], result["patterns"], result["images"])


if __name__ == "__main__":
    main()
