"""Tests for the ratchet staging engine (Task 2)."""

import json

from gocam_unwinder.ratchet.core import Manifest, Verdict
from gocam_unwinder.ratchet.runner import run_stage


class _PredicateAdapter:
    """A trivial adapter that removes items whose id matches a predicate."""

    def __init__(self, predicate, reason):
        self.predicate = predicate
        self.reason = reason

    def apply(self, item, ctx=None):
        if self.predicate(item.id):
            return Verdict.reject(self.reason)
        return Verdict.survive()


def _read_jsonl(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_run_stage_partitions_and_reports(tmp_path):
    manifest = Manifest.of(["a1", "a2", "X3", "X4"])
    adapter = _PredicateAdapter(lambda i: i.startswith("X"), reason="starts_with_X")

    out = run_stage(adapter, manifest, out_dir=tmp_path, index=1,
                    stage_id="starts_with_X")

    assert out.survivors.ids == ["a1", "a2"]
    assert out.rejected_ids == ["X3", "X4"]
    assert out.in_count == 4 and out.out_count == 2

    report = _read_jsonl(tmp_path / "reports" / "01-starts_with_X.jsonl")
    assert {r["id"]: r["decision"] for r in report} == {
        "a1": "keep", "a2": "keep", "X3": "remove", "X4": "remove"}
    assert all(r["reason"] == "starts_with_X"
               for r in report if r["decision"] == "remove")

    # survivor manifest round-trips from disk
    reloaded = Manifest.read(tmp_path / "S01-starts_with_X.manifest.jsonl")
    assert reloaded.ids == ["a1", "a2"]

    # rejects tsv lists removed items only
    rejects = (tmp_path / "01-starts_with_X.rejects.tsv").read_text()
    assert "X3" in rejects and "X4" in rejects and "a1" not in rejects


def test_monotonic_invariant_holds(tmp_path):
    manifest = Manifest.of(["a", "b", "c"])
    keep_all = _PredicateAdapter(lambda i: False, reason="never")
    out = run_stage(keep_all, manifest, out_dir=tmp_path, index=0, stage_id="noop")
    assert out.survivors.ids == ["a", "b", "c"]
    assert out.out_count <= out.in_count


def test_skip_recorded_as_skip_and_keeps_item(tmp_path):
    class _Skipper:
        def apply(self, item, ctx=None):
            return Verdict.skip("ro_not_loaded")

    out = run_stage(_Skipper(), Manifest.of(["a"]), out_dir=tmp_path,
                    index=2, stage_id="ro_rule")
    assert out.survivors.ids == ["a"]  # a skipped rule keeps the item
    report = _read_jsonl(tmp_path / "reports" / "02-ro_rule.jsonl")
    assert report[0]["decision"] == "skip"
    assert report[0]["reason"] == "ro_not_loaded"


def test_detail_fields_flow_into_report(tmp_path):
    class _Detailer:
        def apply(self, item, ctx=None):
            return Verdict.reject("bad_edge", source="GO:1", predicate="located_in",
                                  target="GO:2")

    out = run_stage(_Detailer(), Manifest.of(["m1"]), out_dir=tmp_path,
                    index=3, stage_id="bad_edge")
    report = _read_jsonl(tmp_path / "reports" / "03-bad_edge.jsonl")
    assert report[0]["source"] == "GO:1"
    assert report[0]["predicate"] == "located_in"
    assert out.rejected_ids == ["m1"]
