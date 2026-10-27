"""The staging engine: apply one rule to a set, partition survivors from
rejects, and materialize the stage's outputs.

Per stage, three artifacts are written into ``out_dir`` (mirroring the
pipeline-from-goa convention of numbered stage dirs + sibling reject sets +
per-stage JSONL reports):

  * ``S{NN}-{id}.manifest.jsonl`` -- the surviving set ``Sn`` (carried forward).
  * ``{NN}-{id}.rejects.tsv``     -- the items this rule removed, with reasons.
  * ``reports/{NN}-{id}.jsonl``   -- one row per item: keep / remove / skip.

The monotonic invariant (survivors are a subset of the input) is enforced here.
"""

import json
from pathlib import Path

from .core import Manifest, StageResult

# Item.meta keys copied onto every report row and reject record (the "lines"
# and "contributor" reporting axes). Present on unit items from parse time and
# on Phase A model items only after the removal counting pass.
AXIS_META_KEYS = ("gpad_lines_est", "contributors")

REJECTS_TSV_HEADER = ("id", "rule", "reason", "detail",
                      "gpad_lines_est", "contributors", "all_failed_checks")


def report_row(item, verdict, stage_id, **extra) -> dict:
    """One ``reports/*.jsonl`` row: decision + reason + axis meta + detail."""
    if verdict.skipped:
        decision = "skip"
    elif verdict.keep:
        decision = "keep"
    else:
        decision = "remove"
    row = {"id": item.id, "decision": decision, "rule": stage_id}
    row.update(extra)
    if verdict.reason:
        row["reason"] = verdict.reason
    for key in AXIS_META_KEYS:
        if key in item.meta:
            row[key] = item.meta[key]
    row.update(verdict.detail)
    return row


def run_stage(adapter, manifest: Manifest, *, out_dir, index: int,
              stage_id: str, ctx=None, on_reject=None) -> StageResult:
    """Run one ratchet stage.

    adapter   -- object with ``apply(item, ctx) -> Verdict``.
    manifest  -- the input set (survivors of the previous stage).
    out_dir   -- directory to materialize this stage's outputs into.
    index     -- integer stage number (zero-padded into the ``NN-`` prefix).
    stage_id  -- short rule id; used in filenames and the report ``rule`` column.
    ctx       -- shared context handed to the adapter (e.g. a GoCamGraphBuilder).
    on_reject -- optional ``f(item, verdict)`` called for each removed item
                 BEFORE its report row / reject record is written; may add to
                 ``item.meta`` / ``verdict.detail`` (the Phase A counting pass).
    """
    out_dir = Path(out_dir)
    reports_dir = out_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    survivors = []
    rejected = []
    report_rows = []
    for item in manifest.items:
        verdict = adapter.apply(item, ctx)
        if not verdict.keep and on_reject is not None:
            on_reject(item, verdict)
        report_rows.append(report_row(item, verdict, stage_id))

        if verdict.keep:
            survivors.append(item)
        else:
            rejected.append((item, verdict))

    # Monotonic invariant: a stage may only remove items, never add them.
    assert len(survivors) <= len(manifest.items), "ratchet stage added items"

    survivors_manifest = Manifest(survivors)
    prefix = f"{index:02d}-{stage_id}"
    survivors_manifest.write(out_dir / f"S{index:02d}-{stage_id}.manifest.jsonl")
    _write_rejects_tsv(out_dir / f"{prefix}.rejects.tsv", rejected, stage_id)
    _write_jsonl(reports_dir / f"{prefix}.jsonl", report_rows)

    return StageResult(
        index=index, stage_id=stage_id, survivors=survivors_manifest,
        rejected=rejected, report_rows=report_rows, in_count=len(manifest.items))


def _write_jsonl(path, rows) -> None:
    with open(path, "w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _write_rejects_tsv(path, rejected, stage_id) -> None:
    """Reject record: ``detail`` stays the rule's own pattern fields (JSON);
    the axis columns and the complete failing-check set get their own columns.
    Empty ``gpad_lines_est`` means "not estimated" (no counting pass ran)."""
    with open(path, "w") as fh:
        fh.write("\t".join(REJECTS_TSV_HEADER) + "\n")
        for item, verdict in rejected:
            detail = {k: v for k, v in verdict.detail.items() if k != "all_failed_checks"}
            lines_est = item.meta.get("gpad_lines_est")
            fh.write("\t".join([
                item.id, stage_id, verdict.reason or "",
                json.dumps(detail) if detail else "",
                "" if lines_est is None else str(lines_est),
                "|".join(item.meta.get("contributors") or []),
                "|".join(verdict.detail.get("all_failed_checks") or []),
            ]) + "\n")
