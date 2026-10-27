"""Run the two-level standard-annotation ratchet end to end.

Phase A (model): a disk-materialized model ratchet. Each model rule reads the
prior stage's survivor manifest (a set of .ttl paths), removes models, and
writes survivors + rejects + a per-stage report under ``<out>/phase-a/``.

Phase B (unit): for each surviving model, parse once (classify=False) and run
the ordered unit rules over its annotation units IN MEMORY; a unit is removed by
the first rule that flags it (monotonic). Per-stage reject reports are aggregated
across all models under ``<out>/phase-b/``. This avoids re-parsing / serializing
rdflib graphs between unit stages while preserving the per-stage shrink.

A unit survives all unit rules iff no check flags it iff it is a standard
annotation -- so the final surviving unit set equals the union of every surviving
model's standard annotations.

Reporting axes (2026-10-26): every unit carries an estimated GPAD line count and
its contributors; every removed unit's record carries the COMPLETE set of
failing checks (not just the rule that removed it); Phase A removals get a cheap
counting pass so the summary shows line totals per model stage too, except for
the set-aside rules (``SET_ASIDE_RULE_IDS``), whose models are not losses.
"""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .core import Item, Manifest
from .engine import resolve_adapter
from .model_rules import (build_model_rule_registry, load_true_gocam_ids,
                          resolve_group_allowlist)
from .rules import load_rules
from .runner import _write_jsonl, _write_rejects_tsv, report_row, run_stage
from .summary import write_ledger, write_model_lists, write_rollups, write_summary
from ..gocam_ttl import NESTING_ATTRIBUTABLE_CHECKS
from .unit_rules import (build_unit_rule_registry, count_model, model_contributors,
                         parse_model_units)

# Phase A rules whose removals are "set aside", not lost: true GO-CAMs stay as
# they are in the end state, so their lines are neither counted nor parsed.
# Their models are reported on a separate ``A-set-aside`` summary row.
SET_ASIDE_RULE_IDS = frozenset({"not_true_gocam"})
SET_ASIDE_PHASE = "A-set-aside"

PHASE_ORDER = {"A": 0, SET_ASIDE_PHASE: 1, "B": 2}


@dataclass
class StageStat:
    phase: str
    index: int
    rule_id: str
    in_count: int
    out_count: int
    # Estimated GPAD lines entering / leaving the stage; None = not estimated
    # (no builder, so no counting pass) -- written as empty summary cells.
    lines_in: Optional[int] = None
    lines_out: Optional[int] = None

    @property
    def removed(self) -> int:
        return self.in_count - self.out_count

    @property
    def lines_removed(self) -> Optional[int]:
        if self.lines_in is None or self.lines_out is None:
            return None
        return self.lines_in - self.lines_out


@dataclass
class RatchetResult:
    stages: List[StageStat]
    phase_a_survivors: List[str]
    final_units: List[str]
    out_dir: Path


def _lines(units):
    return sum(u.meta.get("gpad_lines_est", 0) for u in units)


def _run_phase_b(builder, models_manifest, unit_rules, unit_registry, out_dir,
                 base_index=0):
    """Stream each surviving model's units through the ordered unit rules,
    aggregating per-stage reports. Returns
    (stage_stats, final_unit_items, model_records, ledger_rows)."""
    reports_dir = out_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    adapters = [(j, rule, resolve_adapter(rule, unit_registry))
                for j, rule in enumerate(unit_rules)]
    n = len(adapters)
    rows = [[] for _ in range(n)]
    rejects = [[] for _ in range(n)]
    in_count = [0] * n
    out_count = [0] * n
    lines_in = [0] * n
    lines_out = [0] * n
    final_units = []
    model_records = []
    ledger_rows = []

    for item in models_manifest.items:
        gcg, units = parse_model_units(builder, item.path)
        total_units = len(units)
        total_lines = _lines(units)
        # Edges the nested-anatomy fixer would rewrite (bnode ids). A removed
        # unit is "fixable" iff the fixer touches one of its edges AND every
        # failing check is nesting-attributable -- the same definition as
        # ModelStats.fixable_nonstandard_count, so the two reports agree.
        plan = getattr(builder, "plan_nested_anatomy_fixes", None)
        fixer_bnodes = {r["bnode_id"] for r in plan(gcg, warn=False)} if plan else set()
        current = units
        for j, rule, adapter in adapters:
            in_count[j] += len(current)
            lines_in[j] += _lines(current)
            survivors = []
            for unit in current:
                verdict = adapter.apply(unit, builder)
                if not verdict.keep:
                    # The stage attribution stays with this rule; the complete
                    # failing set answers "why" in full (a unit is removed at
                    # its FIRST failing stage, so later failures are otherwise
                    # invisible).
                    checks = builder.run_all_checks(gcg, unit.payload["annotation"])
                    verdict.detail["all_failed_checks"] = sorted(
                        k for k, flagged in checks.items() if flagged)
                    annotation = unit.payload["annotation"]
                    verdict.detail["fixable"] = int(
                        any(b in fixer_bnodes for b in annotation.edges)
                        and set(verdict.detail["all_failed_checks"]) <= NESTING_ATTRIBUTABLE_CHECKS)
                    ledger_rows.append({
                        "model_id": item.id,
                        "unit_index": unit.meta.get("unit_index"),
                        "modelstate": gcg.modelstate,
                        "groups": gcg.groups or [],
                        "contributors": unit.meta.get("contributors") or [],
                        "removed_by_rule": rule.id,
                        "all_failed_checks": verdict.detail["all_failed_checks"],
                        "fixable": verdict.detail["fixable"],
                        "edges": unit.meta.get("edges"),
                        "gpad_lines_est": unit.meta.get("gpad_lines_est"),
                        "title": gcg.title,
                    })
                rows[j].append(report_row(unit, verdict, rule.id,
                                          model=unit.meta.get("model_id")))
                if verdict.keep:
                    survivors.append(unit)
                else:
                    rejects[j].append((unit, verdict))
            out_count[j] += len(survivors)
            lines_out[j] += _lines(survivors)
            current = survivors
        final_units.extend(current)
        # Per-model stats for the dashboard's explorable axes (modelstate,
        # editorial group, pass rate) and the model lists. groups are raw
        # providedBy URIs unless the builder has a groups lookup; the dashboard
        # resolves them to labels.
        model_records.append({
            "model": item.id,
            "modelstate": gcg.modelstate,
            "groups": gcg.groups or [],
            "contributors": model_contributors(gcg),
            "title": gcg.title,
            "total_units": total_units,
            "passing_units": len(current),
            "total_lines_est": total_lines,
            "passing_lines_est": _lines(current),
            "total_evidence_pairs": sum(u.meta.get("evidence_pairs", 0) for u in units),
        })

    stage_stats = []
    for j, rule, _ in adapters:
        idx = base_index + j
        prefix = f"{idx:02d}-{rule.id}"
        _write_jsonl(reports_dir / f"{prefix}.jsonl", rows[j])
        _write_rejects_tsv(out_dir / f"{prefix}.rejects.tsv", rejects[j], rule.id)
        stage_stats.append(StageStat("B", idx, rule.id, in_count[j], out_count[j],
                                     lines_in[j], lines_out[j]))
    return stage_stats, final_units, model_records, ledger_rows


def _make_count_hook(builder):
    """Phase A counting pass: parse a removed model once (same parser as Phase
    B) so its unit / line / contributor totals reach the reject record and the
    summary's line columns. A parse failure is recorded, not fatal: the
    counting pass is reporting, the removal already happened."""
    def on_reject(item, verdict):
        try:
            item.meta.update(count_model(builder, item.path))
        except Exception as exc:  # noqa: BLE001 -- reporting must not kill the run
            item.meta["count_error"] = f"{type(exc).__name__}: {exc}"
    return on_reject


def _fill_phase_a_lines(stages, phase_a_results, lines_into_phase_b):
    """Fill Phase A line columns backwards from the Phase B input total:
    lines_out(i) = lines_in(i+1); lines_in(i) = lines_out(i) + lines removed
    at i (0 for a set-aside rule). The counted universe therefore excludes
    set-aside models. Returns the extra set-aside summary rows (one per
    set-aside rule that removed at least one model; In = Out = models set
    aside, line cells empty because those models are never parsed)."""
    set_aside_rows = []
    lines_out = lines_into_phase_b
    for stat, result in reversed(list(zip(stages, phase_a_results))):
        if stat.rule_id in SET_ASIDE_RULE_IDS:
            removed_lines = 0
            n = len(result.rejected)
            if n:
                set_aside_rows.append(
                    StageStat(SET_ASIDE_PHASE, stat.index, stat.rule_id, n, n))
        else:
            removed_lines = sum(it.meta.get("gpad_lines_est", 0)
                                for it, _ in result.rejected)
        stat.lines_out = lines_out
        stat.lines_in = lines_out + removed_lines
        lines_out = stat.lines_in
    return list(reversed(set_aside_rows))


def _set_aside_rows_without_lines(stages, phase_a_results):
    rows = []
    for stat, result in zip(stages, phase_a_results):
        n = len(result.rejected)
        if stat.rule_id in SET_ASIDE_RULE_IDS and n:
            rows.append(StageStat(SET_ASIDE_PHASE, stat.index, stat.rule_id, n, n))
    return rows


def _sort_stages(stages):
    return sorted(stages, key=lambda s: (PHASE_ORDER.get(s.phase, 9), s.index))


def run_ratchet(models_dir, rules_dir="rules", out_dir=None, *, go=None, ro=None,
                groups=None, skip_prefixes=None, true_gocam_ids=None,
                true_gocam_source=None, limit=None, builder=None,
                model_paths=None, group_allowlist=None) -> RatchetResult:
    """Run the full two-level ratchet over ``models_dir`` (or an explicit
    ``model_paths`` list, used by the sharded runner).

    builder -- optional prebuilt GoCamGraphBuilder (avoids re-parsing the GO
               ontology in tests). If absent and ``go`` is given, one is built.
    """
    out_dir = Path(out_dir)
    phase_a_dir = out_dir / "phase-a"
    phase_b_dir = out_dir / "phase-b"
    phase_a_dir.mkdir(parents=True, exist_ok=True)

    if true_gocam_ids is None and true_gocam_source is not None:
        true_gocam_ids = load_true_gocam_ids(true_gocam_source)
    true_gocam_ids = true_gocam_ids or set()

    if model_paths is not None:
        ttls = [Path(p) for p in model_paths]
    else:
        ttls = sorted(p for p in Path(models_dir).iterdir() if p.suffix == ".ttl")
    if limit:
        ttls = ttls[:limit]
    manifest = Manifest([Item(id=p.stem, path=p) for p in ttls])

    stages: List[StageStat] = []

    if builder is None and go is not None:
        from ..gocam_ttl import GoCamGraphBuilder
        builder = GoCamGraphBuilder(go, ro, groups)
    count_hook = _make_count_hook(builder) if builder is not None else None

    # --- Phase A: model ratchet ---
    model_rules = load_rules(rules_dir, phase="model")
    model_registry = build_model_rule_registry(skip_prefixes=skip_prefixes,
                                               true_gocam_ids=true_gocam_ids,
                                               group_allowlist=group_allowlist)
    phase_a_results = []
    for i, rule in enumerate(model_rules):
        adapter = resolve_adapter(rule, model_registry)
        on_reject = None if rule.id in SET_ASIDE_RULE_IDS else count_hook
        result = run_stage(adapter, manifest, out_dir=phase_a_dir, index=i,
                           stage_id=rule.id, ctx=None, on_reject=on_reject)
        stages.append(StageStat("A", i, rule.id, result.in_count, result.out_count))
        phase_a_results.append(result)
        manifest = result.survivors
    phase_a_survivors = list(manifest.ids)
    manifest.write(out_dir / "SA-final.models.manifest.jsonl")

    # --- Phase B: unit ratchet over surviving models ---
    final_units: List[str] = []
    if builder is not None:
        phase_b_dir.mkdir(parents=True, exist_ok=True)
        unit_rules = load_rules(rules_dir, phase="unit")
        unit_registry = build_unit_rule_registry()
        b_stats, final_unit_items, model_records, ledger_rows = _run_phase_b(
            builder, manifest, unit_rules, unit_registry, phase_b_dir,
            base_index=len(model_rules))
        final_units = [u.id for u in final_unit_items]
        stages.extend(_fill_phase_a_lines(
            stages[:len(model_rules)], phase_a_results,
            b_stats[0].lines_in if b_stats else sum(
                r["total_lines_est"] for r in model_records)))
        stages.extend(b_stats)
        # Survivors keep their meta (model, index, edges, lines, contributors):
        # this manifest is what the regenerate step and the roll-ups read.
        Manifest(final_unit_items).write(out_dir / "SB-final.units.manifest.jsonl")
        with open(out_dir / "model_stats.jsonl", "w") as fh:
            for rec in model_records:
                fh.write(json.dumps(rec) + "\n")
        write_ledger(ledger_rows, out_dir / "ledger.tsv")
        write_rollups(out_dir, [r.id for r in unit_rules])
        write_model_lists(out_dir)
    else:
        stages.extend(_set_aside_rows_without_lines(stages, phase_a_results))

    stages = _sort_stages(stages)
    write_summary(stages, out_dir / "summary.tsv")
    return RatchetResult(stages, phase_a_survivors, final_units, out_dir)


# ----------------------------------------------------------------------
# Sharding (Task 8): split the corpus into N independent shards, run each as
# its own ratchet concurrently, then merge. A model is never split across
# shards, so the merged result is IDENTICAL to a single-process run (the only
# cross-item dependencies -- the annotation partition and the partition-
# dependent unit checks -- stay inside one model). Shards resume via a .done
# marker; each worker process loads its own GO ontology copy.
# ----------------------------------------------------------------------

@dataclass
class ShardedResult:
    stages: List[StageStat]
    out_dir: Path
    n_shards: int


def _partition(items, n):
    """Round-robin partition (spreads file-size skew across shards)."""
    return [items[k::n] for k in range(n)]


def _save_stage_stats(shard_dir, stages):
    data = [{"phase": s.phase, "index": s.index, "rule_id": s.rule_id,
             "in_count": s.in_count, "out_count": s.out_count,
             "lines_in": s.lines_in, "lines_out": s.lines_out} for s in stages]
    (shard_dir / "stages.json").write_text(json.dumps(data))


def _load_stage_stats(shard_dir):
    data = json.loads((shard_dir / "stages.json").read_text())
    return [StageStat(d["phase"], d["index"], d["rule_id"],
                      d["in_count"], d["out_count"],
                      d.get("lines_in"), d.get("lines_out")) for d in data]


def _run_shard(task):
    """Worker entry point (top-level so it is picklable for multiprocessing).
    Runs the ratchet over one shard's models, with shard-level resume."""
    (k, model_path_strs, rules_dir, out_root, go, ro, groups,
     skip_prefixes, true_gocam_ids, group_allowlist) = task
    shard_dir = Path(out_root) / "shards" / f"shard-{k:03d}"
    if (shard_dir / ".done").exists():
        return _load_stage_stats(shard_dir)  # resume: already complete
    shard_dir.mkdir(parents=True, exist_ok=True)
    result = run_ratchet(
        None, rules_dir=rules_dir, out_dir=shard_dir, go=go, ro=ro, groups=groups,
        skip_prefixes=skip_prefixes, true_gocam_ids=set(true_gocam_ids),
        model_paths=model_path_strs, group_allowlist=set(group_allowlist or ()))
    _save_stage_stats(shard_dir, result.stages)
    (shard_dir / ".done").write_text("ok\n")
    return result.stages


def _add_opt(a, b):
    """Sum of two optional line counts; None if either is not estimated."""
    return None if a is None or b is None else a + b


def _aggregate_stage_stats(shard_stage_lists):
    """Sum per-stage in/out counts (units and lines) across shards (shards
    share the rule set; a line total is None if any shard lacks it)."""
    agg = {}
    order = []
    for stages in shard_stage_lists:
        for s in stages:
            key = (s.phase, s.index, s.rule_id)
            if key not in agg:
                agg[key] = StageStat(s.phase, s.index, s.rule_id, 0, 0,
                                     s.lines_in, s.lines_out)
                order.append(key)
            else:
                agg[key].lines_in = _add_opt(agg[key].lines_in, s.lines_in)
                agg[key].lines_out = _add_opt(agg[key].lines_out, s.lines_out)
            agg[key].in_count += s.in_count
            agg[key].out_count += s.out_count
    return _sort_stages([agg[k] for k in order])


def _merge_named(shard_dirs, rel_glob, dest_dir, skip_header):
    """Concatenate same-named files across shards into ``dest_dir``.

    skip_header -- keep only the first file's header line (TSV reject reports);
                   otherwise concatenate verbatim (JSONL reports).
    """
    from collections import defaultdict
    groups = defaultdict(list)
    for sd in shard_dirs:
        for f in sorted(sd.glob(rel_glob)):
            groups[f.name].append(f)
    if groups:
        dest_dir.mkdir(parents=True, exist_ok=True)
    for name, files in sorted(groups.items()):
        with open(dest_dir / name, "w") as out:
            for i, f in enumerate(files):
                lines = f.read_text().splitlines()
                if skip_header and i > 0 and lines:
                    lines = lines[1:]
                for line in lines:
                    out.write(line + "\n")


def _concat_shard_outputs(out_dir, shard_dirs):
    for name in ("SA-final.models.manifest.jsonl", "SB-final.units.manifest.jsonl",
                 "model_stats.jsonl"):
        with open(out_dir / name, "w") as out:
            for sd in shard_dirs:
                f = sd / name
                if f.exists():
                    out.write(f.read_text())
    # Merged reject reports (the "what got filtered out" deliverable) ...
    _merge_named(shard_dirs, "phase-a/*.rejects.tsv", out_dir / "rejects", True)
    _merge_named(shard_dirs, "phase-b/*.rejects.tsv", out_dir / "rejects", True)
    # ... and the per-stage JSONL reports.
    _merge_named(shard_dirs, "phase-a/reports/*.jsonl", out_dir / "reports", False)
    _merge_named(shard_dirs, "phase-b/reports/*.jsonl", out_dir / "reports", False)
    # The ledger concatenates (one row per removed unit, no cross-shard state).
    _merge_named(shard_dirs, "ledger.tsv", out_dir, True)


def _write_merged_rollups(out_dir, stages):
    """Roll-ups and model lists are recomputed from the MERGED files, never by
    summing per-shard roll-ups (a contributor spanning shards would otherwise
    get one row per shard)."""
    if not (out_dir / "ledger.tsv").exists():
        return
    unit_rule_ids = [s.rule_id for s in stages if s.phase == "B"]
    write_rollups(out_dir, unit_rule_ids)
    write_model_lists(out_dir)


def run_ratchet_sharded(models_dir, rules_dir="rules", out_dir=None, *, jobs=4,
                        go=None, ro=None, groups=None, skip_prefixes=None,
                        true_gocam_ids=None, true_gocam_source=None,
                        limit=None, group_allowlist=None) -> ShardedResult:
    """Run the ratchet across ``jobs`` parallel shards, then merge.

    Identical result to a single-process run (a model never spans shards).
    Resumable: a killed run re-does only shards without a .done marker.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if true_gocam_ids is None and true_gocam_source is not None:
        true_gocam_ids = load_true_gocam_ids(true_gocam_source)
    true_gocam_ids = sorted(true_gocam_ids or set())  # picklable + stable

    ttls = sorted(p for p in Path(models_dir).iterdir() if p.suffix == ".ttl")
    if limit:
        ttls = ttls[:limit]

    shards = _partition([str(p) for p in ttls], jobs)
    tasks = [
        (k, shards[k], rules_dir, str(out_dir), go, ro, groups,
         list(skip_prefixes or []), list(true_gocam_ids),
         sorted(group_allowlist or ()))
        for k in range(jobs) if shards[k]
    ]

    if len(tasks) <= 1:
        shard_stage_lists = [_run_shard(t) for t in tasks]
    else:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=len(tasks)) as ex:
            shard_stage_lists = list(ex.map(_run_shard, tasks))

    merged_stages = _aggregate_stage_stats(shard_stage_lists)
    shard_dirs = [out_dir / "shards" / f"shard-{t[0]:03d}" for t in tasks]
    _concat_shard_outputs(out_dir, shard_dirs)
    _write_merged_rollups(out_dir, merged_stages)
    write_summary(merged_stages, out_dir / "summary.tsv")
    return ShardedResult(merged_stages, out_dir, n_shards=len(tasks))


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m gocam_unwinder.ratchet",
        description="Two-level standard-annotation ratchet over a GO-CAM corpus.")
    ap.add_argument("--models-dir", required=True,
                    help="Directory of GO-CAM .ttl models (S0).")
    ap.add_argument("--rules-dir", default="rules",
                    help="Directory of rule manifests (default: rules).")
    ap.add_argument("--out-dir", required=True, help="Output directory.")
    ap.add_argument("--go", help="GO ontology JSON (required for Phase B).")
    ap.add_argument("--ro", help="RO ontology OWL (enables RO-dependent checks).")
    ap.add_argument("--groups", help="groups.yaml for group label resolution.")
    ap.add_argument("--skip-prefix", action="append", dest="skip_prefixes",
                    default=[], metavar="PREFIX",
                    help="Filename prefix to skip in Phase A (repeatable).")
    ap.add_argument("--group", action="append", dest="group_allowlist",
                    default=[], metavar="LABEL_OR_ID",
                    help="Keep only models of this editorial group (repeatable; "
                         "a groups.yaml label needs --groups, an id works alone). "
                         "Phase A gate group_allowlist; no-op when absent.")
    ap.add_argument("--true-gocam-source", metavar="DIR_OR_FILE",
                    help="Dir of true-GO-CAM JSONs (stems) or an id file (R1).")
    ap.add_argument("--limit", type=int, default=None,
                    help="Process only the first N models.")
    ap.add_argument("--jobs", "-j", type=int, default=1,
                    help="Parallel shards/workers (default 1). Each worker loads "
                         "its own GO ontology copy, so bound by RAM. Resumable.")
    args = ap.parse_args(argv)

    group_allowlist = resolve_group_allowlist(args.group_allowlist, args.groups)
    if args.jobs and args.jobs > 1:
        result = run_ratchet_sharded(
            args.models_dir, args.rules_dir, args.out_dir, jobs=args.jobs,
            go=args.go, ro=args.ro, groups=args.groups,
            skip_prefixes=args.skip_prefixes,
            true_gocam_source=args.true_gocam_source, limit=args.limit,
            group_allowlist=group_allowlist)
    else:
        result = run_ratchet(
            args.models_dir, args.rules_dir, args.out_dir, go=args.go, ro=args.ro,
            groups=args.groups, skip_prefixes=args.skip_prefixes,
            true_gocam_source=args.true_gocam_source, limit=args.limit,
            group_allowlist=group_allowlist)
    print((Path(args.out_dir) / "summary.tsv").read_text())
    return result


if __name__ == "__main__":
    main()
