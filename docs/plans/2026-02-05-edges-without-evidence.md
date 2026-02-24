# Handle Edges Without Evidence — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Include edges without evidence in annotation subgraph assembly so that connected GO-CAM components are not incorrectly split, and report the count of evidence-less edges per model.

**Architecture:** Currently, `extract_edges()` only discovers edges that have `lego:evidence` triples. `find_related_edges()` can discover no-evidence edges, but only by following the *target* of known edges — it misses no-evidence edges originating from individuals that are only sources. The fix adds a new method `extract_all_axiom_edges()` that discovers ALL OWL axiom edges (with or without evidence) in a single pass, then feeds them all into the union-find annotation assembly. A new column in the report tracks how many edges lack evidence.

**Tech Stack:** Python, rdflib, pytest

**Affected Areas:** Edge extraction (`extract_edges`), annotation assembly (`extract_standard_annotations`), report output (TSV headers + row), CLI main block

---

## Context

GitHub Issue #14: Some GO-CAM edges lack evidence. The unwinder starts by finding edges with evidence, so evidence-less edges are invisible to the initial `extract_edges()` pass. While `find_related_edges()` can discover some no-evidence edges by traversing from known edge targets, it misses cases where the no-evidence edge is the *connecting bridge* between two subgraphs (e.g., model `66c7d41500000016` where an "indirectly positively regulates" causal edge between two MF nodes has no evidence). This causes a single annotation subgraph to be incorrectly parsed as two separate subgraphs.

Two deliverables:
1. Include no-evidence edges in subgraph assembly so annotation subgraphs are correctly connected.
2. Add a report column counting edges without evidence per model.

## Constraints

- Must not break existing tests or pipeline outputs
- Edges without evidence must still be included in `StandardAnnotation.edges` so filtering checks (mf_causal_mf, etc.) can inspect them
- Evidence-less edges must NOT be considered for evidence splitting (they have no evidence to split)
- The report column should count edges without evidence across the entire model (not per-annotation)

## Out of Scope

- Adding evidence to edges that lack it (that's a Noctua curation task)
- Changing the evidence splitting algorithm itself
- Any new filtering checks based on no-evidence edges

---

## Tasks

### Task 1: Add test model and write failing test for no-evidence edge connectivity

**Files:**
- Create: `resources/test/66c7d41500000016.ttl` (copy from noctua-models)
- Modify: `tests/test_gocam_ttl.py`

**Step 1: Copy the test model**

Download the model file:
```bash
curl -L -o resources/test/66c7d41500000016.ttl \
  "https://raw.githubusercontent.com/geneontology/noctua-models/master/models/66c7d41500000016.ttl"
```

**Step 2: Write the failing test**

Add to `tests/test_gocam_ttl.py`:

```python
def test_edges_without_evidence():
    """
    Test that edges without evidence are included in annotation subgraph assembly.

    Issue #14: Model 66c7d41500000016 has a causal edge (RO:0002407, "indirectly
    positively regulates") between two MF nodes that has no evidence. Without
    handling this, the model is incorrectly parsed as two separate annotation
    subgraphs instead of one.

    The model has 12 OWL axiom edges total, 11 with evidence and 1 without.
    The no-evidence edge connects individual ...17 (GO:0030545, receptor ligand
    activity) to ...25 (GO:0004971, AMPA glutamate receptor activity).
    """
    ro_ontology_file = "resources/test/ro_20250723.owl"
    builder = GoCamGraphBuilder(ontology_file, ro_ontology_file)
    gocam_graph = builder.parse_ttl("resources/test/66c7d41500000016.ttl")

    # The two MF individuals that are bridged by the no-evidence causal edge
    mf_source = rdflib.term.URIRef(
        'http://model.geneontology.org/66c7d41500000016/66c7d41500000017')
    mf_target = rdflib.term.URIRef(
        'http://model.geneontology.org/66c7d41500000016/66c7d41500000025')

    # Both individuals should be in the SAME annotation subgraph
    # (not split into two separate subgraphs)
    all_annotations = gocam_graph.standard_annotations + gocam_graph.non_standard_annotations
    source_annot = None
    target_annot = None
    for annot in all_annotations:
        if mf_source in annot.individuals:
            source_annot = annot
        if mf_target in annot.individuals:
            target_annot = annot

    assert source_annot is not None, "MF source individual should be in an annotation"
    assert target_annot is not None, "MF target individual should be in an annotation"
    assert source_annot is target_annot, \
        "Both MF individuals should be in the SAME annotation (connected via no-evidence edge)"

    # The combined annotation should be non-standard (due to mf_causal_mf or inconsistent_evidence)
    assert source_annot in gocam_graph.non_standard_annotations, \
        "The combined annotation should be non-standard"

    # Verify the no-evidence edge is present in the annotation's edges
    no_evidence_edge_found = False
    for edge in source_annot.edges.values():
        if (edge.source_uri == mf_source and edge.target_uri == mf_target and
                str(edge.property_uri) == "http://purl.obolibrary.org/obo/RO_0002407"):
            no_evidence_edge_found = True
            assert len(edge.evidence_uris) == 0, "The bridging edge should have no evidence"
            break
    assert no_evidence_edge_found, "The no-evidence causal edge should be in the annotation"
```

**Step 3: Run test to verify it fails**

Run: `pytest tests/test_gocam_ttl.py::test_edges_without_evidence -v`
Expected: FAIL — `source_annot is target_annot` assertion fails because the two individuals end up in different annotation subgraphs.

**Step 4: Commit**

```bash
git add resources/test/66c7d41500000016.ttl tests/test_gocam_ttl.py
git commit -m "test: add failing test for edges without evidence (issue #14)"
```

---

### Task 2: Extract all axiom edges including those without evidence

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py:334-338` (`evidence_triples`), `src/gocam_unwinder/gocam_ttl.py:408-423` (`extract_edges`)

The key insight: `extract_edges()` currently only finds edges via `evidence_triples()`. We need to also discover axiom blank nodes that have `owl:annotatedSource`, `owl:annotatedProperty`, and `owl:annotatedTarget` but NO `lego:evidence`. These should be added to `self.edges` with empty `evidence_uris`.

**Step 1: Modify `extract_edges()` to also extract no-evidence axiom edges**

After the existing evidence-triple loop in `extract_edges()` (line 408-423), add a second pass that finds all OWL axiom blank nodes and creates edges for any that weren't already discovered:

```python
def extract_edges(self):
    ets = list(self.evidence_triples())
    for triple in ets:
        bnode = triple[0]
        bnode_id = str(bnode)

        edge = self.get_edge_by_bnode_id(bnode_id)
        if edge is None:
            source_id, target_id, relation, contributors, date, provided_by, created, date_accepted = self.find_axiom_bits(bnode)
            edge = StandardAnnotationEdge(bnode, source_id, target_id, relation)
            self.edges.append(edge)
        evidence_id = triple[2]
        edge.evidence_uris.append(evidence_id)

    # Second pass: find axiom blank nodes without evidence
    # These are owl:Axiom nodes with annotatedSource/Property/Target but no lego:evidence
    evidence_pred = rdflib.URIRef("http://geneontology.org/lego/evidence")
    for bnode in self.g.subjects(rdflib.RDF.type, rdflib.namespace.OWL.Axiom):
        if not isinstance(bnode, rdflib.term.BNode):
            continue
        bnode_id = str(bnode)
        # Skip if already extracted (has evidence)
        if self.get_edge_by_bnode_id(bnode_id) is not None:
            continue
        # Check this axiom has the required OWL annotation bits
        sources = list(self.g.objects(bnode, rdflib.namespace.OWL.annotatedSource))
        targets = list(self.g.objects(bnode, rdflib.namespace.OWL.annotatedTarget))
        properties = list(self.g.objects(bnode, rdflib.namespace.OWL.annotatedProperty))
        if not sources or not targets or not properties:
            continue
        # Only include edges with GO-CAM relations
        if str(properties[0]) not in GOCAM_RELATIONS:
            continue
        edge = StandardAnnotationEdge(bnode, sources[0], targets[0], properties[0])
        self.edges.append(edge)
        # edge.evidence_uris remains empty []

    return self.edges
```

**Step 2: Run failing test to verify it passes**

Run: `pytest tests/test_gocam_ttl.py::test_edges_without_evidence -v`
Expected: PASS — now the no-evidence edge is discovered in `extract_edges()`, the union-find in `extract_standard_annotations()` merges the two subgraphs.

**Step 3: Run full test suite for regressions**

Run: `pytest -v`
Expected: All tests PASS. No-evidence edges are now included in annotation assembly. Existing tests may see different annotation counts since previously-separate subgraphs may now merge.

**Step 4: Fix any existing test assertion counts**

If existing tests fail due to changed annotation counts (from merging previously-separate subgraphs), update the expected counts. Don't weaken assertions — verify the new counts are correct by inspecting the merged annotations.

**Step 5: Commit**

```bash
git add src/gocam_unwinder/gocam_ttl.py tests/test_gocam_ttl.py
git commit -m "feat: extract edges without evidence for correct subgraph assembly (#14)"
```

---

### Task 3: Clean up `find_related_edges` no-evidence edge creation

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py:486-516` (`find_related_edges`)

Now that `extract_edges()` discovers all axiom edges (with or without evidence), the fallback in `find_related_edges()` at lines 507-512 that creates new `StandardAnnotationEdge` objects for missing edges should no longer be needed for axiom edges. However, `find_related_edges` also handles edges that are expressed as direct RDF triples (not OWL axioms) via `predicate_objects`. These direct triples are a different case and may still need the fallback.

**Step 1: Verify behavior**

Review whether `find_related_edges` still needs the `if next_edge is None` branch. It traverses `self.g.predicate_objects(edge.target_uri)` looking for GOCAM_RELATIONS triples, then calls `find_axiom_bnode_by_triple` to find the axiom bnode. If the edge was already extracted (now including no-evidence edges), `get_edge_by_bnode_id` should find it.

Keep the fallback for safety — it handles any edge case where an axiom bnode wasn't discovered in `extract_edges()` (e.g., axioms without the required OWL annotation bits). No code change needed here, just verify tests pass.

**Step 2: Run full test suite**

Run: `pytest -v`
Expected: All tests PASS.

---

### Task 4: Add "Edges w/o Evidence" report column

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py:735` (headers), `src/gocam_unwinder/gocam_ttl.py:804` (row output)
- Modify: `tests/test_gocam_ttl.py`

**Step 1: Write the failing test for the report column**

Add to `tests/test_gocam_ttl.py`:

```python
def test_edges_without_evidence_report_column():
    """
    Test that the report includes a column counting edges without evidence.
    Issue #14: Report out models having edges without evidence.
    """
    ro_ontology_file = "resources/test/ro_20250723.owl"
    builder = GoCamGraphBuilder(ontology_file, ro_ontology_file)
    gocam_graph = builder.parse_ttl("resources/test/66c7d41500000016.ttl")

    # Count edges without evidence across all annotations
    no_evidence_count = 0
    all_annotations = gocam_graph.standard_annotations + gocam_graph.non_standard_annotations
    for annot in all_annotations:
        for edge in annot.edges.values():
            if len(edge.evidence_uris) == 0:
                no_evidence_count += 1

    # Model 66c7d41500000016 has exactly 1 edge without evidence
    assert no_evidence_count == 1, f"Expected 1 edge without evidence, got {no_evidence_count}"

    # Also verify a model with all edges having evidence reports 0
    builder_no_ro = GoCamGraphBuilder(ontology_file)
    gocam_graph_all_ev = builder_no_ro.parse_ttl("resources/test/MGI_MGI_1100089.ttl")

    no_evidence_count_all = 0
    all_annotations_all = gocam_graph_all_ev.standard_annotations + gocam_graph_all_ev.non_standard_annotations
    for annot in all_annotations_all:
        for edge in annot.edges.values():
            if len(edge.evidence_uris) == 0:
                no_evidence_count_all += 1

    assert no_evidence_count_all == 0, f"MGI_MGI_1100089 should have 0 edges without evidence, got {no_evidence_count_all}"
```

**Step 2: Run test to verify it fails (or passes — this test doesn't test CLI output)**

Run: `pytest tests/test_gocam_ttl.py::test_edges_without_evidence_report_column -v`
Expected: PASS if Task 2 is implemented. This validates the counting logic.

**Step 3: Add the report column to CLI output**

In `src/gocam_unwinder/gocam_ttl.py`, modify the headers (line 735) and row output (line 804):

Headers — add `"Edges w/o Evidence"` after `"MF-causal->MF Edges"`:
```python
headers = ["Model ID", "Title", "Standard Annotations", "Non-Standard Annotations",
           "Multi-Evidence Annotations", "Mixed Annotation Type", "MF-causal->MF Edges",
           "Edges w/o Evidence", "Model State", "Groups", "Multi-Evidence GO Terms"]
```

Row output — after the `mf_causal_count` calculation (around line 793), add a count of no-evidence edges:
```python
# Count edges without evidence across all annotations
no_evidence_edge_count = 0
all_annotations = gocam_graph.standard_annotations + gocam_graph.non_standard_annotations
for annot in all_annotations:
    for edge in annot.edges.values():
        if len(edge.evidence_uris) == 0:
            no_evidence_edge_count += 1
```

And insert `str(no_evidence_edge_count)` in the `print("\t".join([...]))` call at the appropriate position (after `str(mf_causal_count)`).

**Step 4: Run full test suite**

Run: `pytest -v`
Expected: All tests PASS.

**Step 5: Commit**

```bash
git add src/gocam_unwinder/gocam_ttl.py tests/test_gocam_ttl.py
git commit -m "feat: add 'Edges w/o Evidence' report column (#14)"
```

---

### Task 5: Update CLAUDE.md documentation

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Update CLAUDE.md**

Add documentation for the new behavior:
- In the `extract_edges()` description, note that it now discovers all OWL axiom edges including those without evidence
- In the report columns section, add the new `"Edges w/o Evidence"` column description
- In the test model descriptions, add `66c7d41500000016.ttl`

Specifically:

1. Under **Statistics report columns** add:
   - `Edges w/o Evidence` — Count of edges (OWL axioms with GO-CAM relations) that have no `lego:evidence` triple

2. Under **Testing** test models, add:
   - `**66c7d41500000016.ttl**: Human NRXN1B-CBLN1-GRID2 trans-synaptic model with 1 evidence-less causal edge (RO:0002407) bridging two MF nodes`

3. Under **Key Algorithm: Standard Annotation Extraction**, update the description of step 1 to note that `extract_edges()` now discovers all axiom edges, not just those with evidence.

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for edges without evidence (#14)"
```

---

## Verification

After all tasks are complete, run these commands to confirm everything works:

```bash
# Full test suite
pytest -v

# Manual smoke test on the issue's example model
python src/gocam_unwinder/gocam_ttl.py \
  -m resources/test/66c7d41500000016.ttl \
  -o target/go_20250601.json \
  -r resources/test/ro_20250723.owl

# Verify the output shows 1 non-standard annotation (not 2 separate ones)
# and the "Edges w/o Evidence" column shows 1
```

**Expected final state:**
- [ ] All tests pass (including new `test_edges_without_evidence` and `test_edges_without_evidence_report_column`)
- [ ] No regressions in existing tests
- [ ] Model 66c7d41500000016 is parsed as having 1 annotation subgraph (not 2)
- [ ] Report output includes "Edges w/o Evidence" column with correct counts
- [ ] CLAUDE.md is updated with new behavior and test model documentation

---

## Notes

- The `find_related_edges()` method already had a fallback for creating no-evidence edges (lines 507-512). The fix moves this discovery earlier into `extract_edges()` so the union-find algorithm in `extract_standard_annotations()` sees all edges from the start.
- The `GOCAM_RELATIONS` filter in the new second pass of `extract_edges()` is important — it prevents non-GO-CAM axioms (like OWL class axioms) from being treated as edges.
- The `has_consistent_evidence_across_edges()` check handles edges with 0 evidence correctly: a single-edge annotation with no evidence passes (len <= 1), and multi-edge annotations where some edges have no evidence will fail consistency (evidence groups won't cover all edges).
- Existing models that had no evidence-less edges should produce identical results — the second pass in `extract_edges()` simply won't find any additional edges.
- Edge case: if ALL edges in a model lack evidence, we'd get annotations with no evidence at all. These would pass the consistency check (vacuously) but would never be split (no multi-evidence). This is correct behavior.
