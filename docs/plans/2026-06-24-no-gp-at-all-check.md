# `no_gp_at_all` Standard-Annotation Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a standard-annotation criteria check (`no_gp_at_all`) that flags any annotation whose subgraph contains no gene product, reported through the criteria-failure report and counted in the extended stats report.

**Architecture:** A new check (#14) in `GoCamGraphBuilder.filter_out_non_std_annotations` reusing the existing `_gene_product_namespace_key` + `GP_NAMESPACE_KEYS` allowlist; `no_gp_at_all` added to the module-level `CHECK_NAMES`. Four minimal relation-test fixtures gain a GP backbone so their passing annotation stays standard; one new synthetic fixture exercises the check.

**Tech Stack:** Python 3, `rdflib`, `pytest`.

**Design doc:** `docs/plans/2026-06-24-no-gp-at-all-check-design.md`

> **Commits:** This repo's owner stages and commits manually. Do **not** run `git add` or `git commit`. Where this plan says "Checkpoint," run the listed verification and stop for the owner to review/commit.

---

## File Structure

- **Modify** `src/gocam_unwinder/gocam_ttl.py`:
  - Add `"no_gp_at_all"` to the `CHECK_NAMES` tuple (after `"enabler_not_gp"`, line 182).
  - Add check #14 in `filter_out_non_std_annotations`, just before `std_annot.failed_checks = failed_checks` (line 1538).
- **Create** `resources/test/no_gp_at_all_example.ttl` (synthetic fixture: one no-GP annotation + one GP-bearing annotation).
- **Modify** four fixtures to add a GP backbone to their standard annotation: `resources/test/mf_cc_relation_example.ttl`, `mf_bp_relation_example.ttl`, `bp_cc_relation_example.ttl`, `mf_occurs_in_anatomy_example.ttl`.
- **Modify** `tests/test_gocam_ttl.py`: append new test functions.
- **Modify** `CLAUDE.md`: document check #14.

---

## Task 1: Add the `no_gp_at_all` check + fixture (TDD)

Implements the check and the CHECK_NAMES entry, tested against a dedicated fixture. After this task the four minimal relation fixtures' standard annotations correctly flip to non-standard (Task 2 restores them); no existing test breaks because they assert on specific check keys.

**Files:**
- Create: `resources/test/no_gp_at_all_example.ttl`
- Modify: `src/gocam_unwinder/gocam_ttl.py` (`CHECK_NAMES` line 182; check #14 before line 1538)
- Test: `tests/test_gocam_ttl.py`

- [ ] **Step 1: Create the fixture `resources/test/no_gp_at_all_example.ttl`**

```turtle
<http://model.geneontology.org/no_gp_at_all_example> a <http://www.w3.org/2002/07/owl#Ontology> ;
	<http://geneontology.org/lego/modelstate> "production" ;
	<http://purl.org/dc/elements/1.1/title> "no_gp_at_all check fixture" ;
	<http://purl.org/pav/providedBy> "http://informatics.jax.org" .

<http://geneontology.org/lego/evidence> a <http://www.w3.org/2002/07/owl#AnnotationProperty> .
<http://purl.org/dc/elements/1.1/date> a <http://www.w3.org/2002/07/owl#AnnotationProperty> .

# Annotation A (NO GP): root-MF -is_active_in-> CC  (valid relation; only failure is no_gp_at_all)
<http://model.geneontology.org/no_gp_at_all_example/mfA>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/GO_0003674> ;
	<http://purl.obolibrary.org/obo/RO_0002432> <http://model.geneontology.org/no_gp_at_all_example/ccA> .
<http://model.geneontology.org/no_gp_at_all_example/ccA>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/GO_0005634> .
<http://model.geneontology.org/no_gp_at_all_example/evA>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/no_gp_at_all_example/mfA> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/RO_0002432> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/no_gp_at_all_example/ccA> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/no_gp_at_all_example/evA> .

# Annotation B (HAS GP): MF -enabled_by-> MGI GP  (standard; not flagged)
<http://model.geneontology.org/no_gp_at_all_example/mfB>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/GO_0004672> ;
	<http://purl.obolibrary.org/obo/RO_0002333> <http://model.geneontology.org/no_gp_at_all_example/gpB> .
<http://model.geneontology.org/no_gp_at_all_example/gpB>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://identifiers.org/mgi/MGI:1234567> .
<http://model.geneontology.org/no_gp_at_all_example/evB>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/no_gp_at_all_example/mfB> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/RO_0002333> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/no_gp_at_all_example/gpB> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/no_gp_at_all_example/evB> .
```

- [ ] **Step 2: Write the failing tests** — Append to `tests/test_gocam_ttl.py`:

```python
def test_no_gp_at_all_flags_gp_less_annotation(builder):
    """no_gp_at_all flags an annotation whose subgraph has no gene product, and
    leaves a GP-bearing annotation standard. All edges of the flagged annotation
    are recorded."""
    gocam = builder.parse_ttl("resources/test/no_gp_at_all_example.ttl")
    mfA = rdflib.term.URIRef("http://model.geneontology.org/no_gp_at_all_example/mfA")
    gpB = rdflib.term.URIRef("http://model.geneontology.org/no_gp_at_all_example/gpB")
    annots = gocam.standard_annotations + gocam.non_standard_annotations
    a = next(x for x in annots if mfA in x.individuals)
    b = next(x for x in annots if gpB in x.individuals)
    assert set(a.failed_checks) == {"no_gp_at_all"}
    assert a.failed_checks["no_gp_at_all"] == set(a.edges.keys())
    assert b.failed_checks == {}  # standard, GP present, not flagged


def test_no_gp_at_all_in_check_names():
    from gocam_unwinder.gocam_ttl import CHECK_NAMES, NESTING_ATTRIBUTABLE_CHECKS
    assert "no_gp_at_all" in CHECK_NAMES
    assert "no_gp_at_all" not in NESTING_ATTRIBUTABLE_CHECKS


def test_no_gp_at_all_in_criteria_report(builder):
    """print_non_standard_annotation_failed_checks emits a no_gp_at_all row."""
    import io
    gocam = builder.parse_ttl("resources/test/no_gp_at_all_example.ttl")
    buf = io.StringIO()
    builder.print_non_standard_annotation_failed_checks(gocam, report_file=buf)
    assert "no_gp_at_all" in buf.getvalue()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `source env/bin/activate && pytest tests/test_gocam_ttl.py -k "no_gp_at_all" -v`
Expected: FAIL — `no_gp_at_all` is not in `CHECK_NAMES` and the check is not implemented (annotation A has empty `failed_checks`).

- [ ] **Step 4: Add `no_gp_at_all` to `CHECK_NAMES`**

In `src/gocam_unwinder/gocam_ttl.py`, in the `CHECK_NAMES` tuple, add a line after `"enabler_not_gp",` (line 182):

```python
    "enabler_not_gp",
    "no_gp_at_all",
```

- [ ] **Step 5: Implement the check**

In `src/gocam_unwinder/gocam_ttl.py`, inside `filter_out_non_std_annotations`, insert immediately before `std_annot.failed_checks = failed_checks` (line 1538):

```python
            # Check #14: the annotation subgraph must contain at least one gene
            # product. A standard annotation links a GP to GO; a subgraph with no
            # GP at all (e.g. a bare anatomy/chemical placement) is non-standard.
            # All edges are recorded (annotation-level failure).
            has_gp = any(
                self._gene_product_namespace_key(t) in self.GP_NAMESPACE_KEYS
                for edge in std_annot.edges.values()
                for t in (edge.source_type, edge.target_type)
            )
            if not has_gp:
                failed_checks["no_gp_at_all"] = set(std_annot.edges.keys())

```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `source env/bin/activate && pytest tests/test_gocam_ttl.py -k "no_gp_at_all" -v`
Expected: 3 PASS.

- [ ] **Step 7: Run the full suite (no existing test should break)**

Run: `source env/bin/activate && pytest -q`
Expected: all PASS. (The four minimal relation fixtures' standard annotations now flip to non-standard, but every affected test asserts on a specific check key, so none break. Task 2 restores those fixtures.)

- [ ] **Step 8: Checkpoint** — stop for review/commit.

---

## Task 2: Keep the four relation-test fixtures standard (TDD)

Add a GP backbone to each of the four fixtures' standard annotation so it stays standard. Each added edge goes into the **same** subgraph and carries evidence whose metadata matches the existing edge's evidence (ECO_0000314 + date; `mf_occurs_in_anatomy_example` also matches contributor + source), so check #1 (`inconsistent_evidence`) still passes.

**Files:**
- Modify: `resources/test/mf_cc_relation_example.ttl`, `mf_bp_relation_example.ttl`, `bp_cc_relation_example.ttl`, `mf_occurs_in_anatomy_example.ttl`
- Test: `tests/test_gocam_ttl.py`

- [ ] **Step 1: Write the failing regression test** — Append to `tests/test_gocam_ttl.py`:

```python
def test_relation_fixtures_keep_one_standard_annotation(builder):
    """After adding a GP backbone, each relation-test fixture's passing annotation
    stays standard (exactly one standard annotation, empty failed_checks, has a GP)."""
    for fname in ["mf_cc_relation_example", "mf_bp_relation_example",
                  "bp_cc_relation_example", "mf_occurs_in_anatomy_example"]:
        gocam = builder.parse_ttl(f"resources/test/{fname}.ttl")
        assert len(gocam.standard_annotations) == 1, \
            f"{fname}: expected 1 standard annotation, got {len(gocam.standard_annotations)}"
        std = gocam.standard_annotations[0]
        assert std.failed_checks == {}, f"{fname}: standard annotation has failures"
        has_gp = any(
            builder._gene_product_namespace_key(t) in builder.GP_NAMESPACE_KEYS
            for e in std.edges.values() for t in (e.source_type, e.target_type))
        assert has_gp, f"{fname}: standard annotation should contain a GP"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `source env/bin/activate && pytest tests/test_gocam_ttl.py::test_relation_fixtures_keep_one_standard_annotation -v`
Expected: FAIL — after Task 1 each fixture has 0 standard annotations (the GP-less passing annotation flipped to non-standard).

- [ ] **Step 3: Add the GP backbone to `mf_cc_relation_example.ttl`**

Append to `resources/test/mf_cc_relation_example.ttl` (adds `mf1 -enabled_by-> MGI GP`, matching evidence):

```turtle
# GP backbone so the passing annotation has a gene product (no_gp_at_all check)
<http://model.geneontology.org/mf_cc_relation_example/gp1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://identifiers.org/mgi/MGI:1234567> .
<http://model.geneontology.org/mf_cc_relation_example/ev3>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/mf_cc_relation_example/mf1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/RO_0002333> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/mf_cc_relation_example/gp1> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/mf_cc_relation_example/ev3> .
```

- [ ] **Step 4: Add the GP backbone to `mf_bp_relation_example.ttl`**

Append to `resources/test/mf_bp_relation_example.ttl` (adds `mf1 -enabled_by-> MGI GP`):

```turtle
# GP backbone so the passing annotation has a gene product (no_gp_at_all check)
<http://model.geneontology.org/mf_bp_relation_example/gp1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://identifiers.org/mgi/MGI:1234567> .
<http://model.geneontology.org/mf_bp_relation_example/ev3>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/mf_bp_relation_example/mf1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/RO_0002333> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/mf_bp_relation_example/gp1> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/mf_bp_relation_example/ev3> .
```

- [ ] **Step 5: Add the GP backbone to `bp_cc_relation_example.ttl`**

Append to `resources/test/bp_cc_relation_example.ttl` (adds `MGI GP -acts_upstream_of_or_within-> bp1`, a valid GP→BP relation):

```turtle
# GP backbone so the passing annotation has a gene product (no_gp_at_all check)
<http://model.geneontology.org/bp_cc_relation_example/gp1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://identifiers.org/mgi/MGI:1234567> .
<http://model.geneontology.org/bp_cc_relation_example/ev3>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/bp_cc_relation_example/gp1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/RO_0002264> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/bp_cc_relation_example/bp1> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/bp_cc_relation_example/ev3> .
```

- [ ] **Step 6: Add the GP backbone to `mf_occurs_in_anatomy_example.ttl`**

Append to `resources/test/mf_occurs_in_anatomy_example.ttl` (adds `mf1 -enabled_by-> MGI GP`; the evidence node duplicates ev1's full metadata — ECO, contributor, source, date — so grouping stays consistent):

```turtle
# GP backbone so the passing annotation has a gene product (no_gp_at_all check)
<http://model.geneontology.org/mf_occurs_in_anatomy_example/gp1>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://identifiers.org/mgi/MGI:1234567> .
<http://model.geneontology.org/mf_occurs_in_anatomy_example/ev2>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/contributor> "https://orcid.org/0000-0003-3394-9805" ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" ;
	<http://purl.org/dc/elements/1.1/source> "PMID:22222222" .
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/mf_occurs_in_anatomy_example/mf1> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/RO_0002333> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/mf_occurs_in_anatomy_example/gp1> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/mf_occurs_in_anatomy_example/ev2> ;
	<http://purl.org/dc/elements/1.1/contributor> "https://orcid.org/0000-0003-3394-9805" ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
```

- [ ] **Step 7: Run the regression test + the four original target-check tests**

Run:
```bash
source env/bin/activate && pytest tests/test_gocam_ttl.py -k "relation_fixtures_keep_one_standard or invalid_mf_cc_relation or invalid_mf_bp_relation or invalid_bp_cc_relation or gp_mf_relation_ignores_anatomy" -v
```
Expected: all PASS. The regression test confirms each fixture has exactly one standard annotation (with a GP); the four target-check tests confirm the original defect edges are still flagged only on their intended relation.

If `test_relation_fixtures_keep_one_standard_annotation` reports a fixture with 0 standard annotations, the added GP edge did not join the passing annotation's subgraph (check the `annotatedSource`/`annotatedTarget` reference the existing `mf1`/`bp1` individual). If it reports 2+ failures on the standard annotation, the added evidence metadata does not match (check #1 `inconsistent_evidence`) — verify the new evidence node's ECO/contributor/source equal the existing edge's.

- [ ] **Step 8: Run the full suite**

Run: `source env/bin/activate && pytest -q`
Expected: all PASS.

- [ ] **Step 9: Checkpoint** — stop for review/commit.

---

## Task 3: Document the check in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (the "#### Filter Checks" section)

- [ ] **Step 1: Add the check documentation**

In `CLAUDE.md`, in the numbered "Filter Checks" list, after the `enabler_not_gp` entry (#12 in that list), add:

```markdown
13. **No gene product in subgraph** (`no_gp_at_all`):
    - Flags annotations whose subgraph contains no gene product (no individual whose type resolves to a `GP_NAMESPACE_KEYS` namespace via `_gene_product_namespace_key()`). A standard annotation links a gene product to GO; a subgraph that is, e.g., a bare anatomy or chemical placement has no GP and is non-standard.
    - When failed, **all** edges of the annotation are recorded (annotation-level failure).
```

(If the list numbering differs, insert it as the next sequential item after `enabler_not_gp` and keep the surrounding numbering consistent.)

- [ ] **Step 2: Verify nothing else references a stale check count**

Run: `grep -n "no_gp_at_all" CLAUDE.md`
Expected: the new entry is present.

- [ ] **Step 3: Checkpoint** — stop for review/commit.

---

## Verification

After all tasks:

```bash
# New + regression tests
source env/bin/activate && pytest tests/test_gocam_ttl.py -k "no_gp_at_all or relation_fixtures_keep_one_standard or invalid_mf_cc_relation or invalid_mf_bp_relation or invalid_bp_cc_relation or gp_mf_relation_ignores_anatomy" -v

# Full suite
source env/bin/activate && pytest -q
```

Expected final state:
- [ ] All tests pass.
- [ ] `no_gp_at_all` is a recorded failed check for GP-less annotations and appears in the criteria-fail report.
- [ ] `no_gp_at_all` is in `CHECK_NAMES` (so the extended stats report has a `fail:no_gp_at_all` column) and not in `NESTING_ATTRIBUTABLE_CHECKS`.
- [ ] The four edited relation fixtures each keep exactly one standard annotation (now with a GP); their original target-check tests still pass.
- [ ] `CLAUDE.md` documents the new check.

---

## Notes

- **Why scan edge source_type/target_type:** every individual in a `StandardAnnotation` is an edge endpoint (`individuals.add(source_uri/target_uri)`), so scanning each edge's two type URIs covers the whole subgraph — consistent with how `enabler_not_gp`/`invalid_gp_mf_relation` identify GPs.
- **Why the four fixtures need a *matching*-evidence GP edge:** their standard annotation is currently single-edge (so `inconsistent_evidence` passes trivially). Adding a second edge makes it multi-edge; only matching evidence metadata keeps the evidence groups consistent so the annotation stays standard.
- **Why `bp_cc_relation_example` uses GP→BP instead of enabled_by:** that fixture's passing annotation is BP-centric with no MF, so the natural GP attachment is a valid `GP -acts_upstream_of_or_within-> BP` edge (RO:0002264), not `enabled_by`.
- **Files worth reading before starting:**
  - `docs/plans/2026-06-24-no-gp-at-all-check-design.md` — the design (incl. the blast-radius table).
  - `src/gocam_unwinder/gocam_ttl.py` — `CHECK_NAMES` (170-184), `_gene_product_namespace_key`/`GP_NAMESPACE_KEYS` (861-865, ~1098), `filter_out_non_std_annotations` (1415-1547), `print_non_standard_annotation_failed_checks` (~1651).
  - `resources/test/mf_cc_relation_example.ttl` — the fixture-edit template.
```
