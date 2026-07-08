"""Tests for parallel sharding + resume (Task 8)."""

import shutil
from pathlib import Path

from gocam_unwinder.ratchet.cli import (
    _aggregate_stage_stats,
    _partition,
    run_ratchet,
    run_ratchet_sharded,
)

RES = Path("resources/test")
GO_FILE = "target/go_20250601.json"
RO_FILE = "resources/test/ro_20250723.owl"
GROUPS_FILE = "resources/test/groups.yaml"


def _corpus(tmp_path, names):
    d = tmp_path / "models"
    d.mkdir()
    for name in names:
        shutil.copy(RES / name, d / name)
    return d


def _lines(path):
    return [l for l in Path(path).read_text().splitlines() if l.strip()]


def test_partition_round_robin():
    assert _partition([1, 2, 3, 4, 5], 2) == [[1, 3, 5], [2, 4]]
    assert _partition([1, 2], 3) == [[1], [2], []]  # extra shard is empty


def test_aggregate_sums_per_stage():
    from gocam_unwinder.ratchet.cli import StageStat
    a = [StageStat("A", 0, "r0", 10, 8), StageStat("A", 1, "r1", 8, 5)]
    b = [StageStat("A", 0, "r0", 5, 5), StageStat("A", 1, "r1", 5, 4)]
    merged = _aggregate_stage_stats([a, b])
    assert [(m.rule_id, m.in_count, m.out_count) for m in merged] == [
        ("r0", 15, 13), ("r1", 13, 9)]


def test_sharded_phase_a_parity_and_resume(tmp_path):
    names = ["MGI_MGI_1100089.ttl", "R-HSA-9937080.ttl", "SYNGO_5371.ttl",
             "5b318d0900000481.ttl", "66c7d41500000016.ttl", "61452e3d00000323.ttl"]
    models = _corpus(tmp_path, names)

    # Single-process Phase A (no ontology -> Phase B skipped, fast).
    run_ratchet(models, out_dir=tmp_path / "single", skip_prefixes=["SYNGO", "R-HSA"])
    single = sorted(_lines(tmp_path / "single" / "SA-final.models.manifest.jsonl"))

    # Sharded, 3 ways.
    sharded = run_ratchet_sharded(models, out_dir=tmp_path / "shard", jobs=3,
                                  skip_prefixes=["SYNGO", "R-HSA"])
    merged = sorted(_lines(tmp_path / "shard" / "SA-final.models.manifest.jsonl"))

    # Exactness: sharded == single.
    assert merged == single
    # Summary sums: the final Phase A 'Out' equals the survivor count.
    last_a = [s for s in sharded.stages if s.phase == "A"][-1]
    assert last_a.out_count == len(merged)
    # All shards completed (.done markers).
    assert all((tmp_path / "shard" / "shards" / f"shard-{k:03d}" / ".done").exists()
               for k in range(3))
    # Merged reject reports exist (e.g. skip_prefix removed SYNGO + R-HSA).
    assert (tmp_path / "shard" / "rejects" / "01-skip_prefix.rejects.tsv").exists()

    # Resume: a second run yields identical per-stage counts (shards skipped).
    again = run_ratchet_sharded(models, out_dir=tmp_path / "shard", jobs=3,
                                skip_prefixes=["SYNGO", "R-HSA"])
    assert [(s.phase, s.index, s.in_count, s.out_count) for s in again.stages] == \
           [(s.phase, s.index, s.in_count, s.out_count) for s in sharded.stages]


def test_sharded_full_parity_with_units(tmp_path, builder):
    """End-to-end: sharded (2 workers, each building its own GO builder) yields
    the same surviving-unit count as the monolithic classifier."""
    names = ["MGI_MGI_1100089.ttl", "5b318d0900000481.ttl"]
    models = _corpus(tmp_path, names)
    expected = sum(len(builder.parse_ttl(str(RES / n), classify=True).standard_annotations)
                   for n in names)

    out = tmp_path / "out"
    run_ratchet_sharded(models, out_dir=out, jobs=2,
                        go=GO_FILE, ro=RO_FILE, groups=GROUPS_FILE)

    final = _lines(out / "SB-final.units.manifest.jsonl")
    assert len(final) == expected
    assert (out / "summary.tsv").exists()
    assert (out / "rejects").exists()
