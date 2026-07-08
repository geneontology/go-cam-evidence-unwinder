"""Rule adapters: the two ways a rule decides keep/remove for an item, both
behind a uniform ``apply(item, ctx) -> Verdict``. Per the engine decision,
models are processed per-file in-process with rdflib (no Blazegraph journal, no
``arq`` subprocess-per-file).

  * ``PythonRuleAdapter`` wraps a Python predicate ``(ctx, item) -> Verdict``.
    This is how the existing ontology-aware ``gocam_ttl`` checks are reused as
    rules without rewriting them as SPARQL.
  * ``SparqlRuleAdapter`` runs a SPARQL ASK/SELECT over the item's model graph
    and maps the result to a verdict. This is how new/simple structural rules
    (the noctua-models ``.rq`` style) are authored as data, no code.
"""

from pathlib import Path

import rdflib

from .core import Verdict


class PythonRuleAdapter:
    """Adapt a Python predicate ``func(ctx, item) -> Verdict`` into a rule.

    ``reason`` is the default removal reason, filled in when the predicate
    returns a removal without its own reason.
    """

    def __init__(self, func, reason=None):
        self.func = func
        self.reason = reason

    def apply(self, item, ctx=None) -> Verdict:
        verdict = self.func(ctx, item)
        if not verdict.keep and verdict.reason is None and self.reason:
            verdict.reason = self.reason
        return verdict


class SparqlRuleAdapter:
    """Run a SPARQL query over the item's model TTL and decide keep/remove.

    remove_when:
      "ask_true"        -- remove when the ASK returns True (the query describes
                           a disqualifying violation).
      "ask_false"       -- remove when the ASK returns False (the query asserts a
                           required property the item must have).
      "select_nonempty" -- remove when a SELECT returns one or more rows.
    """

    def __init__(self, query=None, query_path=None, remove_when="ask_true",
                 reason="sparql_rule"):
        if query is None and query_path is None:
            raise ValueError("SparqlRuleAdapter needs query or query_path")
        if query is None:
            query = Path(query_path).read_text()
        self.query = query
        self.remove_when = remove_when
        self.reason = reason

    def apply(self, item, ctx=None) -> Verdict:
        graph = rdflib.Graph()
        graph.parse(str(item.path), format="ttl")
        result = graph.query(self.query)

        if self.remove_when in ("ask_true", "ask_false"):
            ask = bool(result.askAnswer)
            violated = ask if self.remove_when == "ask_true" else (not ask)
        elif self.remove_when == "select_nonempty":
            violated = len(result) > 0
        else:
            raise ValueError(f"unknown remove_when {self.remove_when!r}")

        if violated:
            return Verdict.reject(self.reason)
        return Verdict.survive()


def resolve_adapter(rule, python_registry=None):
    """Bind a ``Rule`` (manifest data) to a runnable adapter.

    python_registry -- dict ``{entrypoint_name: callable(ctx, item) -> Verdict}``
                       used to resolve ``kind: python`` rules.
    """
    if rule.kind == "sparql":
        return SparqlRuleAdapter(query_path=rule.query_path,
                                 remove_when=rule.remove_when, reason=rule.id)
    python_registry = python_registry or {}
    func = python_registry.get(rule.entrypoint)
    if func is None:
        raise KeyError(
            f"rule {rule.id!r}: no python entrypoint {rule.entrypoint!r} registered")
    return PythonRuleAdapter(func, reason=rule.id)
