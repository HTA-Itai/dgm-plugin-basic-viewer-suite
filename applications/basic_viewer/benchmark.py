"""Measure local two-file reads; run after Builder, never convert source data here."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import time

from engine import Store


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def measure(paths, repetitions):
    runs = []
    for _ in range(repetitions):
        run = {}
        def timed(name, action):
            start = time.perf_counter()
            result = action()
            run[name] = time.perf_counter() - start
            return result
        store = timed("validate_seconds", lambda: Store(*paths))
        index = timed("index_seconds", store.index)
        pno = min(e["pno"] for e in index["images"])
        result = timed("statistics_seconds", lambda: store.statistics(dict(pno=pno)))
        preferred = index["metadata"].get("test_chip_override", {}).get("initial_image_id")
        first = next(e["id"] for e in index["images"] if e["role"] == "base")
        image = timed("image_seconds", lambda: store.image(preferred or first))
        run.update(images=len(index["images"]), pairs=result["paired_images"],
                   valid_counts=[r["n"] for r in result["table"] if r["metric"] == "dx"],
                   matched_patterns=image["matched"],
                   vectors={k: len(v) for k, v in image["vectors"].items()})
        runs.append(run)
    return dict(runs=runs, median_seconds={k: statistics.median(r[k] for r in runs)
                                         for k in runs[0] if k.endswith("_seconds")})


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("base", type=Path)
    cli.add_argument("comparison", type=Path)
    cli.add_argument("--legacy", type=Path)
    cli.add_argument("--repetitions", type=int, default=3)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    if args.repetitions < 1:
        cli.error("--repetitions must be positive")
    paths = [args.base, args.comparison] + ([args.legacy] if args.legacy else [])
    before = {str(p): digest(p) for p in paths}
    report = dict(environment="Linux, local filesystem, caches not cleared; not a Windows/cold-cache benchmark",
                  files={str(p): dict(bytes=p.stat().st_size, sha256=before[str(p)]) for p in paths},
                  two_files=measure([args.base, args.comparison], args.repetitions))
    if args.legacy:
        report["legacy_combined"] = measure([args.legacy], args.repetitions)
    assert before == {str(p): digest(p) for p in paths}, "Input files changed during read-only benchmark"
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
