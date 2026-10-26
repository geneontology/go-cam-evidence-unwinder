# De-nest onto the Extension-Start Node — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-point a nested anatomy extension edge onto the backbone individual where its extension chain departs (the "extension starting individual"), inheriting that departing edge's relation, instead of always the lead-aspect primary individual.

**Architecture:** Add one pure helper method `_extension_chain_start()` on `GoCamGraphBuilder` that walks backwards through an annotation's extension edges to the backbone individual the chain attaches to. `plan_nested_anatomy_fixes()` calls it to set each rewrite's new source + relation (replacing the old lead-primary + occurs_in/keep logic). Both existing gates and the both-anatomical filter are untouched. The `--nested-fix-report` gains a `New Source` column.

**Tech Stack:** Python 3, rdflib, ontobio; pytest. Design doc: `docs/plans/2026-06-30-extension-start-node-denesting-design.md`.

## Global Constraints

- **No `git add` / `git commit`** in any step — the user stages and commits manually (per project convention). Each task ends by running tests, not committing.
- Tests require the GO ontology at `target/go_20250601.json` and RO at `resources/test/ro_20250723.owl` (already present in the dev environment).
- Do **not** modify `_anatomy_attachment_is_simple()` or the lead-primary-count gate — scope is "minimal: only change the attach node."
- All existing tests must continue to pass unchanged (the chain-walk reproduces every current fixture's `new_source_uri` / `new_property_uri`).

## Out of Scope

- Dropping the lead-primary-count gate (would expand the fixer to ambiguous-primary annotations such as `57c82fad00000252`).
- Relation composition beyond inheriting the direct extension edge's relation.
- Any GPAD-export changes.

---

## Tasks

### Task 1: Copy the reference fixture and add the `_extension_chain_start` helper

**Files:**
- Create: `resources/test/5fb9cc0600000760.ttl` (copy of `/Users/ebertdu/go/noctua-models/models/5fb9cc0600000760.ttl`)
- Modify: `src/gocam_unwinder/gocam_ttl.py` (insert new method immediately before `plan_nested_anatomy_fixes`, i.e. after `_anatomy_attachment_is_simple` which ends at line 1380)
- Test: `tests/test_gocam_ttl.py`

**Interfaces:**
- Consumes: `self.get_extension_edges(annot)`, `self._backbone_role(edge)`, `StandardAnnotation.edges` (dict of `StandardAnnotationEdge`), each edge's `.source_uri`, `.target_uri`, `.source_type`, `.property_uri`.
- Produces: `_extension_chain_start(self, annot, edge) -> tuple | None`. On success returns `(start_uri: URIRef, start_type: URIRef, relation_uri: URIRef)`; returns `None` when the chain start is ambiguous (a traversed node has ≠1 extension-edge predecessor, a cycle is hit, or no backbone individual is reached). Task 2 consumes this.

- [ ] **Step 1: Copy the fixture into the test corpus**

Run:
```bash
cp /Users/ebertdu/go/noctua-models/models/5fb9cc0600000760.ttl resources/test/5fb9cc0600000760.ttl
```
Expected: `resources/test/5fb9cc0600000760.ttl` exists (140 lines).

- [ ] **Step 2: Write the failing helper unit test**

Add to `tests/test_gocam_ttl.py` (near the other planner tests, e.g. after `test_plan_nested_anatomy_fixes_warn_param` around line 1665). `OCCURS_IN`, `PART_OF`, `_annotation_with_individual`, and the `builder` fixture already exist in this file.

```python
def test_extension_chain_start(builder):
    """_extension_chain_start walks back to the backbone individual where the
    extension chain departs, inheriting the relation of the edge that leaves it.

    5fb9cc0600000760 (BP-led): the nested `mvb -part_of-> late endosome` chain
    departs from the ROOT MF (...761), NOT the lead-aspect BP primary (...764),
    via occurs_in.

    cc_nested_anatomy_example (CC-led): the chain departs from the CC individual
    (cc1), NOT the GP, via part_of -- the case a boundary-edge definition gets
    wrong because the CC is itself anatomical.
    """
    # BP-led, chain start != lead primary
    gocam = builder.parse_ttl("resources/test/5fb9cc0600000760.ttl")
    mvb = rdflib.term.URIRef(
        "http://model.geneontology.org/5fb9cc0600000760/5fb9cc0600000766")
    annot = _annotation_with_individual(gocam, mvb)
    assert annot is not None
    nested = next(e for e in annot.edges.values()
                  if str(e.source_type) == "http://purl.obolibrary.org/obo/GO_0005771"
                  and str(e.target_type) == "http://purl.obolibrary.org/obo/GO_0005770")
    start = builder._extension_chain_start(annot, nested)
    assert start is not None
    start_uri, start_type, relation = start
    assert str(start_uri) == \
        "http://model.geneontology.org/5fb9cc0600000760/5fb9cc0600000761"
    assert str(start_type) == "http://purl.obolibrary.org/obo/GO_0003674"
    assert str(relation) == OCCURS_IN

    # CC-led, chain start is the CC (cc1), NOT the GP
    cc = builder.parse_ttl("resources/test/cc_nested_anatomy_example.ttl")
    cc_annot = (cc.standard_annotations + cc.non_standard_annotations)[0]
    nested_cc = next(
        e for e in cc_annot.edges.values()
        if str(e.source_type) == "http://purl.obolibrary.org/obo/CL_0000202"
        and str(e.target_type) == "http://purl.obolibrary.org/obo/EMAPA_17597")
    start_cc = builder._extension_chain_start(cc_annot, nested_cc)
    assert start_cc is not None
    assert str(start_cc[0]) == \
        "http://model.geneontology.org/cc_nested_anatomy_example/cc1"
    assert str(start_cc[2]) == PART_OF
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest tests/test_gocam_ttl.py::test_extension_chain_start -v`
Expected: FAIL with `AttributeError: 'GoCamGraphBuilder' object has no attribute '_extension_chain_start'`

- [ ] **Step 4: Implement `_extension_chain_start`**

Insert this method in `src/gocam_unwinder/gocam_ttl.py` immediately before `def plan_nested_anatomy_fixes` (currently line 1382):

```python
    def _extension_chain_start(self, annot: StandardAnnotation,
                               edge: StandardAnnotationEdge):
        """
        Walk the extension chain backwards from `edge.source_uri` to the backbone
        individual where the chain departs from the backbone.

        A backbone individual is any endpoint of a backbone edge (_backbone_role
        is not None). The relation returned is the property of the *direct
        extension edge* -- the edge that leaves the backbone individual toward the
        chain (the last edge the walk traverses).

        Returns (start_uri, start_type, relation_uri) on success, or None when the
        chain start is ambiguous: a traversed node has != 1 extension-edge
        predecessor, a cycle is hit, or no backbone individual is reached.
        """
        extension_edges = self.get_extension_edges(annot)
        backbone_individuals = set()
        uri_to_type = {}
        for e in annot.edges.values():
            uri_to_type[e.source_uri] = e.source_type
            uri_to_type[e.target_uri] = e.target_type
            if self._backbone_role(e) is not None:
                backbone_individuals.add(e.source_uri)
                backbone_individuals.add(e.target_uri)

        current = edge.source_uri
        relation = None
        visited = set()
        while current not in backbone_individuals:
            if current in visited:
                return None  # cycle
            visited.add(current)
            preds = [e for e in extension_edges if e.target_uri == current]
            if len(preds) != 1:
                return None  # ambiguous / dead-end
            relation = preds[0].property_uri
            current = preds[0].source_uri
        if relation is None:
            return None  # edge.source_uri was already a backbone node
        return current, uri_to_type.get(current), relation
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_gocam_ttl.py::test_extension_chain_start -v`
Expected: PASS

---

### Task 2: Wire the chain-walk into `plan_nested_anatomy_fixes`

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py:1419-1444` (the tail of `plan_nested_anatomy_fixes`: the unused `primary_individual` assignment and the per-edge loop)
- Test: `tests/test_gocam_ttl.py`

**Interfaces:**
- Consumes: `self._extension_chain_start(annot, edge)` from Task 1.
- Produces: instruction dicts gain a new key `new_source_type`; `new_source_uri` and `new_property_uri` now come from the chain-walk. Task 3 (report) consumes `new_source_type`.

- [ ] **Step 1: Write the failing planner test**

Add to `tests/test_gocam_ttl.py` after `test_plan_nested_anatomy_fixes_cc` (around line 1655):

```python
def test_plan_nested_anatomy_fixes_chain_start_differs_from_primary(builder):
    """5fb9cc0600000760: BP-led, but the nested `mvb -part_of-> late endosome`
    edge's extension chain departs from the ROOT MF, not the BP primary. The one
    planned rewrite re-points onto the root MF (...761) with occurs_in, NOT onto
    the lead-aspect BP primary (...764)."""
    gocam = builder.parse_ttl("resources/test/5fb9cc0600000760.ttl")
    plan = builder.plan_nested_anatomy_fixes(gocam)

    assert len(plan) == 1, f"Expected 1 rewrite, got {len(plan)}"
    r = plan[0]
    assert r["lead_aspect"] == "BP"
    assert str(r["old_source_uri"]) == \
        "http://model.geneontology.org/5fb9cc0600000760/5fb9cc0600000766"  # mvb
    assert str(r["target_uri"]) == \
        "http://model.geneontology.org/5fb9cc0600000760/5fb9cc0600000768"  # late endosome
    assert str(r["new_source_uri"]) == \
        "http://model.geneontology.org/5fb9cc0600000760/5fb9cc0600000761"  # root MF
    assert str(r["new_property_uri"]) == OCCURS_IN
    assert str(r["new_source_type"]) == "http://purl.obolibrary.org/obo/GO_0003674"

    # The chain start is NOT the lead-aspect (BP) primary individual.
    annot = _annotation_with_individual(
        gocam, rdflib.term.URIRef(r["new_source_uri"]))
    bp_primary = builder.get_primary_individuals(annot)["BP"][0]
    assert str(bp_primary) == \
        "http://model.geneontology.org/5fb9cc0600000760/5fb9cc0600000764"
    assert r["new_source_uri"] != bp_primary
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_gocam_ttl.py::test_plan_nested_anatomy_fixes_chain_start_differs_from_primary -v`
Expected: FAIL — `new_source_uri` is currently the BP primary `...764` (assertion mismatch), and `new_source_type` KeyError (key not yet added).

- [ ] **Step 3: Replace the source/relation computation in the loop**

In `src/gocam_unwinder/gocam_ttl.py`, replace this block (currently lines 1419-1444):

```python
            primary_individual = primary_individuals[0]
            # `lead` came from find_nested_extensions -> pick_lead_aspect over
            # get_primary_go_terms, so it is guaranteed to be a present key here.
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
```

with:

```python
            # `lead` came from find_nested_extensions -> pick_lead_aspect over
            # get_primary_go_terms, so it is guaranteed to be a present key here.
            primary_term = self.get_primary_go_terms(annot)[lead][0]
            for edge in nested:
                if not (self._is_anatomical_structure(edge.source_type)
                        and self._is_anatomical_structure(edge.target_type)):
                    continue
                # Re-point onto the backbone individual where this extension chain
                # departs (the "extension starting individual"), inheriting the
                # relation of the edge leaving it -- NOT the lead-aspect primary.
                start = self._extension_chain_start(annot, edge)
                if start is None:
                    if warn:
                        print(f"WARNING: skipping nested edge in {gocam.model_id} "
                              f"({gocam.title}) — ambiguous extension chain start")
                    continue
                new_source_uri, new_source_type, new_property = start
                plan.append({
                    "model_id": gocam.model_id,
                    "title": gocam.title,
                    "lead_aspect": lead,
                    "primary_term": primary_term,
                    "bnode_id": edge.bnode_id,
                    "old_source_uri": edge.source_uri,
                    "old_property_uri": edge.property_uri,
                    "target_uri": edge.target_uri,
                    "new_source_uri": new_source_uri,
                    "new_property_uri": new_property,
                    "new_source_type": new_source_type,
                    "old_source_type": edge.source_type,
                    "target_type": edge.target_type,
                })
```

Note: the `primary_individuals` list and its `len(...) != 1` skip (just above this block) are **kept** — only the now-unused `primary_individual = primary_individuals[0]` line is removed. Also update the method docstring's returns line to add `new_source_type` and its "re-point onto ... primary individual" wording to "re-point onto the extension-start individual."

- [ ] **Step 4: Run the new test to verify it passes**

Run: `pytest tests/test_gocam_ttl.py::test_plan_nested_anatomy_fixes_chain_start_differs_from_primary -v`
Expected: PASS

- [ ] **Step 5: Run the existing planner + gate tests for regressions**

Run:
```bash
pytest tests/test_gocam_ttl.py -v -k "plan_nested_anatomy_fixes or anatomy_attachment or rewrite_edge or get_primary_individuals"
```
Expected: All PASS — `test_plan_nested_anatomy_fixes_bp/mf/cc` still get `new_source_uri` = `...0004`/`mf1`/`cc1` and relations `occurs_in`/`occurs_in`/`part_of` (the chain-walk reproduces them), and `test_plan_nested_anatomy_fixes_skips_multi_boundary` / `test_plan_nested_anatomy_fixes_warn_param` are unaffected.

---

### Task 3: Add the `New Source` column to `--nested-fix-report`

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py:1968-1981` (the nested-fix-report writer in `main()`)
- Test: `tests/test_gocam_ttl.py`

**Interfaces:**
- Consumes: `rec["new_source_type"]` from Task 2's instruction dicts; `go_cam_graph_builder.term_label(...)`.
- Produces: report header `[..., "Target", "New Source", "New Relation"]`.

- [ ] **Step 1: Write the failing report test**

Add to `tests/test_gocam_ttl.py` (near the other `main()` end-to-end tests, e.g. after `test_report_file_basic_with_flag` around line 1919). It runs `--fix-nested-anatomy` on the single new fixture and checks the report header + the one row.

```python
def test_nested_fix_report_has_new_source_column(builder, tmp_path, monkeypatch):
    """--nested-fix-report gains a `New Source` column between Target and New
    Relation. For 5fb9cc0600000760 the New Source is the root MF's term
    (GO:0003674), distinct from the Primary Term (axis elongation, GO:0003401).
    Expected label strings are resolved via the builder so the test is immune to
    underscore-vs-space label rendering."""
    report = tmp_path / "nested_fixes.tsv"
    outdir = tmp_path / "out"
    monkeypatch.setattr("gocam_unwinder.gocam_ttl.GoCamGraphBuilder",
                        lambda *a, **k: builder)
    monkeypatch.setattr(sys, "argv", [
        "gocam_ttl.py", "-m", "resources/test/5fb9cc0600000760.ttl",
        "-o", "target/go_20250601.json",
        "-r", "resources/test/ro_20250723.owl",
        "--no-label-api",
        "--fix-nested-anatomy",
        "--output-dir", str(outdir),
        "--nested-fix-report", str(report),
    ])

    gocam_ttl.main()

    lines = [ln.rstrip("\n") for ln in report.read_text().splitlines() if ln.strip()]
    header = lines[0].split("\t")
    assert header == ["Model ID", "Title", "Lead Aspect", "Primary Term",
                      "Old Source", "Old Relation", "Target",
                      "New Source", "New Relation"]
    row = dict(zip(header, lines[1].split("\t")))
    root_mf_label = builder.term_label(
        rdflib.term.URIRef("http://purl.obolibrary.org/obo/GO_0003674"))
    axis_label = builder.term_label(
        rdflib.term.URIRef("http://purl.obolibrary.org/obo/GO_0003401"))
    occurs_in_label = builder.term_label(rdflib.term.URIRef(OCCURS_IN))
    assert row["New Source"] == root_mf_label       # root MF, the chain start
    assert row["New Relation"] == occurs_in_label   # BFO:0000066
    assert row["Primary Term"] == axis_label        # GO:0003401, still context
    assert row["New Source"] != row["Primary Term"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_gocam_ttl.py::test_nested_fix_report_has_new_source_column -v`
Expected: FAIL — current header has no `New Source` column (assertion mismatch on `header`).

- [ ] **Step 3: Add the column to the report writer**

In `src/gocam_unwinder/gocam_ttl.py`, replace the nested-fix-report block (currently lines 1968-1981):

```python
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

with:

```python
    if args.nested_fix_report and all_nested_fix_records:
        with open(args.nested_fix_report, 'w') as nfr_file:
            nfr_headers = ["Model ID", "Title", "Lead Aspect", "Primary Term",
                           "Old Source", "Old Relation", "Target",
                           "New Source", "New Relation"]
            print("\t".join(nfr_headers), file=nfr_file)
            for rec in all_nested_fix_records:
                primary_label = go_cam_graph_builder.term_label(rec["primary_term"]) if rec["primary_term"] else ""
                old_source_label = go_cam_graph_builder.term_label(rec["old_source_type"]) if rec["old_source_type"] else ""
                old_rel_label = go_cam_graph_builder.term_label(rec["old_property_uri"]) if rec["old_property_uri"] else ""
                target_label = go_cam_graph_builder.term_label(rec["target_type"]) if rec["target_type"] else ""
                new_source_label = go_cam_graph_builder.term_label(rec["new_source_type"]) if rec.get("new_source_type") else ""
                new_rel_label = go_cam_graph_builder.term_label(rec["new_property_uri"]) if rec["new_property_uri"] else ""
                cols = [rec["model_id"], rec["title"], rec["lead_aspect"], primary_label,
                        old_source_label, old_rel_label, target_label,
                        new_source_label, new_rel_label]
                print("\t".join(cols), file=nfr_file)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_gocam_ttl.py::test_nested_fix_report_has_new_source_column -v`
Expected: PASS

---

### Task 4: Extend the remainders `Fixable` test and run the full suite

**Files:**
- Modify: `tests/test_gocam_ttl.py:1943-1995` (`test_remainders_report_fixable_column`)

**Interfaces:**
- Consumes: the new `resources/test/5fb9cc0600000760.ttl` fixture (already scanned by the test's `-d resources/test/` invocation) and `_find_remainders_row`.

- [ ] **Step 1: Add the new assertion to the existing remainders test**

In `tests/test_gocam_ttl.py`, inside `test_remainders_report_fixable_column`, add after the existing `complex_row` assertion (currently ending line 1995):

```python
    # New: chain-start != lead primary. 5fb9cc0600000760's nested
    # `multivesicular body -part of-> late endosome` is fixable (re-pointed onto
    # the root MF); its remainders row reads Fixable=Yes.
    chain_row = _find_remainders_row(
        rows, "5fb9cc0600000760", "multivesicular body", "part of", "late endosome")
    assert chain_row["Fixable"] == "Yes"
```

- [ ] **Step 2: Run the remainders test to verify it passes**

Run: `pytest tests/test_gocam_ttl.py::test_remainders_report_fixable_column -v`
Expected: PASS (the fixture is picked up by the `-d resources/test/` scan; the new row is `Fixable=Yes`).

- [ ] **Step 3: Run the full suite for regressions**

Run: `pytest -v`
Expected: All tests PASS — including every pre-existing test in `tests/test_gocam_ttl.py`.

---

## Verification

After all tasks are complete:

```bash
# Full test suite
pytest -v

# Manual smoke test: de-nest the reference model and inspect the rewrite
python src/gocam_unwinder/gocam_ttl.py \
  -m resources/test/5fb9cc0600000760.ttl \
  -o target/go_20250601.json \
  -r resources/test/ro_20250723.owl \
  --fix-nested-anatomy \
  --output-dir /tmp/nested_fix_out \
  --nested-fix-report /tmp/nested_fixes.tsv
cat /tmp/nested_fixes.tsv
grep -n "5fb9cc0600000768" /tmp/nested_fix_out/5fb9cc0600000760.ttl
```

**Expected final state:**
- [ ] All tests pass, no regressions.
- [ ] `--fix-nested-anatomy` on `5fb9cc0600000760` rewrites `multivesicular body ─part_of→ late endosome` to `root MF (…761) ─occurs_in→ late endosome (…768)` — i.e. the output TTL has `…761 <BFO_0000066> …768` and the axiom bnode's `annotatedSource` is `…761` (not the BP `…764`).
- [ ] `nested_fixes.tsv` has a `New Source` column reading `molecular_function` for that row, with `Primary Term` = `axis elongation`.
- [ ] `test_plan_nested_anatomy_fixes_bp/mf/cc` are unchanged and passing (chain-walk reproduces their outputs).

---

## Notes

- **Why chain-walk, not the anatomy-region boundary edge:** in CC-led annotations the primary CC is itself anatomical, so it is absorbed into the anatomy region and the region's boundary edge is `GP ─located_in→ CC` — a boundary-based attach would wrongly pick the GP. The chain-walk stops at the CC (a backbone individual). See the design doc's "Why a chain-walk" section.
- **Both gates stay:** `_anatomy_attachment_is_simple` and the `len(primary_individuals) != 1` skip are unchanged; only the attach target changes. `57c82fad00000252`'s nucleus edge therefore stays `Fixable=No`.
- **The `_extension_chain_start` `None` return** (ambiguous chain) is defensive: past `_anatomy_attachment_is_simple` and the both-anatomical filter, a well-formed single-attachment chain resolves uniquely. `None` just skips that one edge with a warning.
- Related code to read first: `src/gocam_unwinder/gocam_ttl.py:1217-1445` (`_backbone_role`, `get_extension_edges`, `get_primary_individuals`, `_anatomy_attachment_is_simple`, `plan_nested_anatomy_fixes`) and `find_nested_extensions` at `:149-167`.
