"""Tests for Phase A model-level rules (Task 4)."""

from pathlib import Path

from gocam_unwinder.ratchet.core import Item, Manifest
from gocam_unwinder.ratchet.engine import resolve_adapter
from gocam_unwinder.ratchet.model_rules import (
    build_model_rule_registry,
    load_true_gocam_ids,
    make_not_true_gocam_gate,
    make_skip_prefix_gate,
    modelstate_gate,
    parse_true_gocam_report,
)
from gocam_unwinder.ratchet.rules import load_rules
from gocam_unwinder.ratchet.runner import run_stage

RES = Path("resources/test")

_MODELSTATE_TTL = """@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix lego: <http://geneontology.org/lego/> .
<http://model.geneontology.org/M1> a owl:Ontology ;
    lego:modelstate "%s" .
"""


def _state_model(tmp_path, name, state):
    p = tmp_path / name
    p.write_text(_MODELSTATE_TTL % state)
    return Item(id=name.replace(".ttl", ""), path=p)


def test_modelstate_delete_removed(tmp_path):
    assert modelstate_gate(None, _state_model(tmp_path, "d.ttl", "delete")).keep is False
    assert modelstate_gate(None, _state_model(tmp_path, "p.ttl", "production")).keep is True


def test_modelstate_missing_is_kept(tmp_path):
    p = tmp_path / "n.ttl"
    p.write_text('@prefix owl: <http://www.w3.org/2002/07/owl#> .\n'
                 '<http://x/M> a owl:Ontology .\n')
    assert modelstate_gate(None, Item(id="n", path=p)).keep is True


def test_skip_prefix_removes_configured():
    gate = make_skip_prefix_gate(["SYNGO", "R-HSA"])
    assert gate(None, Item(id="SYNGO_5371")).keep is False
    assert gate(None, Item(id="R-HSA-9937080")).keep is False
    assert gate(None, Item(id="MGI_MGI_1100089")).keep is True


def test_skip_prefix_matches_on_filename_when_path_present():
    gate = make_skip_prefix_gate(["SYNGO"])
    v = gate(None, Item(id="SYNGO_5371", path=RES / "SYNGO_5371.ttl"))
    assert v.keep is False and v.detail.get("prefix") == "SYNGO"


def test_not_true_gocam_removes_members():
    gate = make_not_true_gocam_gate({"605c123400000001", "abc"})
    # a model that IS a true GO-CAM is removed (it cannot be a standard annotation)
    assert gate(None, Item(id="605c123400000001")).keep is False
    assert gate(None, Item(id="MGI_MGI_1100089")).keep is True


def test_not_true_gocam_empty_set_is_noop():
    gate = make_not_true_gocam_gate(set())
    assert gate(None, Item(id="anything")).keep is True


def test_load_true_gocam_ids_from_file(tmp_path):
    f = tmp_path / "ids.txt"
    f.write_text("# header comment\nM1\nM2\n\nM3\n")
    assert load_true_gocam_ids(f) == {"M1", "M2", "M3"}


def test_load_true_gocam_ids_from_dir(tmp_path):
    d = tmp_path / "go-cams-json"
    d.mkdir()
    (d / "M1.json").write_text("{}")
    (d / "M2.json").write_text("{}")
    assert load_true_gocam_ids(d) == {"M1", "M2"}


# The published pipeline-from-goa filter report (reports/go-cam/02-filter.jsonl):
# one line per model, status "success" == a true GO-CAM.
_FILTER_REPORT = (
    '{"model_id": "0000000300000001", "status": "filtered", "reason": "not prod"}\n'
    '{"model_id": "5323da1800000002", "status": "success"}\n'
    '\n'
    '{"model_id": "5408ded300000003", "status": "filtered", "reason": "not prod"}\n'
    '{"model_id": "5a5fc23a00000137", "status": "success"}\n'
)


def test_parse_true_gocam_report_keeps_only_success():
    assert parse_true_gocam_report(_FILTER_REPORT) == {
        "5323da1800000002", "5a5fc23a00000137"}


def test_load_true_gocam_ids_from_jsonl_file(tmp_path):
    f = tmp_path / "02-filter.jsonl"
    f.write_text(_FILTER_REPORT)
    assert load_true_gocam_ids(f) == {"5323da1800000002", "5a5fc23a00000137"}


def test_build_registry_resolves_entrypoints():
    reg = build_model_rule_registry(skip_prefixes=["SYNGO"], true_gocam_ids={"X"})
    assert set(reg) == {"modelstate_gate", "skip_prefix_gate", "not_true_gocam_gate"}
    assert reg["skip_prefix_gate"](None, Item(id="SYNGO_1")).keep is False
    assert reg["not_true_gocam_gate"](None, Item(id="X")).keep is False


def test_phase_a_pipeline_through_runner(tmp_path):
    """Tasks 1-4 together: load the model rules, run them in order through the
    runner over real resource models, and confirm the surviving set shrinks
    to just the MGI model (SYNGO + R-HSA removed by skip_prefix)."""
    registry = build_model_rule_registry(skip_prefixes=["SYNGO", "R-HSA"],
                                          true_gocam_ids=set())
    model_rules = load_rules("rules", phase="model")
    assert [r.id for r in model_rules] == [
        "modelstate_delete", "skip_prefix", "not_true_gocam", "model_has_activity_edges"]

    items = [Item(id=p.stem, path=p) for p in (
        RES / "SYNGO_5371.ttl",
        RES / "R-HSA-9937080.ttl",
        RES / "MGI_MGI_1100089.ttl",
    )]
    manifest = Manifest(items)

    out_dir = tmp_path / "out"
    for i, rule in enumerate(model_rules):
        adapter = resolve_adapter(rule, registry)
        result = run_stage(adapter, manifest, out_dir=out_dir, index=i, stage_id=rule.id)
        # monotonic at every step
        assert result.out_count <= result.in_count
        manifest = result.survivors

    assert manifest.ids == ["MGI_MGI_1100089"]
