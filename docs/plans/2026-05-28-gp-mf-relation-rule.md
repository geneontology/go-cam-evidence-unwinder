# GP↔MF Relation Rule Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a new standard-annotation filter check that flags any annotation whose Gene Product ↔ Molecular Function edge uses a predicate other than `enables` (TTL: `enabled_by`) or `contributes to` (`RO:0002326`).

**Architecture:** Add a fifth, independent check (`invalid_gp_mf_relation`) to `GoCamGraphBuilder.filter_out_non_std_annotations`, parallel to the existing four checks. Add a small helper `_resolve_mf_type()` to recognize MF type both directly and through an `owl:complementOf` class expression (the NOT-qualifier representation), so NOT-enables still detects MF correctly.

**Tech Stack:** Python 3, `rdflib`, `ontobio` (already in the project). No new dependencies.

**Affected Areas:** Filter logic in `src/gocam_unwinder/gocam_ttl.py` (`filter_out_non_std_annotations` and a new helper); tests in `tests/test_gocam_ttl.py`; new test TTL fixtures in `resources/test/`.

---

## Context

This is the first rule from a new internal list of standard-annotation criteria the GO team is rolling out. The list refines what counts as a "standard annotation" beyond the current four checks. This plan implements the first row of that list:

> **Type of edge — Relation between Gene Product and MF**
> Rule: one of `enables` OR `contributes to` (the negation qualifier `NOT` allowed).

In GO-CAM TTL:
- `enables` is stored as the inverse `enabled_by` on the MF individual (`MF -enabled_by-> GP`).
- `contributes to` is stored directly as `GP -RO:0002326-> MF`.
- The `NOT` qualifier is encoded on the *MF individual's type* as an `owl:complementOf` class expression, e.g.
  ```
  <mf_indiv> rdf:type [ rdf:type owl:Class ; owl:complementOf <GO_xxxxxxx> ] .
  ```
  rather than altering the predicate. So the rule's predicate check is unchanged by NOT, but MF-type detection must look through `owl:complementOf`.

The implementation establishes the pattern (check name, edge-bnode tracking, label resolution) that the remaining rules in the list will follow.

## Constraints

- Must not change classification for any existing test model. `MGI_MGI_1100089`, `5b318d0900000481`, `5966411600000001`, `61452e3d00000323`, and the rest must stay in their current `standard` vs `non_standard` buckets.
- Must integrate cleanly with `print_non_standard_annotation_failed_checks()` — i.e., the new check name surfaces in the TSV report with predicate/source/target labels resolved via `term_label()`, with no edits to the reporter.
- No new CLI flag, no new report column. (Aggregate stats columns can be added when later rules are wired in.)
- No new external dependencies.

## Out of Scope

- The other rules in the new criteria list (Relation between MF and BP, Relation between GP and CC, etc.). Each will be its own plan.
- A real `uri_is_gene_product()` helper. The check uses a "not GO/RO/BFO" CURIE heuristic for the GP endpoint. A proper helper can come later if a future rule needs it.
- Changes to `get_extension_edges()` or `get_primary_go_terms()`. The new check is its own loop; it does not redefine the "backbone."
- CLI flag for opting in/out of the new check. The check always runs.

---

## Tasks

### Task 1: Verify TTL representation of `contributes_to` and gather/synthesize fixtures

**Files:**
- Inspect (no edits): `resources/test/*.ttl`
- Possibly create: `resources/test/contributes_to_example.ttl`
- Possibly create: `resources/test/invalid_gp_mf_relation_example.ttl`

**Step 1: Confirm RO:0002326 URI form**

Run:
```
grep -rn "RO_0002326\|contributes_to\|contributes to" resources/test/ src/
```

Expected: Either the URI `<http://purl.obolibrary.org/obo/RO_0002326>` appears in at least one model, or no hits (need to synthesize).

**Step 2: Confirm `relations.lookup_label("contributes to")` behavior** (already verified at plan-write time)

Sanity check (idempotent — re-run inside the project venv before implementing):
```
source env/bin/activate
python -c "from ontobio.rdfgen import relations; print(relations.lookup_label('contributes to'))"
```

Expected: `http://purl.obolibrary.org/obo/RO_0002326`. (Confirmed when this plan was written.) Use `URIRef(relations.lookup_label("contributes to"))` in Task 3 — same style as the surrounding code (`enabled_by`, `part_of`, `located_in`, `is_active_in`).

**Step 3: Acquire or synthesize fixtures**

- If a real model already contains a `GP -RO:0002326-> MF` axiom, copy/trim it as `resources/test/contributes_to_example.ttl`.
- Otherwise, copy `resources/test/MGI_MGI_1100089.ttl` and modify one annotation block so that the GP–MF edge is `GP RO:0002326 MF` (direct direction) in place of `MF enabled_by GP`. Save as `resources/test/contributes_to_example.ttl`. Keep evidence intact so the annotation passes other checks.
- Copy `contributes_to_example.ttl` and swap `RO:0002326` for a clearly-disallowed relation between GP and MF (e.g., `RO:0002233` — "has input"). Save as `resources/test/invalid_gp_mf_relation_example.ttl`.

**Step 4: Manually parse each fixture once**

Run:
```
python src/gocam_unwinder/gocam_ttl.py -m resources/test/contributes_to_example.ttl -o target/go_20250601.json
python src/gocam_unwinder/gocam_ttl.py -m resources/test/invalid_gp_mf_relation_example.ttl -o target/go_20250601.json
```

Expected: both parse without error. (Standard/non-standard categorization at this point may not yet reflect the new rule — that's expected; the rule is added in Task 3.)

---

### Task 2: Add `_resolve_mf_type()` helper

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py` (add method on `GoCamGraphBuilder`, near `uri_is_molecular_function` ~line 690)
- Test: `tests/test_gocam_ttl.py`

**Step 1: Write the failing test**

```python
def test_resolve_mf_type_direct_uri():
    builder = GoCamGraphBuilder("target/go_20250601.json")
    mf_uri = rdflib.URIRef("http://purl.obolibrary.org/obo/GO_0004672")  # protein kinase activity
    assert builder._resolve_mf_type(mf_uri) == mf_uri


def test_resolve_mf_type_non_mf_uri_returns_none():
    builder = GoCamGraphBuilder("target/go_20250601.json")
    bp_uri = rdflib.URIRef("http://purl.obolibrary.org/obo/GO_0006954")  # inflammatory response (BP)
    assert builder._resolve_mf_type(bp_uri) is None


def test_resolve_mf_type_complement_of_mf():
    # Parse a TTL graph that contains [ owl:Class ; owl:complementOf <MF_URI> ]
    builder = GoCamGraphBuilder("target/go_20250601.json")
    g = rdflib.Graph()
    g.parse(data='''
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
        _:mf rdf:type [ rdf:type owl:Class ;
                        owl:complementOf <http://purl.obolibrary.org/obo/GO_0004672> ] .
    ''', format="ttl")
    bnode = next(g.objects(rdflib.BNode("mf"), rdflib.RDF.type))
    # Caller passes the bnode type; helper needs access to the graph.
    resolved = builder._resolve_mf_type(bnode, g)
    assert resolved == rdflib.URIRef("http://purl.obolibrary.org/obo/GO_0004672")
```

Note: the helper signature accepts `(type_node, graph)` so it can introspect the graph for `owl:complementOf`. Calls from inside `filter_out_non_std_annotations` will pass `go_cam_graph.g`.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_gocam_ttl.py::test_resolve_mf_type_complement_of_mf -v`
Expected: FAIL with `AttributeError: 'GoCamGraphBuilder' object has no attribute '_resolve_mf_type'`

**Step 3: Write minimal implementation**

In `src/gocam_unwinder/gocam_ttl.py`, near `uri_is_molecular_function` (~line 690):

```python
def _resolve_mf_type(self, type_node, graph):
    """
    Resolve `type_node` to an underlying MF URI, treating an
    `owl:complementOf <GO_xxxxxxx>` class expression as the wrapped GO term.

    Returns the MF URIRef if `type_node` is (or wraps) a molecular function;
    None otherwise.
    """
    if isinstance(type_node, rdflib.URIRef):
        return type_node if self.uri_is_molecular_function(type_node) else None
    if isinstance(type_node, rdflib.BNode):
        # Look for: type_node owl:complementOf ?wrapped
        for wrapped in graph.objects(type_node, rdflib.OWL.complementOf):
            if isinstance(wrapped, rdflib.URIRef) and self.uri_is_molecular_function(wrapped):
                return wrapped
        return None
    return None
```

(`rdflib.OWL` may need an `from rdflib.namespace import OWL` import if not already present.)

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gocam_ttl.py::test_resolve_mf_type_direct_uri tests/test_gocam_ttl.py::test_resolve_mf_type_non_mf_uri_returns_none tests/test_gocam_ttl.py::test_resolve_mf_type_complement_of_mf -v`
Expected: PASS

---

### Task 3: Add `invalid_gp_mf_relation` check to `filter_out_non_std_annotations`

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py:819-866` (the `filter_out_non_std_annotations` body)
- Test: `tests/test_gocam_ttl.py`

**Step 1: Write the failing tests**

```python
def test_gp_mf_relation_allows_enables():
    builder = GoCamGraphBuilder("target/go_20250601.json")
    gocam = builder.parse_ttl("resources/test/MGI_MGI_1100089.ttl")
    for annot in gocam.standard_annotations + gocam.non_standard_annotations:
        assert "invalid_gp_mf_relation" not in annot.failed_checks, (
            f"enabled_by-based annotation incorrectly flagged: {annot.failed_checks}"
        )


def test_gp_mf_relation_allows_contributes_to():
    builder = GoCamGraphBuilder("target/go_20250601.json")
    gocam = builder.parse_ttl("resources/test/contributes_to_example.ttl")
    # The contributes_to annotation should not be flagged for invalid_gp_mf_relation
    flagged = [
        a for a in gocam.non_standard_annotations
        if "invalid_gp_mf_relation" in a.failed_checks
    ]
    assert flagged == [], f"contributes_to incorrectly flagged: {flagged}"


def test_gp_mf_relation_rejects_other_predicate():
    builder = GoCamGraphBuilder("target/go_20250601.json")
    gocam = builder.parse_ttl("resources/test/invalid_gp_mf_relation_example.ttl")
    flagged_edges = set()
    for a in gocam.non_standard_annotations:
        flagged_edges |= a.failed_checks.get("invalid_gp_mf_relation", set())
    assert len(flagged_edges) >= 1, (
        "Annotation using a non-allowed GP↔MF predicate should be flagged"
    )
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_gocam_ttl.py::test_gp_mf_relation_rejects_other_predicate -v`
Expected: FAIL with `assert 0 >= 1` (check not yet implemented).

**Step 3: Write minimal implementation**

In `src/gocam_unwinder/gocam_ttl.py`, inside `filter_out_non_std_annotations`, after Check 4 (`edge_without_evidence`), add:

```python
            # Check 5: GP↔MF edge must use `enabled_by` (MF as source) or
            # `contributes to` (RO:0002326, MF as target). NOT-qualified MFs
            # are recognized via owl:complementOf class expressions.
            enabled_by = URIRef(relations.lookup_label("enabled by"))
            contributes_to = URIRef(relations.lookup_label("contributes to"))
            for edge in std_annot.edges.values():
                src_mf = self._resolve_mf_type(edge.source_type, go_cam_graph.g)
                tgt_mf = self._resolve_mf_type(edge.target_type, go_cam_graph.g)
                # Skip non-MF edges and MF↔MF edges (the latter is mf_causal_mf's domain)
                if (src_mf is None) == (tgt_mf is None):
                    continue
                # Identify the "other" endpoint and confirm it's a presumed GP
                if src_mf is not None:
                    other_type = edge.target_type
                    mf_on = "source"
                else:
                    other_type = edge.source_type
                    mf_on = "target"
                if not isinstance(other_type, URIRef):
                    continue
                other_curie_list = curie_util.contract_uri(str(other_type))
                if other_curie_list:
                    prefix = other_curie_list[0].split(":", 1)[0]
                    if prefix in {"GO", "RO", "BFO"}:
                        continue
                # OK, this is a GP↔MF edge. Validate predicate by orientation.
                if mf_on == "source" and edge.property_uri == enabled_by:
                    continue
                if mf_on == "target" and edge.property_uri == contributes_to:
                    continue
                failed_checks.setdefault("invalid_gp_mf_relation", set()).add(edge.bnode_id)
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gocam_ttl.py -k "gp_mf_relation" -v`
Expected: PASS — all three tests green.

**Step 5: Run full test suite for regressions**

Run: `pytest -v`
Expected: All previously-passing tests still pass. In particular `test_gocam_ttl`, `test_multi_edge_evidence_grouping`, `test_mf_causal_mf_filtering`, `test_get_extension_edges`, `test_edge_without_evidence_filter`, `test_print_non_standard_annotation_failed_checks*` should be unchanged.

---

### Task 4: Verify TSV reporter surfaces the new check name correctly

**Files:**
- No production code change expected.
- Test: `tests/test_gocam_ttl.py`

**Step 1: Write the failing test**

```python
def test_print_non_standard_annotation_failed_checks_includes_gp_mf_relation(tmp_path):
    builder = GoCamGraphBuilder("target/go_20250601.json")
    gocam = builder.parse_ttl("resources/test/invalid_gp_mf_relation_example.ttl")
    out = tmp_path / "report.tsv"
    with open(out, "w") as fh:
        builder.print_non_standard_annotation_failed_checks(gocam, fh)
    contents = out.read_text()
    assert "invalid_gp_mf_relation" in contents
```

**Step 2: Run test to verify it passes**

Run: `pytest tests/test_gocam_ttl.py::test_print_non_standard_annotation_failed_checks_includes_gp_mf_relation -v`
Expected: PASS (no code change should be required — the reporter is check-name-agnostic).

If FAIL: inspect `print_non_standard_annotation_failed_checks` for any hard-coded check-name allowlist. If present, extend it; otherwise treat the failure as a real bug in this plan and stop.

---

## Verification

After all tasks are complete, run these commands to confirm everything works:

```bash
# Full test suite
pytest -v

# Manual smoke test on the example fixtures
python src/gocam_unwinder/gocam_ttl.py \
  -m resources/test/invalid_gp_mf_relation_example.ttl \
  -o target/go_20250601.json \
  -r target/ro_current.owl \
  --criteria-fail-report /tmp/failures.tsv
grep invalid_gp_mf_relation /tmp/failures.tsv

# Confirm an unmodified model is unchanged
python src/gocam_unwinder/gocam_ttl.py \
  -m resources/test/MGI_MGI_1100089.ttl \
  -o target/go_20250601.json \
  --criteria-fail-report /tmp/baseline.tsv
! grep -q invalid_gp_mf_relation /tmp/baseline.tsv
```

**Expected final state:**
- [ ] All tests pass
- [ ] No regressions: pre-existing tests still in their original standard/non-standard buckets
- [ ] Models with `enabled_by` GP→MF edges are not flagged by the new check
- [ ] Models with `RO:0002326` (contributes_to) GP→MF edges are not flagged by the new check
- [ ] Models with any other predicate between GP and MF land in `non_standard_annotations` with the offending edge in `failed_checks["invalid_gp_mf_relation"]`
- [ ] `--criteria-fail-report` output contains `invalid_gp_mf_relation` rows with resolved labels (not raw URIs) for predicate/source/target

---

## Notes

- The "other end is a presumed GP" test uses CURIE-prefix exclusion (`not in {GO, RO, BFO}`). This is intentionally coarse for this iteration. A future plan can introduce `uri_is_gene_product()` once a rule actually requires positive GP identification.
- `_resolve_mf_type()` lives on `GoCamGraphBuilder` because it needs ontology access via `uri_is_molecular_function()`. It also accepts the `rdflib.Graph` so it can look up `owl:complementOf` for bnode types.
- The MF↔MF edges (both endpoints MF) are intentionally skipped by Check 5; `mf_causal_mf` (Check 3) owns that case.
- Future rules from the same list (MF↔BP relation, GP↔CC relation, evidence types, etc.) will follow the same pattern: add a check block in `filter_out_non_std_annotations`, name it `<thing>_<violation>`, record offending edge bnodes in `failed_checks`. The reporter requires no edits per rule.
- Related code worth reading before implementing: `src/gocam_unwinder/gocam_ttl.py:718-758` (`get_extension_edges` for the backbone-vs-extension intuition) and `src/gocam_unwinder/gocam_ttl.py:819-870` (current filter check structure).
