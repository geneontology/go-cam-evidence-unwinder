"""Standard-annotation ratchet: a two-level, monotonic filtering mini-pipeline.

The ratchet carries a shrinking *set* of items through an ordered series of rule
stages. Each stage reads the prior stage's survivors, applies one rule, and
writes the new survivors plus a "what got filtered out" report. Phase A ratchets
whole models; Phase B ratchets annotation units over the surviving models.

Rules are data (``rules/NN-name/rule.yaml`` [+ ``query.rq``]); the engine runs
them per-file in-process with rdflib, supporting both Python predicates (reusing
the existing ``gocam_ttl`` checks) and standalone SPARQL ASK/SELECT rules.

See ``docs/plans/2026-06-25-standard-annotation-ratchet.md``.
"""

from .core import Item, Manifest, StageResult, Verdict
from .rules import Rule, load_rules
from .runner import run_stage
from .engine import PythonRuleAdapter, SparqlRuleAdapter, resolve_adapter

__all__ = [
    "Item",
    "Manifest",
    "StageResult",
    "Verdict",
    "Rule",
    "load_rules",
    "run_stage",
    "PythonRuleAdapter",
    "SparqlRuleAdapter",
    "resolve_adapter",
]
