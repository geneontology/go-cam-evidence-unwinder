# `get_extension_edges()` Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `GoCamGraphBuilder.get_extension_edges(annot)`, returning the subset of edges in a `StandardAnnotation` that are annotation extensions (i.e., not part of the gene-product → MF/BP/CC backbone).

**Architecture:** Adds one public method on `GoCamGraphBuilder` (which already owns aspect classification via `GoAspector`) plus two thin aspect-check helpers (`uri_is_biological_process`, `uri_is_cellular_component`) that mirror the existing `uri_is_molecular_function`. The classifier walks each edge in the annotation, checks it against three backbone patterns, and returns the complement set — preserving `annot.edges` insertion order. No changes to parsing, filtering, or splitting; this is a pure read-side query.

**Tech Stack:** Python 3, `rdflib`, `ontobio` (`GoAspector`, `relations.lookup_label`), `prefixcommons.curie_util`. Tests use `pytest`.

**Affected Areas:** `GoCamGraphBuilder` class in `src/gocam_unwinder/gocam_ttl.py`; one new test function and one new test fixture file.

---

## Context

GO-CAM models hold "annotation extensions" — edges that decorate a primary annotation with additional context (e.g., "occurs in this cell type which is part of this anatomy"). Today the codebase identifies connected subgraphs (`StandardAnnotation`s) and classifies them as standard/non-standard, but it does not surface *which edges within an annotation are the extensions*. This is needed for downstream work (e.g., GPAD col-16 emission, extension QC).

The full design — including the precise definition of "backbone" per GO aspect, the algorithm, edge cases, and a worked example — lives in `docs/plans/2026-04-30-extension-edges-method-design.md`. The reference example is the 5-edge `GO:0120045` (stereocilium maintenance) annotation in `5966411600000001.ttl`: 2 backbone edges (`MF─enabled_by→GP`, `MF─part_of→BP`) and 3 extension edges, including a multi-hop `BP─occurs_in→CL─part_of→EMAPA` chain.

The four backbone relation URIs all resolve via `ontobio.rdfgen.relations.lookup_label()` (verified at design time): `"enabled by"` → `RO:0002333`, `"part of"` → `BFO:0000050`, `"located in"` → `RO:0001025`, `"is active in"` → `RO:0002432`. The existing code already uses this lookup pattern (see `src/gocam_unwinder/gocam_ttl.py:734`).

## Constraints

- Method must live on `GoCamGraphBuilder` (it needs `GoAspector` for aspect classification — same reason `filter_out_non_std_annotations` lives there).
- Must work on any `StandardAnnotation`, regardless of whether it ended up in `standard_annotations` or `non_standard_annotations`. The method is descriptive, not prescriptive — no exceptions raised on malformed input.
- Must preserve the order of `annot.edges` in the returned list.
- Helpers must follow the existing `uri_is_molecular_function` shape: contract the URI to a CURIE via `prefixcommons.curie_util`, return `False` for non-GO URIs, otherwise delegate to `GoAspector`.
- No new dependencies. No CLI changes. No changes to existing report columns or split behavior.

## Out of Scope

- Per-aspect grouping of extensions (Option C from the design's brainstorming).
- Generating GPAD col-16 strings from extensions.
- Validation of extension well-formedness (e.g., warning on unexpected predicates).
- Surfacing extensions in the report TSV or in any CLI output.
- Unit tests for the new aspect helpers in isolation — the codebase convention (see `uri_is_molecular_function`, `uri_is_causal_relation`) is to test these via the methods that consume them.

---

## Tasks

### Task 1: Add the reference model as a test fixture

The new test reads `5966411600000001.ttl`. The codebase's tests are self-contained under `resources/test/` — every existing test fixture lives there. Copy the model in.

**Files:**
- Create: `resources/test/5966411600000001.ttl` (copy of `/Users/ebertdu/go/noctua-models/models/5966411600000001.ttl`)

- [ ] **Step 1: Copy the file**

Run:
```bash
cp /Users/ebertdu/go/noctua-models/models/5966411600000001.ttl resources/test/5966411600000001.ttl
```

- [ ] **Step 2: Verify the copy succeeded**

Run:
```bash
ls -la resources/test/5966411600000001.ttl && head -5 resources/test/5966411600000001.ttl
```
Expected: file is non-empty and the first lines contain RDF/Turtle prefix declarations (`@prefix` lines or `<http://...>` triples).

- [ ] **Step 3: Verify the GO:0120045 individual is present**

Run:
```bash
grep -c "GO_0120045" resources/test/5966411600000001.ttl
```
Expected: count ≥ 2 (one class declaration + at least one rdf:type usage).

---

### Task 2: Add `uri_is_biological_process` and `uri_is_cellular_component` helpers

These mirror `uri_is_molecular_function` (`src/gocam_unwinder/gocam_ttl.py:688-698`). They contract the URI to a CURIE and delegate to `GoAspector`. Unlike the MF helper, neither needs a special-case for `reacto.owl#molecular_event` — that token only stands in for MF.

`GoAspector` already exposes `is_biological_process(curie)` and `is_cellular_component(curie)` (used elsewhere in `ontobio`); no setup change needed. The methods are added to `GoCamGraphBuilder` immediately after `uri_is_molecular_function` so all aspect helpers are co-located.

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py` (insert after line 698, immediately after the `uri_is_molecular_function` method)

- [ ] **Step 1: Insert the two helpers**

In `src/gocam_unwinder/gocam_ttl.py`, find the `uri_is_molecular_function` method (currently ending at line 698 with `return False`). Add the following two methods directly after it, before `parse_ttl`:

```python
    def uri_is_biological_process(self, uri: URIRef) -> bool:
        """
        Check if the URI refers to a biological process in the GO ontology.
        """
        parsed_curies = curie_util.contract_uri(str(uri))
        if parsed_curies and parsed_curies[0].startswith("GO:"):
            return self.go_aspector.is_biological_process(parsed_curies[0])
        return False

    def uri_is_cellular_component(self, uri: URIRef) -> bool:
        """
        Check if the URI refers to a cellular component in the GO ontology.
        """
        parsed_curies = curie_util.contract_uri(str(uri))
        if parsed_curies and parsed_curies[0].startswith("GO:"):
            return self.go_aspector.is_cellular_component(parsed_curies[0])
        return False
```

- [ ] **Step 2: Verify the existing test suite still passes**

Run:
```bash
pytest tests/test_gocam_ttl.py -v
```
Expected: all currently-passing tests continue to PASS. (The helpers are pure additions — they should not affect any existing behavior.)

- [ ] **Step 3: Sanity-check the helpers against known GO terms**

Run (one-liner, ad-hoc):
```bash
python3 -c "
from rdflib import URIRef
from gocam_unwinder.gocam_ttl import GoCamGraphBuilder
b = GoCamGraphBuilder('target/go_20250601.json')
print('BP GO:0120045 ->', b.uri_is_biological_process(URIRef('http://purl.obolibrary.org/obo/GO_0120045')))
print('CC GO:0005737 ->', b.uri_is_cellular_component(URIRef('http://purl.obolibrary.org/obo/GO_0005737')))
print('non-GO CL:0000202 ->', b.uri_is_biological_process(URIRef('http://purl.obolibrary.org/obo/CL_0000202')))
print('MF GO:0003674 as BP ->', b.uri_is_biological_process(URIRef('http://purl.obolibrary.org/obo/GO_0003674')))
"
```
Expected output:
```
BP GO:0120045 -> True
CC GO:0005737 -> True
non-GO CL:0000202 -> False
MF GO:0003674 as BP -> False
```

If any line is wrong, the helper logic is off — fix before moving on. (Most likely cause: typo in the delegated `GoAspector` method name.)

---

### Task 3: Implement `get_extension_edges` (TDD)

This task follows full test-first discipline: write the failing test, watch it fail with the right error, implement the method, watch it pass, and re-run the suite.

**Files:**
- Modify: `tests/test_gocam_ttl.py` (append new test function `test_get_extension_edges` to the end of the file)
- Modify: `src/gocam_unwinder/gocam_ttl.py` (add `get_extension_edges` method on `GoCamGraphBuilder`, immediately after `uri_is_cellular_component` from Task 2, before `parse_ttl`)

#### Test design

The test uses the fixture from Task 1 to:
1. Parse `5966411600000001.ttl` with the GO ontology already loaded.
2. Locate the annotation containing the GO:0120045 individual (`http://model.geneontology.org/5966411600000001/5966411600000004`). The annotation could be in either `standard_annotations` or `non_standard_annotations` — search both.
3. Confirm the annotation has all 5 edges (sanity check).
4. Call `builder.get_extension_edges(annot)` and assert it returns exactly 3 edges.
5. Verify the 3 returned edges are the correct ones by comparing `(property_uri, source_type, target_type)` tuples — *not* by bnode ID, which is fragile across rdflib parses.

The expected extension tuples (from the design walk-through, lines 71-79):

| pred URI | source type | target type |
|---|---|---|
| `BFO:0000050` (part_of) | `GO:0120045` (BP) | `GO:0007605` (BP) |
| `BFO:0000066` (occurs_in) | `GO:0120045` (BP) | `CL:0000202` |
| `BFO:0000050` (part_of) | `CL:0000202` | `EMAPA:17597` |

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gocam_ttl.py`:

```python
def test_get_extension_edges():
    """
    Test that get_extension_edges() returns the non-backbone edges of a
    StandardAnnotation. Uses the GO:0120045 (stereocilium maintenance)
    annotation in 5966411600000001.ttl, which has 5 edges total:
    - 2 backbone edges (MF-enabled_by->GP, MF-part_of->BP)
    - 3 extension edges (BP-part_of->BP, BP-occurs_in->CL, CL-part_of->EMAPA)
    """
    builder = GoCamGraphBuilder(ontology_file)
    gocam_graph = builder.parse_ttl("resources/test/5966411600000001.ttl")

    # Locate the annotation containing the GO:0120045 individual.
    # The annotation may be in either standard or non_standard list.
    bp_individual = rdflib.term.URIRef(
        'http://model.geneontology.org/5966411600000001/5966411600000004')
    target_annot = None
    for annot in gocam_graph.standard_annotations + gocam_graph.non_standard_annotations:
        if bp_individual in annot.individuals:
            target_annot = annot
            break
    assert target_annot is not None, \
        "Annotation containing the GO:0120045 individual should exist"

    # Sanity: the annotation in this model has 5 edges
    assert len(target_annot.edges) == 5, \
        f"Expected 5 edges in the GO:0120045 annotation, got {len(target_annot.edges)}"

    # Call the method under test
    extensions = builder.get_extension_edges(target_annot)

    # Should return exactly 3 extension edges
    assert len(extensions) == 3, \
        f"Expected 3 extension edges, got {len(extensions)}"

    # Verify each returned edge by (predicate, source_type, target_type) tuple.
    # bnode IDs are not stable across parses — use type triples instead.
    actual_tuples = {
        (str(e.property_uri), str(e.source_type), str(e.target_type))
        for e in extensions
    }
    expected_tuples = {
        # BP -part_of-> BP (GO:0120045 -> GO:0007605)
        ("http://purl.obolibrary.org/obo/BFO_0000050",
         "http://purl.obolibrary.org/obo/GO_0120045",
         "http://purl.obolibrary.org/obo/GO_0007605"),
        # BP -occurs_in-> CL (GO:0120045 -> CL:0000202)
        ("http://purl.obolibrary.org/obo/BFO_0000066",
         "http://purl.obolibrary.org/obo/GO_0120045",
         "http://purl.obolibrary.org/obo/CL_0000202"),
        # CL -part_of-> EMAPA (CL:0000202 -> EMAPA:17597)
        ("http://purl.obolibrary.org/obo/BFO_0000050",
         "http://purl.obolibrary.org/obo/CL_0000202",
         "http://purl.obolibrary.org/obo/EMAPA_17597"),
    }
    assert actual_tuples == expected_tuples, \
        f"Extension edges differ.\n  expected: {expected_tuples}\n  got:      {actual_tuples}"

    # Verify the 2 backbone edges (MF-enabled_by->GP, MF-part_of->BP) are NOT in the result
    extension_bnodes = {e.bnode_id for e in extensions}
    backbone_tuples_seen = set()
    for edge in target_annot.edges.values():
        if edge.bnode_id in extension_bnodes:
            continue
        backbone_tuples_seen.add(
            (str(edge.property_uri), str(edge.source_type), str(edge.target_type)))
    expected_backbone = {
        # MF -enabled_by-> GP
        ("http://purl.obolibrary.org/obo/RO_0002333",
         "http://purl.obolibrary.org/obo/GO_0003674",
         "http://identifiers.org/mgi/MGI:2139535"),
        # MF -part_of-> BP
        ("http://purl.obolibrary.org/obo/BFO_0000050",
         "http://purl.obolibrary.org/obo/GO_0003674",
         "http://purl.obolibrary.org/obo/GO_0120045"),
    }
    assert backbone_tuples_seen == expected_backbone, \
        f"Backbone edges differ.\n  expected: {expected_backbone}\n  got:      {backbone_tuples_seen}"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
pytest tests/test_gocam_ttl.py::test_get_extension_edges -v
```
Expected: FAIL with `AttributeError: 'GoCamGraphBuilder' object has no attribute 'get_extension_edges'`. (If the failure is anything else — e.g., `FileNotFoundError` for the model file — Task 1 didn't land cleanly; go fix that first.)

- [ ] **Step 3: Implement `get_extension_edges`**

In `src/gocam_unwinder/gocam_ttl.py`, add the following method to `GoCamGraphBuilder` immediately after the `uri_is_cellular_component` helper added in Task 2, before `parse_ttl`:

```python
    def get_extension_edges(self, annot: StandardAnnotation) -> List[StandardAnnotationEdge]:
        """
        Return edges in `annot` that are annotation extensions —
        edges that fall outside the gene-product -> MF/BP/CC backbone.

        Backbone patterns (any match -> the edge is backbone, not extension):
          1. MF backbone: predicate == enabled_by AND source is MF
          2. BP backbone: predicate == part_of AND source is MF AND target is BP
          3. CC backbone: predicate in {located_in, is_active_in} AND target is CC

        Multi-hop extensions (e.g., CL -part_of-> EMAPA reached via the BP node)
        are returned because they fail to match any backbone pattern.

        The returned list preserves the order of `annot.edges`.
        """
        enabled_by = URIRef(relations.lookup_label("enabled by"))
        part_of = URIRef(relations.lookup_label("part of"))
        located_in = URIRef(relations.lookup_label("located in"))
        is_active_in = URIRef(relations.lookup_label("is active in"))
        cc_predicates = {located_in, is_active_in}

        backbone_bnode_ids = set()
        for edge in annot.edges.values():
            # Rule 1: MF backbone (MF -enabled_by-> GP). Also covers the
            # MF-enabled_by-GP edge inside a BP or CC annotation.
            if edge.property_uri == enabled_by and self.uri_is_molecular_function(edge.source_type):
                backbone_bnode_ids.add(edge.bnode_id)
                continue
            # Rule 2: BP backbone (MF -part_of-> BP)
            if (edge.property_uri == part_of
                    and self.uri_is_molecular_function(edge.source_type)
                    and self.uri_is_biological_process(edge.target_type)):
                backbone_bnode_ids.add(edge.bnode_id)
                continue
            # Rule 3: CC backbone (GP -located_in/is_active_in-> CC)
            if (edge.property_uri in cc_predicates
                    and self.uri_is_cellular_component(edge.target_type)):
                backbone_bnode_ids.add(edge.bnode_id)
                continue

        return [edge for edge in annot.edges.values() if edge.bnode_id not in backbone_bnode_ids]
```

Note: `List` is already imported from `typing` at the top of the file (line 11), and `URIRef` and `relations` are already imported (lines 8-9). No new imports needed.

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
pytest tests/test_gocam_ttl.py::test_get_extension_edges -v
```
Expected: PASS.

If it fails:
- `Expected 5 edges ... got N` → the model parses to a different edge count than the design assumed; investigate by re-reading `5966411600000001.ttl` with the parser (likely the annotation got merged with another subgraph or split apart by a no-evidence edge).
- `Expected 3 extension edges ... got 4` → likely Rule 1/2/3 are over-matching a non-backbone edge as backbone, or one of the helpers from Task 2 is returning the wrong aspect.
- `Expected 3 extension edges ... got 2` → Rule 1/2/3 are under-matching (an actual backbone edge is being returned as an extension); double-check the helper return values for the actual source/target types.
- The expected `actual_tuples` mismatch → compare the printed sets and identify which edge is misclassified; trace through the three rules manually for that edge.

- [ ] **Step 5: Run the full test suite for regressions**

Run:
```bash
pytest -v
```
Expected: all tests PASS. The new method is a pure addition on `GoCamGraphBuilder`; no existing test should be affected.

---

## Verification

After all three tasks are complete, run the following to confirm everything works:

```bash
# Full test suite with verbose output
pytest -v

# The new test specifically
pytest tests/test_gocam_ttl.py::test_get_extension_edges -v

# Quick smoke test from a Python REPL (proves the API works end-to-end)
python3 -c "
from gocam_unwinder.gocam_ttl import GoCamGraphBuilder
b = GoCamGraphBuilder('target/go_20250601.json')
g = b.parse_ttl('resources/test/5966411600000001.ttl')
for annot in g.standard_annotations + g.non_standard_annotations:
    if any('5966411600000004' in str(ind) for ind in annot.individuals):
        exts = b.get_extension_edges(annot)
        print(f'annotation has {len(annot.edges)} edges, {len(exts)} extensions:')
        for e in exts:
            print(f'  {e.source_type} -[{e.property_uri}]-> {e.target_type}')
"
```

Expected smoke-test output (order may vary, since it follows `annot.edges` insertion order which depends on rdflib iteration):
```
annotation has 5 edges, 3 extensions:
  http://purl.obolibrary.org/obo/GO_0120045 -[http://purl.obolibrary.org/obo/BFO_0000050]-> http://purl.obolibrary.org/obo/GO_0007605
  http://purl.obolibrary.org/obo/GO_0120045 -[http://purl.obolibrary.org/obo/BFO_0000066]-> http://purl.obolibrary.org/obo/CL_0000202
  http://purl.obolibrary.org/obo/CL_0000202 -[http://purl.obolibrary.org/obo/BFO_0000050]-> http://purl.obolibrary.org/obo/EMAPA_17597
```

**Expected final state:**
- [ ] `resources/test/5966411600000001.ttl` is committed in the test fixtures.
- [ ] `GoCamGraphBuilder` exposes `uri_is_biological_process`, `uri_is_cellular_component`, and `get_extension_edges`.
- [ ] `test_get_extension_edges` passes and asserts the exact 3 extension tuples for the GO:0120045 annotation.
- [ ] No regressions in any other test in `tests/test_gocam_ttl.py`.
- [ ] No CLI, report-format, or split-behavior changes (this is a pure read-side addition).

---

## Notes

- **Why the test searches both annotation lists:** the GO:0120045 annotation may end up in `non_standard_annotations` (e.g., if dates differ across edges and trip the inconsistent-evidence check, or if some edge fails another filter). `get_extension_edges` is descriptive and doesn't care about classification — the test should not couple to it either.

- **Why match by tuple, not bnode ID:** rdflib generates blank-node IDs from a counter that resets per parse, so `_:t1866544` is not guaranteed across runs or rdflib versions. The `(predicate, source_type, target_type)` triple is stable across parses.

- **Why no separate unit tests for the aspect helpers:** the existing convention (`uri_is_molecular_function`, `uri_is_causal_relation`) is to test these via the methods that consume them. The Task 2 sanity-check `python3 -c "..."` one-liner is enough to confirm the helpers wire up to `GoAspector` correctly without growing the test surface.

- **Edge cases the spec notes (lines 82-86) but no dedicated test exercises:**
  - "No backbone matched" → all edges returned. Implicitly covered: if the implementation incorrectly raises on missing backbone, the test parser would crash before reaching the assertions.
  - "Annotation with only an MF backbone" (single-edge MF) → returns `[]`. Not tested directly; the algorithm trivially handles it because Rule 1 marks the lone edge as backbone and the comprehension returns `[]`.
  - "Multiple aspects in one subgraph" → union of backbones. The reference example *is* such a subgraph (it has both MF-enabled_by-GP and MF-part_of-BP backbone matches in one annotation), so the main test already covers this.
  Adding tests for the first two would gold-plate beyond the spec's testing requirement; revisit if a downstream consumer hits a regression.

- **Files worth reading before starting:**
  - `docs/plans/2026-04-30-extension-edges-method-design.md` — the full design doc, especially the walk-through table.
  - `src/gocam_unwinder/gocam_ttl.py:688-698` — the existing `uri_is_molecular_function` helper to copy the pattern from.
  - `src/gocam_unwinder/gocam_ttl.py:717-768` — `filter_out_non_std_annotations`, which is the closest existing example of "iterate edges in a `StandardAnnotation` and check predicate + aspect".