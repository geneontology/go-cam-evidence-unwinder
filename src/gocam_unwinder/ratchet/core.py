"""Core data types for the standard-annotation ratchet.

These are deliberately generic over what an "item" is: a whole model in Phase A,
an annotation unit in Phase B. The rule adapters know how to interpret an item;
the runner only needs each item's stable ``id`` and the ability to hand the item
to a rule. Survivor sets are serialized as JSONL manifests so runs are
resumable and each stage reads the prior stage's output from disk.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple


@dataclass
class Item:
    """One element of the set flowing through the ratchet.

    id    -- stable, unique identifier within the set (e.g. a model id, or
             ``"<model_id>#<unit_index>"`` for an annotation unit).
    path  -- backing file for the item, if any (the model ``.ttl`` in Phase A).
    meta  -- arbitrary metadata carried forward between stages; persisted in the
             survivor manifest so later stages and reports can use it.
    """

    id: str
    path: Optional[Path] = None
    meta: dict = field(default_factory=dict)
    # Runtime-only payload (e.g. a parsed GoCamGraph + annotation for a Phase B
    # unit). Deliberately NOT serialized into the manifest and excluded from
    # equality, so it can hold non-JSON-able in-memory objects.
    payload: dict = field(default_factory=dict, compare=False, repr=False)

    def to_record(self) -> dict:
        rec = {"id": self.id}
        if self.path is not None:
            rec["path"] = str(self.path)
        if self.meta:
            rec["meta"] = self.meta
        return rec

    @classmethod
    def from_record(cls, rec: dict) -> "Item":
        path = rec.get("path")
        return cls(id=rec["id"], path=Path(path) if path else None,
                   meta=rec.get("meta", {}))


@dataclass
class Verdict:
    """The outcome of applying one rule to one item.

    keep    -- True if the item survives this stage (passes the rule).
    reason  -- short machine key explaining a removal (or a skip); typically the
               rule id. ``None`` for a plain pass.
    detail  -- extra fields for the report row (e.g. the source/predicate/target
               of the offending edge).
    skipped -- True if the rule did not actually run (e.g. an RO-dependent rule
               with no RO loaded). A skipped rule KEEPS the item but is recorded
               as ``skip`` so a stage that silently did nothing stays visible.
    """

    keep: bool
    reason: Optional[str] = None
    detail: dict = field(default_factory=dict)
    skipped: bool = False

    @classmethod
    def survive(cls, **detail) -> "Verdict":
        return cls(keep=True, detail=detail)

    @classmethod
    def reject(cls, reason, **detail) -> "Verdict":
        return cls(keep=False, reason=reason, detail=detail)

    @classmethod
    def skip(cls, reason) -> "Verdict":
        return cls(keep=True, reason=reason, skipped=True)


@dataclass
class Manifest:
    """An ordered set of items = the set ``Sn`` at some point in the ratchet."""

    items: List[Item]

    @property
    def ids(self) -> List[str]:
        return [it.id for it in self.items]

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    @classmethod
    def of(cls, ids) -> "Manifest":
        """Convenience for tests / bootstrapping: a manifest from bare ids."""
        return cls([Item(id=i) for i in ids])

    def write(self, path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as fh:
            for it in self.items:
                fh.write(json.dumps(it.to_record()) + "\n")

    @classmethod
    def read(cls, path) -> "Manifest":
        items = []
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    items.append(Item.from_record(json.loads(line)))
        return cls(items)


@dataclass
class StageResult:
    """What one stage produced, returned to the orchestrator."""

    index: int
    stage_id: str
    survivors: Manifest
    rejected: List[Tuple[Item, Verdict]]
    report_rows: List[dict]
    in_count: int

    @property
    def out_count(self) -> int:
        return len(self.survivors)

    @property
    def rejected_ids(self) -> List[str]:
        return [it.id for it, _ in self.rejected]
