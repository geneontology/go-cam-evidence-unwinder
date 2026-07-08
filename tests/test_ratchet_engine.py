"""Tests for the ratchet rule adapters (Task 3)."""

import textwrap

from gocam_unwinder.ratchet.core import Item, Verdict
from gocam_unwinder.ratchet.engine import (
    PythonRuleAdapter,
    SparqlRuleAdapter,
    resolve_adapter,
)
from gocam_unwinder.ratchet.rules import Rule

EDGE_NO_EV_ASK = textwrap.dedent("""
    PREFIX owl: <http://www.w3.org/2002/07/owl#>
    PREFIX lego: <http://geneontology.org/lego/>
    ASK {
      ?ax a owl:Axiom ;
          owl:annotatedSource ?s ;
          owl:annotatedProperty ?p ;
          owl:annotatedTarget ?t .
      FILTER(STRSTARTS(STR(?p), "http://purl.obolibrary.org/obo/"))
      FILTER NOT EXISTS { ?ax lego:evidence ?e }
    }
""")

_MODEL = """@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix obo: <http://purl.obolibrary.org/obo/> .
@prefix lego: <http://geneontology.org/lego/> .
@prefix ex: <http://model.geneontology.org/ex/> .

ex:s a owl:NamedIndividual .
ex:t a owl:NamedIndividual .
ex:ev a owl:NamedIndividual .
[] a owl:Axiom ;
   owl:annotatedSource ex:s ;
   owl:annotatedProperty obo:RO_0002333 ;
   owl:annotatedTarget ex:t %s .
"""


def _model(tmp_path, name, evidence):
    ev = " ;\n   lego:evidence ex:ev" if evidence else ""
    p = tmp_path / name
    p.write_text(_MODEL % ev)
    return Item(id=name, path=p)


def test_sparql_ask_rule_removes_on_match(tmp_path):
    rule = SparqlRuleAdapter(query=EDGE_NO_EV_ASK, remove_when="ask_true",
                             reason="edge_without_evidence")
    no_ev = _model(tmp_path, "no_ev.ttl", evidence=False)
    has_ev = _model(tmp_path, "has_ev.ttl", evidence=True)

    v = rule.apply(no_ev)
    assert v.keep is False and v.reason == "edge_without_evidence"
    assert rule.apply(has_ev).keep is True


def test_sparql_ask_false_inverts(tmp_path):
    # "ask_false" removes when the ASK is False (i.e. asserts a required shape).
    rule = SparqlRuleAdapter(query=EDGE_NO_EV_ASK, remove_when="ask_false",
                             reason="must_have_evidence_gap")
    no_ev = _model(tmp_path, "no_ev.ttl", evidence=False)
    has_ev = _model(tmp_path, "has_ev.ttl", evidence=True)
    assert rule.apply(no_ev).keep is True   # ASK true -> not removed
    assert rule.apply(has_ev).keep is False  # ASK false -> removed


def test_python_rule_fills_default_reason():
    def predicate(ctx, item):
        if "bad" in item.id:
            return Verdict.reject(None)  # no reason -> adapter supplies default
        return Verdict.survive()

    rule = PythonRuleAdapter(predicate, reason="is_bad")
    assert rule.apply(Item(id="good")).keep is True
    v = rule.apply(Item(id="bad1"))
    assert v.keep is False and v.reason == "is_bad"


def test_resolve_adapter_dispatches_python(tmp_path):
    reg = {"my_fn": lambda ctx, item: Verdict.survive()}
    prule = Rule(id="p", phase="unit", kind="python", dir=tmp_path, entrypoint="my_fn")
    assert resolve_adapter(prule, reg).apply(Item(id="x")).keep is True


def test_resolve_adapter_dispatches_sparql(tmp_path):
    qdir = tmp_path / "sp"
    qdir.mkdir()
    (qdir / "query.rq").write_text(EDGE_NO_EV_ASK)
    srule = Rule(id="edge_without_evidence", phase="unit", kind="sparql",
                 dir=qdir, remove_when="ask_true")
    item = _model(tmp_path, "n.ttl", evidence=False)
    assert resolve_adapter(srule).apply(item).keep is False


def test_resolve_adapter_unknown_python_entrypoint_raises(tmp_path):
    prule = Rule(id="p", phase="unit", kind="python", dir=tmp_path, entrypoint="nope")
    try:
        resolve_adapter(prule, {})
        assert False, "expected KeyError"
    except KeyError as e:
        assert "nope" in str(e)
