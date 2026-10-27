"""Phase B unit-level rules: wrap each per-check callable from gocam_ttl as a
ratchet rule over a single annotation unit.

A unit item carries the parsed model and one annotation in ``item.payload``
(``gcg`` + ``annotation``) -- in-memory, not serialized. The shared ``ctx`` is
the ``GoCamGraphBuilder`` (GO/RO loaded once). A unit rule removes the unit when
its check flags one or more edges; RO-dependent checks SKIP (keep + record) when
no RO ontology is loaded, mirroring gocam_ttl's gating.
"""

from pathlib import Path

import rdflib

from .core import Item, Verdict

DC_CONTRIBUTOR = rdflib.namespace.DC.contributor

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


def gpad_lines_estimate(annotation):
    """Estimated GPAD line count for one annotation unit: the number of
    (edge, evidence individual) pairs over ALL of the unit's edges.

    CAVEAT: minerva's --lego-to-gpad-sparql emits one line per (backbone edge,
    evidence) and folds extension edges (occurs_in, has_input, ...) into the
    line's extension column, so a unit with evidenced extension edges is
    over-counted here. It is an estimate; scripts/validate_gpad_lines.py
    measures it against a real export.
    """
    return sum(len(edge.evidence_uris) for edge in annotation.edges.values())


def unit_contributors(gcg, annotation):
    """Sorted dc:contributor values on the unit's evidence individuals and on
    its edges' reified owl:Axiom nodes (both carry them in Noctua output; the
    model-level and individual-level contributors are deliberately excluded)."""
    found = set()
    for edge in annotation.edges.values():
        found.update(str(c) for c in gcg.g.objects(edge.bnode, DC_CONTRIBUTOR))
        for evidence_uri in edge.evidence_uris:
            found.update(str(c) for c in gcg.g.objects(evidence_uri, DC_CONTRIBUTOR))
    return sorted(found)


def model_contributors(gcg):
    """Sorted dc:contributor values on the model-level owl:Ontology subject."""
    if not gcg.model_id:
        return []
    model_uri = rdflib.URIRef(gcg.model_id)
    return sorted({str(c) for c in gcg.g.objects(model_uri, DC_CONTRIBUTOR)})


def parse_model_units(builder, ttl_path):
    """Parse a model WITHOUT classifying; return ``(gcg, units)`` where ``units``
    is one Item per raw annotation unit (connected-component subgraph). The
    returned ``gcg`` carries model-level metadata (modelstate, groups, title)
    for per-model stats.

    Unit meta carries the two reporting axes: ``gpad_lines_est`` (see
    ``gpad_lines_estimate``) and ``contributors`` (see ``unit_contributors``).
    """
    gcg = builder.parse_ttl(str(ttl_path), classify=False)
    items = []
    for index, annotation in enumerate(gcg.standard_annotations):
        items.append(Item(
            id=f"{gcg.model_id}#{index}",
            path=Path(ttl_path),
            meta={"model_id": gcg.model_id, "unit_index": index,
                  "edges": len(annotation.edges),
                  "gpad_lines_est": gpad_lines_estimate(annotation),
                  "contributors": unit_contributors(gcg, annotation)},
            payload={"gcg": gcg, "annotation": annotation},
        ))
    return gcg, items


def count_model(builder, ttl_path):
    """Cheap counting pass over one model (no checks run): the per-model fields
    the ratchet reports for a model that Phase A removed. Same parser as Phase
    B, so the line estimate is comparable across phases."""
    gcg, units = parse_model_units(builder, ttl_path)
    return {
        "modelstate": gcg.modelstate,
        "groups": gcg.groups or [],
        "title": gcg.title,
        "contributors": model_contributors(gcg),
        "total_units": len(units),
        "gpad_lines_est": sum(u.meta["gpad_lines_est"] for u in units),
    }


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
