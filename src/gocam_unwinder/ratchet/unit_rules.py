"""Phase B unit-level rules: wrap each per-check callable from gocam_ttl as a
ratchet rule over a single annotation unit.

A unit item carries the parsed model and one annotation in ``item.payload``
(``gcg`` + ``annotation``) -- in-memory, not serialized. The shared ``ctx`` is
the ``GoCamGraphBuilder`` (GO/RO loaded once). A unit rule removes the unit when
its check flags one or more edges; RO-dependent checks SKIP (keep + record) when
no RO ontology is loaded, mirroring gocam_ttl's gating.
"""

from pathlib import Path

from .core import Item, Verdict

# Checks that need the RO ontology; without it they would silently pass, so we
# record them as skipped instead of a misleading "kept".
RO_DEPENDENT = {"mf_causal_mf", "invalid_gp_bp_relation", "invalid_mf_bp_relation"}


def make_unit_rule(check_key):
    """Build a unit rule ``(ctx, item) -> Verdict`` for one check key.

    ctx  -- the GoCamGraphBuilder (provides run_check / term_label / ro_ontology).
    item -- item.payload has "gcg" (GoCamGraph) and "annotation" (StandardAnnotation).
    """

    def unit_rule(ctx, item):
        builder = ctx
        if check_key in RO_DEPENDENT and getattr(builder, "ro_ontology", None) is None:
            return Verdict.skip(check_key + "_skipped_no_ro")

        gcg = item.payload["gcg"]
        annotation = item.payload["annotation"]
        flagged = builder.run_check(check_key, gcg, annotation)
        if flagged:
            # Surface one offending edge in the report (sorted for determinism).
            edge = annotation.edges[sorted(flagged)[0]]
            return Verdict.reject(
                check_key,
                source=builder.term_label(edge.source_type),
                predicate=builder.term_label(edge.property_uri),
                target=builder.term_label(edge.target_type),
                flagged_edges=len(flagged),
            )
        return Verdict.survive()

    unit_rule.__name__ = "unit_rule_" + check_key
    return unit_rule


def parse_model_units(builder, ttl_path):
    """Parse a model WITHOUT classifying; return ``(gcg, units)`` where ``units``
    is one Item per raw annotation unit (connected-component subgraph). The
    returned ``gcg`` carries model-level metadata (modelstate, groups, title)
    for per-model stats."""
    gcg = builder.parse_ttl(str(ttl_path), classify=False)
    items = []
    for index, annotation in enumerate(gcg.standard_annotations):
        items.append(Item(
            id=f"{gcg.model_id}#{index}",
            path=Path(ttl_path),
            meta={"model_id": gcg.model_id, "unit_index": index,
                  "edges": len(annotation.edges)},
            payload={"gcg": gcg, "annotation": annotation},
        ))
    return gcg, items


def extract_units(builder, ttl_path):
    """Return one Item per raw annotation unit (see ``parse_model_units``)."""
    return parse_model_units(builder, ttl_path)[1]


def build_unit_rule_registry(check_keys=None):
    """Map unit-rule entrypoint names (``unit_<key>``) to callables.

    Defaults to the full ordered check set from the builder. Used by the CLI to
    resolve ``kind: python`` unit rules.
    """
    from ..gocam_ttl import GoCamGraphBuilder
    keys = check_keys if check_keys is not None else GoCamGraphBuilder.UNIT_CHECK_KEYS
    return {f"unit_{key}": make_unit_rule(key) for key in keys}
