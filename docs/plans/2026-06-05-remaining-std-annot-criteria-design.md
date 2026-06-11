# Remaining Standard-Annotation Criteria Checks — Design

Issue: #22 (standard-annotation criteria). Branch: `issue-22-std-annot-criteria`.

Source of truth for the rule set: `std_annot_rules.tsv`.

## Problem

`std_annot_rules.tsv` enumerates the criteria that distinguish a "standard
annotation" from a non-standard one. Only a subset is implemented in
`GoCamGraphBuilder.filter_out_non_std_annotations`
(`src/gocam_unwinder/gocam_ttl.py`):

| TSV # | Rule | Status | Existing `failed_checks` key |
|------|------|--------|------------------------------|
| 1  | Causal relation MF→MF not allowed | done | `mf_causal_mf` |
| 2  | GP↔MF relation must be enables / contributes_to | done | `invalid_gp_mf_relation` |
| 15 | Assertions must have evidence | done | `edge_without_evidence` |
| 3  | GP → non-root CC must be `located_in` | **TODO** | — |
| 4  | GP → BP (incl. root) must be `acts_upstream_of_or_within` (+children) | **TODO** | — |
| 5  | root-MF → non-root BP must be `acts_upstream_of_or_within` (+children) | **TODO** | — |
| 6  | root-MF → non-root CC must be `is_active_in` | **TODO** | — |
| 7  | non-root MF → non-root BP — "exported as extension; nesting not supported" | **informational** | — |
| 8  | non-root MF → non-root CC — "exported as extension; nesting not supported" | **informational** | — |
| 9  | non-root BP → non-root BP — "exported as part of extension; nesting not supported" | **informational** | — |
| 10 | BP → CC/anatomy must be `occurs_in` | **TODO** | — |
| 11 | Cardinality MF→BP should be 1 | **partial** | `multiple_mf_part_of` (counts MF-`part_of`, any target) |
| 12 | Cardinality MF→anatomical-structure should be 1 | **TODO** | — |
| 13 | Enabler must be a gene product (not GO or ChEBI) | **TODO** | — |

(There is no rule #14 in the TSV. The existing `inconsistent_evidence` check is
an internal evidence-grouping consistency requirement, not one of the numbered
TSV rules.)

This design covers the remaining checks **#3, #4, #5, #6, #10** (relation
validity), **#11, #12** (cardinality), and **#13** (enabler QC), and records the
explicit decision that **#7, #8, #9** are informational only (no criteria
failure). The emphasis, per the issue, is **standardization**: a small shared
layer of classifiers / relation lookups / constants that every check reuses,
and a declarative table for the relation-validity rules.

## Decisions (resolved during brainstorming)

1. **Architecture — hybrid.** A declarative rule-table drives the
   relation-validity rules (#3, #4, #5, #6, #10) through one shared evaluation
   loop. Cardinality (#11, #12), nesting (#7–9), and enabler (#13) reuse the
   same shared classifier / relation-set / constant helpers but remain small
   dedicated blocks (they are not relation lookups, so a pure table is a poor
   fit). Each rule still records under its own `failed_checks` key.

2. **MF/GP → BP relation.**
   - **MF → BP (#5)** accepts `part_of` (BFO:0000050), the
     `acts_upstream_of_or_within` (RO:0002264) family, **and** the
     `causally_upstream_of_or_within` (RO:0002418) family. The RO:0002418 family
     was added by a follow-up decision (2026-06-09): the canonical MOD
     "BP-only annotation" pattern (gene product with unknown/root MF
     `causally_upstream_of_or_within` a BP) uses RO:0002418, and must remain a
     standard, splittable annotation. This is looser than the TSV's literal
     "acts_upstream_of_or_within" wording, by design. #5 still flags clearly-wrong
     relations on root-MF→BP (e.g. `located_in`, `occurs_in`, `is_active_in`,
     `enabled_by`, `contributes_to`).
   - **GP → BP (#4)** accepts **only** the `acts_upstream_of_or_within`
     (RO:0002264) family — `part_of` is **not** accepted for GP→BP, and the
     RO:0002418 relaxation does **not** extend to GP→BP (the follow-up decision
     was scoped to root-MF→BP).
   - **#5 fires only when the MF source is the root MF `GO:0003674`.** A
     non-root MF → BP edge is rule #7 (informational extension), not a failure.
   - **#11 (MF→BP cardinality)** counts MF→BP edges using this same #5 relation
     set (`part_of` ∪ RO:0002264 family ∪ RO:0002418 family), for consistency.

3. **#7, #8, #9 — informational only.** "Exported as extension; nesting is not
   supported" describes export behavior, not a validation failure. The
   validator performs **no** check and records **no** `failed_checks` entry for
   these edge shapes. (The `find_nested_extensions` logic in
   `debug_non_standard.py` is **not** promoted into the filter.)

4. **#13 — separate check.** A distinct `enabler_not_gp` check, complementary
   to #2 (which *skips* non-GP endpoints). Reuses the GP allowlist.

## Reference: relation and term URIs

| Label | URI | Used by |
|-------|-----|---------|
| enables / enabled by | RO:0002333 | #2, #13 |
| contributes to | RO:0002326 | #2 |
| has input | RO:0002233 | #2 (allowed MF→GP extension) |
| has output | RO:0002234 | #2 (allowed MF→GP extension) |
| part of | BFO:0000050 | #5, #11 |
| located in | RO:0001025 | #3 |
| is active in | RO:0002432 | #6 |
| occurs in | BFO:0000066 | #10 |
| acts upstream of or within | RO:0002264 (+11 children) | #4, #5 |
| causally upstream of or within | RO:0002418 (+22 children) | #1 (existing) |
| root MF | GO:0003674 | root checks |
| root BP | GO:0008150 | root checks |
| root CC | GO:0005575 | root checks |

Verified fact: `RO:0002264` (acts upstream of or within) is **not** a descendant
of `RO:0002418` (causally upstream of or within); they are separate branches.
`RO:0002413` (provides input for) **is** a descendant of `RO:0002418`, so a
*materialized* `provides_input_for` MF→MF edge is already caught by the existing
`mf_causal_mf` (#1).

## Shared infrastructure (added to `GoCamGraphBuilder`)

This is the standardization core; every new check is built from it.

### Constants

```python
# Aspect root terms (GO_xxxxxxx full URIs)
ROOT_GO_TERMS = {
    "http://purl.obolibrary.org/obo/GO_0003674",  # molecular_function
    "http://purl.obolibrary.org/obo/GO_0008150",  # biological_process
    "http://purl.obolibrary.org/obo/GO_0005575",  # cellular_component
}

# Anatomical-structure namespaces, keyed like GP_NAMESPACE_KEYS (the OBO PURL
# prefix / identifiers.org path segment from _gene_product_namespace_key).
# Seeded from go-site metadata + the model corpus. (Exact membership is a
# spec-review point.)
ANATOMY_NAMESPACE_KEYS = {
    "cl", "uberon", "emapa", "wbbt", "fbbt", "zfa", "ma", "po",
    # plus any further MOD anatomy ontologies confirmed during review
}
```

### Cached relation URIs (resolved once in `__init__`)

Replace the scattered `URIRef(relations.lookup_label(...))` calls in the filter
with instance attributes resolved once:

```python
self.rel_enabled_by    = URIRef(relations.lookup_label("enabled by"))      # RO:0002333
self.rel_contributes_to= URIRef(relations.lookup_label("contributes to"))  # RO:0002326
self.rel_has_input     = URIRef(relations.lookup_label("has input"))       # RO:0002233
self.rel_has_output    = URIRef(relations.lookup_label("has output"))      # RO:0002234
self.rel_part_of       = URIRef(relations.lookup_label("part of"))         # BFO:0000050
self.rel_located_in    = URIRef(relations.lookup_label("located in"))      # RO:0001025
self.rel_is_active_in  = URIRef(relations.lookup_label("is active in"))    # RO:0002432
self.rel_occurs_in     = URIRef(relations.lookup_label("occurs in"))       # BFO:0000066
```

And, only when an RO ontology is provided (alongside the existing
`self.causal_relations`):

```python
ACTS_UPSTREAM_OF_OR_WITHIN = "http://purl.obolibrary.org/obo/RO_0002264"
self.acts_upstream_relations = get_relation_descendants(
    self.ro_ontology, ACTS_UPSTREAM_OF_OR_WITHIN)   # set of URI strings, 11 members
```

(When no RO ontology is provided, `self.acts_upstream_relations = set()`, mirroring
the existing `self.causal_relations` handling.)

### Helpers

```python
def _go_aspect(self, type_node, graph) -> str | None:
    """Return "MF" | "BP" | "CC" | None for an individual's type node.
    MF resolution goes through _resolve_mf_type (NOT/owl:complementOf aware);
    BP/CC use the existing uri_is_biological_process / uri_is_cellular_component.
    Single entry point so every check classifies aspect identically."""

def _is_root_go_term(self, uri) -> bool:
    """True if str(uri) in ROOT_GO_TERMS."""

def _is_anatomical_structure(self, type_uri) -> bool:
    """True if the target is a CC (GO) OR its namespace key
    (_gene_product_namespace_key) is in ANATOMY_NAMESPACE_KEYS.
    Used by #10 (BP→CC/anatomy) and #12 (MF→anatomy cardinality)."""
```

`_go_aspect` consolidates the per-edge classification that #3–#13 all need
(`source_aspect`, `target_aspect`). `_resolve_mf_type`,
`uri_is_molecular_function/_biological_process/_cellular_component`, and
`_gene_product_namespace_key` / `GP_NAMESPACE_KEYS` are reused unchanged.

## Relation-validity engine + table (#3, #4, #5, #6, #10)

A declarative list of rule specs, evaluated by one shared loop over
`std_annot.edges`. For each edge: classify `source_aspect` / `target_aspect`,
GP-ness, anatomy-ness, and root-ness; find the first matching rule; if
`edge.property_uri` is not in the rule's valid set, record the edge bnode under
the rule's `failed_checks` key.

Rule rows (built once, after relation sets are resolved):

| # | source category | target category | valid relation(s) | `failed_checks` key | needs RO |
|---|-----------------|-----------------|-------------------|---------------------|----------|
| 3 | GP | CC, non-root | `{located_in}` | `invalid_gp_cc_relation` | no |
| 4 | GP | BP (incl. root) | `acts_upstream_relations` | `invalid_gp_bp_relation` | yes |
| 5 | MF **and** root (`GO:0003674`) | BP, non-root | `{part_of} ∪ acts_upstream_relations ∪ causal_relations (RO:0002418 family)` | `invalid_mf_bp_relation` | yes |
| 6 | MF **and** root (`GO:0003674`) | CC, non-root | `{is_active_in}` | `invalid_mf_cc_relation` | no |
| 10 | BP | CC or anatomy | `{occurs_in}` | `invalid_bp_cc_relation` | no |

Notes:
- **Direction.** In the TTL the "actor" (GP / MF / BP) is the `annotatedSource`
  and the location/process/structure is the `annotatedTarget` — verified across
  real models (`MF─enabled_by→GP`, `root-MF─RO:0002418→BP`, `BP─occurs_in→CL`,
  `CL─part_of→EMAPA`). The engine classifies by source vs. target accordingly.
- **Category matching.** "GP" = `_gene_product_namespace_key(type) ∈
  GP_NAMESPACE_KEYS`. "CC/BP/MF" via `_go_aspect`. "non-root" via
  `not _is_root_go_term`. "anatomy" via `_is_anatomical_structure`.
- **RO-dependent rows (#4, #5)** are skipped when no RO ontology is loaded
  (`acts_upstream_relations` empty) — same policy as `mf_causal_mf`. The
  exact-match rows (#3, #6, #10) run regardless.
- **Out of scope by construction:** GP↔MF edges (#2's domain), MF↔MF edges
  (#1's domain), non-root-MF→BP/CC and BP→BP edges (#7/#8/#9 — match no row,
  no failure). A `located_in`/`is_active_in` on a *root* CC target matches no
  row (#3/#6 require non-root), so it is not flagged.
- **Backbone-only gate (2026-06-09 decision).** The relation-validity rules
  validate the annotation **backbone** only. Before rule matching, an edge is
  skipped unless its relation is in `self.backbone_relations` — the set of
  recognized backbone/placement relations: `{located_in, is_active_in,
  occurs_in, part_of}` ∪ the acts_upstream (RO:0002264) family ∪ the
  causally_upstream (RO:0002418) family. Edges using any other relation are
  annotation **extensions** (e.g. `BP ─results_in_development_of(RO:0002296)→
  anatomy`, `BP ─acts_on_population_of(RO:0012003)→ CL`) and are informational,
  not failures — consistent with the #7/#8/#9 decision. A *misused* placement
  relation (e.g. `located_in` on a `BP→CC` edge where `occurs_in` is required)
  is still in `backbone_relations`, so its rule still flags it. This avoids the
  circularity of `get_extension_edges` (which classifies backbone by relation
  pattern, so a wrong-relation edge would wrongly escape as an "extension").
  Rationale: applying the rules to *every* matching-shape edge wrongly
  reclassified ~35% of real models (e.g. `MGI_MGI_1100089` 28→24 standard) by
  flagging legitimate extension edges; the backbone gate restores 28.
- **#6 and `occurs_in` (2026-06-09 decision).** `occurs_in` is a recognized
  backbone relation, so a `root-MF ─occurs_in→ CC` edge IS validated by #6 and
  flagged `invalid_mf_cc_relation` (the MF→CC placement relation must be
  `is_active_in`). `SYNGO_5371` is therefore correctly non-standard.
- **Per-rule firing semantics:** among backbone edges, each rule independently
  records every edge of its `(source, target)` shape whose relation is wrong.
  (Unlike #2 there is no "at least one valid backbone exempts the rest" escape —
  each backbone edge stands alone for #3/#4/#5/#6/#10.)

## Cardinality checks (#11, #12)

These reuse `_go_aspect` / `_is_anatomical_structure` / the relation sets but are
not relation lookups, so they stay as small dedicated blocks.

- **#11 `multiple_mf_bp`** — *refines and replaces* `multiple_mf_part_of`.
  Count edges where `source_aspect == "MF"` and `target_aspect == "BP"` and
  `property_uri ∈ ({part_of} ∪ acts_upstream_relations ∪ causal_relations)` — the
  same MF→BP relation set as #5. If more than one, flag all such edges. (The old
  check counted MF-`part_of` edges regardless of target aspect; the refinement
  narrows to MF→BP and admits the upstream + causal families.)
- **#12 `multiple_mf_anatomy`** — count edges where `source_aspect == "MF"` and
  the target is an anatomical structure (`_is_anatomical_structure`, i.e. CC or
  anatomy namespace). If more than one, flag all such edges.

Default (spec-review point): both count across **all** MF in the annotation, not
only the root MF. Cardinality is evaluated per `StandardAnnotation`, matching the
existing `multiple_mf_part_of` scope.

## Enabler QC (#13) — `enabler_not_gp`

For each edge whose `property_uri == self.rel_enabled_by`, the enabler is the
non-MF (target) endpoint. Flag the edge when
`_gene_product_namespace_key(enabler) ∉ GP_NAMESPACE_KEYS` — this catches GO,
ChEBI, and any non-gene-product enabler. Records under `enabler_not_gp`.

This is complementary to #2: `invalid_gp_mf_relation` *skips* edges whose non-MF
endpoint is not a GP, so a GO/ChEBI enabler currently passes silently; #13 closes
that gap for `enabled_by` edges specifically. Protein complexes (`complexportal`)
and PR (`pr`) are in `GP_NAMESPACE_KEYS`, so they correctly pass.

## Out of scope

- **#7, #8, #9** — informational only; no check, no `failed_checks` entry.
- **#1 parenthetical** — the *inferred* causal relation from a
  `has_output`/`has_input` chain (`has_output_over_has_input == provides_input_for`)
  across two edges. #1 is considered complete: a *materialized* `provides_input_for`
  (RO:0002413) MF→MF edge is already covered by `mf_causal_mf`; the two-edge
  inference is deferred as a separate future item.
- Changes to the statistics report schema or the criteria-fail report schema.
- Changes to evidence splitting, date handling, `get_extension_edges`,
  `get_primary_go_terms`, or `find_nested_extensions`.
- A CLI/Makefile/runtime-fetch mechanism for `ANATOMY_NAMESPACE_KEYS` (hardcoded
  constant, like `GP_NAMESPACE_KEYS`).

## Reporting

Each new rule records per-edge bnode IDs under its own `failed_checks` key, so
`print_non_standard_annotation_failed_checks` surfaces them unchanged: the new
key becomes the `Reason` column and the edge's source/predicate/target labels
fill the remaining columns. No report-schema change. The statistics-report
columns are unchanged (the new checks add to `Non-Standard Annotations` /
`Mixed Annotation Type` naturally; no new dedicated columns unless requested).

## Test plan

Per-helper unit tests plus per-rule pass/fail tests, following the existing
`tests/test_gocam_ttl.py` conventions.

**Helper unit tests**
- `_go_aspect` — MF (incl. NOT/`complementOf`), BP, CC, GP, anatomy, None.
- `_is_root_go_term` — the three roots true; a non-root GO term false.
- `_is_anatomical_structure` — CC (GO) true; CL/UBERON/EMAPA true; GP/ChEBI/MF/BP false.

**Rule tests (reuse existing fixtures where the shape exists)**
- **#10** pass: `MGI_MGI_1100089` / `5966411600000001` (`BP─occurs_in→CL/EMAPA`).
- **#5** pass: `5966411600000001` (`root-MF─part_of→BP`).
- **#5** fail: `MGI_MGI_1100089` (`root-MF─RO:0002418→BP`; RO:0002418 ∉
  `{part_of} ∪ acts_upstream`).
- **#3, #4, #6, #13** — small synthetic fixtures in the established
  `resources/test/*_example.ttl` pattern (mirroring
  `mf_occurs_in_anatomy_example.ttl`, `mf_to_gp_has_input_output_example.ttl`):
  - #3: `GP─located_in→CC` (pass) vs `GP─<wrong>→CC` (fail).
  - #4: `GP─acts_upstream_of_or_within→BP` (pass) vs `GP─part_of→BP` (fail —
    part_of not accepted for GP→BP).
  - #6: `root-MF─is_active_in→CC` (pass) vs `root-MF─<wrong>→CC` (fail).
  - #13: `MF─enabled_by→GP` (pass) vs `MF─enabled_by→GO/ChEBI` (fail).
- **#11** refinement: a fixture with two MF→BP edges (mixing `part_of` and an
  upstream relation) flags both; reconcile/repoint any existing
  `multiple_mf_part_of` test.
- **#12**: a fixture with two MF→anatomy edges flags both.

RO-dependent tests (#4, #5) require `resources/test/ro_20250723.owl`; all tests
require `target/go_20250601.json`.

## Spec-review points

1. `ANATOMY_NAMESPACE_KEYS` exact membership.
2. #11 / #12 count across all MF in the annotation (not only root MF).
3. #1's two-edge inferred-causal treated as out of scope.
4. Renaming `multiple_mf_part_of` → `multiple_mf_bp` (and its test).
