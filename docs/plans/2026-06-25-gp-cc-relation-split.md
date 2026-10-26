# GP→CC Relation Rule (#3) Split — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the `invalid_gp_cc_relation` (#3) check so a gene product's valid relation to a cellular component depends on which CC subhierarchy the target falls under: `part_of` for a protein-containing complex (`GO:0032991`), `located_in` **or** `is_active_in` for a cellular anatomical structure (`GO:0110165`) / virion component (`GO:0044423`).

**Architecture:** Adds one shared classifier (`_cc_branch`) that buckets a GO CC term into `"complex"` vs `"anatomical"` by its is_a closure (the same closure `GoAspector` uses for aspect classification), extends the existing declarative relation-rule schema with an optional `tgt_cc_branch` constraint, and replaces the single #3 rule row with two rows (one per branch) that share the `invalid_gp_cc_relation` `failed_checks` key. No change to the evaluation loop, the report schema, or any other check.

**Tech Stack:** Python 3, `rdflib`, `ontobio` (`GoAspector.get_isa_closure`), `prefixcommons.curie_util`, `pytest`. Fixtures are hand-written TTL in `resources/test/` following the existing `*_example.ttl` pattern.

**Affected Areas:** `GoCamGraphBuilder` in `src/gocam_unwinder/gocam_ttl.py` (new constants + `_cc_branch` helper, `_matches_relation_rule` schema extension, `_build_relation_rules` #3 rows); `tests/test_gocam_ttl.py` (new unit test + extended #3 test + new `_flagged_sigs` helper); `resources/test/gp_cc_relation_example.ttl` (extended fixture); `CLAUDE.md` (check-#3 documentation).

---

## Context

`std_annot_rules.tsv` row #3 was just changed from a single rule into **two** rules:

| TSV line | Parameter | Rule |
|----------|-----------|------|
| 4 | Relation between Gene Product and a child of `GO:0110165` OR `GO:0044423` | Must be "located in" OR "is active in" |
| 5 | Relation between Gene Product and `GO:0032991` or a child | Must be 'part of' |

Today the code implements the **old** single rule: "GP → non-root CC must be `located_in`" (`src/gocam_unwinder/gocam_ttl.py:1069-1072`). The cellular_component aspect (`GO:0005575`) has three disjoint top branches:

- `GO:0110165` cellular anatomical structure
- `GO:0044423` virion component
- `GO:0032991` protein-containing complex

The new rule says: a GP is **`part_of`** a complex, but **`located_in` / `is_active_in`** an anatomical structure (or virion component). So the valid relation set now depends on which branch the CC target is in — the declarative rule table currently keys only on the disjoint endpoint category (`_category() == "CC"`) and cannot make that distinction, hence the new `_cc_branch` classifier and the `tgt_cc_branch` rule constraint.

**Real-world impact (verified against `target_20260609/models_split_criteria_failures_20260609.tsv`):** edges like `MGI_MGI_99660` (`mgi:99660 ─part of→ alpha DNA polymerase:primase complex`) and `SGD_S000028423` (`─part of→ GPI-GnT complex`) are flagged `invalid_gp_cc_relation` today. Under the new rule, GP `part_of` a complex is **valid**, so these correctly stop being flagged. GP `part_of` an *anatomical* CC (e.g. nucleus, cytosol) still fails.

### Verified domain facts (checked against `target/go_20250601.json` via `GoAspector`)

- The split is computed from the **is_a closure** (`GoAspector.get_isa_closure(curie)`, which is `subClassOf`-only — *not* the default all-relations ancestry). This matches how `is_cellular_component` / aspect classification already work. Using the default `ontology.ancestors()` (which also follows `part_of`) would misclassify (e.g. a complex that is `part_of` an anatomical structure).
- `get_isa_closure` is **non-reflexive** (it does not include the term itself), so `_cc_branch` must add the term back in to classify a branch-root term used directly as a target.
- is_a closure results: `GO:0000307` (cyclin-dependent protein kinase holoenzyme complex) → under `GO:0032991` only (`"complex"`); `GO:0005634` (nucleus) and `GO:0005829` (cytosol) → under `GO:0110165` only (`"anatomical"`). The three branches are disjoint at the top (none is under another).
- `GO:0044423` (virion component) is **not** under `GO:0110165` via is_a — it is its own top branch — so it must be matched directly (handled by the reflexive add + `ANATOMICAL_CC_ROOTS` membership).
- Term labels: `GO:0110165` = "cellular anatomical structure", `GO:0032991` = "protein-containing complex", `GO:0044423` = "virion component".
- Relation URIs: `located_in` = `RO_0001025`, `is_active_in` = `RO_0002432`, `part_of` = `BFO_0000050`. All three are already in `self.backbone_relations`, so the backbone gate already lets every #3-relevant edge through to rule evaluation — no change needed there.
- `curie_util.contract_uri(str(uri))` returns CURIEs like `["GO:0005634"]` (used by the existing `uri_is_cellular_component`); `_cc_branch` reuses this pattern.

## Constraints

- Match existing code/test style. Tests use the session-scoped `builder` fixture (`tests/conftest.py`, loads GO + RO + groups.yaml).
- The `failed_checks` key stays `invalid_gp_cc_relation` (both new sub-rules record under it), so the criteria-fail report (6 cols) and statistics report are unchanged. The allowed-reasons list in `tests/test_gocam_ttl.py:233-238` and the `test_print_*` reporting tests need no change.
- This is a **non-RO-dependent** rule (like the current #3 and #6): it must work whether or not an RO ontology is loaded.
- Per project policy, **do not run `git add` / `git commit`** — the user stages and commits manually. Each task ends with a checkpoint (run tests) and a suggested commit message only.

## Out of Scope

- Any other TSV rule (#1, #2, #4, #5, #6, #10, #11, #12, #13, #15) — only #3 changes.
- The `_is_anatomical_structure` helper (used by #10/#12) — it intentionally treats *all* GO CCs (incl. complexes) as anatomical structures for the BP→CC/anatomy and MF→anatomy cardinality checks; that semantics is unrelated to the GP→CC branch split and is left untouched.
- Re-running the full pipeline / regenerating reports.

---

## Tasks

### Task 1: `_cc_branch` classifier + CC-branch constants

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py` — add class constants after `ANATOMY_NAMESPACE_KEYS` (~line 893); add `_cc_branch` method after `_is_anatomical_structure` (~line 1037, before `_category`).
- Test: `tests/test_gocam_ttl.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gocam_ttl.py` (next to the other helper unit tests such as `test_category`):

```python
def test_cc_branch(builder):
    U = rdflib.URIRef
    # protein-containing complex (GO:0032991) subtree -> "complex"
    assert builder._cc_branch(U("http://purl.obolibrary.org/obo/GO_0000307")) == "complex"  # CDK holoenzyme complex
    assert builder._cc_branch(U("http://purl.obolibrary.org/obo/GO_0032991")) == "complex"  # branch root (reflexive)
    # cellular anatomical structure (GO:0110165) subtree -> "anatomical"
    assert builder._cc_branch(U("http://purl.obolibrary.org/obo/GO_0005634")) == "anatomical"  # nucleus
    assert builder._cc_branch(U("http://purl.obolibrary.org/obo/GO_0005829")) == "anatomical"  # cytosol
    assert builder._cc_branch(U("http://purl.obolibrary.org/obo/GO_0110165")) == "anatomical"  # branch root (reflexive)
    # virion component (GO:0044423) is its own top branch (NOT under GO:0110165) -> "anatomical"
    assert builder._cc_branch(U("http://purl.obolibrary.org/obo/GO_0044423")) == "anatomical"
    # Non-CC GO terms and non-GO nodes -> None
    assert builder._cc_branch(U("http://purl.obolibrary.org/obo/GO_0008150")) is None  # BP root
    assert builder._cc_branch(U("http://purl.obolibrary.org/obo/GO_0042802")) is None  # MF
    assert builder._cc_branch(U("http://identifiers.org/mgi/MGI:1100089")) is None     # GP
    assert builder._cc_branch(None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source env/bin/activate && pytest tests/test_gocam_ttl.py::test_cc_branch -v`
Expected: FAIL with `AttributeError: 'GoCamGraphBuilder' object has no attribute '_cc_branch'`.

- [ ] **Step 3: Add the CC-branch constants**

Insert immediately after the `ANATOMY_NAMESPACE_KEYS` block (after `src/gocam_unwinder/gocam_ttl.py:893`):

```python
    # Top-level cellular-component branches (CURIEs), used by _cc_branch to pick
    # the valid GP->CC relation for check #3. The CC aspect (GO:0005575) splits
    # into these disjoint is_a subtrees:
    #   GO:0032991 protein-containing complex  -> GP must be "part of"
    #   GO:0110165 cellular anatomical structure | GO:0044423 virion component
    #                                           -> GP must be "located in" / "is active in"
    COMPLEX_CC_ROOT = "GO:0032991"
    ANATOMICAL_CC_ROOTS = {"GO:0110165", "GO:0044423"}
```

- [ ] **Step 4: Implement `_cc_branch`**

Insert after `_is_anatomical_structure` (after `src/gocam_unwinder/gocam_ttl.py:1037`, before `_category`):

```python
    def _cc_branch(self, type_node):
        """Bucket a GO cellular-component type into its top CC branch, by is_a
        closure: "complex" (GO:0032991 protein-containing complex subtree) or
        "anatomical" (GO:0110165 cellular anatomical structure / GO:0044423
        virion component subtree). Returns None for non-GO / non-CC nodes (and
        for a CC under none of the three named branches, e.g. the bare CC root).

        Uses the is_a closure (subClassOf only) -- the same closure GoAspector
        uses for aspect classification -- so a complex that is part_of an
        anatomical structure is still classified "complex". The closure is
        non-reflexive, so the term itself is added back to classify a
        branch-root term used directly as a target. Used by check #3 via the
        tgt_cc_branch rule constraint."""
        if not isinstance(type_node, URIRef):
            return None
        parsed = curie_util.contract_uri(str(type_node))
        if not parsed or not parsed[0].startswith("GO:"):
            return None
        curie = parsed[0]
        closure = set(self.go_aspector.get_isa_closure(curie))
        closure.add(curie)  # reflexive: a branch-root term belongs to its own branch
        if self.COMPLEX_CC_ROOT in closure:
            return "complex"
        if closure & self.ANATOMICAL_CC_ROOTS:
            return "anatomical"
        return None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_gocam_ttl.py::test_cc_branch -v`
Expected: PASS

- [ ] **Step 6: Checkpoint** — run `pytest tests/test_gocam_ttl.py -q` (no regressions). Suggested commit message: `feat(#3): add _cc_branch CC-subhierarchy classifier`. (User stages/commits manually.)

---

### Task 2: Split rule #3 (complex→part_of, anatomical→located_in/is_active_in)

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py` — extend `_matches_relation_rule` (~lines 1096-1105) with the `tgt_cc_branch` constraint; replace the single #3 rule row in `_build_relation_rules` (~lines 1069-1072) with two rows.
- Modify: `resources/test/gp_cc_relation_example.ttl` — extend with complex-branch and `is_active_in` cases.
- Modify: `tests/test_gocam_ttl.py` — add `_flagged_sigs` helper; rewrite `test_invalid_gp_cc_relation` (~lines 1014-1018).
- Test: `tests/test_gocam_ttl.py`

- [ ] **Step 1: Extend the fixture**

Append the following annotations to `resources/test/gp_cc_relation_example.ttl` (keep the existing `gp1` located_in→nucleus PASS and `gp2` part_of→cytosol FAIL blocks unchanged):

```turtle
# PASS: GP -part_of-> protein-containing complex (GO:0000307)  [new #3 complex branch]
<http://model.geneontology.org/gp_cc_relation_example/gp3>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://identifiers.org/mgi/MGI:99830> ;
	<http://purl.obolibrary.org/obo/BFO_0000050> <http://model.geneontology.org/gp_cc_relation_example/cc3> .
<http://model.geneontology.org/gp_cc_relation_example/cc3>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/GO_0000307> .
<http://model.geneontology.org/gp_cc_relation_example/ev3>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/gp_cc_relation_example/gp3> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/BFO_0000050> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/gp_cc_relation_example/cc3> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/gp_cc_relation_example/ev3> .

# FAIL: GP -located_in-> protein-containing complex (GO:0000307)  (should be part_of)
<http://model.geneontology.org/gp_cc_relation_example/gp4>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://identifiers.org/mgi/MGI:88276> ;
	<http://purl.obolibrary.org/obo/RO_0001025> <http://model.geneontology.org/gp_cc_relation_example/cc4> .
<http://model.geneontology.org/gp_cc_relation_example/cc4>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/GO_0000307> .
<http://model.geneontology.org/gp_cc_relation_example/ev4>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/gp_cc_relation_example/gp4> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/RO_0001025> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/gp_cc_relation_example/cc4> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/gp_cc_relation_example/ev4> .

# PASS: GP -is_active_in-> cellular anatomical structure (GO:0005634 nucleus)  [is_active_in now valid for anatomical]
<http://model.geneontology.org/gp_cc_relation_example/gp5>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://identifiers.org/mgi/MGI:96522> ;
	<http://purl.obolibrary.org/obo/RO_0002432> <http://model.geneontology.org/gp_cc_relation_example/cc5> .
<http://model.geneontology.org/gp_cc_relation_example/cc5>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/GO_0005634> .
<http://model.geneontology.org/gp_cc_relation_example/ev5>
	a <http://www.w3.org/2002/07/owl#NamedIndividual> , <http://purl.obolibrary.org/obo/ECO_0000314> ;
	<http://purl.org/dc/elements/1.1/date> "2024-01-15" .
[] a <http://www.w3.org/2002/07/owl#Axiom> ;
	<http://www.w3.org/2002/07/owl#annotatedSource> <http://model.geneontology.org/gp_cc_relation_example/gp5> ;
	<http://www.w3.org/2002/07/owl#annotatedProperty> <http://purl.obolibrary.org/obo/RO_0002432> ;
	<http://www.w3.org/2002/07/owl#annotatedTarget> <http://model.geneontology.org/gp_cc_relation_example/cc5> ;
	<http://geneontology.org/lego/evidence> <http://model.geneontology.org/gp_cc_relation_example/ev5> .
```

After this edit the fixture has five single-edge GP→CC annotations:

| Individual | Relation | Target | Branch | Expectation |
|------------|----------|--------|--------|-------------|
| gp1 | located_in (`RO_0001025`) | GO:0005634 nucleus | anatomical | PASS |
| gp2 | part_of (`BFO_0000050`) | GO:0005829 cytosol | anatomical | **FAIL** |
| gp3 | part_of (`BFO_0000050`) | GO:0000307 complex | complex | PASS |
| gp4 | located_in (`RO_0001025`) | GO:0000307 complex | complex | **FAIL** |
| gp5 | is_active_in (`RO_0002432`) | GO:0005634 nucleus | anatomical | PASS |

- [ ] **Step 2: Add `_flagged_sigs` helper and rewrite the failing test**

`_flagged_props` collapses by property URI only — too weak here, because `part_of` and `located_in` each appear on both a passing and a failing edge. Add a per-edge signature helper next to `_flagged_props` (~line 995 in `tests/test_gocam_ttl.py`):

```python
def _flagged_sigs(gocam, key):
    """Return {(property_uri, target_type)} string-tuples flagged under `key`
    across all annotations -- precise enough to distinguish edges that share a
    relation but differ in target."""
    sigs = set()
    for annot in gocam.standard_annotations + gocam.non_standard_annotations:
        for bnode_id in annot.failed_checks.get(key, set()):
            e = annot.edges[bnode_id]
            sigs.add((str(e.property_uri), str(e.target_type)))
    return sigs
```

Replace the existing `test_invalid_gp_cc_relation` (`tests/test_gocam_ttl.py:1014-1018`) with:

```python
def test_invalid_gp_cc_relation(builder):
    gocam = builder.parse_ttl("resources/test/gp_cc_relation_example.ttl")
    # #3 splits by CC subhierarchy:
    #   complex (GO:0032991 subtree)            -> part_of valid; located_in invalid
    #   anatomical (GO:0110165 / GO:0044423)    -> located_in / is_active_in valid; part_of invalid
    # Only gp2 (part_of -> cytosol, anatomical) and gp4 (located_in -> complex)
    # are flagged. gp1 (located_in -> nucleus), gp3 (part_of -> complex), and
    # gp5 (is_active_in -> nucleus) all pass.
    assert _flagged_sigs(gocam, "invalid_gp_cc_relation") == {
        ("http://purl.obolibrary.org/obo/BFO_0000050",
         "http://purl.obolibrary.org/obo/GO_0005829"),  # part_of -> cytosol (anatomical) FAIL
        ("http://purl.obolibrary.org/obo/RO_0001025",
         "http://purl.obolibrary.org/obo/GO_0000307"),  # located_in -> complex FAIL
    }
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_gocam_ttl.py::test_invalid_gp_cc_relation -v`
Expected: FAIL — the unsplit rule flags every non-`located_in` GP→CC edge, so the flagged set wrongly includes `(BFO_0000050, GO:0000307)` (gp3 part_of→complex) and omits `(RO_0001025, GO:0000307)` (gp4 located_in→complex).

- [ ] **Step 4: Extend `_matches_relation_rule` with the branch constraint**

In `src/gocam_unwinder/gocam_ttl.py`, add the `tgt_cc_branch` check to `_matches_relation_rule` (insert before its final `return True`, after the `tgt_nonroot` check at ~line 1104):

```python
        branch = rule.get("tgt_cc_branch")
        if branch is not None and self._cc_branch(edge.target_type) != branch:
            return False
```

(Existing rules omit the `tgt_cc_branch` key, so `rule.get("tgt_cc_branch")` is `None` for them and the check is skipped — no behavior change for #4/#5/#6/#10.)

- [ ] **Step 5: Replace the #3 rule row with two branch-specific rows**

In `_build_relation_rules`, replace the single #3 entry (`src/gocam_unwinder/gocam_ttl.py:1069-1072`):

```python
            # #3  GP -> non-root CC : located_in
            {"key": "invalid_gp_cc_relation", "src": {"GP"}, "tgt": {"CC"},
             "src_root_mf": False, "tgt_nonroot": True,
             "valid": {str(self.rel_located_in)}},
```

with:

```python
            # #3a  GP -> non-root protein-containing complex (GO:0032991 subtree) : part_of
            {"key": "invalid_gp_cc_relation", "src": {"GP"}, "tgt": {"CC"},
             "src_root_mf": False, "tgt_nonroot": True, "tgt_cc_branch": "complex",
             "valid": {str(self.rel_part_of)}},
            # #3b  GP -> non-root cellular anatomical structure (GO:0110165) /
            #      virion component (GO:0044423) subtree : located_in OR is_active_in
            {"key": "invalid_gp_cc_relation", "src": {"GP"}, "tgt": {"CC"},
             "src_root_mf": False, "tgt_nonroot": True, "tgt_cc_branch": "anatomical",
             "valid": {str(self.rel_located_in), str(self.rel_is_active_in)}},
```

The two rows are mutually exclusive (a CC target is in exactly one branch), so the evaluation loop's first-match-`break` semantics are preserved: for a complex target #3a matches; for an anatomical target #3a fails the branch check and the loop falls through to #3b. Both record under the same `invalid_gp_cc_relation` key, so the report is unchanged.

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_gocam_ttl.py::test_invalid_gp_cc_relation -v`
Expected: PASS

- [ ] **Step 7: Checkpoint** — run the full file `pytest tests/test_gocam_ttl.py -q`. Pay attention to:
  - `test_compute_model_stats_unfixable_nonstandard` — its fixture GP edge is `part_of` → `GO:0005634` (nucleus, anatomical), which still fails #3, so `failure_counts["invalid_gp_cc_relation"] >= 1` must still hold.
  - `test_relation_rules_ignore_extension_edges` (MGI_MGI_1100089, 28 standard) — unaffected (its GP↔CC edges, if any, were already passing).

  Suggested commit message: `feat(#3): split GP->CC relation by complex vs anatomical CC branch`.

---

### Task 3: Update `CLAUDE.md` documentation

**Files:**
- Modify: `CLAUDE.md` — the check-#3 description (~line 315), the `gp_cc_relation_example.ttl` fixture note (~line 436), and the `test_invalid_gp_cc_relation` description (~line 522). (No code/tests in this task — docs only.)

- [ ] **Step 1: Update the check-#3 description**

Replace the existing item 6 (`CLAUDE.md:315-317`):

```markdown
6. **Invalid GP→CC relation** (`invalid_gp_cc_relation`):
   - A gene product (GP) connected to a non-root GO cellular component (CC) must use `located_in` (RO:0001025)
   - When failed, only the offending edge is recorded
   - Implemented via the declarative relation-validity rule table
```

with:

```markdown
6. **Invalid GP→CC relation** (`invalid_gp_cc_relation`):
   - A gene product (GP) connected to a non-root GO cellular component (CC) must use a relation that depends on the CC's subhierarchy (`_cc_branch`):
     - **protein-containing complex** (`GO:0032991` is_a subtree) → must be `part_of` (BFO:0000050)
     - **cellular anatomical structure** (`GO:0110165`) / **virion component** (`GO:0044423`) is_a subtree → must be `located_in` (RO:0001025) OR `is_active_in` (RO:0002432)
   - The branch is determined by `_cc_branch()` via the GO is_a closure (the same closure `GoAspector` uses for aspect classification), exposed to the rule table as the `tgt_cc_branch` constraint (`"complex"` / `"anatomical"`). Implemented as two rows in `_build_relation_rules` that share the `invalid_gp_cc_relation` key.
   - When failed, only the offending edge is recorded
```

- [ ] **Step 2: Update the fixture note**

Replace the `gp_cc_relation_example.ttl` bullet (`CLAUDE.md:435-436`):

```markdown
- **gp_cc_relation_example.ttl**: Synthetic model with a passing `GP─located_in→CC` and a failing `GP─part_of→CC` edge
  - Used to test `invalid_gp_cc_relation` check (#3): only the wrong-relation edge is flagged
```

with:

```markdown
- **gp_cc_relation_example.ttl**: Synthetic model with five single-edge GP→CC annotations covering both #3 branches: `GP─located_in→anatomical-CC` (pass), `GP─part_of→anatomical-CC` (fail), `GP─part_of→complex` (pass), `GP─located_in→complex` (fail), `GP─is_active_in→anatomical-CC` (pass)
  - Used to test `invalid_gp_cc_relation` check (#3): a GP must be `part_of` a protein-containing complex (`GO:0032991`) but `located_in`/`is_active_in` a cellular anatomical structure (`GO:0110165`)/virion component (`GO:0044423`)
```

- [ ] **Step 3: Update the test description and add the new helper/unit test entries**

Replace the `test_invalid_gp_cc_relation()` bullet (`CLAUDE.md:522`):

```markdown
- `test_invalid_gp_cc_relation()`: Tests that only the wrong-relation `GP─part_of→CC` edge in gp_cc_relation_example.ttl is flagged under `invalid_gp_cc_relation`
```

with:

```markdown
- `test_invalid_gp_cc_relation()`: Tests the #3 CC-subhierarchy split on gp_cc_relation_example.ttl via the per-edge `_flagged_sigs` helper — only `GP─part_of→anatomical-CC` and `GP─located_in→complex` are flagged; `located_in`/`is_active_in`→anatomical and `part_of`→complex pass
- `test_cc_branch()`: Unit test for `_cc_branch()`: protein-containing complex terms (incl. the `GO:0032991` root, reflexive) return `"complex"`; cellular anatomical structures, the `GO:0110165` root, and virion component `GO:0044423` return `"anatomical"`; non-CC GO terms, GPs, and `None` return `None`
```

Also add a one-line note to the `GoCamGraphBuilder` "Key methods" list (near `_is_anatomical_structure`, `CLAUDE.md` Architecture section) and to the class-constants list (near `ANATOMY_NAMESPACE_KEYS`):

```markdown
  - `_cc_branch()`: Returns `"complex"` | `"anatomical"` | `None` — buckets a GO cellular component into its top is_a branch (protein-containing complex `GO:0032991` vs cellular anatomical structure `GO:0110165` / virion component `GO:0044423`). Drives the #3 `invalid_gp_cc_relation` branch split via the rule table's `tgt_cc_branch` constraint
```

```markdown
  - `COMPLEX_CC_ROOT` / `ANATOMICAL_CC_ROOTS`: CURIEs of the top CC is_a branches used by `_cc_branch` for the #3 GP→CC relation split (`GO:0032991`; `GO:0110165`, `GO:0044423`)
```

- [ ] **Step 4: Checkpoint** — full run `source env/bin/activate && pytest -q` (or `make test`). Expected: all green. Suggested commit message: `docs(#3): document GP->CC relation split by CC subhierarchy`.

---

## Self-Review

- **Spec coverage:** TSV #3 line 4 (GP→`GO:0110165`/`GO:0044423` child → located_in OR is_active_in) → rule #3b + fixture gp1/gp5 (pass) / gp2 (fail). TSV #3 line 5 (GP→`GO:0032991` or child → part_of) → rule #3a + fixture gp3 (pass) / gp4 (fail). Both covered.
- **Branch classification source:** `_cc_branch` uses `GoAspector.get_isa_closure` (subClassOf-only, reflexive-corrected) — verified to disjointly separate the three CC branches against `target/go_20250601.json`.
- **Key/report stability:** both new rows keep `key == "invalid_gp_cc_relation"`; allowed-reasons list and report schema unchanged.
- **No regressions identified:** `test_compute_model_stats_unfixable_nonstandard` (part_of→nucleus still fails), the existing gp1/gp2 cases (located_in→nucleus pass, part_of→cytosol fail), and `test_relation_rules_ignore_extension_edges` all hold under the new rows.
- **Type consistency:** `_cc_branch` returns `"complex"`/`"anatomical"`/`None`; rule rows use `"tgt_cc_branch": "complex"|"anatomical"`; `_matches_relation_rule` compares against those exact strings. Constant names `COMPLEX_CC_ROOT` (str) and `ANATOMICAL_CC_ROOTS` (set) used consistently.
- **No placeholders:** every step contains the literal code/TTL/commands to apply.

## Execution Handoff

Plan complete and saved to `docs/plans/2026-06-25-gp-cc-relation-split.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
