# Nested Anatomy Extension Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `--fix-nested-anatomy` mode to `gocam_ttl.py` that rewrites nested anatomy extension edges so the anatomy target attaches directly to the annotation's primary individual (relation → `occurs_in` for MF/BP-led, kept for CC-led), writing the fixed models to an output directory with an optional change report.

**Architecture:** Detection lives on `GoCamGraphBuilder` (reuses `get_extension_edges`, `get_primary_go_terms`, `_is_anatomical_structure`) and produces a rewrite *plan*; the low-level graph mutation lives on `GoCamGraph` (alongside `update_evidence_date`, `clone_bnode`, `write_ttl`). Shared detection helpers (`pick_lead_aspect`, `find_nested_extensions`) move from `debug_non_standard.py` into the library so both the fixer and the debug script use them.

**Tech Stack:** Python 3, `rdflib`, `ontobio` (`GoAspector`, `relations.lookup_label`). Tests use `pytest`.

**Design doc:** `docs/plans/2026-06-19-nested-anatomy-extension-fix-design.md`

> **Commits:** This repo's owner stages and commits manually. Do **not** run `git add` or `git commit`. Where this plan says "Checkpoint," run the listed verification and stop for the owner to review/commit.

---

## File Structure

- **Modify** `src/gocam_unwinder/gocam_ttl.py`:
  - New module-level helpers `ASPECT_PRIORITY`, `pick_lead_aspect`, `find_nested_extensions` (moved from `debug_non_standard.py`).
  - New `GoCamGraphBuilder.get_primary_individuals()` method.
  - New `GoCamGraphBuilder.plan_nested_anatomy_fixes()` method.
  - New `GoCamGraph.rewrite_edge_source_and_relation()` method.
  - New CLI args `--fix-nested-anatomy`, `--nested-fix-report`; new fix loop + report writer in `main()`.
- **Modify** `debug_non_standard.py`: delete the three local helper defs; import them from `gocam_ttl`.
- **Create** `resources/test/mf_nested_anatomy_example.ttl`, `resources/test/cc_nested_anatomy_example.ttl` (synthetic fixtures).
- **Modify** `tests/test_gocam_ttl.py`: append new test functions.

---

## Task 1: Promote shared detection helpers into the library

Move `ASPECT_PRIORITY`, `pick_lead_aspect`, and `find_nested_extensions` from `debug_non_standard.py` into `gocam_ttl.py`, and re-import them in the debug script. Pure refactor — no behavior change. Verified by the existing test suite (including `test_mgi_2182965_lead_aspect_is_mf`, which does `from debug_non_standard import pick_lead_aspect`).

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py` (add helpers after `load_groups_lookup`, which ends at line 125, before `class StandardAnnotationEdge` at line 128)
- Modify: `debug_non_standard.py:14-46` (delete local defs; update import)

- [ ] **Step 1: Add the helpers to `gocam_ttl.py`**

Insert after the end of `load_groups_lookup` (line 125) and before `class StandardAnnotationEdge` (line 128):

```python
# Lead-aspect priority order for picking a single aspect per annotation.
# BP wins when present (the BP backbone subsumes MF in the design); CC is
# next, MF is the fallback for annotations with only an MF backbone.
ASPECT_PRIORITY = ("BP", "CC", "MF")


def pick_lead_aspect(primary_terms):
    """Return the lead aspect for an annotation, by priority BP > CC > MF, or None."""
    for aspect in ASPECT_PRIORITY:
        if aspect in primary_terms:
            return aspect
    return None


def find_nested_extensions(annot, builder):
    """Return (lead_aspect, nested_edges) for an annotation, or (None, []) if there
    is no backbone or no nested extension.

    A nested extension edge is an extension edge (per builder.get_extension_edges)
    whose source_type is not in the lead aspect's primary GO term URI set.
    """
    primary_terms = builder.get_primary_go_terms(annot)
    lead = pick_lead_aspect(primary_terms)
    if lead is None:
        return None, []
    primary_uri_set = set(primary_terms[lead])
    nested = [
        edge for edge in builder.get_extension_edges(annot)
        if edge.source_type not in primary_uri_set
    ]
    if not nested:
        return lead, []
    return lead, nested
```

- [ ] **Step 2: Update `debug_non_standard.py` to import them**

Replace the import block and the three local defs. The current file has (lines 8-46): an import of `GoCamGraphBuilder, collect_model_files, load_skip_filenames`, then `ASPECT_PRIORITY = (...)`, then `def pick_lead_aspect(...)`, then `def find_nested_extensions(...)`.

Change the import (lines 8-12) to:

```python
from gocam_unwinder.gocam_ttl import (
    GoCamGraphBuilder,
    collect_model_files,
    load_skip_filenames,
    pick_lead_aspect,
    find_nested_extensions,
)
```

Then delete the now-duplicate module-level definitions in `debug_non_standard.py`: the `ASPECT_PRIORITY = ("BP", "CC", "MF")` constant (and its 2 preceding comment lines) and the entire `def pick_lead_aspect(...)` and `def find_nested_extensions(...)` functions (lines 14-46). The next remaining function after the import block should be `classify_multiple_mf_bp`.

- [ ] **Step 3: Verify the debug script still imports and re-exports the helpers**

Run:
```bash
source env/bin/activate && python3 -c "from debug_non_standard import pick_lead_aspect, find_nested_extensions; print('ok')"
```
Expected: prints `ok`, exit code 0. (The re-export keeps `from debug_non_standard import pick_lead_aspect` working for the existing test.)

- [ ] **Step 4: Run the full suite to confirm no regression**

Run:
```bash
source env/bin/activate && pytest -q
```
Expected: all 64 existing tests PASS (notably `test_mgi_2182965_lead_aspect_is_mf`, which imports `pick_lead_aspect` from `debug_non_standard`).

- [ ] **Step 5: Checkpoint** — refactor complete; stop for review/commit.

---

## Task 2: `GoCamGraphBuilder.get_primary_individuals()` (TDD)

Mirror of `get_primary_go_terms` (`gocam_ttl.py:1043-1072`) that returns the backbone *individual* URI per aspect instead of the type URI. Needed so the fixer knows which individual to re-point the nested edge onto.

**Files:**
- Modify: `tests/test_gocam_ttl.py` (append `test_get_primary_individuals`)
- Modify: `src/gocam_unwinder/gocam_ttl.py` (add method immediately after `get_primary_go_terms`, which ends at line 1072, before `parse_ttl` at line 1074)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gocam_ttl.py`:

```python
def test_get_primary_individuals(builder):
    """
    get_primary_individuals() returns the backbone *individual* URIs per aspect.
    On 5966411600000001.ttl's GO:0120045 annotation:
      - MF backbone (MF -enabled_by-> GP) -> primary MF individual = ...0003
      - BP backbone (root-MF -part_of-> BP) -> primary BP individual = ...0004
      - no CC backbone -> "CC" absent
    """
    gocam_graph = builder.parse_ttl("resources/test/5966411600000001.ttl")

    bp_individual = rdflib.term.URIRef(
        'http://model.geneontology.org/5966411600000001/5966411600000004')
    target_annot = None
    for annot in gocam_graph.standard_annotations + gocam_graph.non_standard_annotations:
        if bp_individual in annot.individuals:
            target_annot = annot
            break
    assert target_annot is not None

    primaries = builder.get_primary_individuals(target_annot)

    assert set(primaries.keys()) == {"MF", "BP"}, \
        f"Expected {{'MF','BP'}}, got {set(primaries.keys())}"
    assert len(primaries["MF"]) == 1 and len(primaries["BP"]) == 1
    assert str(primaries["MF"][0]) == \
        "http://model.geneontology.org/5966411600000001/5966411600000003"
    assert str(primaries["BP"][0]) == \
        "http://model.geneontology.org/5966411600000001/5966411600000004"
    assert "CC" not in primaries
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
source env/bin/activate && pytest tests/test_gocam_ttl.py::test_get_primary_individuals -v
```
Expected: FAIL with `AttributeError: 'GoCamGraphBuilder' object has no attribute 'get_primary_individuals'`.

- [ ] **Step 3: Implement `get_primary_individuals`**

In `src/gocam_unwinder/gocam_ttl.py`, add immediately after `get_primary_go_terms` (after its `return primary` at line 1072) and before `def parse_ttl`:

```python
    def get_primary_individuals(self, annot: StandardAnnotation) -> dict:
        """
        Return the primary *individual* URIs of an annotation, grouped by aspect.

        Mirror of get_primary_go_terms, but collects the backbone individual URI
        (not its type) per aspect, using the same _backbone_role dispatch:
          - MF: source_uri of an MF -enabled_by-> GP edge
          - BP: target_uri of a root-MF -part_of-> BP edge
          - CC: target_uri of a   ? -located_in/is_active_in-> CC edge

        Returns a dict mapping aspect ("MF", "BP", "CC") to a list of individual
        URIs. Aspect keys are absent when no backbone match is found. Lists are
        typically length 1 (longer surfaces anomalies, like get_primary_go_terms).
        """
        primary = {}
        for edge in annot.edges.values():
            role = self._backbone_role(edge)
            if role == "MF":
                primary.setdefault("MF", []).append(edge.source_uri)
            elif role == "BP":
                primary.setdefault("BP", []).append(edge.target_uri)
            elif role == "CC":
                primary.setdefault("CC", []).append(edge.target_uri)
        return primary
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
source env/bin/activate && pytest tests/test_gocam_ttl.py::test_get_primary_individuals -v
```
Expected: PASS.

- [ ] **Step 5: Checkpoint** — stop for review/commit.

---

## Task 3: `GoCamGraph.rewrite_edge_source_and_relation()` (TDD)

The low-level mutation: re-point an edge's source individual and relation, updating both the assertion triple and its reified `owl:Axiom` bnode, preserving target/evidence/dates/contributors.

**Files:**
- Modify: `tests/test_gocam_ttl.py` (append `test_rewrite_edge_source_and_relation`)
- Modify: `src/gocam_unwinder/gocam_ttl.py` (add method to `GoCamGraph`, immediately after `clone_individual`, which ends at line 455, before `evidence_triples` at line 457)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gocam_ttl.py`:

```python
def test_rewrite_edge_source_and_relation(builder):
    """
    rewrite_edge_source_and_relation re-points the CL -part_of-> EMAPA nested edge
    in 5966411600000001.ttl onto the primary BP individual with occurs_in, updating
    BOTH the assertion triple and the owl:Axiom bnode, preserving target + evidence.
    """
    gocam_graph = builder.parse_ttl("resources/test/5966411600000001.ttl")

    cl_type = rdflib.term.URIRef("http://purl.obolibrary.org/obo/CL_0000202")
    emapa_type = rdflib.term.URIRef("http://purl.obolibrary.org/obo/EMAPA_17597")
    part_of = rdflib.term.URIRef("http://purl.obolibrary.org/obo/BFO_0000050")
    occurs_in = rdflib.term.URIRef("http://purl.obolibrary.org/obo/BFO_0000066")
    bp_individual = rdflib.term.URIRef(
        "http://model.geneontology.org/5966411600000001/5966411600000004")

    # Locate the nested CL -part_of-> EMAPA edge.
    edge = None
    for annot in gocam_graph.standard_annotations + gocam_graph.non_standard_annotations:
        for e in annot.edges.values():
            if e.source_type == cl_type and e.target_type == emapa_type and e.property_uri == part_of:
                edge = e
                break
        if edge:
            break
    assert edge is not None, "Expected a CL -part_of-> EMAPA edge"

    old_source = edge.source_uri      # the CL individual (...0009)
    target = edge.target_uri          # the EMAPA individual (...0010)
    axiom_bnode = rdflib.term.BNode(edge.bnode_id)
    # Capture evidence on the axiom bnode before the rewrite.
    evidence_pred = rdflib.term.URIRef("http://geneontology.org/lego/evidence")
    evidence_before = set(gocam_graph.g.objects(axiom_bnode, evidence_pred))
    assert evidence_before, "Nested edge axiom should have evidence"

    gocam_graph.rewrite_edge_source_and_relation(
        edge.bnode_id, old_source, part_of, target, bp_individual, occurs_in)

    g = gocam_graph.g
    # Assertion triple rewritten
    assert (bp_individual, occurs_in, target) in g
    assert (old_source, part_of, target) not in g
    # Axiom bnode rewritten
    assert (axiom_bnode, rdflib.namespace.OWL.annotatedSource, bp_individual) in g
    assert (axiom_bnode, rdflib.namespace.OWL.annotatedSource, old_source) not in g
    assert (axiom_bnode, rdflib.namespace.OWL.annotatedProperty, occurs_in) in g
    assert (axiom_bnode, rdflib.namespace.OWL.annotatedProperty, part_of) not in g
    # Target + evidence preserved
    assert (axiom_bnode, rdflib.namespace.OWL.annotatedTarget, target) in g
    assert set(g.objects(axiom_bnode, evidence_pred)) == evidence_before
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
source env/bin/activate && pytest tests/test_gocam_ttl.py::test_rewrite_edge_source_and_relation -v
```
Expected: FAIL with `AttributeError: 'GoCamGraph' object has no attribute 'rewrite_edge_source_and_relation'`.

- [ ] **Step 3: Implement the method**

In `src/gocam_unwinder/gocam_ttl.py`, add to `GoCamGraph` immediately after `clone_individual` (after its body ending at line 455) and before `def evidence_triples`:

```python
    def rewrite_edge_source_and_relation(self, bnode_id, old_source, old_property,
                                         target, new_source, new_property):
        """
        Re-point an edge's source individual and relation, updating BOTH the
        assertion triple and its reified owl:Axiom bnode.

        Used to de-nest an anatomy extension: the nested edge
        (old_source -old_property-> target) becomes
        (new_source -new_property-> target), where new_source is the annotation's
        primary individual. annotatedTarget, evidence, dates, and contributors on
        the axiom bnode are left untouched. The axiom bnode is updated only if it
        exists (defensive — a bare assertion may never have been reified).
        """
        # Assertion triple
        self.g.remove((old_source, old_property, target))
        self.g.add((new_source, new_property, target))

        # Reified owl:Axiom bnode
        bnode = rdflib.term.BNode(bnode_id)
        if (bnode, rdflib.namespace.OWL.annotatedSource, old_source) in self.g:
            self.g.remove((bnode, rdflib.namespace.OWL.annotatedSource, old_source))
            self.g.add((bnode, rdflib.namespace.OWL.annotatedSource, new_source))
        if (bnode, rdflib.namespace.OWL.annotatedProperty, old_property) in self.g:
            self.g.remove((bnode, rdflib.namespace.OWL.annotatedProperty, old_property))
            self.g.add((bnode, rdflib.namespace.OWL.annotatedProperty, new_property))
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
source env/bin/activate && pytest tests/test_gocam_ttl.py::test_rewrite_edge_source_and_relation -v
```
Expected: PASS.

- [ ] **Step 5: Checkpoint** — stop for review/commit.

---

## Task 4: Synthetic fixtures for MF-led and CC-led cases

Create two small TTL fixtures so the MF→`occurs_in` and CC→keep-`part_of` relation branches are both exercised. Modeled on `resources/test/mf_cc_relation_example.ttl`.

**Files:**
- Create: `resources/test/mf_nested_anatomy_example.ttl`
- Create: `resources/test/cc_nested_anatomy_example.ttl`

- [ ] **Step 1: Create `resources/test/mf_nested_anatomy_example.ttl`**

MF-led: `MF(GO:0004672) -enabled_by-> GP`, `MF -occurs_in-> CL` (direct extension), `CL -part_of-> EMAPA` (nested; both anatomy).

```turtle
<http://model.geneontology.org/mf_nested_anatomy_example> a <http://www.w3.org/2002/07/owl#Ontology> ;
	<http://geneontology.org/lego/modelstate> "production" ;
	<http://purl.org/dc/elements/1.1/title> "MF-led nested anatomy fix fixture" ;
	<http://purl.org/pav/providedBy> "http://informatics.jax.org" .

<http://geneontology.org/lego/evidence> a <http://www.w3.org/2002/07/owl#AnnotationProperty> .
<http://purl.org/dc/elements/1.1/date> a <http://www.w3.org/2002/07/owl#AnnotationProperty> .

# MF backbone: MF -enabled_by-> GP ; plus MF -occurs_in-> CL (direct extension)
<http://model.geneontology.org/mf_nested_anatomy_example/mf1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/GO_0004672> ;
	<http://purl.obolibrary.org/obo/RO_0002333> <http://model.geneontology.org/mf_nested_anatomy_example/gp1> ;
	<http://purl.obolibrary.org/obo/BFO_0000066> <http://model.geneontology.org/mf_nested_anatomy_example/anat1> .
<http://model.geneontology.org/mf_nested_anatomy_example/gp1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://identifiers.org/mgi/MGI:1234567> .
<http://model.geneontology.org/mf_nested_anatomy_example/anat1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/CL_0000202> ;
	<http://purl.obolibrary.org/obo/BFO_0000050> <http://model.geneontology.org/mf_nested_anatomy_example/anat2> .
<http://model.geneontology.org/mf_nested_anatomy_example/anat2>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/EMAPA_17597> .
<http://model.geneontology.org/mf_nested_anatomy_example/ev1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
<http://model.geneontology.org/mf_nested_anatomy_example/ev2>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
<http://model.geneontology.org/mf_nested_anatomy_example/ev3>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .

# MF -enabled_by-> GP
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/mf_nested_anatomy_example/mf1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/RO_0002333> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/mf_nested_anatomy_example/gp1> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/mf_nested_anatomy_example/ev1> .

# MF -occurs_in-> CL  (direct extension; source is the primary MF)
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/mf_nested_anatomy_example/mf1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/BFO_0000066> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/mf_nested_anatomy_example/anat1> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/mf_nested_anatomy_example/ev2> .

# CL -part_of-> EMAPA  (NESTED: both anatomy, source != primary MF)
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/mf_nested_anatomy_example/anat1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/BFO_0000050> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/mf_nested_anatomy_example/anat2> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/mf_nested_anatomy_example/ev3> .
```

- [ ] **Step 2: Create `resources/test/cc_nested_anatomy_example.ttl`**

CC-led: `GP -located_in-> CC(GO:0005634)`, `CC -part_of-> CL` (direct extension), `CL -part_of-> EMAPA` (nested; both anatomy).

```turtle
<http://model.geneontology.org/cc_nested_anatomy_example> a <http://www.w3.org/2002/07/owl#Ontology> ;
	<http://geneontology.org/lego/modelstate> "production" ;
	<http://purl.org/dc/elements/1.1/title> "CC-led nested anatomy fix fixture" ;
	<http://purl.org/pav/providedBy> "http://informatics.jax.org" .

<http://geneontology.org/lego/evidence> a <http://www.w3.org/2002/07/owl#AnnotationProperty> .
<http://purl.org/dc/elements/1.1/date> a <http://www.w3.org/2002/07/owl#AnnotationProperty> .

# CC backbone: GP -located_in-> CC ; CC -part_of-> CL (direct extension)
<http://model.geneontology.org/cc_nested_anatomy_example/gp1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://identifiers.org/mgi/MGI:1234567> ;
	<http://purl.obolibrary.org/obo/RO_0001025> <http://model.geneontology.org/cc_nested_anatomy_example/cc1> .
<http://model.geneontology.org/cc_nested_anatomy_example/cc1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/GO_0005634> ;
	<http://purl.obolibrary.org/obo/BFO_0000050> <http://model.geneontology.org/cc_nested_anatomy_example/anat1> .
<http://model.geneontology.org/cc_nested_anatomy_example/anat1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/CL_0000202> ;
	<http://purl.obolibrary.org/obo/BFO_0000050> <http://model.geneontology.org/cc_nested_anatomy_example/anat2> .
<http://model.geneontology.org/cc_nested_anatomy_example/anat2>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/EMAPA_17597> .
<http://model.geneontology.org/cc_nested_anatomy_example/ev1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
<http://model.geneontology.org/cc_nested_anatomy_example/ev2>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
<http://model.geneontology.org/cc_nested_anatomy_example/ev3>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .

# GP -located_in-> CC
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/cc_nested_anatomy_example/gp1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/RO_0001025> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/cc_nested_anatomy_example/cc1> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/cc_nested_anatomy_example/ev1> .

# CC -part_of-> CL  (direct extension; source is the primary CC)
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/cc_nested_anatomy_example/cc1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/BFO_0000050> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/cc_nested_anatomy_example/anat1> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/cc_nested_anatomy_example/ev2> .

# CL -part_of-> EMAPA  (NESTED: both anatomy, source != primary CC)
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/cc_nested_anatomy_example/anat1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/BFO_0000050> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/cc_nested_anatomy_example/anat2> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/cc_nested_anatomy_example/ev3> .
```

- [ ] **Step 3: Sanity-check both fixtures parse and assemble one annotation each**

Run:
```bash
source env/bin/activate && python3 -c "
from gocam_unwinder.gocam_ttl import GoCamGraphBuilder, find_nested_extensions
b = GoCamGraphBuilder('target/go_20250601.json', 'resources/test/ro_20250723.owl', resolve_labels_api=False)
for f, want in [('mf_nested_anatomy_example','MF'), ('cc_nested_anatomy_example','CC')]:
    g = b.parse_ttl(f'resources/test/{f}.ttl')
    annots = g.standard_annotations + g.non_standard_annotations
    leads = [find_nested_extensions(a, b)[0] for a in annots if find_nested_extensions(a, b)[1]]
    print(f, leads)
    assert leads == [want], f'{f}: expected [{want}], got {leads}'
print('ok')
"
```
Expected: prints two lines plus `ok`. The MF fixture's lead aspect is `MF`, the CC fixture's is `CC`. (If a lead is wrong, recheck the fixture's backbone edges — e.g. a stray `enabled_by` would add an MF backbone.)

- [ ] **Step 4: Checkpoint** — stop for review/commit.

---

## Task 5: `GoCamGraphBuilder.plan_nested_anatomy_fixes()` (TDD)

The planner: for every annotation, find nested extension edges whose endpoints are both anatomical structures and emit a rewrite instruction per edge (source → primary individual; relation → `occurs_in` for MF/BP, kept for CC). Skip annotations whose lead aspect has != 1 primary individual.

**Files:**
- Modify: `tests/test_gocam_ttl.py` (append three test functions)
- Modify: `src/gocam_unwinder/gocam_ttl.py` (add method to `GoCamGraphBuilder`, immediately after the new `get_primary_individuals` from Task 2, before `def parse_ttl`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gocam_ttl.py`:

```python
OCCURS_IN = "http://purl.obolibrary.org/obo/BFO_0000066"
PART_OF = "http://purl.obolibrary.org/obo/BFO_0000050"


def test_plan_nested_anatomy_fixes_bp(builder):
    """BP-led real fixture: exactly the CL -part_of-> EMAPA edge is planned,
    re-pointed onto the primary BP individual (...0004) with occurs_in."""
    gocam_graph = builder.parse_ttl("resources/test/5966411600000001.ttl")
    plan = builder.plan_nested_anatomy_fixes(gocam_graph)

    assert len(plan) == 1, f"Expected 1 rewrite, got {len(plan)}"
    r = plan[0]
    assert r["lead_aspect"] == "BP"
    assert str(r["new_property_uri"]) == OCCURS_IN
    assert str(r["old_property_uri"]) == PART_OF
    assert str(r["new_source_uri"]) == \
        "http://model.geneontology.org/5966411600000001/5966411600000004"
    assert str(r["old_source_uri"]) == \
        "http://model.geneontology.org/5966411600000001/5966411600000009"
    assert str(r["target_uri"]) == \
        "http://model.geneontology.org/5966411600000001/5966411600000010"
    assert str(r["primary_term"]) == "http://purl.obolibrary.org/obo/GO_0120045"
    assert str(r["old_source_type"]) == "http://purl.obolibrary.org/obo/CL_0000202"
    assert str(r["target_type"]) == "http://purl.obolibrary.org/obo/EMAPA_17597"


def test_plan_nested_anatomy_fixes_mf(builder):
    """MF-led synthetic fixture: nested CL -part_of-> EMAPA re-pointed onto the
    primary MF individual with occurs_in (MF/BP-led relation)."""
    gocam_graph = builder.parse_ttl("resources/test/mf_nested_anatomy_example.ttl")
    plan = builder.plan_nested_anatomy_fixes(gocam_graph)

    assert len(plan) == 1
    r = plan[0]
    assert r["lead_aspect"] == "MF"
    assert str(r["new_property_uri"]) == OCCURS_IN
    assert str(r["new_source_uri"]) == \
        "http://model.geneontology.org/mf_nested_anatomy_example/mf1"
    assert str(r["target_type"]) == "http://purl.obolibrary.org/obo/EMAPA_17597"


def test_plan_nested_anatomy_fixes_cc(builder):
    """CC-led synthetic fixture: nested CL -part_of-> EMAPA re-pointed onto the
    primary CC individual but KEEPS part_of (CC-led keeps the relation)."""
    gocam_graph = builder.parse_ttl("resources/test/cc_nested_anatomy_example.ttl")
    plan = builder.plan_nested_anatomy_fixes(gocam_graph)

    assert len(plan) == 1
    r = plan[0]
    assert r["lead_aspect"] == "CC"
    assert str(r["new_property_uri"]) == PART_OF, "CC-led keeps the original relation"
    assert str(r["new_source_uri"]) == \
        "http://model.geneontology.org/cc_nested_anatomy_example/cc1"
    assert str(r["target_type"]) == "http://purl.obolibrary.org/obo/EMAPA_17597"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
source env/bin/activate && pytest tests/test_gocam_ttl.py -k plan_nested_anatomy_fixes -v
```
Expected: 3 FAIL with `AttributeError: 'GoCamGraphBuilder' object has no attribute 'plan_nested_anatomy_fixes'`.

- [ ] **Step 3: Implement `plan_nested_anatomy_fixes`**

In `src/gocam_unwinder/gocam_ttl.py`, add to `GoCamGraphBuilder` immediately after `get_primary_individuals` (Task 2) and before `def parse_ttl`:

```python
    def plan_nested_anatomy_fixes(self, gocam: GoCamGraph) -> list:
        """
        Build a list of rewrite instructions for nested anatomy extension edges.

        For every annotation (standard and non-standard), find extension edges
        whose source is not the lead-aspect primary term (nested) and whose source
        and target types are both anatomical structures (_is_anatomical_structure),
        and plan to re-point each onto the annotation's primary individual. The
        relation becomes occurs_in for MF/BP-led annotations, or is kept as-is
        (typically part_of) for CC-led annotations.

        Annotations whose lead aspect has 0 or >1 primary individual are skipped
        with a warning (ambiguous attach point).

        Returns a list of instruction dicts (see the design doc for the schema).
        """
        plan = []
        all_annots = gocam.standard_annotations + gocam.non_standard_annotations
        for annot in all_annots:
            lead, nested = find_nested_extensions(annot, self)
            if not nested:
                continue
            primary_individuals = self.get_primary_individuals(annot).get(lead, [])
            if len(primary_individuals) != 1:
                print(f"WARNING: skipping annotation in {gocam.model_id} "
                      f"({gocam.title}) — expected 1 primary {lead} individual, "
                      f"found {len(primary_individuals)}")
                continue
            primary_individual = primary_individuals[0]
            primary_term = self.get_primary_go_terms(annot)[lead][0]
            for edge in nested:
                if not (self._is_anatomical_structure(edge.source_type)
                        and self._is_anatomical_structure(edge.target_type)):
                    continue
                if lead in ("MF", "BP"):
                    new_property = self.rel_occurs_in
                else:
                    new_property = edge.property_uri  # CC-led keeps the relation
                plan.append({
                    "model_id": gocam.model_id,
                    "title": gocam.title,
                    "lead_aspect": lead,
                    "primary_term": primary_term,
                    "bnode_id": edge.bnode_id,
                    "old_source_uri": edge.source_uri,
                    "old_property_uri": edge.property_uri,
                    "target_uri": edge.target_uri,
                    "new_source_uri": primary_individual,
                    "new_property_uri": new_property,
                    "old_source_type": edge.source_type,
                    "target_type": edge.target_type,
                })
        return plan
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
source env/bin/activate && pytest tests/test_gocam_ttl.py -k plan_nested_anatomy_fixes -v
```
Expected: 3 PASS.

If `test_plan_nested_anatomy_fixes_bp` reports `Expected 1 rewrite, got 0`, the BP backbone isn't being detected (check the RO ontology loaded via the `builder` fixture). If it reports >1, another annotation in the model also has a nested anatomy edge — re-examine the model.

- [ ] **Step 5: Checkpoint** — stop for review/commit.

---

## Task 6: CLI wiring in `main()` + smoke test

Expose the fixer via `--fix-nested-anatomy` (+ reuse `--output-dir`) and `--nested-fix-report`. Write only changed models; write a labeled TSV report when requested.

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py` (argparse block near line 29; `main()` body)

- [ ] **Step 1: Add the CLI arguments**

In `src/gocam_unwinder/gocam_ttl.py`, after the `--date-change-report` argument (line 29) and before `--no-label-api` (line 30), add:

```python
parser.add_argument('--fix-nested-anatomy', action='store_true',
                    help="Rewrite nested anatomy extension edges to attach directly to the annotation's primary individual")
parser.add_argument('--nested-fix-report', help="Output TSV file for the nested-anatomy fix report (one row per rewritten edge)")
```

- [ ] **Step 2: Add the output-dir guard and record accumulator**

In `main()`, find the existing block (lines 1397-1400):

```python
    if args.split_evidence and args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)

    all_date_change_records = []
```

Replace with:

```python
    if (args.split_evidence or args.fix_nested_anatomy) and args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)

    all_date_change_records = []
    all_nested_fix_records = []
```

- [ ] **Step 3: Add the fix loop inside the model loop**

In `main()`, find the end of the split-evidence block (lines 1480-1482):

```python
            date_records = gocam_graph.split_evidence_and_write_ttl(output_filename)
            all_date_change_records.extend(date_records)
            print(f"Split evidence for {filename} -> {output_filename}")
```

Immediately after those three lines (still inside the `for f in model_files:` loop, at the same indentation as the `if args.split_evidence ...` block), add:

```python
        # Fix nested anatomy extensions if requested (independent of --split-evidence)
        if args.fix_nested_anatomy:
            nested_plan = go_cam_graph_builder.plan_nested_anatomy_fixes(gocam_graph)
            if nested_plan:
                for rec in nested_plan:
                    gocam_graph.rewrite_edge_source_and_relation(
                        rec["bnode_id"], rec["old_source_uri"], rec["old_property_uri"],
                        rec["target_uri"], rec["new_source_uri"], rec["new_property_uri"])
                all_nested_fix_records.extend(nested_plan)
                if args.output_dir:
                    fix_output_filename = os.path.join(args.output_dir, filename)
                else:
                    fix_output_filename = os.path.splitext(f)[0] + "_nested_fixed.ttl"
                gocam_graph.write_ttl(fix_output_filename)
                print(f"Fixed nested anatomy for {filename} -> {fix_output_filename} ({len(nested_plan)} edges)")
```

- [ ] **Step 4: Add the report writer after the model loop**

In `main()`, find the date-change-report block (lines 1485-1495) and add the nested-fix report writer immediately after it (before the `if report_file:` close at line 1498):

```python
    # Write nested-anatomy fix report if requested
    if args.nested_fix_report and all_nested_fix_records:
        with open(args.nested_fix_report, 'w') as nfr_file:
            nfr_headers = ["Model ID", "Title", "Lead Aspect", "Primary Term",
                           "Old Source", "Old Relation", "Target", "New Relation"]
            print("\t".join(nfr_headers), file=nfr_file)
            for rec in all_nested_fix_records:
                primary_label = go_cam_graph_builder.term_label(rec["primary_term"]) if rec["primary_term"] else ""
                old_source_label = go_cam_graph_builder.term_label(rec["old_source_type"]) if rec["old_source_type"] else ""
                old_rel_label = go_cam_graph_builder.term_label(rec["old_property_uri"]) if rec["old_property_uri"] else ""
                target_label = go_cam_graph_builder.term_label(rec["target_type"]) if rec["target_type"] else ""
                new_rel_label = go_cam_graph_builder.term_label(rec["new_property_uri"]) if rec["new_property_uri"] else ""
                cols = [rec["model_id"], rec["title"], rec["lead_aspect"], primary_label,
                        old_source_label, old_rel_label, target_label, new_rel_label]
                print("\t".join(cols), file=nfr_file)
```

- [ ] **Step 5: Smoke-test the CLI end-to-end**

Run:
```bash
source env/bin/activate && rm -rf /tmp/nested_fix_out && python src/gocam_unwinder/gocam_ttl.py \
  -d resources/test/ -o target/go_20250601.json -r resources/test/ro_20250723.owl \
  --fix-nested-anatomy --output-dir /tmp/nested_fix_out \
  --nested-fix-report /tmp/nested_fix_report.tsv >/tmp/nested_fix_stdout.txt 2>&1; echo "exit=$?"
ls /tmp/nested_fix_out
echo "--- report ---"; cat /tmp/nested_fix_report.tsv
```
Expected:
- `exit=0`.
- `/tmp/nested_fix_out` contains `5966411600000001.ttl`, `mf_nested_anatomy_example.ttl`, `cc_nested_anatomy_example.ttl` (the changed models), and NOT unchanged models like `MGI_MGI_1100089.ttl`.
- The report has a header row plus one data row per fixture: the BP and MF rows show `New Relation` = `occurs in`; the CC row shows `New Relation` = `part of`. `Old Relation` is `part of` for all three.

- [ ] **Step 6: Confirm the rewrite landed in the written TTL**

Run:
```bash
grep -A4 "5966411600000010" /tmp/nested_fix_out/5966411600000001.ttl | grep -E "annotatedSource|BFO_0000066" || \
grep -E "5966411600000004.*BFO_0000066.*5966411600000010|5966411600000010" /tmp/nested_fix_out/5966411600000001.ttl
```
Expected: the EMAPA individual (`...0010`) is now the target of an `occurs_in` (BFO_0000066) edge sourced from the primary BP individual (`...0004`), confirming the de-nesting persisted to disk.

- [ ] **Step 7: Run the full suite**

Run:
```bash
source env/bin/activate && pytest -q
```
Expected: all tests PASS (64 existing + 6 new = 70).

- [ ] **Step 8: Checkpoint** — stop for review/commit.

---

## Verification

After all tasks:

```bash
# Full suite (6 new tests: get_primary_individuals, rewrite_edge_source_and_relation,
# plan_nested_anatomy_fixes_{bp,mf,cc})
source env/bin/activate && pytest -q

# Debug script still imports the promoted helpers
source env/bin/activate && python3 -c "import debug_non_standard; from debug_non_standard import pick_lead_aspect, find_nested_extensions; print('ok')"

# End-to-end fixer smoke test
source env/bin/activate && rm -rf /tmp/nested_fix_out && python src/gocam_unwinder/gocam_ttl.py \
  -d resources/test/ -o target/go_20250601.json -r resources/test/ro_20250723.owl \
  --fix-nested-anatomy --output-dir /tmp/nested_fix_out --nested-fix-report /tmp/nested_fix_report.tsv
```

Expected final state:
- [ ] All 70 tests pass.
- [ ] `pick_lead_aspect` / `find_nested_extensions` live in `gocam_ttl.py`; `debug_non_standard.py` imports them (its behavior unchanged).
- [ ] `GoCamGraphBuilder` exposes `get_primary_individuals()` and `plan_nested_anatomy_fixes()`; `GoCamGraph` exposes `rewrite_edge_source_and_relation()`.
- [ ] `gocam_ttl.py --fix-nested-anatomy` writes only changed models to `--output-dir`, de-nesting each qualifying anatomy edge onto the primary individual (`occurs_in` for MF/BP-led, `part_of` kept for CC-led), and writes a labeled TSV when `--nested-fix-report` is given.

---

## Notes

- **Why a free function, not a builder method, for `find_nested_extensions`:** it already takes `(annot, builder)` and is called that way in `debug_non_standard.py`. Keeping the signature avoids touching the debug script's call sites beyond the import line.
- **Why scan both standard and non-standard annotations:** mirrors the debug script's nested-extension bucketing, which checks `gocam.standard_annotations + gocam.non_standard_annotations`. A nested anatomy chain can occur in either.
- **Why `BNode(bnode_id)` round-trips:** `split_evidence_and_write_ttl` already reconstructs bnodes this way (`rdflib.term.BNode(edge.bnode_id)`, lines 378/400/407-408) and mutates them in the same graph, so the pattern is established and safe here.
- **Files worth reading before starting:**
  - `docs/plans/2026-06-19-nested-anatomy-extension-fix-design.md` — the design.
  - `src/gocam_unwinder/gocam_ttl.py` — `_backbone_role` (994-1026), `get_extension_edges`/`get_primary_go_terms` (1028-1072), `split_evidence_and_write_ttl`/`clone_*` (319-455), `_is_anatomical_structure` (859-871), the argparse block (15-31) and `main()` model loop (1402-1499).
  - `resources/test/mf_cc_relation_example.ttl` — the synthetic-fixture template.
