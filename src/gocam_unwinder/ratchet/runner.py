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


def run_stage(adapter, manifest: Manifest, *, out_dir, index: int,
              stage_id: str, ctx=None) -> StageResult:
    """Run one ratchet stage.

    adapter  -- object with ``apply(item, ctx) -> Verdict``.
    manifest -- the input set (survivors of the previous stage).
    out_dir  -- directory to materialize this stage's outputs into.
    index    -- integer stage number (zero-padded into the ``NN-`` prefix).
    stage_id -- short rule id; used in filenames and the report ``rule`` column.
    ctx      -- shared context handed to the adapter (e.g. a GoCamGraphBuilder).
    """
    out_dir = Path(out_dir)
    reports_dir = out_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    survivors = []
    rejected = []
    report_rows = []
    for item in manifest.items:
        verdict = adapter.apply(item, ctx)
        if verdict.skipped:
            decision = "skip"
        elif verdict.keep:
            decision = "keep"
        else:
            decision = "remove"
        row = {"id": item.id, "decision": decision, "rule": stage_id}
        if verdict.reason:
            row["reason"] = verdict.reason
        row.update(verdict.detail)
        report_rows.append(row)

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
    with open(path, "w") as fh:
        fh.write("id\trule\treason\tdetail\n")
        for item, verdict in rejected:
            detail = json.dumps(verdict.detail) if verdict.detail else ""
            fh.write(f"{item.id}\t{stage_id}\t{verdict.reason or ''}\t{detail}\n")
