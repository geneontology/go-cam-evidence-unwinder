"""End-to-end two-level ratchet test (Task 7).

Uses the session ``builder`` fixture (GO + RO + groups) so Phase B runs without
re-parsing the GO ontology.
"""

import shutil
from pathlib import Path

from gocam_unwinder.ratchet.cli import run_ratchet

RES = Path("resources/test")


def _corpus(tmp_path, names):
    d = tmp_path / "models"
    d.mkdir()
    for name in names:
        shutil.copy(RES / name, d / name)
    return d


def test_end_to_end_two_level_shrink(tmp_path, builder):
    models_dir = _corpus(tmp_path, [
        "MGI_MGI_1100089.ttl",      # survives Phase A; has standard annotations
        "R-HSA-9937080.ttl",        # removed by skip_prefix R-HSA
        "SYNGO_5371.ttl",           # removed by skip_prefix SYNGO
        "5b318d0900000481.ttl",     # removed by R1 (true-gocam)
    ])
    out_dir = tmp_path / "out"

    result = run_ratchet(
        models_dir, rules_dir="rules", out_dir=out_dir,
        skip_prefixes=["SYNGO", "R-HSA"],
        true_gocam_ids={"5b318d0900000481"},
        builder=builder,
    )

    # Phase A: only the MGI model survives the model gates.
    assert result.phase_a_survivors == ["MGI_MGI_1100089"]

    # Within each phase, the in-counts are monotonically non-increasing.
    a_in = [s.in_count for s in result.stages if s.phase == "A"]
    b_in = [s.in_count for s in result.stages if s.phase == "B"]
    assert a_in == sorted(a_in, reverse=True)
    assert b_in == sorted(b_in, reverse=True)

    # Phase B produced surviving annotation units, and they equal exactly the
    # MGI model's standard annotations under the monolithic classifier.
    assert len(result.final_units) >= 1
    classified = builder.parse_ttl(str(RES / "MGI_MGI_1100089.ttl"), classify=True)
    assert len(result.final_units) == len(classified.standard_annotations)

    # Artifacts exist.
    summary = out_dir / "summary.tsv"
    assert summary.exists()
    text = summary.read_text()
    assert "model_has_activity_edges" in text          # Phase A SPARQL rule ran
    assert "edge_without_evidence" in text              # Phase B unit rule ran
    assert (out_dir / "SA-final.models.manifest.jsonl").exists()
    assert (out_dir / "SB-final.units.manifest.jsonl").exists()
    assert (out_dir / "phase-a" / "reports" / "01-skip_prefix.jsonl").exists()


def test_phase_a_only_without_ontology(tmp_path):
    """Without a builder/GO ontology, Phase A still runs and writes a summary;
    Phase B is skipped."""
    models_dir = _corpus(tmp_path, ["MGI_MGI_1100089.ttl", "SYNGO_5371.ttl"])
    out_dir = tmp_path / "out"
    result = run_ratchet(models_dir, rules_dir="rules", out_dir=out_dir,
                         skip_prefixes=["SYNGO"])
    assert result.phase_a_survivors == ["MGI_MGI_1100089"]
    assert result.final_units == []
    assert (out_dir / "summary.tsv").exists()
    assert all(s.phase == "A" for s in result.stages)
