# Modularize Per-Model Statistics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the inline per-model statistics block in `gocam_ttl.py`'s `main()` into a reusable `ModelStats` dataclass + `GoCamGraphBuilder.compute_model_stats()` method, leaving `--report-file` byte-identical, and add an extended-column stats TSV to `debug_non_standard.py`.

**Architecture:** Computation moves to a no-I/O builder method returning a `ModelStats`; formatting (base = today's 11 columns; extended = base + triage columns) lives on the dataclass as the single source of column truth. `main()` uses the base row; `debug_non_standard.py` uses the extended row via a new `--stats-output`. The extended fields (nested-extension bucket counts, per-check failure counts, "fixable" counts) are gated behind an `extended` flag so the frozen report path is unchanged.

**Tech Stack:** Python 3, `rdflib`, `ontobio`, `dataclasses`. Tests use `pytest`.

**Design doc:** `docs/plans/2026-06-24-modular-model-stats-design.md`

> **Commits:** This repo's owner stages and commits manually. Do **not** run `git add` or `git commit`. Where this plan says "Checkpoint," run the listed verification and stop for the owner to review/commit.

---

## Context

The per-model stats row is computed inline in `main()` (`src/gocam_unwinder/gocam_ttl.py:1574-1631`) and cannot be reused. `debug_non_standard.py` has no model-level stats output, only a per-edge "bucket" TSV. We want the same stats there plus triage columns, without changing `--report-file` (its 11 columns are documented in `CLAUDE.md` and feed the Makefile's `noctua_models_graph_counts_*.tsv`). The "fixable" columns reuse the existing `plan_nested_anatomy_fixes` planner so they can't drift from what the `--fix-nested-anatomy` fixer actually does.

## Constraints

- `--report-file` / stdout output must stay **byte-identical** (same header, same 11 columns, same `gomodel:` prefix, same ordering).
- No change to the classification pipeline (`filter_out_non_std_annotations`) or `failed_checks` semantics.
- `debug_non_standard.py`'s existing `--tsv-output` bucket report and console summary stay unchanged.
- The only change to `plan_nested_anatomy_fixes` is an additive `warn` parameter (default preserves current behavior).

## Out of Scope

- Adding the new columns to `--report-file` (frozen by decision).
- Introducing a tracked `failed_checks` entry for nested anatomy.
- Re-validating or re-classifying models after a fix.

---

## File Structure

- **Modify** `src/gocam_unwinder/gocam_ttl.py`:
  - Add `from dataclasses import dataclass, field` (line 12 area).
  - New module-level `CHECK_NAMES`, `NESTING_ATTRIBUTABLE_CHECKS`, and `ModelStats` dataclass (inserted after `find_nested_extensions`, which ends at line 164, before `class StandardAnnotationEdge` at line 166).
  - Add `warn=True` parameter to `plan_nested_anatomy_fixes` (def at line 1166).
  - New `GoCamGraphBuilder.compute_model_stats()` (added after `plan_nested_anatomy_fixes`, before `parse_ttl` at line 1224).
  - Rewire `main()` header (lines 1547-1548), stats block (1574-1631), and split decision (1634).
- **Modify** `debug_non_standard.py`: add `import os`, a `--stats-output` arg, a stats accumulator in the model loop, and a TSV writer after the loop.
- **Create** `resources/test/mf_nested_anatomy_noev_example.ttl`, `resources/test/mf_nested_anatomy_unfixable_example.ttl` (synthetic fixtures for the non-standard "fixable" rule).
- **Modify** `tests/test_gocam_ttl.py`: append new test functions.

---

## Task 1: Add `warn` parameter to `plan_nested_anatomy_fixes`

Additive, behavior-preserving by default. `compute_model_stats` (Task 5) calls it with `warn=False` so stats computation is silent.

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py:1166-1195`
- Test: `tests/test_gocam_ttl.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_gocam_ttl.py`:

```python
def test_plan_nested_anatomy_fixes_warn_param(builder):
    """plan_nested_anatomy_fixes accepts warn=False and returns the same plan
    as the default call (the flag only gates warning output, not results)."""
    gocam_graph = builder.parse_ttl("resources/test/5966411600000001.ttl")
    default_plan = builder.plan_nested_anatomy_fixes(gocam_graph)
    quiet_plan = builder.plan_nested_anatomy_fixes(gocam_graph, warn=False)
    assert [r["bnode_id"] for r in quiet_plan] == [r["bnode_id"] for r in default_plan]
    assert len(quiet_plan) == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `source env/bin/activate && pytest tests/test_gocam_ttl.py::test_plan_nested_anatomy_fixes_warn_param -v`
Expected: FAIL with `TypeError: plan_nested_anatomy_fixes() got an unexpected keyword argument 'warn'`.

- [ ] **Step 3: Add the parameter**

In `src/gocam_unwinder/gocam_ttl.py`, change the signature at line 1166:

```python
    def plan_nested_anatomy_fixes(self, gocam: GoCamGraph, warn: bool = True) -> list:
```

and guard the warning print (currently lines 1192-1194) with `if warn:`:

```python
            if len(primary_individuals) != 1:
                if warn:
                    print(f"WARNING: skipping annotation in {gocam.model_id} "
                          f"({gocam.title}) — expected 1 primary {lead} individual, "
                          f"found {len(primary_individuals)}")
                continue
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `source env/bin/activate && pytest tests/test_gocam_ttl.py::test_plan_nested_anatomy_fixes_warn_param -v`
Expected: PASS.

- [ ] **Step 5: Checkpoint** — stop for review/commit.

---

## Task 2: `CHECK_NAMES`, `NESTING_ATTRIBUTABLE_CHECKS`, `ModelStats` dataclass

The data container + formatters. Pure data/formatting — testable from a hand-built object with no graph.

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py:12` (import) and after line 164 (new code)
- Test: `tests/test_gocam_ttl.py`

- [ ] **Step 1: Write the tests**

Append to `tests/test_gocam_ttl.py`:

```python
def test_model_stats_base_header():
    """base_header() is exactly today's 11-column --report-file header."""
    from gocam_unwinder.gocam_ttl import ModelStats
    assert ModelStats.base_header() == [
        "Model ID", "Title", "Standard Annotations", "Non-Standard Annotations",
        "Multi-Evidence Annotations", "Mixed Annotation Type", "MF-causal->MF Edges",
        "Edges w/o Evidence", "Model State", "Groups", "Multi-Evidence GO Terms"]


def test_model_stats_base_row_formatting():
    """to_base_row() renders bool/list/str fields exactly as the legacy block did."""
    from gocam_unwinder.gocam_ttl import ModelStats
    s = ModelStats(
        model_id="gomodel:X", title="T", standard_count=2, non_standard_count=1,
        multi_evidence_count=3, mixed_annotation_type=True, mf_causal_edge_count=0,
        no_evidence_edge_count=1, modelstate="production", groups=["MGI", "SGD"],
        multi_evidence_go_terms=["alpha", "beta"], std_multi_evidence_count=2)
    assert s.to_base_row() == [
        "gomodel:X", "T", "2", "1", "3", "Yes", "0", "1", "production",
        "MGI|SGD", "alpha|beta"]


def test_model_stats_base_row_empties():
    """Empty groups/terms render as '' and a None modelstate as ''; mixed False -> 'No'."""
    from gocam_unwinder.gocam_ttl import ModelStats
    s = ModelStats(
        model_id="gomodel:Y", title="T2", standard_count=0, non_standard_count=0,
        multi_evidence_count=0, mixed_annotation_type=False, mf_causal_edge_count=0,
        no_evidence_edge_count=0, modelstate=None, groups=[],
        multi_evidence_go_terms=[], std_multi_evidence_count=0)
    row = s.to_base_row()
    assert row[5] == "No" and row[8] == "" and row[9] == "" and row[10] == ""


def test_model_stats_extended_header():
    """extended_header() = base + 5 triage columns + one fail:<name> per CHECK_NAMES."""
    from gocam_unwinder.gocam_ttl import ModelStats, CHECK_NAMES
    hdr = ModelStats.extended_header()
    assert hdr[:11] == ModelStats.base_header()
    assert hdr[11:16] == ["Nested MF Extensions", "Nested BP Extensions",
                          "Nested CC Extensions", "Fixable (Standard)",
                          "Fixable (Non-Standard)"]
    assert hdr[16:] == [f"fail:{n}" for n in CHECK_NAMES]
    assert len(hdr) == 16 + len(CHECK_NAMES)


def test_model_stats_extended_row_failcounts_order():
    """to_extended_row() emits failure_counts in CHECK_NAMES order, 0 where absent."""
    from gocam_unwinder.gocam_ttl import ModelStats, CHECK_NAMES
    s = ModelStats(
        model_id="gomodel:Z", title="T3", standard_count=1, non_standard_count=1,
        multi_evidence_count=0, mixed_annotation_type=True, mf_causal_edge_count=0,
        no_evidence_edge_count=0, modelstate="production", groups=[],
        multi_evidence_go_terms=[], std_multi_evidence_count=0,
        nested_mf_count=1, nested_bp_count=2, nested_cc_count=3,
        fixable_standard_count=4, fixable_nonstandard_count=5)
    s.failure_counts["edge_without_evidence"] = 7
    row = s.to_extended_row()
    assert row[11:16] == ["1", "2", "3", "4", "5"]
    idx = 16 + CHECK_NAMES.index("edge_without_evidence")
    assert row[idx] == "7"
    # every other check column is "0"
    assert sum(1 for c in row[16:] if c == "0") == len(CHECK_NAMES) - 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `source env/bin/activate && pytest tests/test_gocam_ttl.py -k model_stats -v`
Expected: FAIL with `ImportError: cannot import name 'ModelStats'`.

- [ ] **Step 3: Add the import**

In `src/gocam_unwinder/gocam_ttl.py`, after `from typing import List` (line 12), add:

```python
from dataclasses import dataclass, field
```

- [ ] **Step 4: Add the constants and dataclass**

In `src/gocam_unwinder/gocam_ttl.py`, insert after `find_nested_extensions` (ends line 164) and before `class StandardAnnotationEdge` (line 166):

```python
# Canonical order of all standard-annotation failure-check names. Fixed so the
# extended stats report has stable columns even when a model has zero of a given
# failure. Mirrors the checks recorded by filter_out_non_std_annotations.
CHECK_NAMES = (
    "inconsistent_evidence",
    "multiple_mf_bp",
    "mf_causal_mf",
    "edge_without_evidence",
    "invalid_gp_mf_relation",
    "invalid_gp_cc_relation",
    "invalid_gp_bp_relation",
    "invalid_mf_bp_relation",
    "invalid_mf_cc_relation",
    "invalid_bp_cc_relation",
    "multiple_mf_anatomy",
    "enabler_not_gp",
)

# The only checks a both-anatomical nested extension edge can contribute to: a
# relation-validity check never fires on an anatomy->anatomy edge (no rule has an
# anatomy source category), so de-nesting accounts for the whole of a
# non-standard annotation's failures only when every failing check is one of
# these. Defines a "fixable" non-standard annotation in compute_model_stats.
NESTING_ATTRIBUTABLE_CHECKS = frozenset({
    "inconsistent_evidence",
    "edge_without_evidence",
})


@dataclass
class ModelStats:
    """Per-model statistics. Base fields reproduce the --report-file columns
    exactly; extended fields back the debug_non_standard.py stats report."""

    # --- base fields (today's --report-file columns) ---
    model_id: str                        # exact "Model ID" cell (caller-supplied)
    title: str
    standard_count: int
    non_standard_count: int
    multi_evidence_count: int            # std + non-std
    mixed_annotation_type: bool
    mf_causal_edge_count: int            # "MF-causal->MF Edges" (EDGE count)
    no_evidence_edge_count: int
    modelstate: str
    groups: list                         # list[str], already resolved to labels
    multi_evidence_go_terms: list        # sorted list[str], resolved labels
    std_multi_evidence_count: int        # std-only; drives the --split decision

    # --- extended fields (debug stats report only) ---
    nested_mf_count: int = 0
    nested_bp_count: int = 0
    nested_cc_count: int = 0
    fixable_standard_count: int = 0
    fixable_nonstandard_count: int = 0
    failure_counts: dict = field(default_factory=lambda: {n: 0 for n in CHECK_NAMES})

    BASE_HEADERS = ["Model ID", "Title", "Standard Annotations",
        "Non-Standard Annotations", "Multi-Evidence Annotations",
        "Mixed Annotation Type", "MF-causal->MF Edges", "Edges w/o Evidence",
        "Model State", "Groups", "Multi-Evidence GO Terms"]

    @classmethod
    def base_header(cls):
        return list(cls.BASE_HEADERS)

    def to_base_row(self):
        return [self.model_id, self.title, str(self.standard_count),
            str(self.non_standard_count), str(self.multi_evidence_count),
            "Yes" if self.mixed_annotation_type else "No",
            str(self.mf_causal_edge_count), str(self.no_evidence_edge_count),
            self.modelstate or "", "|".join(self.groups),
            "|".join(self.multi_evidence_go_terms)]

    @classmethod
    def extended_header(cls):
        return cls.base_header() + [
            "Nested MF Extensions", "Nested BP Extensions", "Nested CC Extensions",
            "Fixable (Standard)", "Fixable (Non-Standard)",
        ] + [f"fail:{n}" for n in CHECK_NAMES]

    def to_extended_row(self):
        return self.to_base_row() + [
            str(self.nested_mf_count), str(self.nested_bp_count),
            str(self.nested_cc_count), str(self.fixable_standard_count),
            str(self.fixable_nonstandard_count),
        ] + [str(self.failure_counts[n]) for n in CHECK_NAMES]
```

Note: `BASE_HEADERS` is a plain class attribute (a list literal, not a dataclass
field because it has no type annotation), so it is shared across instances and
not part of the generated `__init__`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `source env/bin/activate && pytest tests/test_gocam_ttl.py -k model_stats -v`
Expected: 5 PASS.

- [ ] **Step 6: Checkpoint** — stop for review/commit.

---

## Task 3: `compute_model_stats` — base fields

Lift the inline computation (`main()` lines 1574-1631) into a builder method, computing only base fields for now (the `extended` block is filled in Tasks 4-5).

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py` (add method after `plan_nested_anatomy_fixes`, which ends at line 1222, before `def parse_ttl` at line 1224)
- Test: `tests/test_gocam_ttl.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_gocam_ttl.py`:

```python
def test_compute_model_stats_base(builder):
    """compute_model_stats reproduces the documented base fields for
    MGI_MGI_1100089 (28 standard annotations; a multi-evidence positive case)."""
    gocam_graph = builder.parse_ttl("resources/test/MGI_MGI_1100089.ttl")
    stats = builder.compute_model_stats(gocam_graph, "gomodel:MGI_MGI_1100089")

    assert stats.standard_count == 28
    assert stats.std_multi_evidence_count >= 1          # positive multi-evidence model
    row = stats.to_base_row()
    assert len(row) == 11
    assert row[0] == "gomodel:MGI_MGI_1100089"
    assert row[2] == "28"
    # extended fields untouched on a base (extended=False) call
    assert stats.nested_bp_count == 0 and stats.fixable_standard_count == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `source env/bin/activate && pytest tests/test_gocam_ttl.py::test_compute_model_stats_base -v`
Expected: FAIL with `AttributeError: 'GoCamGraphBuilder' object has no attribute 'compute_model_stats'`.

- [ ] **Step 3: Implement the base method**

In `src/gocam_unwinder/gocam_ttl.py`, add to `GoCamGraphBuilder` after `plan_nested_anatomy_fixes` (after its `return plan` at line 1222) and before `def parse_ttl`:

```python
    def compute_model_stats(self, gocam: GoCamGraph, model_id: str,
                            extended: bool = False) -> ModelStats:
        """
        Compute per-model statistics (no I/O). `model_id` is the exact "Model ID"
        cell the caller wants (e.g. "gomodel:" + filename stem). When `extended`
        is True, also compute the triage fields (nested buckets, per-check failure
        counts, fixable counts) used by the debug stats report.
        """
        std = gocam.standard_annotations
        nonstd = gocam.non_standard_annotations
        all_annots = std + nonstd

        std_multi = sum(1 for a in std if a.has_muliple_evidence())
        multi_ev_terms = set()
        for a in std:
            if a.has_muliple_evidence():
                for edge in a.edges.values():
                    for t in (edge.source_type, edge.target_type):
                        if t:
                            label = self.term_label(t)
                            if label and not label.startswith("http") and ":" not in label:
                                multi_ev_terms.add(label)
        nonstd_multi = sum(1 for a in nonstd if a.has_muliple_evidence())
        mf_causal_edges = sum(len(a.failed_checks.get("mf_causal_mf", set()))
                              for a in nonstd)
        no_ev_edges = sum(1 for a in all_annots
                          for e in a.edges.values() if not e.evidence_uris)

        stats = ModelStats(
            model_id=model_id,
            title=gocam.title,
            standard_count=len(std),
            non_standard_count=len(nonstd),
            multi_evidence_count=std_multi + nonstd_multi,
            mixed_annotation_type=bool(std) and bool(nonstd),
            mf_causal_edge_count=mf_causal_edges,
            no_evidence_edge_count=no_ev_edges,
            modelstate=gocam.modelstate,
            groups=list(gocam.groups or []),
            multi_evidence_go_terms=sorted(multi_ev_terms),
            std_multi_evidence_count=std_multi,
        )
        if not extended:
            return stats
        return stats
```

(The duplicated `return stats` is intentional scaffolding — Tasks 4 and 5 insert
the extended block between the two returns.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `source env/bin/activate && pytest tests/test_gocam_ttl.py::test_compute_model_stats_base -v`
Expected: PASS.

- [ ] **Step 5: Checkpoint** — stop for review/commit.

---

## Task 4: `compute_model_stats` — nested buckets + per-check failure counts

Fill the first part of the extended block. These need no planner — just `find_nested_extensions` and `failed_checks`.

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py` (inside `compute_model_stats`, between the `if not extended: return stats` and the final `return stats`)
- Test: `tests/test_gocam_ttl.py`

- [ ] **Step 1: Write the tests**

Append to `tests/test_gocam_ttl.py`:

```python
def test_compute_model_stats_nested_buckets(builder):
    """On 5966411600000001.ttl the GO:0120045 annotation is BP-led with a nested
    anatomy edge -> nested_bp_count == 1, MF/CC == 0 (matches the debug script)."""
    gocam_graph = builder.parse_ttl("resources/test/5966411600000001.ttl")
    stats = builder.compute_model_stats(gocam_graph, "gomodel:x", extended=True)
    assert stats.nested_bp_count == 1
    assert stats.nested_mf_count == 0
    assert stats.nested_cc_count == 0


def test_compute_model_stats_failure_counts(builder):
    """Per-check counts are annotation-level. SYNGO_5371 fails invalid_mf_cc_relation;
    every CHECK_NAMES key is present (0 where absent)."""
    from gocam_unwinder.gocam_ttl import CHECK_NAMES
    gocam_graph = builder.parse_ttl("resources/test/SYNGO_5371.ttl")
    stats = builder.compute_model_stats(gocam_graph, "gomodel:syngo", extended=True)
    assert set(stats.failure_counts.keys()) == set(CHECK_NAMES)
    assert stats.failure_counts["invalid_mf_cc_relation"] >= 1
    # a check this model does not trip stays 0
    assert stats.failure_counts["mf_causal_mf"] == 0


def test_compute_model_stats_edge_without_evidence_count(builder):
    """66c7d41500000016.ttl has a no-evidence causal edge -> at least one
    annotation flagged edge_without_evidence."""
    gocam_graph = builder.parse_ttl("resources/test/66c7d41500000016.ttl")
    stats = builder.compute_model_stats(gocam_graph, "gomodel:n", extended=True)
    assert stats.failure_counts["edge_without_evidence"] >= 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `source env/bin/activate && pytest tests/test_gocam_ttl.py -k "nested_buckets or failure_counts or edge_without_evidence_count" -v`
Expected: FAIL — `nested_bp_count` / `failure_counts[...]` are still their `0` defaults.

- [ ] **Step 3: Implement the block**

In `compute_model_stats`, replace the scaffold tail:

```python
        if not extended:
            return stats
        return stats
```

with:

```python
        if not extended:
            return stats

        # per-check annotation-level counts (number of annotations with >=1
        # failure of each check)
        for a in all_annots:
            for name in a.failed_checks:
                if name in stats.failure_counts:
                    stats.failure_counts[name] += 1

        # nested-extension bucket counts, by lead aspect (BP > CC > MF)
        nested = {"MF": 0, "BP": 0, "CC": 0}
        for a in all_annots:
            lead, nested_edges = find_nested_extensions(a, self)
            if nested_edges:
                nested[lead] += 1
        stats.nested_mf_count = nested["MF"]
        stats.nested_bp_count = nested["BP"]
        stats.nested_cc_count = nested["CC"]

        return stats
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `source env/bin/activate && pytest tests/test_gocam_ttl.py -k "nested_buckets or failure_counts or edge_without_evidence_count" -v`
Expected: 3 PASS.

- [ ] **Step 5: Checkpoint** — stop for review/commit.

---

## Task 5: `compute_model_stats` — fixable counts + fixtures

Add the "fixable" computation, reusing the real planner (`warn=False`) so it can't diverge from the fixer. Two synthetic fixtures exercise the non-standard branch.

**Files:**
- Create: `resources/test/mf_nested_anatomy_noev_example.ttl`
- Create: `resources/test/mf_nested_anatomy_unfixable_example.ttl`
- Modify: `src/gocam_unwinder/gocam_ttl.py` (inside `compute_model_stats`, before the final `return stats`)
- Test: `tests/test_gocam_ttl.py`

- [ ] **Step 1: Create `resources/test/mf_nested_anatomy_noev_example.ttl`**

MF-led, fixer target, but the nested `CL ─part_of→ EMAPA` edge has **no
evidence** → sole failed check `edge_without_evidence` → non-standard but fixable.

```turtle
<http://model.geneontology.org/mf_nested_anatomy_noev_example> a <http://www.w3.org/2002/07/owl#Ontology> ;
	<http://geneontology.org/lego/modelstate> "production" ;
	<http://purl.org/dc/elements/1.1/title> "MF-led nested anatomy, no-evidence nested edge" ;
	<http://purl.org/pav/providedBy> "http://informatics.jax.org" .

<http://geneontology.org/lego/evidence> a <http://www.w3.org/2002/07/owl#AnnotationProperty> .
<http://purl.org/dc/elements/1.1/date> a <http://www.w3.org/2002/07/owl#AnnotationProperty> .

<http://model.geneontology.org/mf_nested_anatomy_noev_example/mf1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/GO_0004672> ;
	<http://purl.obolibrary.org/obo/RO_0002333> <http://model.geneontology.org/mf_nested_anatomy_noev_example/gp1> ;
	<http://purl.obolibrary.org/obo/BFO_0000066> <http://model.geneontology.org/mf_nested_anatomy_noev_example/anat1> .
<http://model.geneontology.org/mf_nested_anatomy_noev_example/gp1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://identifiers.org/mgi/MGI:1234567> .
<http://model.geneontology.org/mf_nested_anatomy_noev_example/anat1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/CL_0000202> ;
	<http://purl.obolibrary.org/obo/BFO_0000050> <http://model.geneontology.org/mf_nested_anatomy_noev_example/anat2> .
<http://model.geneontology.org/mf_nested_anatomy_noev_example/anat2>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/EMAPA_17597> .
<http://model.geneontology.org/mf_nested_anatomy_noev_example/ev1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
<http://model.geneontology.org/mf_nested_anatomy_noev_example/ev2>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .

# MF -enabled_by-> GP
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/mf_nested_anatomy_noev_example/mf1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/RO_0002333> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/mf_nested_anatomy_noev_example/gp1> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/mf_nested_anatomy_noev_example/ev1> .

# MF -occurs_in-> CL  (direct extension; source is the primary MF)
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/mf_nested_anatomy_noev_example/mf1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/BFO_0000066> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/mf_nested_anatomy_noev_example/anat1> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/mf_nested_anatomy_noev_example/ev2> .

# CL -part_of-> EMAPA  (NESTED, both anatomy) — NO evidence triple
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/mf_nested_anatomy_noev_example/anat1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/BFO_0000050> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/mf_nested_anatomy_noev_example/anat2> .
```

- [ ] **Step 2: Create `resources/test/mf_nested_anatomy_unfixable_example.ttl`**

MF-led, fixer target (nested `CL ─part_of→ EMAPA`, all edges have evidence), but
the annotation **also** has a `GP ─part_of→ CC` edge → `invalid_gp_cc_relation`,
a check **not** in `NESTING_ATTRIBUTABLE_CHECKS` → non-standard and **not**
fixable.

```turtle
<http://model.geneontology.org/mf_nested_anatomy_unfixable_example> a <http://www.w3.org/2002/07/owl#Ontology> ;
	<http://geneontology.org/lego/modelstate> "production" ;
	<http://purl.org/dc/elements/1.1/title> "MF-led nested anatomy with an unrelated GP->CC failure" ;
	<http://purl.org/pav/providedBy> "http://informatics.jax.org" .

<http://geneontology.org/lego/evidence> a <http://www.w3.org/2002/07/owl#AnnotationProperty> .
<http://purl.org/dc/elements/1.1/date> a <http://www.w3.org/2002/07/owl#AnnotationProperty> .

<http://model.geneontology.org/mf_nested_anatomy_unfixable_example/mf1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/GO_0004672> ;
	<http://purl.obolibrary.org/obo/RO_0002333> <http://model.geneontology.org/mf_nested_anatomy_unfixable_example/gp1> ;
	<http://purl.obolibrary.org/obo/BFO_0000066> <http://model.geneontology.org/mf_nested_anatomy_unfixable_example/anat1> .
<http://model.geneontology.org/mf_nested_anatomy_unfixable_example/gp1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://identifiers.org/mgi/MGI:1234567> ;
	<http://purl.obolibrary.org/obo/BFO_0000050> <http://model.geneontology.org/mf_nested_anatomy_unfixable_example/cc1> .
<http://model.geneontology.org/mf_nested_anatomy_unfixable_example/cc1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/GO_0005634> .
<http://model.geneontology.org/mf_nested_anatomy_unfixable_example/anat1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/CL_0000202> ;
	<http://purl.obolibrary.org/obo/BFO_0000050> <http://model.geneontology.org/mf_nested_anatomy_unfixable_example/anat2> .
<http://model.geneontology.org/mf_nested_anatomy_unfixable_example/anat2>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/EMAPA_17597> .
<http://model.geneontology.org/mf_nested_anatomy_unfixable_example/ev1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
<http://model.geneontology.org/mf_nested_anatomy_unfixable_example/ev2>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
<http://model.geneontology.org/mf_nested_anatomy_unfixable_example/ev3>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
<http://model.geneontology.org/mf_nested_anatomy_unfixable_example/ev4>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .

# MF -enabled_by-> GP
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/mf_nested_anatomy_unfixable_example/mf1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/RO_0002333> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/mf_nested_anatomy_unfixable_example/gp1> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/mf_nested_anatomy_unfixable_example/ev1> .

# MF -occurs_in-> CL  (direct extension)
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/mf_nested_anatomy_unfixable_example/mf1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/BFO_0000066> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/mf_nested_anatomy_unfixable_example/anat1> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/mf_nested_anatomy_unfixable_example/ev2> .

# CL -part_of-> EMAPA  (NESTED, both anatomy) — fixer target
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/mf_nested_anatomy_unfixable_example/anat1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/BFO_0000050> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/mf_nested_anatomy_unfixable_example/anat2> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/mf_nested_anatomy_unfixable_example/ev3> .

# GP -part_of-> CC  (WRONG relation; GP->CC must be located_in) -> invalid_gp_cc_relation
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/mf_nested_anatomy_unfixable_example/gp1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/BFO_0000050> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/mf_nested_anatomy_unfixable_example/cc1> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/mf_nested_anatomy_unfixable_example/ev4> .
```

- [ ] **Step 3: Sanity-check both fixtures parse with the expected failure shape**

Run:
```bash
source env/bin/activate && python3 -c "
from gocam_unwinder.gocam_ttl import GoCamGraphBuilder, NESTING_ATTRIBUTABLE_CHECKS
b = GoCamGraphBuilder('target/go_20250601.json', 'resources/test/ro_20250723.owl', resolve_labels_api=False)
for f in ['mf_nested_anatomy_noev_example', 'mf_nested_anatomy_unfixable_example']:
    g = b.parse_ttl(f'resources/test/{f}.ttl')
    fixer_bnodes = {r['bnode_id'] for r in b.plan_nested_anatomy_fixes(g, warn=False)}
    annots = g.standard_annotations + g.non_standard_annotations
    for a in annots:
        if any(bid in fixer_bnodes for bid in a.edges):
            print(f, 'fixer-target failed_checks =', set(a.failed_checks))
"
```
Expected: the `noev` fixture prints a **non-empty subset of**
`NESTING_ATTRIBUTABLE_CHECKS` — `{'edge_without_evidence'}`, and possibly also
`'inconsistent_evidence'` (a no-evidence edge breaks the per-edge evidence-group
invariant); either way it is ⊆ attributable → fixable. The `unfixable` fixture
prints a set containing `invalid_gp_cc_relation` (not ⊆ attributable → not
fixable). If `noev` prints an empty set, the no-evidence edge was dropped during
extraction — confirm `BFO_0000050` is an OBO-namespace relation (it is) so the
edge is kept.

- [ ] **Step 4: Write the fixable tests**

Append to `tests/test_gocam_ttl.py`:

```python
def test_compute_model_stats_fixable_standard(builder):
    """mf_nested_anatomy_example.ttl: the nested-anatomy annotation is standard
    and a fixer target -> fixable_standard_count == 1, non-standard == 0."""
    gocam_graph = builder.parse_ttl("resources/test/mf_nested_anatomy_example.ttl")
    stats = builder.compute_model_stats(gocam_graph, "gomodel:mf", extended=True)
    assert stats.fixable_standard_count == 1
    assert stats.fixable_nonstandard_count == 0


def test_compute_model_stats_fixable_nonstandard(builder):
    """noev fixture: fixer target whose only failed check is edge_without_evidence
    (in NESTING_ATTRIBUTABLE_CHECKS) -> fixable_nonstandard_count == 1."""
    gocam_graph = builder.parse_ttl("resources/test/mf_nested_anatomy_noev_example.ttl")
    stats = builder.compute_model_stats(gocam_graph, "gomodel:noev", extended=True)
    assert stats.fixable_nonstandard_count == 1
    assert stats.fixable_standard_count == 0


def test_compute_model_stats_unfixable_nonstandard(builder):
    """unfixable fixture: fixer target but also fails invalid_gp_cc_relation
    (not attributable to nesting) -> neither fixable count increments."""
    gocam_graph = builder.parse_ttl("resources/test/mf_nested_anatomy_unfixable_example.ttl")
    stats = builder.compute_model_stats(gocam_graph, "gomodel:unfix", extended=True)
    assert stats.fixable_standard_count == 0
    assert stats.fixable_nonstandard_count == 0
    assert stats.failure_counts["invalid_gp_cc_relation"] >= 1
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `source env/bin/activate && pytest tests/test_gocam_ttl.py -k "fixable_standard or fixable_nonstandard or unfixable_nonstandard" -v`
Expected: FAIL — `fixable_*_count` are still `0`.

- [ ] **Step 6: Implement the fixable block**

In `compute_model_stats`, immediately before the final `return stats` (after the
nested-bucket block from Task 4), insert:

```python
        # fixable counts — reuse the real planner so this can't diverge from the
        # fixer. An annotation is a "fixer target" iff the planner would rewrite
        # one of its edges (bnode-id membership). Standard targets are fixable;
        # non-standard targets are fixable only when every failing check is
        # attributable to the nesting (NESTING_ATTRIBUTABLE_CHECKS).
        fixer_bnodes = {r["bnode_id"]
                        for r in self.plan_nested_anatomy_fixes(gocam, warn=False)}
        for a in all_annots:
            if not any(bnid in fixer_bnodes for bnid in a.edges):
                continue
            if not a.failed_checks:
                stats.fixable_standard_count += 1
            elif set(a.failed_checks) <= NESTING_ATTRIBUTABLE_CHECKS:
                stats.fixable_nonstandard_count += 1
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `source env/bin/activate && pytest tests/test_gocam_ttl.py -k "fixable_standard or fixable_nonstandard or unfixable_nonstandard" -v`
Expected: 3 PASS.

- [ ] **Step 8: Checkpoint** — stop for review/commit.

---

## Task 6: Rewire `main()` (keep `--report-file` byte-identical)

Replace the inline stats block with a `compute_model_stats` call. Verify the
report output is unchanged by diffing before/after.

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py:1547-1548` (header), `:1574-1631` (stats block), `:1634` (split decision)

- [ ] **Step 1: Capture a baseline (current code, before editing main)**

Run:
```bash
source env/bin/activate && python src/gocam_unwinder/gocam_ttl.py \
  -d resources/test/ -o target/go_20250601.json -r resources/test/ro_20250723.owl \
  --no-label-api --report-file /tmp/stats_baseline.tsv >/dev/null 2>&1; echo "exit=$?"
wc -l /tmp/stats_baseline.tsv
```
Expected: `exit=0`; the file has a header plus one row per non-delete model.

- [ ] **Step 2: Replace the header line**

In `src/gocam_unwinder/gocam_ttl.py`, replace lines 1547-1548:

```python
    headers = ["Model ID", "Title", "Standard Annotations", "Non-Standard Annotations", "Multi-Evidence Annotations", "Mixed Annotation Type", "MF-causal->MF Edges", "Edges w/o Evidence", "Model State", "Groups", "Multi-Evidence GO Terms"]
    print("\t".join(headers), file=output)
```

with:

```python
    print("\t".join(ModelStats.base_header()), file=output)
```

- [ ] **Step 3: Replace the inline stats block**

In `src/gocam_unwinder/gocam_ttl.py`, replace the entire block at lines 1574-1631
(from the `# Print statistics` comment through the `print("\t".join([...]), file=output)`
row write) with:

```python
        # Compute per-model statistics (base fields only for the frozen report)
        stats = go_cam_graph_builder.compute_model_stats(
            gocam_graph, "gomodel:" + model_id)

        if criteria_fail_output:
            # print standard annotation fail_checks by edge
            go_cam_graph_builder.print_non_standard_annotation_failed_checks(
                gocam_graph, report_file=criteria_fail_output)

        print("\t".join(stats.to_base_row()), file=output)
```

- [ ] **Step 4: Update the split decision to read from `stats`**

In `src/gocam_unwinder/gocam_ttl.py`, change the line that was 1634:

```python
        if args.split_evidence and std_multi_evidence_count >= 1:
```

to:

```python
        if args.split_evidence and stats.std_multi_evidence_count >= 1:
```

- [ ] **Step 5: Regenerate the report and diff against the baseline**

Run:
```bash
source env/bin/activate && python src/gocam_unwinder/gocam_ttl.py \
  -d resources/test/ -o target/go_20250601.json -r resources/test/ro_20250723.owl \
  --no-label-api --report-file /tmp/stats_after.tsv >/dev/null 2>&1; echo "exit=$?"
diff /tmp/stats_baseline.tsv /tmp/stats_after.tsv && echo "IDENTICAL"
```
Expected: `exit=0` and `IDENTICAL` (no diff output). If the diff is non-empty,
the extraction changed a value — compare the differing column against the
`to_base_row()` / `compute_model_stats` field for that column.

- [ ] **Step 6: Run the full suite**

Run: `source env/bin/activate && pytest -q`
Expected: all tests PASS (existing + the new stats tests). The `--split-evidence`
tests in particular confirm `stats.std_multi_evidence_count` still gates splitting.

- [ ] **Step 7: Checkpoint** — stop for review/commit.

---

## Task 7: `debug_non_standard.py --stats-output`

Add the extended stats TSV to the debug script. Its existing bucket report
(`--tsv-output`) and console summary are untouched.

**Files:**
- Modify: `debug_non_standard.py` (imports, argparse near lines 82-93, model loop near line 127, after-loop writer near line 231)

- [ ] **Step 1: Import `os`, `ModelStats`, and add the arg**

In `debug_non_standard.py`, add `import os` near the top (after `import argparse`,
line 4), and add `ModelStats` to the existing import from `gocam_unwinder.gocam_ttl`
(lines 8-14):

```python
from gocam_unwinder.gocam_ttl import (
    GoCamGraphBuilder,
    ModelStats,
    collect_model_files,
    load_skip_filenames,
    pick_lead_aspect,
    find_nested_extensions,
)
```

Add the argument alongside `--tsv-output` (line 90):

```python
    ap.add_argument("--stats-output", help="TSV output file for per-model statistics (extended columns)")
```

- [ ] **Step 2: Accumulate stats in the model loop**

In `main()`, after the `tsv_rows = []` initialization (line 122) add:

```python
    all_stats = []
```

Then in the per-file loop, right after the `modelstate == "delete"` skip
(currently lines 129-130, before `groups = ...`), add:

```python
        model_id = "gomodel:" + os.path.basename(ttl_path).split(".")[0]
        all_stats.append(builder.compute_model_stats(gocam, model_id, extended=True))
```

- [ ] **Step 3: Write the extended TSV after the loop**

In `main()`, after the bucket-report writer block (the `if args.tsv_output:` block
ending at line 231), add:

```python
    # Write extended per-model stats report
    if args.stats_output:
        with open(args.stats_output, "w") as f:
            f.write("\t".join(ModelStats.extended_header()) + "\n")
            for s in all_stats:
                f.write("\t".join(s.to_extended_row()) + "\n")
        print(f"\nStats report written to {args.stats_output} ({len(all_stats)} models)")
```

- [ ] **Step 4: Smoke-test the debug stats output**

Run:
```bash
source env/bin/activate && python3 debug_non_standard.py resources/test/ \
  -o target/go_20250601.json -r resources/test/ro_20250723.owl --no-label-api \
  --stats-output /tmp/debug_stats.tsv >/dev/null 2>&1; echo "exit=$?"
head -1 /tmp/debug_stats.tsv | tr '\t' '\n' | head -20
echo "--- rows (excl header) ---"; tail -n +2 /tmp/debug_stats.tsv | wc -l
```
Expected: `exit=0`; the header's first 11 fields are the base columns, followed
by `Nested MF Extensions`, ..., `Fixable (Non-Standard)`, then `fail:` columns;
one data row per non-delete model in `resources/test/`.

- [ ] **Step 5: Confirm the column count matches the header programmatically**

Run:
```bash
source env/bin/activate && python3 -c "
from gocam_unwinder.gocam_ttl import ModelStats
n = len(ModelStats.extended_header())
import csv
with open('/tmp/debug_stats.tsv') as f:
    rows = list(csv.reader(f, delimiter='\t'))
assert all(len(r) == n for r in rows), 'ragged rows'
print('all', len(rows), 'rows have', n, 'columns')
"
```
Expected: prints `all <N> rows have <M> columns` (no assertion error).

- [ ] **Step 6: Run the full suite**

Run: `source env/bin/activate && pytest -q`
Expected: all tests PASS.

- [ ] **Step 7: Checkpoint** — stop for review/commit.

---

## Verification

After all tasks:

```bash
# Full suite
source env/bin/activate && pytest -q

# --report-file output unchanged vs a fresh baseline from git HEAD's behavior
# (Task 6 already diffed; re-run if in doubt)
source env/bin/activate && python src/gocam_unwinder/gocam_ttl.py \
  -d resources/test/ -o target/go_20250601.json -r resources/test/ro_20250723.owl \
  --no-label-api --report-file /tmp/stats_final.tsv && diff /tmp/stats_baseline.tsv /tmp/stats_final.tsv && echo IDENTICAL

# Debug extended stats report
source env/bin/activate && python3 debug_non_standard.py resources/test/ \
  -o target/go_20250601.json -r resources/test/ro_20250723.owl --no-label-api \
  --stats-output /tmp/debug_stats.tsv
```

Expected final state:
- [ ] All tests pass (existing + the new `model_stats` / `compute_model_stats` / `*fixable*` / `plan_nested_anatomy_fixes_warn_param` tests).
- [ ] `--report-file` output is byte-identical to the pre-refactor baseline.
- [ ] `ModelStats` + `compute_model_stats(gocam, model_id, extended=…)` exist; `main()` and `debug_non_standard.py` both use them.
- [ ] `debug_non_standard.py --stats-output` writes the extended TSV (11 base + 5 triage + 12 `fail:` columns); its `--tsv-output` bucket report is unchanged.
- [ ] `plan_nested_anatomy_fixes` accepts `warn=False`; the fixer path (`--fix-nested-anatomy`) is unchanged.

---

## Notes

- **Why `extended` gates the new block:** the `--report-file` path (Task 6) calls `compute_model_stats(gocam, model_id)` with the default `extended=False`, so it computes and costs exactly what the inline block did — no planner call, no nested-extension scan. Only `debug_non_standard.py` pays for the extended fields.
- **Why reuse the planner for "fixable":** `plan_nested_anatomy_fixes` is the single definition of "what the fixer touches." Detecting fixer targets by bnode-id membership in its output guarantees the stat can't drift from the fixer's actual behavior.
- **Why two fixtures in Task 5:** `mf_nested_anatomy_noev_example` exercises the *fixable* non-standard branch (only `edge_without_evidence`); `mf_nested_anatomy_unfixable_example` is the discriminator proving the `set(failed_checks) <= NESTING_ATTRIBUTABLE_CHECKS` test actually excludes annotations with unrelated failures.
- **Files worth reading before starting:**
  - `docs/plans/2026-06-24-modular-model-stats-design.md` — the design.
  - `src/gocam_unwinder/gocam_ttl.py` — module helpers (134-164), `has_muliple_evidence` (204), `plan_nested_anatomy_fixes` (1166-1222), `get_primary_go_terms`/`get_primary_individuals` (1107-1164), `term_label` (1439), `main()` header/stats block/split decision (1547-1634).
  - `resources/test/mf_nested_anatomy_example.ttl`, `resources/test/cc_nested_anatomy_example.ttl` — fixture templates.
```
