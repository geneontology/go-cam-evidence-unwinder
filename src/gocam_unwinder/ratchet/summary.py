"""Roll-up outputs written beside a run's per-stage artifacts.

  * ``summary.tsv``        -- one row per stage: in / removed / out in units
                              (models for Phase A) AND in estimated GPAD lines.
  * ``ledger.tsv``         -- one row per removed annotation unit: where (model,
                              rule), why (the complete failing-check set), by
                              whom (groups, contributors), how much (edges,
                              estimated GPAD lines).
  * ``ledger.by-group.tsv`` / ``ledger.by-contributor.tsv`` -- roll-ups of the
                              ledger per editorial group / contributor, with one
                              lost-units column per unit rule.
  * ``models.partial.tsv`` / ``models.all-standard.tsv`` -- the model lists the
                              freeze-and-regenerate workflow consumes.

The roll-ups and model lists are recomputed from the merged ``ledger.tsv`` +
``SB-final.units.manifest.jsonl`` + ``model_stats.jsonl`` (never by summing
per-shard roll-ups), so a sharded run and a single-process run agree.
"""

import json
from collections import defaultdict
from pathlib import Path

SUMMARY_COLUMNS = ("Phase", "Stage", "Rule", "In", "Removed", "Out",
                   "LinesIn", "LinesRemoved", "LinesOut")

LEDGER_COLUMNS = ("model_id", "unit_index", "modelstate", "groups", "contributors",
                  "removed_by_rule", "all_failed_checks", "edges", "gpad_lines_est",
                  "title")
LEDGER_LIST_COLUMNS = ("groups", "contributors", "all_failed_checks")

MODEL_LIST_COLUMNS = ("model_id", "modelstate", "groups", "total_units",
                      "passing_units", "total_lines_est", "passing_lines_est", "title")

# Bucket label for a unit/model with no group or no contributor, so every unit
# lands in exactly one row of the "(none)" kind rather than vanishing.
NONE_BUCKET = "(none)"
LIST_SEP = "|"


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return LIST_SEP.join(str(v) for v in value)
    return str(value).replace("\t", " ").replace("\n", " ")


def _write_tsv(path, columns, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        fh.write("\t".join(columns) + "\n")
        for row in rows:
            fh.write("\t".join(_cell(row.get(c)) for c in columns) + "\n")
    return path


def _read_tsv(path):
    rows = []
    with open(path) as fh:
        header = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            parts += [""] * (len(header) - len(parts))
            rows.append(dict(zip(header, parts)))
    return rows


def _split_list(cell):
    return [v for v in cell.split(LIST_SEP) if v] if cell else []


# ----------------------------------------------------------------------
# summary.tsv
# ----------------------------------------------------------------------

def write_summary(stages, path):
    """``stages`` expose phase, index, rule_id, in_count, removed, out_count and
    (optionally None) lines_in, lines_removed, lines_out. Empty line cells mean
    "not estimated"."""
    path = Path(path)
    lines = ["\t".join(SUMMARY_COLUMNS)]
    for s in stages:
        lines.append("\t".join([
            s.phase, f"{s.index:02d}", s.rule_id,
            str(s.in_count), str(s.removed), str(s.out_count),
            _cell(getattr(s, "lines_in", None)),
            _cell(getattr(s, "lines_removed", None)),
            _cell(getattr(s, "lines_out", None)),
        ]))
    path.write_text("\n".join(lines) + "\n")
    return path


# ----------------------------------------------------------------------
# ledger.tsv and its roll-ups
# ----------------------------------------------------------------------

def write_ledger(rows, path):
    return _write_tsv(path, LEDGER_COLUMNS, rows)


def read_ledger(path):
    rows = _read_tsv(path)
    for row in rows:
        for col in LEDGER_LIST_COLUMNS:
            row[col] = _split_list(row.get(col, ""))
        row["unit_index"] = int(row["unit_index"]) if row["unit_index"] else None
        row["edges"] = int(row["edges"]) if row["edges"] else 0
        row["gpad_lines_est"] = int(row["gpad_lines_est"]) if row["gpad_lines_est"] else 0
    return rows


def read_model_stats(path):
    records = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def _read_unit_manifest(path):
    items = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def _or_none_bucket(values):
    return list(values) if values else [NONE_BUCKET]


def _rollup(ledger_rows, totals, key, unit_rule_ids):
    """Rows keyed by ``key`` ("groups" | "contributors") -> aggregate dicts."""
    agg = {}

    def bucket(name):
        if name not in agg:
            agg[name] = {"total_units": 0, "lost_units": 0, "lost_lines_est": 0,
                         **{rule: 0 for rule in unit_rule_ids}}
        return agg[name]

    for name, n in totals.items():
        bucket(name)["total_units"] += n
    for row in ledger_rows:
        for name in _or_none_bucket(row[key]):
            b = bucket(name)
            b["lost_units"] += 1
            b["lost_lines_est"] += row["gpad_lines_est"]
            rule = row["removed_by_rule"]
            if rule not in b:
                b[rule] = 0  # a rule absent from unit_rule_ids still gets counted
            b[rule] += 1
    return agg


def _rollup_rows(agg, label_col):
    order = sorted(agg.items(),
                   key=lambda kv: (-kv[1]["lost_units"], -kv[1]["total_units"], kv[0]))
    return [{label_col: name, **b} for name, b in order]


def write_rollups(out_dir, unit_rule_ids):
    """Recompute ``ledger.by-group.tsv`` and ``ledger.by-contributor.tsv`` from
    the (merged) ledger, surviving-unit manifest and model stats in ``out_dir``.

    "total units" per group comes from model_stats (groups are model-level);
    per contributor it is counted over every unit, surviving or removed, that
    lists the contributor. A unit with two contributors counts once in each row,
    so contributor columns do not sum to the summary totals (groups do, as long
    as every model has at most one group).
    """
    out_dir = Path(out_dir)
    ledger_rows = read_ledger(out_dir / "ledger.tsv")
    model_stats = read_model_stats(out_dir / "model_stats.jsonl")
    survivors = _read_unit_manifest(out_dir / "SB-final.units.manifest.jsonl")

    group_totals = defaultdict(int)
    for rec in model_stats:
        for g in _or_none_bucket(rec.get("groups")):
            group_totals[g] += rec.get("total_units", 0)

    contributor_totals = defaultdict(int)
    for it in survivors:
        for c in _or_none_bucket((it.get("meta") or {}).get("contributors")):
            contributor_totals[c] += 1
    for row in ledger_rows:
        for c in _or_none_bucket(row["contributors"]):
            contributor_totals[c] += 1

    rule_cols = list(unit_rule_ids)
    for row in ledger_rows:
        if row["removed_by_rule"] not in rule_cols:
            rule_cols.append(row["removed_by_rule"])

    by_group = _rollup(ledger_rows, group_totals, "groups", rule_cols)
    by_contrib = _rollup(ledger_rows, contributor_totals, "contributors", rule_cols)
    base = ["total_units", "lost_units", "lost_lines_est"] + rule_cols
    paths = (
        _write_tsv(out_dir / "ledger.by-group.tsv", ["group"] + base,
                   _rollup_rows(by_group, "group")),
        _write_tsv(out_dir / "ledger.by-contributor.tsv", ["contributor"] + base,
                   _rollup_rows(by_contrib, "contributor")),
    )
    return paths


def read_rollup(path):
    """-> (rule column ids, rows as dicts with int counts)."""
    rows = _read_tsv(path)
    with open(path) as fh:
        header = fh.readline().rstrip("\n").split("\t")
    rule_cols = header[4:]
    for row in rows:
        for col in header[1:]:
            row[col] = int(row[col]) if row[col] else 0
    return rule_cols, rows


# ----------------------------------------------------------------------
# model lists for the freeze-and-regenerate workflow
# ----------------------------------------------------------------------

def _model_list_row(rec):
    return {
        "model_id": rec["model"],
        "modelstate": rec.get("modelstate"),
        "groups": rec.get("groups") or [],
        "total_units": rec.get("total_units", 0),
        "passing_units": rec.get("passing_units", 0),
        "total_lines_est": rec.get("total_lines_est"),
        "passing_lines_est": rec.get("passing_lines_est"),
        "title": rec.get("title"),
    }


def write_model_lists(out_dir):
    """``models.partial.tsv``: 0 < passing < total (frozen, then regenerated
    from the surviving units). ``models.all-standard.tsv``: passing == total > 0.
    Models with 0 passing units, or 0 units, are in neither list."""
    out_dir = Path(out_dir)
    records = read_model_stats(out_dir / "model_stats.jsonl")
    partial, all_standard = [], []
    for rec in records:
        total, passing = rec.get("total_units", 0), rec.get("passing_units", 0)
        if 0 < passing < total:
            partial.append(_model_list_row(rec))
        elif passing == total and total > 0:
            all_standard.append(_model_list_row(rec))
    key = lambda r: r["model_id"]
    return (
        _write_tsv(out_dir / "models.partial.tsv", MODEL_LIST_COLUMNS,
                   sorted(partial, key=key)),
        _write_tsv(out_dir / "models.all-standard.tsv", MODEL_LIST_COLUMNS,
                   sorted(all_standard, key=key)),
    )
