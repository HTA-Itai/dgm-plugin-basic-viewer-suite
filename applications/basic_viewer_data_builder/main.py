from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from basic_sqlite_builder import build_basic_sqlite


def main() -> None:
    parser = argparse.ArgumentParser(description="Build one dataset for the BasicViewer.")
    parser.add_argument("trigger", nargs="?", type=Path)
    parser.add_argument("--source", type=Path, help="Directory containing report/ and viewer/.")
    parser.add_argument("--output", type=Path, help="New SQLite file; must not exist.")
    parser.add_argument("--dataset-id")
    parser.add_argument("--target-csv", default="result.csv")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.trigger:
        trigger = json.loads(args.trigger.read_text(encoding="utf-8"))
        if not isinstance(trigger, dict):
            raise ValueError("Trigger must be an object.")
        target = trigger.get("TargetData")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("TargetData is required.")
        source = (Path("input") / target).resolve()
        if not source.is_relative_to(Path("input").resolve()):
            raise ValueError("TargetData must remain inside input/.")
        dataset_id = trigger.get("Msr") or target
        target_csv = trigger.get("TargetCSV") or "result.csv"
        output = Path("output") / f"{dataset_id}.sqlite"
    else:
        if not args.source or not args.output:
            parser.error("--source and --output are required without a trigger.")
        source = args.source
        output = args.output
        dataset_id = args.dataset_id or source.resolve().name
        target_csv = args.target_csv

    result = build_basic_sqlite(source, output, dataset_id, target_csv)
    logging.info("Completed dataset: %s (%s patterns, %s images)",
                 result["path"], result["patterns"], result["images"])


if __name__ == "__main__":
    main()
