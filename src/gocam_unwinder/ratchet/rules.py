"""Rule manifests: rules are data, stored as ``rules/NN-name/rule.yaml``.

A rule directory holds a ``rule.yaml`` describing the rule and, for SPARQL
rules, a sibling ``query.rq``. ``load_rules`` discovers rule dirs and orders
them by directory name -- a zero-padded ``NN-`` prefix makes lexical order equal
execution order. The runner walks them in order; adding a rule needs no code
change to the runner.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

PHASES = ("model", "unit")
KINDS = ("python", "sparql")
REMOVE_WHEN = ("ask_true", "ask_false", "select_nonempty")


@dataclass
class Rule:
    """Parsed ``rule.yaml`` -- pure manifest data, resolved to an adapter later.

    id          -- stable rule key (matches a ``failed_checks`` key where one
                   exists). Used in report rows and output filenames.
    phase       -- "model" (Phase A) or "unit" (Phase B).
    kind        -- "python" (a registered predicate) or "sparql" (a query.rq).
    stage       -- cost tier; only affects ordering hints within a phase.
    tsv         -- std_annot_rules.tsv number, or None.
    entrypoint  -- python: callable name in the rule registry. sparql: unused.
    inputs      -- declared dependencies (e.g. ["go", "ro", "gp_allowlist",
                   "true_gocam_set"]); informational + used by the CLI to fail
                   fast if an input is missing.
    remove_when -- sparql: how the query result maps to a removal.
    """

    id: str
    phase: str
    kind: str
    dir: Path
    stage: int = 0
    tsv: Optional[int] = None
    entrypoint: Optional[str] = None
    inputs: List[str] = field(default_factory=list)
    description: str = ""
    enabled: bool = True
    remove_when: str = "ask_true"
    order_key: str = ""

    @property
    def query_path(self) -> Path:
        return self.dir / "query.rq"


def _parse_rule(rule_dir: Path) -> Rule:
    manifest = rule_dir / "rule.yaml"
    with open(manifest) as fh:
        data = yaml.safe_load(fh) or {}

    if "id" not in data:
        raise ValueError(f"{manifest}: missing required 'id'")
    phase = data.get("phase")
    if phase not in PHASES:
        raise ValueError(f"{manifest}: phase must be one of {PHASES}, got {phase!r}")
    kind = data.get("kind")
    if kind not in KINDS:
        raise ValueError(f"{manifest}: kind must be one of {KINDS}, got {kind!r}")
    remove_when = data.get("remove_when", "ask_true")
    if remove_when not in REMOVE_WHEN:
        raise ValueError(
            f"{manifest}: remove_when must be one of {REMOVE_WHEN}, got {remove_when!r}")
    if kind == "sparql" and not (rule_dir / "query.rq").exists():
        raise ValueError(f"{manifest}: kind 'sparql' requires a sibling query.rq")
    if kind == "python" and not data.get("entrypoint"):
        raise ValueError(f"{manifest}: kind 'python' requires an 'entrypoint'")

    return Rule(
        id=data["id"],
        phase=phase,
        kind=kind,
        dir=rule_dir,
        stage=int(data.get("stage", 0)),
        tsv=data.get("tsv"),
        entrypoint=data.get("entrypoint"),
        inputs=list(data.get("inputs", []) or []),
        description=data.get("description", "") or "",
        enabled=bool(data.get("enabled", True)),
        remove_when=remove_when,
        order_key=rule_dir.name,
    )


def load_rules(rules_dir, phase: Optional[str] = None,
               include_disabled: bool = False) -> List[Rule]:
    """Load rules under ``rules_dir``, ordered by directory name.

    phase            -- if given ("model"|"unit"), only that phase's rules.
    include_disabled -- if False (default), drop rules with ``enabled: false``.
    """
    rules_dir = Path(rules_dir)
    rules = []
    for manifest in sorted(rules_dir.glob("*/rule.yaml"),
                           key=lambda p: p.parent.name):
        rule = _parse_rule(manifest.parent)
        if not include_disabled and not rule.enabled:
            continue
        if phase is not None and rule.phase != phase:
            continue
        rules.append(rule)
    return rules
