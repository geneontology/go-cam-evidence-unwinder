# Nested-Anatomy "Single Clean Attach Point" Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `--fix-nested-anatomy` (and, derived from it, the remainders report's `Fixable` column) from de-nesting anatomy edges in complex, richly-connected subgraphs — those whose anatomy region attaches to the rest of the model through more than one boundary edge (e.g. developmental `results_in_*` links), like `5745387b00001376` "mouse-Fat1-eye development".

**Architecture:** Add one private helper `GoCamGraphBuilder._anatomy_attachment_is_simple(annot)` that returns `True` only when every connected anatomy region in the annotation has at most one *boundary edge* (an edge with exactly one anatomical endpoint). Add a single gate in `plan_nested_anatomy_fixes()` that skips the whole annotation when the helper returns `False`. Because `plan_nested_anatomy_fixes()` is the single source of truth for the fixer and the report column, this one change propagates everywhere — no change to `debug_non_standard.py` or the fixer CLI.

**Tech Stack:** Python 3, `rdflib`, `ontobio`. Tests use `pytest` with the session-scoped `builder` fixture (`tests/conftest.py`).

**Design doc:** `docs/plans/2026-06-26-nested-anatomy-attachment-gate-design.md`

> **Commits:** This repo's owner stages and commits manually. Do **not** run `git add` or `git commit`. Where this plan says "Checkpoint," run the listed verification and stop for the owner to review/commit.

---

## File Structure

- **Create** `resources/test/nested_anatomy_multi_boundary_example.ttl` — synthetic BP-led fixture: one anatomy region with two boundary edges (a direct `occurs_in` placement plus a `results_in_development_of` from a second BP). The not-fixable unit-test anchor.
- **Add** `resources/test/5745387b00001376.ttl` — copied verbatim from `/Users/ebertdu/go/noctua-models/models/5745387b00001376.ttl`. The real-world regression model.
- **Modify** `src/gocam_unwinder/gocam_ttl.py`:
  - Add `_anatomy_attachment_is_simple(self, annot)` immediately before `plan_nested_anatomy_fixes` (currently at line 1320).
  - Add the gate inside `plan_nested_anatomy_fixes`'s annotation loop (between the `if not nested: continue` and the `primary_individuals = ...` lines, currently 1343–1344).
- **Modify** `tests/test_gocam_ttl.py`:
  - Add `test_anatomy_attachment_is_simple`, `test_plan_nested_anatomy_fixes_skips_multi_boundary`.
  - Extend `test_remainders_report_fixable_column` with one `Fixable=No` assertion on a `5745387b00001376` nested row.
- **Modify** `CLAUDE.md` — document the gate, the new fixtures, and the new tests.

No change to `debug_non_standard.py` or the `--fix-nested-anatomy` CLI flow.

---

## Task 1: Create the synthetic not-fixable fixture

Test data for Tasks 2 and 3. A minimal BP-led annotation whose single anatomy region `{CL:0000202, UBERON:0000019}` has **two** boundary edges: `BP1 ─occurs_in→ CL` (the direct placement) and `BP2 ─results_in_development_of→ UBERON` (the disqualifier). `BP2` is reached only via `results_in_development_of`, so it is not a second BP backbone — the lead aspect stays BP with one primary individual, and the new attachment gate (not the primary-count check) is what will skip it.

**Files:**
- Create: `resources/test/nested_anatomy_multi_boundary_example.ttl`

- [ ] **Step 1: Write the fixture file**

Create `resources/test/nested_anatomy_multi_boundary_example.ttl` with exactly this content:

```turtle
<http://model.geneontology.org/nested_anatomy_multi_boundary_example> a <http://www.w3.org/2002/07/owl#Ontology> ;
	<http://geneontology.org/lego/modelstate> "production" ;
	<http://purl.org/dc/elements/1.1/title> "BP-led nested anatomy with multiple attachment points (not fixable)" ;
	<http://purl.org/pav/providedBy> "http://informatics.jax.org" .

<http://geneontology.org/lego/evidence> a <http://www.w3.org/2002/07/owl#AnnotationProperty> .
<http://purl.org/dc/elements/1.1/date> a <http://www.w3.org/2002/07/owl#AnnotationProperty> .

# Backbone: root-MF -enabled_by-> GP ; root-MF -part_of-> BP1 (primary BP)
<http://model.geneontology.org/nested_anatomy_multi_boundary_example/mf1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/GO_0003674> ;
	<http://purl.obolibrary.org/obo/RO_0002333> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/gp1> ;
	<http://purl.obolibrary.org/obo/BFO_0000050> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/bp1> .
<http://model.geneontology.org/nested_anatomy_multi_boundary_example/gp1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://identifiers.org/mgi/MGI:109168> .
# BP1 (eye development) -occurs_in-> CL  (direct placement; boundary #1)
<http://model.geneontology.org/nested_anatomy_multi_boundary_example/bp1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/GO_0001654> ;
	<http://purl.obolibrary.org/obo/BFO_0000066> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/anat1> .
# CL -part_of-> UBERON  (NESTED: both anatomy, source != primary BP)
<http://model.geneontology.org/nested_anatomy_multi_boundary_example/anat1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/CL_0000202> ;
	<http://purl.obolibrary.org/obo/BFO_0000050> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/anat2> .
<http://model.geneontology.org/nested_anatomy_multi_boundary_example/anat2>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/UBERON_0000019> .
# BP2 (camera-type eye development) -results_in_development_of-> UBERON  (boundary #2; the disqualifier)
<http://model.geneontology.org/nested_anatomy_multi_boundary_example/bp2>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/GO_0043010> ;
	<http://purl.obolibrary.org/obo/RO_0002296> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/anat2> .

<http://model.geneontology.org/nested_anatomy_multi_boundary_example/ev1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
<http://model.geneontology.org/nested_anatomy_multi_boundary_example/ev2>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
<http://model.geneontology.org/nested_anatomy_multi_boundary_example/ev3>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
<http://model.geneontology.org/nested_anatomy_multi_boundary_example/ev4>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
<http://model.geneontology.org/nested_anatomy_multi_boundary_example/ev5>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .

# root-MF -enabled_by-> GP
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/mf1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/RO_0002333> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/gp1> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/ev1> .

# root-MF -part_of-> BP1  (BP backbone)
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/mf1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/BFO_0000050> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/bp1> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/ev2> .

# BP1 -occurs_in-> CL  (direct placement; boundary #1)
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/bp1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/BFO_0000066> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/anat1> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/ev3> .

# CL -part_of-> UBERON  (NESTED both-anatomical candidate)
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/anat1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/BFO_0000050> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/anat2> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/ev4> .

# BP2 -results_in_development_of-> UBERON  (boundary #2; the disqualifier)
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/bp2> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/RO_0002296> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/anat2> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/nested_anatomy_multi_boundary_example/ev5> .
```

- [ ] **Step 2: Sanity-check the fixture parses and is a single connected annotation**

Run:
```bash
source env/bin/activate && python3 -c "
from gocam_unwinder.gocam_ttl import GoCamGraphBuilder
b = GoCamGraphBuilder('target/go_20250601.json', 'resources/test/ro_20250723.owl')
g = b.parse_ttl('resources/test/nested_anatomy_multi_boundary_example.ttl')
annots = g.standard_annotations + g.non_standard_annotations
print('annotations:', len(annots))
print('total edges:', sum(len(a.edges) for a in annots))
print('primary terms:', {a_i: [str(t).split('/')[-1] for t in v] for a in annots for a_i,v in b.get_primary_go_terms(a).items()})
"
```
Expected: `annotations: 1`, `total edges: 5`, and `primary terms` showing `MF` (GO_0003674) and `BP` (GO_0001654). If `annotations` is not 1, the bnode axioms are malformed — recheck the file.

- [ ] **Step 3: Checkpoint** — stop for review/commit.

---

## Task 2: Add `_anatomy_attachment_is_simple` helper (TDD)

The helper computes, per connected anatomy region, the number of distinct boundary edges and returns `True` iff every region has ≤ 1. `5966411600000001` (single boundary) → `True`; the Task 1 fixture (two boundaries in one region) → `False`.

**Files:**
- Test: `tests/test_gocam_ttl.py` (append a new test at end of file)
- Modify: `src/gocam_unwinder/gocam_ttl.py` (insert the method before `plan_nested_anatomy_fixes`, line 1320)

- [ ] **Step 1: Write the failing test**

Append to the end of `tests/test_gocam_ttl.py`:

```python
def _annotation_with_individual(gocam_graph, individual_uri):
    """Return the (standard or non-standard) annotation containing `individual_uri`."""
    target = rdflib.term.URIRef(individual_uri)
    for annot in gocam_graph.standard_annotations + gocam_graph.non_standard_annotations:
        if target in annot.individuals:
            return annot
    return None


def test_anatomy_attachment_is_simple(builder):
    """_anatomy_attachment_is_simple is True for a single-boundary anatomy region
    (5966411600000001: BP -occurs_in-> CL -part_of-> EMAPA) and False when the
    region has >1 boundary edge (the multi-boundary synthetic fixture)."""
    simple = builder.parse_ttl("resources/test/5966411600000001.ttl")
    simple_annot = _annotation_with_individual(
        simple, "http://model.geneontology.org/5966411600000001/5966411600000004")
    assert simple_annot is not None
    assert builder._anatomy_attachment_is_simple(simple_annot) is True

    complex_g = builder.parse_ttl(
        "resources/test/nested_anatomy_multi_boundary_example.ttl")
    complex_annot = _annotation_with_individual(
        complex_g,
        "http://model.geneontology.org/nested_anatomy_multi_boundary_example/anat2")
    assert complex_annot is not None
    assert builder._anatomy_attachment_is_simple(complex_annot) is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
source env/bin/activate && pytest tests/test_gocam_ttl.py::test_anatomy_attachment_is_simple -v
```
Expected: FAIL with `AttributeError: 'GoCamGraphBuilder' object has no attribute '_anatomy_attachment_is_simple'`.

- [ ] **Step 3: Implement the helper**

In `src/gocam_unwinder/gocam_ttl.py`, find the line:

```python
    def plan_nested_anatomy_fixes(self, gocam: GoCamGraph, warn: bool = True) -> list:
```

Insert this method **immediately before** it (same indentation, one blank line between):

```python
    def _anatomy_attachment_is_simple(self, annot: StandardAnnotation) -> bool:
        """
        Return True if every connected anatomy region in `annot` attaches to the
        rest of the model through at most one boundary edge.

        An anatomy region is a connected component of anatomical individuals
        (_is_anatomical_structure: GO cellular components + anatomy-ontology terms
        such as CL/UBERON/EMAPA), joined by *internal* edges (both endpoints
        anatomical -- the part_of chains). A *boundary* edge has exactly one
        anatomical endpoint (it links the region to a non-anatomy node, e.g. a
        primary BP -occurs_in-> CL placement, or a stray
        BP -results_in_development_of-> anatomy edge). Boundary edges are counted
        as distinct (source_uri, property_uri, target_uri) triples, so a
        multi-evidence placement (two axiom bnodes, same triple) counts once.

        A region with more than one boundary edge signals a complex (e.g.
        developmental) subgraph that should not be auto-de-nested; this returns
        False for the whole annotation in that case.
        """
        # 1. Anatomical flag per individual, from edge endpoint types.
        is_anat = {}
        for e in annot.edges.values():
            if e.source_uri is not None:
                is_anat[e.source_uri] = self._is_anatomical_structure(e.source_type)
            if e.target_uri is not None:
                is_anat[e.target_uri] = self._is_anatomical_structure(e.target_type)

        # 2. Union-find over anatomical individuals joined by internal edges.
        parent = {u: u for u, a in is_anat.items() if a}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            parent[find(a)] = find(b)

        for e in annot.edges.values():
            if is_anat.get(e.source_uri) and is_anat.get(e.target_uri):
                union(e.source_uri, e.target_uri)

        # 3. Distinct boundary edges per region (exactly one anatomical endpoint).
        boundary = {}
        for e in annot.edges.values():
            s_anat = is_anat.get(e.source_uri, False)
            t_anat = is_anat.get(e.target_uri, False)
            if s_anat != t_anat:
                anat_node = e.source_uri if s_anat else e.target_uri
                root = find(anat_node)
                boundary.setdefault(root, set()).add(
                    (e.source_uri, e.property_uri, e.target_uri))

        # 4. Simple iff every region has at most one boundary edge.
        return all(len(edges) <= 1 for edges in boundary.values())
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
source env/bin/activate && pytest tests/test_gocam_ttl.py::test_anatomy_attachment_is_simple -v
```
Expected: PASS.

- [ ] **Step 5: Checkpoint** — stop for review/commit.

---

## Task 3: Add the gate to `plan_nested_anatomy_fixes` (TDD)

With the helper in place, gate the planner: skip any annotation that is not simply attached. The Task 1 fixture currently yields one rewrite (the `CL -part_of-> UBERON` edge); after the gate it yields zero. The simple fixture `5966411600000001` keeps its single rewrite.

**Files:**
- Test: `tests/test_gocam_ttl.py` (append a new test at end of file)
- Modify: `src/gocam_unwinder/gocam_ttl.py` (the loop in `plan_nested_anatomy_fixes`, currently lines 1340–1344)

- [ ] **Step 1: Write the failing test**

Append to the end of `tests/test_gocam_ttl.py`:

```python
def test_plan_nested_anatomy_fixes_skips_multi_boundary(builder):
    """plan_nested_anatomy_fixes plans NO rewrite for an annotation whose anatomy
    region has more than one boundary edge (the multi-boundary fixture), even
    though it contains a both-anatomical nested edge. The simple fixture is
    unaffected (still one rewrite)."""
    complex_g = builder.parse_ttl(
        "resources/test/nested_anatomy_multi_boundary_example.ttl")
    assert builder.plan_nested_anatomy_fixes(complex_g, warn=False) == []

    simple = builder.parse_ttl("resources/test/5966411600000001.ttl")
    assert len(builder.plan_nested_anatomy_fixes(simple, warn=False)) == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
source env/bin/activate && pytest tests/test_gocam_ttl.py::test_plan_nested_anatomy_fixes_skips_multi_boundary -v
```
Expected: FAIL on the first assertion — the pre-gate planner returns one instruction (the `CL -part_of-> UBERON` edge), so `plan_nested_anatomy_fixes(...) == []` is `False`.

- [ ] **Step 3: Add the gate**

In `src/gocam_unwinder/gocam_ttl.py`, inside `plan_nested_anatomy_fixes`, the loop currently reads:

```python
        for annot in all_annots:
            lead, nested = find_nested_extensions(annot, self)
            if not nested:
                continue
            primary_individuals = self.get_primary_individuals(annot).get(lead, [])
```

Change it to (insert the gate between the `continue` and `primary_individuals` lines):

```python
        for annot in all_annots:
            lead, nested = find_nested_extensions(annot, self)
            if not nested:
                continue
            if not self._anatomy_attachment_is_simple(annot):
                if warn:
                    print(f"WARNING: skipping annotation in {gocam.model_id} "
                          f"({gocam.title}) — anatomy has multiple attachment "
                          f"points (complex subgraph)")
                continue
            primary_individuals = self.get_primary_individuals(annot).get(lead, [])
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
source env/bin/activate && pytest tests/test_gocam_ttl.py::test_plan_nested_anatomy_fixes_skips_multi_boundary -v
```
Expected: PASS.

- [ ] **Step 5: Confirm existing planner tests still pass**

Run:
```bash
source env/bin/activate && pytest tests/test_gocam_ttl.py -k "plan_nested_anatomy_fixes or compute_model_stats_fixable or compute_model_stats_unfixable" -v
```
Expected: all PASS (the BP/MF/CC single-attach fixtures and the fixable/unfixable stats tests are unaffected — every one of those fixtures has single-boundary anatomy regions).

- [ ] **Step 6: Checkpoint** — stop for review/commit.

---

## Task 4: Real-model regression (`5745387b00001376`)

Add the real motivating model and assert the report marks its previously-rewritten nested edges `Fixable=No`.

**Files:**
- Add: `resources/test/5745387b00001376.ttl`
- Test: `tests/test_gocam_ttl.py` (extend `test_remainders_report_fixable_column`)

- [ ] **Step 1: Copy the real model into the test resources**

Run (idempotent):
```bash
cp /Users/ebertdu/go/noctua-models/models/5745387b00001376.ttl \
   /Users/ebertdu/go/go-cam-evidence-unwinder/resources/test/5745387b00001376.ttl
```
Expected: the file exists at `resources/test/5745387b00001376.ttl` (~14 owl:Axiom edges, lead aspect BP, primary BP `GO:0003412`).

- [ ] **Step 2: Add the failing assertion to the report test**

In `tests/test_gocam_ttl.py`, find the end of `test_remainders_report_fixable_column` — the existing final block reads:

```python
    # Design-intent guard: BOTH endpoints anatomical, but the annotation has an
    # ambiguous (!=1) primary individual, so the planner skips it -> No.
    skip_row = _find_remainders_row(
        rows, "57c82fad00000252", "nucleus", "part of", "WBbt:0005396")
    assert skip_row["Fixable"] == "No"
```

Add, immediately after that block (still inside the function):

```python
    # Real-world regression: a both-anatomical nested edge in a complex
    # developmental subgraph (5745387b00001376) is reported but NOT fixable,
    # because its anatomy region has multiple attachment points.
    complex_row = _find_remainders_row(
        rows, "5745387b00001376", "UBERON:0000965", "part of", "UBERON:0000019")
    assert complex_row["Fixable"] == "No"
```

- [ ] **Step 3: Run the report test to verify it passes**

Run:
```bash
source env/bin/activate && pytest tests/test_gocam_ttl.py::test_remainders_report_fixable_column -v
```
Expected: PASS. The `5745387b00001376` `UBERON:0000965 -part of-> UBERON:0000019` row exists (it is a `nested_bp_extensions` row — RO is loaded so the BP backbone is detected) and reads `Fixable=No`. If `_find_remainders_row` raises, confirm Step 1 copied the file and that the RO ontology resolved (`-r resources/test/ro_20250723.owl` is already in the test's `sys.argv`). The CL/UBERON CURIEs stay un-labelled under `--no-label-api`, so match on CURIEs (as the existing assertions do); the predicate `BFO:0000050` resolves to `part of` from the loaded RO ontology.

- [ ] **Step 4: Checkpoint** — stop for review/commit.

---

## Task 5: Documentation

Update `CLAUDE.md` so the architecture notes, fixture list, and test list match the new behavior.

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the `plan_nested_anatomy_fixes` description**

In `CLAUDE.md`, find the `plan_nested_anatomy_fixes(gocam)` bullet (under **GoCamGraphBuilder** → Key methods). After the sentence describing the both-endpoints-anatomical qualification, add a sentence:

```
An additional **attachment gate** (`_anatomy_attachment_is_simple`) skips the *entire* annotation when any connected anatomy region attaches to the rest of the model through more than one *boundary edge* (an edge with exactly one anatomical endpoint — e.g. a primary `occurs_in` placement plus a stray `results_in_development_of` from another BP). This leaves complex, richly-connected developmental subgraphs (e.g. `5745387b00001376`) unfixed while the simple single-attach chains stay fixable.
```

- [ ] **Step 2: Add a `_anatomy_attachment_is_simple` entry to the Key methods list**

In `CLAUDE.md`, in the **GoCamGraphBuilder** Key methods list, add a bullet near `_is_anatomical_structure`:

```
- `_anatomy_attachment_is_simple()`: Returns `True` iff every connected anatomy region in an annotation (anatomical individuals joined by anatomy↔anatomy edges) has at most one *boundary edge* (an edge with exactly one anatomical endpoint, counted as distinct `(source, relation, target)` triples). Used by `plan_nested_anatomy_fixes()` to skip complex multi-attachment-point subgraphs.
```

- [ ] **Step 3: Update the `--fix-nested-anatomy` mode paragraph**

In `CLAUDE.md`, in the `**`--fix-nested-anatomy`` mode:**` paragraph, add a sentence noting the gate:

```
Annotations whose anatomy region has more than one attachment point (boundary edge) to the rest of the model — the hallmark of a complex developmental subgraph — are left entirely unchanged (and still appear in the remainders report as `Fixable=No`).
```

- [ ] **Step 4: Update the remainders-report `Fixable` paragraph**

In `CLAUDE.md`, in the **Remainders report** paragraph describing the `Fixable` column, extend the note about why a both-anatomical nested edge can read `No`:

```
A both-anatomical nested edge can also read `No` when its annotation's anatomy region has more than one attachment point (e.g. `5745387b00001376`, where the camera-type-eye / lens / epithelial-cell anatomy nodes each carry a `results_in_development_of`/`results_in_morphogenesis_of` edge from a developmental BP) — `plan_nested_anatomy_fixes` skips such complex subgraphs via `_anatomy_attachment_is_simple`.
```

- [ ] **Step 5: Add the new fixtures and tests to the lists**

In `CLAUDE.md`, in the **Testing** fixtures list, add:

```
- **nested_anatomy_multi_boundary_example.ttl**: Synthetic BP-led model — `root-MF ─enabled_by→ GP`, `root-MF ─part_of→ BP1` (GO:0001654, primary), `BP1 ─occurs_in→ CL:0000202` (boundary #1), `CL:0000202 ─part_of→ UBERON:0000019` (nested both-anatomical candidate), and `BP2 (GO:0043010) ─results_in_development_of→ UBERON:0000019` (boundary #2). Its single anatomy region has 2 boundary edges, so `_anatomy_attachment_is_simple` returns `False` and `plan_nested_anatomy_fixes` plans no rewrite. Used by `test_anatomy_attachment_is_simple` and `test_plan_nested_anatomy_fixes_skips_multi_boundary`
- **5745387b00001376.ttl**: Real mouse-Fat1-eye-development model (MGI). One BP-led subgraph (primary BP `GO:0003412`) whose anatomy `part_of` chain (plasma membrane → CL → UBERON → lens → eye) is woven into a multi-BP developmental web — each terminal anatomy node also receives a `results_in_development_of`/`results_in_morphogenesis_of` edge from its own BP. The anatomy region has multiple boundary edges, so the attachment gate marks all its nested edges `Fixable=No`. Regression for `test_anatomy_attachment_is_simple`, `test_plan_nested_anatomy_fixes_skips_multi_boundary`, and `test_remainders_report_fixable_column`
```

And in the **Test Functions** list, add:

```
- `test_anatomy_attachment_is_simple()`: Tests `_anatomy_attachment_is_simple` — `True` for `5966411600000001`'s single-boundary anatomy region, `False` for `nested_anatomy_multi_boundary_example` and `5745387b00001376` (regions with >1 boundary edge)
- `test_plan_nested_anatomy_fixes_skips_multi_boundary()`: Tests that `plan_nested_anatomy_fixes` plans no rewrite for the multi-boundary fixture (and for `5745387b00001376`) while `5966411600000001` still yields its one rewrite
```

Also extend the existing `test_remainders_report_fixable_column()` entry to mention the new `5745387b00001376` `UBERON:0000965 ─part_of→ UBERON:0000019` row asserted `Fixable=No`.

- [ ] **Step 6: Checkpoint** — stop for review/commit.

---

## Task 6: Full regression + end-to-end smoke

**Files:** none (verification only).

- [ ] **Step 1: Run the full suite**

Run:
```bash
source env/bin/activate && pytest -q
```
Expected: all tests PASS, including the two new tests and the extended report test. No previously-passing test changes behavior (every existing fixer-target fixture is single-boundary).

- [ ] **Step 2: End-to-end smoke of the report**

Run:
```bash
source env/bin/activate && python3 debug_non_standard.py resources/test/ \
  -o target/go_20250601.json -r resources/test/ro_20250723.owl \
  --no-label-api --tsv-output /tmp/remainders_gate.tsv >/dev/null 2>&1; echo "exit=$?"
echo "=== 5745387b00001376 nested rows (all should read No) ==="
awk -F'\t' 'NR>1 && $1 ~ /5745387b00001376/ && $3 ~ /^nested_/ {print $7"\t"$4" -["$5"]-> "$6}' /tmp/remainders_gate.tsv
echo "=== 5966411600000001 CL->EMAPA (should read Yes) ==="
awk -F'\t' 'NR>1 && $1 ~ /5966411600000001/ && $4=="CL:0000202" {print $7"\t"$4" -["$5"]-> "$6}' /tmp/remainders_gate.tsv
```
Expected:
- `exit=0`.
- Every `5745387b00001376` nested row reads `No` in the first column.
- The `5966411600000001` `CL:0000202 -[part of]-> EMAPA:17597` row reads `Yes` (unchanged).

- [ ] **Step 3: Checkpoint** — stop for review/commit.

---

## Verification

After all tasks:

```bash
# Full suite
source env/bin/activate && pytest -q

# Report smoke (gate applied)
source env/bin/activate && python3 debug_non_standard.py resources/test/ \
  -o target/go_20250601.json -r resources/test/ro_20250723.owl \
  --no-label-api --tsv-output /tmp/remainders_gate.tsv
awk -F'\t' 'NR>1 && $1 ~ /5745387b00001376/ {print $7}' /tmp/remainders_gate.tsv | sort | uniq -c
```

Expected final state:
- [ ] All tests pass, including `test_anatomy_attachment_is_simple` and `test_plan_nested_anatomy_fixes_skips_multi_boundary`.
- [ ] `5745387b00001376`'s nested rows all read `Fixable=No`; `5966411600000001`'s `CL→EMAPA` row reads `Fixable=Yes`.
- [ ] `--fix-nested-anatomy` no longer rewrites `5745387b00001376` (it produces no plan for that model); `src/gocam_unwinder/gocam_ttl.py` is the only source file changed, plus two fixtures and `CLAUDE.md`.
- [ ] `debug_non_standard.py` and the `--fix-nested-anatomy` CLI flow are unchanged.

---

## Notes

- **Why the gate lives in `plan_nested_anatomy_fixes`:** it is the single source of truth for both the fixer and the report's `Fixable` column (the column is `{rec["bnode_id"] for rec in builder.plan_nested_anatomy_fixes(gocam, warn=False)}`). One gate there can never diverge between "what the fixer rewrites" and "what the report calls fixable."
- **Why `warn` guards the print:** report generation and `compute_model_stats` already call the planner with `warn=False`; the new warning follows the existing `!=1 primary individual` warning's convention so report runs stay quiet.
- **Why distinct triples for boundary counting:** `annot.edges` is keyed by axiom bnode, so a multi-evidence placement appears as two entries for the same `(source, relation, target)`; deduping prevents a duplicated single placement from falsely tripping the >1 rule.
- **Files worth reading before starting:**
  - `docs/plans/2026-06-26-nested-anatomy-attachment-gate-design.md` — the design.
  - `src/gocam_unwinder/gocam_ttl.py:1041` (`_is_anatomical_structure`), `:1320` (`plan_nested_anatomy_fixes`), `:146` (`find_nested_extensions`).
  - `resources/test/mf_nested_anatomy_example.ttl` — the fixture format to mirror.
  - `tests/conftest.py` — the session-scoped `builder` fixture and ontology paths.
```
