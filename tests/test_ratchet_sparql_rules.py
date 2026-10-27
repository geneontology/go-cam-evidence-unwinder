"""Tests for the native .rq SPARQL rules (Task 6)."""

from pathlib import Path

from gocam_unwinder.ratchet.core import Item
from gocam_unwinder.ratchet.engine import resolve_adapter
from gocam_unwinder.ratchet.rules import load_rules

RES = Path("resources/test")


def _model_rule(rule_id):
    rules = load_rules("rules", phase="model", include_disabled=True)
    return next(r for r in rules if r.id == rule_id)


def test_has_activity_edges_keeps_real_removes_empty(tmp_path):
    adapter = resolve_adapter(_model_rule("model_has_activity_edges"))

    real = Item(id="MGI_MGI_1100089", path=RES / "MGI_MGI_1100089.ttl")
    assert adapter.apply(real).keep is True  # has reified OBO edges

    empty = tmp_path / "empty.ttl"
    empty.write_text('@prefix owl: <http://www.w3.org/2002/07/owl#> .\n'
                     '<http://model.geneontology.org/E> a owl:Ontology .\n')
    assert adapter.apply(Item(id="E", path=empty)).keep is False  # no edges -> dropped


def test_disconnected_individuals_select_rule(tmp_path):
    adapter = resolve_adapter(_model_rule("model_disconnected_individuals"))

    # An orphan named individual -> SELECT returns a row -> removed.
    orphan = tmp_path / "orphan.ttl"
    orphan.write_text(
        '@prefix owl: <http://www.w3.org/2002/07/owl#> .\n'
        '@prefix ex: <http://x/> .\n'
        'ex:a a owl:NamedIndividual .\n')
    assert adapter.apply(Item(id="o", path=orphan)).keep is False

    # Two individuals joined by a reified edge -> SELECT empty -> kept.
    connected = tmp_path / "connected.ttl"
    connected.write_text(
        '@prefix owl: <http://www.w3.org/2002/07/owl#> .\n'
        '@prefix obo: <http://purl.obolibrary.org/obo/> .\n'
        '@prefix ex: <http://x/> .\n'
        'ex:a a owl:NamedIndividual .\n'
        'ex:b a owl:NamedIndividual .\n'
        '[] a owl:Axiom ; owl:annotatedSource ex:a ;\n'
        '   owl:annotatedProperty obo:RO_0002333 ; owl:annotatedTarget ex:b .\n')
    assert adapter.apply(Item(id="c", path=connected)).keep is True
