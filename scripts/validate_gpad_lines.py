#!/usr/bin/env python3
"""Compare the ratchet's estimated GPAD line counts with a real GPAD export.

Inputs: a run's ``model_stats.jsonl`` (per model: ``total_lines_est``) and a
directory of per-model GPAD files named ``<model_id>.gpad``. In a GPAD file a
line starting with ``!`` is a header; every other non-blank line is one
annotation line.

Prints one row per model (model_id, est, actual, diff) and an aggregate block:
sum est, sum actual, ratio, exact matches, median absolute error, the N worst
models by |diff|. Models with no GPAD file are listed as missing and excluded
from the aggregates (they say nothing about the estimate).

    python3 scripts/validate_gpad_lines.py \\
        --model-stats target_YYYYMMDD/ratchet/model_stats.jsonl \\
        --gpad-dir /path/to/gpad --worst 20 [--no-per-model]
"""

import argparse
import json
import statistics
import sys
from pathlib import Path


def count_gpad_lines(path):
    n = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.strip() and not line.startswith("!"):
                n += 1
    return n


def read_model_stats(path):
    records = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def compare(model_stats_path, gpad_dir, worst=20):
    gpad_dir = Path(gpad_dir)
    rows = []
    missing = []
    for rec in read_model_stats(model_stats_path):
        model_id = rec["model"]
        est = rec.get("total_lines_est")
        gpad = gpad_dir / f"{model_id}.gpad"
        if est is None or not gpad.exists():
            missing.append(model_id)
            continue
        actual = count_gpad_lines(gpad)
        rows.append({"model_id": model_id, "est": est, "actual": actual,
                     "diff": est - actual})
    sum_est = sum(r["est"] for r in rows)
    sum_actual = sum(r["actual"] for r in rows)
    abs_errors = [abs(r["diff"]) for r in rows]
    return {
        "rows": rows,
        "missing": missing,
        "n": len(rows),
        "sum_est": sum_est,
        "sum_actual": sum_actual,
        "ratio": (sum_est / sum_actual) if sum_actual else None,
        "exact": sum(1 for r in rows if r["diff"] == 0),
        "median_abs_err": statistics.median(abs_errors) if abs_errors else None,
        "worst": sorted(rows, key=lambda r: (-abs(r["diff"]), r["model_id"]))[:worst],
    }


def format_report(result):
    out = []
    out.append("# models compared\t%d" % result["n"])
    out.append("# models missing a .gpad\t%d" % len(result["missing"]))
    out.append("# sum est\t%d" % result["sum_est"])
    out.append("# sum actual\t%d" % result["sum_actual"])
    ratio = result["ratio"]
    out.append("# ratio est/actual\t%s" % ("n/a" if ratio is None else "%.4f" % ratio))
    out.append("# exact matches\t%d" % result["exact"])
    med = result["median_abs_err"]
    out.append("# median abs error\t%s" % ("n/a" if med is None else "%g" % med))
    if result["worst"]:
        out.append("# worst %d models by |est - actual|:" % len(result["worst"]))
        out.append("# model_id\test\tactual\tdiff")
        for r in result["worst"]:
            out.append("# %s\t%d\t%d\t%+d" % (r["model_id"], r["est"], r["actual"], r["diff"]))
    if result["missing"]:
        shown = result["missing"][:10]
        more = len(result["missing"]) - len(shown)
        out.append("# missing: %s%s" % (", ".join(shown), " ... +%d" % more if more else ""))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Validate ratchet GPAD line estimates against real per-model GPAD files.")
    ap.add_argument("--model-stats", required=True, help="A run's model_stats.jsonl.")
    ap.add_argument("--gpad-dir", required=True,
                    help="Directory of <model_id>.gpad files (one per model).")
    ap.add_argument("--worst", type=int, default=20, help="How many worst models to list.")
    ap.add_argument("--no-per-model", action="store_true",
                    help="Print only the aggregate block.")
    args = ap.parse_args(argv)

    result = compare(args.model_stats, args.gpad_dir, worst=args.worst)
    if not args.no_per_model:
        print("model_id\test\tactual\tdiff")
        for r in result["rows"]:
            print("%s\t%d\t%d\t%+d" % (r["model_id"], r["est"], r["actual"], r["diff"]))
    print(format_report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
