"""Tests for Phase B unit-level rules (Task 5).

These reuse the session ``builder`` fixture (GO + RO + groups) from conftest and
assert that each unit rule, applied to a model's raw annotation units, matches
the classification the monolithic filter_out_non_std_annotations() produces.
"""

from pathlib import Path

import pytest

from gocam_unwinder.ratchet.core import Item
from gocam_unwinder.ratchet.unit_rules import (
    build_unit_rule_registry,
    extract_units,
    make_unit_rule,
)

RES = Path("resources/test")


@pytest.mark.parametrize("model,check_key,expect_fail", [
    ("66c7d41500000016.ttl", "edge_without_evidence", True),
    ("MGI_MGI_1100089.ttl", "edge_without_evidence", False),
    ("5b318d0900000481.ttl", "mf_causal_mf", True),
    ("enabler_not_gp_example.ttl", "enabler_not_gp", True),
    ("multi_mf_bp_example.ttl", "multiple_mf_bp", True),
    ("multi_mf_anatomy_example.ttl", "multiple_mf_anatomy", True),
    ("bp_cc_relation_example.ttl", "invalid_bp_cc_relation", True),
    ("gp_cc_relation_example.ttl", "invalid_gp_cc_relation", True),
    ("mf_cc_relation_example.ttl", "invalid_mf_cc_relation", True),
    ("gp_bp_relation_example.ttl", "invalid_gp_bp_relation", True),
    ("mf_bp_relation_example.ttl", "invalid_mf_bp_relation", True),
])
def test_unit_rule_matches_classification(builder, model, check_key, expect_fail):
    units = extract_units(builder, RES / model)
    rule = make_unit_rule(check_key)
    any_removed = any(rule(builder, u).keep is False for u in units)
    assert any_removed is expect_fail


def test_unit_rule_reject_carries_labels(builder):
    """A removal verdict surfaces resolved source/predicate/target labels."""
    units = extract_units(builder, RES / "enabler_not_gp_example.ttl")
    rule = make_unit_rule("enabler_not_gp")
    rejects = [rule(builder, u) for u in units]
    rejects = [v for v in rejects if v.keep is False]
    assert rejects, "expected at least one enabler_not_gp removal"
    v = rejects[0]
    assert "predicate" in v.detail and "source" in v.detail and "target" in v.detail
    assert v.detail["flagged_edges"] >= 1


def test_ro_dependent_rule_skips_without_ro():
    """RO-dependent checks skip (keep + record) when no RO is loaded."""
    class _NoRoCtx:
        ro_ontology = None

    item = Item(id="x#0")  # payload not touched on the skip path
    for key in ("mf_causal_mf", "invalid_gp_bp_relation", "invalid_mf_bp_relation"):
        v = make_unit_rule(key)(_NoRoCtx(), item)
        assert v.skipped is True and v.keep is True
        assert v.reason.endswith("_skipped_no_ro")


def test_full_classification_parity_via_units(builder):
    """The union of per-unit rule removals reproduces the monolithic
    classification: a model's non-standard annotations are exactly the units
    flagged by at least one unit rule."""
    model = "5b318d0900000481.ttl"  # has MF-causal->MF (non-standard) + others
    keys = build_unit_rule_registry()  # all unit rules
    rules = {k: v for k, v in keys.items()}

    units = extract_units(builder, RES / model)
    removed_unit_ids = set()
    for u in units:
        for rule in rules.values():
            if rule(builder, u).keep is False:
                removed_unit_ids.add(u.id)
                break

    # Classified reference: parse the same model WITH classification.
    classified = builder.parse_ttl(str(RES / model), classify=True)
    # Re-extract raw units (same order) to map index -> standard/non-standard.
    raw = builder.parse_ttl(str(RES / model), classify=False)
    n_non_standard = len(classified.non_standard_annotations)
    assert len(removed_unit_ids) == n_non_standard
    assert len(raw.standard_annotations) - len(removed_unit_ids) == \
        len(classified.standard_annotations)


def test_build_unit_rule_registry_has_all_checks():
    from gocam_unwinder.gocam_ttl import GoCamGraphBuilder
    reg = build_unit_rule_registry()
    assert set(reg) == {f"unit_{k}" for k in GoCamGraphBuilder.UNIT_CHECK_KEYS}
    assert len(reg) == 12
