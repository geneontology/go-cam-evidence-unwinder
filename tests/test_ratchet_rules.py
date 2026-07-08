"""Tests for the ratchet rule-manifest loader (Task 1)."""

import pytest
import yaml

from gocam_unwinder.ratchet.rules import load_rules


def _write_rule(d, **fields):
    d.mkdir(parents=True, exist_ok=True)
    (d / "rule.yaml").write_text(yaml.safe_dump(fields))


def test_load_rules_ordered_and_parsed(tmp_path):
    rules_dir = tmp_path / "rules"
    # Written out of lexical order on disk; loader must sort by dir prefix.
    _write_rule(rules_dir / "10-b", id="b", phase="unit", stage=2,
                kind="python", entrypoint="b_fn")
    _write_rule(rules_dir / "00-a", id="a", phase="model", stage=0,
                kind="python", entrypoint="a_fn")

    rules = load_rules(rules_dir)

    assert [r.id for r in rules] == ["a", "b"]
    assert rules[0].phase == "model"
    assert rules[1].kind == "python"
    assert rules[1].stage == 2


def test_phase_filter_and_disabled(tmp_path):
    rules_dir = tmp_path / "rules"
    _write_rule(rules_dir / "00-m", id="m", phase="model", kind="python", entrypoint="x")
    _write_rule(rules_dir / "01-u", id="u", phase="unit", kind="python", entrypoint="y")
    _write_rule(rules_dir / "02-off", id="off", phase="unit", kind="python",
                entrypoint="z", enabled=False)

    assert [r.id for r in load_rules(rules_dir, phase="model")] == ["m"]
    # disabled rule excluded by default
    assert [r.id for r in load_rules(rules_dir, phase="unit")] == ["u"]
    assert [r.id for r in load_rules(rules_dir, phase="unit", include_disabled=True)] \
        == ["u", "off"]


def test_sparql_rule_requires_query_rq(tmp_path):
    rules_dir = tmp_path / "rules"
    _write_rule(rules_dir / "00-s", id="s", phase="unit", kind="sparql")
    with pytest.raises(ValueError, match="query.rq"):
        load_rules(rules_dir)


def test_python_rule_requires_entrypoint(tmp_path):
    rules_dir = tmp_path / "rules"
    _write_rule(rules_dir / "00-p", id="p", phase="unit", kind="python")
    with pytest.raises(ValueError, match="entrypoint"):
        load_rules(rules_dir)


def test_bad_phase_and_kind_rejected(tmp_path):
    rules_dir = tmp_path / "rules"
    _write_rule(rules_dir / "00-x", id="x", phase="galaxy", kind="python", entrypoint="e")
    with pytest.raises(ValueError, match="phase"):
        load_rules(rules_dir)


def test_seed_rules_dir_loads():
    """The committed rules/ dir parses into the full two-level rule set."""
    model_rules = load_rules("rules", phase="model")
    unit_rules = load_rules("rules", phase="unit")
    model_ids = [r.id for r in model_rules]
    unit_ids = [r.id for r in unit_rules]

    # Phase A gates in dir order; the disabled disconnected example is excluded.
    assert model_ids[:3] == ["modelstate_delete", "skip_prefix", "not_true_gocam"]
    assert "model_has_activity_edges" in model_ids
    assert "model_disconnected_individuals" not in model_ids  # enabled: false
    assert "model_disconnected_individuals" in [
        r.id for r in load_rules("rules", phase="model", include_disabled=True)]

    # Phase B: 12 unit checks in tier order; edge_without_evidence is python now.
    assert len(unit_ids) == 12
    assert unit_ids[0] == "edge_without_evidence"
    assert unit_ids[-1] == "inconsistent_evidence"
    by_id = {r.id: r for r in unit_rules}
    assert by_id["edge_without_evidence"].kind == "python"
    assert by_id["edge_without_evidence"].entrypoint == "unit_edge_without_evidence"

    # the enabled SPARQL model rule has its query.rq
    sparql = next(r for r in model_rules if r.kind == "sparql")
    assert sparql.query_path.exists()
