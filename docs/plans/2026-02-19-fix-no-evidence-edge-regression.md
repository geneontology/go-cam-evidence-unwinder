# Fix No-Evidence Edge Regression — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the regression where model 57c82fad00000252 (and potentially others) is incorrectly parsed as 4 subgraphs (3 standard + 1 non-standard) instead of 1 non-standard annotation, caused by the edges-without-evidence implementation (Issue #14).

**Architecture:** The root cause is a missing predicate filter in `extract_edges()`'s second pass (lines 430-448). The original plan specified a `GOCAM_RELATIONS` filter, but the implementation omitted it. Without a filter, non-GO-CAM OWL axiom edges (like `oboInOwl#id` and `rdfs:label` reifications) are extracted and fed into the union-find algorithm, creating spurious annotation subgraphs. The fix adds an OBO namespace prefix filter — broader than `GOCAM_RELATIONS` (which misses valid relations like `RO:0002407`) but sufficient to exclude all non-GO-CAM predicates.

**Tech Stack:** Python, rdflib, pytest

**Affected Areas:** Edge extraction (`extract_edges`), tests

---

## Context

The edges-without-evidence plan (`docs/plans/2026-02-05-edges-without-evidence.md`) added a second pass to `extract_edges()` to discover OWL axiom blank nodes without `lego:evidence` triples. The plan specified a `GOCAM_RELATIONS` filter to only include edges with GO-CAM relations, but it was **not implemented** (see `src/gocam_unwinder/gocam_ttl.py:430-448`). Without it, ALL axiom edges without evidence are extracted, including:

- `oboInOwl#id` axiom reifications (`http://www.geneontology.org/formats/oboInOwl#id`)
- `rdfs:label` axiom reifications (`http://www.w3.org/2000/01/rdf-schema#label`)

These non-GO-CAM axiom edges have source/target URIs that are classes (not GO-CAM individuals). When fed into the union-find in `extract_standard_annotations()`, they create spurious annotation objects, causing a single connected component to be split into multiple subgraphs.

## Constraints

- Must fix model 57c82fad00000252 to parse as 1 non-standard annotation (not 3 standard + 1 non-standard)
- Must not break existing tests (test_edges_without_evidence, etc.)
- Model 66c7d41500000016 must still parse correctly (1 annotation, no-evidence causal edge included)

## Out of Scope

- Refactoring `find_related_edges()` or the second pass in `extract_standard_annotations()`
- Changes to evidence splitting logic
- Any new filtering checks

---

## Tasks

### Task 1: Add test model and write failing regression test

**Status:** ✅ Complete

**Files:**
- Create: `resources/test/57c82fad00000252.ttl` (download from noctua-models)
- Modify: `tests/test_gocam_ttl.py`

**Step 1: Download the test model**

```bash
curl -L -o resources/test/57c82fad00000252.ttl \
  "https://raw.githubusercontent.com/geneontology/noctua-models/master/models/57c82fad00000252.ttl"
```

**Step 2: Run the tool against the model to understand current (broken) behavior**

```bash
python src/gocam_unwinder/gocam_ttl.py \
  -m resources/test/57c82fad00000252.ttl \
  -o target/go_20250601.json \
  -r resources/test/ro_20250723.owl
```

**Result:** Confirmed broken behavior: 3 standard + 1 non-standard.

**Step 3: Write the failing regression test**

Added `test_no_evidence_edge_gocam_relations_filter()` to `tests/test_gocam_ttl.py` asserting 0 standard + 1 non-standard annotation.

**Step 4: Run test to verify it fails**

Run: `pytest tests/test_gocam_ttl.py::test_no_evidence_edge_gocam_relations_filter -v`
**Result:** FAIL as expected — `Expected 0 standard annotations, got 3`.

---

### Task 2: Add OBO namespace filter to extract_edges() second pass

**Status:** ✅ Complete

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py:443-447`

**Initial approach: GOCAM_RELATIONS allowlist**

The plan specified using `GOCAM_RELATIONS` as an allowlist filter. This was tried first but caused `test_edges_without_evidence` and `test_edges_without_evidence_report_column` to fail.

**Investigation:** `RO:0002407` (indirectly positively regulates) — the no-evidence causal edge in model 66c7d41500000016 — is NOT in `GOCAM_RELATIONS`. The `GOCAM_RELATIONS` list is built from `ontobio.rdfgen.relations.__relation_label_lookup` which only contains 94 relations and misses valid GO-CAM causal relations.

**Analysis of actual spurious predicates:**

| Model | No-evidence axiom predicates | In GOCAM_RELATIONS? |
|-------|------------------------------|---------------------|
| 57c82fad00000252 | `BFO:0000050` (part_of) | ✅ Yes |
| 57c82fad00000252 | `RO:0002349` | ✅ Yes |
| 57c82fad00000252 | `oboInOwl#id` | ❌ No (non-OBO namespace) |
| 57c82fad00000252 | `rdfs:label` | ❌ No (non-OBO namespace) |
| 66c7d41500000016 | `RO:0002407` (indirectly positively regulates) | ❌ No (but IS a valid GO-CAM relation) |

**Key insight:** All GO-CAM relations (BFO, RO) use the OBO namespace prefix (`http://purl.obolibrary.org/obo/`). All spurious predicates use other namespaces (`oboInOwl#`, `rdfs:`, `rdf:`, `owl:`).

**Final approach: OBO namespace prefix filter**

Instead of the `GOCAM_RELATIONS` allowlist, used an OBO namespace prefix check:

```python
# Only include OBO relation edges (skip rdf:type, rdfs:label, oboInOwl#id, etc.)
# All GO-CAM relations (BFO, RO) use the OBO namespace prefix.
# GOCAM_RELATIONS is too restrictive (misses valid relations like RO:0002407).
if not str(properties[0]).startswith("http://purl.obolibrary.org/obo/"):
    continue
```

**Results:**
- `pytest -v`: All 8 tests pass
- CLI smoke test on 57c82fad00000252: 0 standard, 1 non-standard ✅
- CLI smoke test on 66c7d41500000016: 1 annotation with no-evidence edge ✅

---

### Task 3: Commit and update CLAUDE.md

**Status:** ✅ Complete

**Files:**
- Modify: `CLAUDE.md`

Updated CLAUDE.md with:
- New test model description for `57c82fad00000252.ttl`
- New test function description for `test_no_evidence_edge_gocam_relations_filter()`
- Updated `extract_edges()` algorithm description to note OBO namespace filter

---

## Verification

**All verification criteria met:**

```
tests/test_gocam_ttl.py::test_gocam_ttl PASSED
tests/test_gocam_ttl.py::test_multi_edge_evidence_grouping PASSED
tests/test_gocam_ttl.py::test_mf_causal_mf_filtering PASSED
tests/test_gocam_ttl.py::test_print_non_standard_annotation_failed_checks PASSED
tests/test_gocam_ttl.py::test_print_non_standard_annotation_failed_checks_multiple_reasons PASSED
tests/test_gocam_ttl.py::test_edges_without_evidence PASSED
tests/test_gocam_ttl.py::test_edges_without_evidence_report_column PASSED
tests/test_gocam_ttl.py::test_no_evidence_edge_gocam_relations_filter PASSED
8 passed
```

CLI smoke test on 57c82fad00000252:
```
Model ID	Title	Standard Annotations	Non-Standard Annotations	...
gomodel:57c82fad00000252	C. elegans - SAB neuron synaptogenesis	0	1	...
```

**Final state:**
- [x] All 8 tests pass (including new `test_no_evidence_edge_gocam_relations_filter`)
- [x] No regressions in existing tests
- [x] Model 57c82fad00000252 reports 0 standard, 1 non-standard annotation
- [x] Model 66c7d41500000016 still reports 1 annotation with no-evidence edge included
- [x] CLAUDE.md updated with new test model and test function documentation

---

## Notes

- The original plan specified `GOCAM_RELATIONS` as the filter, but this was too restrictive. The `GOCAM_RELATIONS` list (94 relations from `ontobio.rdfgen.relations.__relation_label_lookup`) misses valid GO-CAM causal relations like `RO:0002407` (indirectly positively regulates).
- The OBO namespace prefix filter (`http://purl.obolibrary.org/obo/`) is the right granularity: all GO-CAM relations (BFO_*, RO_*) use this prefix, while all spurious predicates (`oboInOwl#id`, `rdfs:label`, `rdf:type`, `owl:equivalentClass`) use other namespaces.
- The `find_related_edges()` method at line 524 uses `GOCAM_RELATIONS` as its filter, which is correct for its traversal purpose but would be too restrictive for the `extract_edges()` second pass. The two methods intentionally use different filter strategies.
- Changes are NOT yet committed — awaiting user go-ahead.