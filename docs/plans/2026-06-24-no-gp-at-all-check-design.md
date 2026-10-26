# `no_gp_at_all` Standard-Annotation Check Design

Branch: `nested-anatomy-extensions`.

## Problem

A standard GO-CAM annotation should connect a gene product (GP) to GO terms.
Some annotation subgraphs contain **no GP at all** (e.g. model
`62f58d8800003667`). These should not be classified as standard annotations.
There is currently no criteria check for this, so a GP-less subgraph can pass
all existing checks and be treated as standard/splittable.

We add a new check, reason `no_gp_at_all`, that flags any annotation whose
subgraph contains no gene-product individual, reported through the existing
criteria-failure report.

## Solution

A new check (#14) in `GoCamGraphBuilder.filter_out_non_std_annotations`, placed
after #13 (`enabler_not_gp`). GP identity reuses the existing
`_gene_product_namespace_key()` + `GP_NAMESPACE_KEYS` allowlist (the same
mechanism used by `enabler_not_gp` and `invalid_gp_mf_relation`).

```python
# Check #14: the annotation subgraph must contain at least one gene product.
# A standard annotation links a GP to GO; a subgraph with no GP at all (e.g. a
# bare anatomy/chemical placement) is not a standard annotation.
has_gp = any(
    self._gene_product_namespace_key(t) in self.GP_NAMESPACE_KEYS
    for edge in std_annot.edges.values()
    for t in (edge.source_type, edge.target_type)
)
if not has_gp:
    failed_checks["no_gp_at_all"] = set(std_annot.edges.keys())
```

- **Scan target.** Every individual in a `StandardAnnotation` is an edge
  endpoint, so scanning each edge's `source_type` and `target_type` covers the
  whole subgraph. This matches how the other GP checks identify gene products.
- **Edges recorded.** The failure is annotation-level (no single offending
  edge), so **all** edges of the annotation are recorded — mirroring
  `inconsistent_evidence`. The criteria-fail report then lists each edge of the
  annotation under reason `no_gp_at_all`.
- **Independent of other checks.** It can co-occur with e.g. `enabler_not_gp`
  (a ChEBI-enabled subgraph with no other GP fails both) — both are recorded;
  neither suppresses the other.

### Reporting and stats integration

- `print_non_standard_annotation_failed_checks` is generic (it iterates
  `failed_checks` keys), so `no_gp_at_all` rows appear in the criteria-fail
  report with no change to that method.
- Add `"no_gp_at_all"` to the module-level `CHECK_NAMES` tuple (→ 13 entries) so
  the extended per-model stats report (`debug_non_standard.py --stats-output`)
  gains a `fail:no_gp_at_all` column. The stats tests reference `CHECK_NAMES`
  dynamically (`set(failure_counts) == set(CHECK_NAMES)`,
  `[f"fail:{n}" for n in CHECK_NAMES]`), so they remain correct.
- Do **not** add it to `NESTING_ATTRIBUTABLE_CHECKS` — it is unrelated to the
  nested-anatomy fixer.

## Blast radius on existing fixtures/tests

Measured empirically against all `resources/test/*.ttl`. Eight fixtures contain
a GP-less annotation; in four, a currently-**standard** annotation is GP-less and
would flip to non-standard:

| Fixture | GP-less annotation | Effect |
|---|---|---|
| `mf_cc_relation_example` | `root-MF ─is_active_in→ CC` (standard) | flips |
| `mf_bp_relation_example` | `root-MF ─causally_upstream→ BP` (standard) | flips |
| `bp_cc_relation_example` | `BP ─occurs_in→ CC` (standard) | flips |
| `mf_occurs_in_anatomy_example` | `MF ─occurs_in→ WBbt` (standard) | flips |
| `R-HSA-9937080`, `enabler_not_gp_example`, `multi_mf_anatomy_example`, `multi_mf_bp_example` | already non-standard | gains `no_gp_at_all` (harmless) |

**No existing test breaks** either way: every affected test asserts on a
*specific* check key (via the `_flagged_props(gocam, "<check>")` helper or
`failed_checks.get("<check>")`), never on exact `failed_checks` equality or on
"is standard". `MGI_MGI_1100089` (the "28 standard annotations" regression) is
not GP-less, so its count is unchanged.

### Decision: keep the four flipping fixtures standard (add a GP)

To preserve each relation-test fixture's intent (a clean, single-defect model
whose passing annotation is genuinely standard), add a GP backbone to the
**standard** annotation of each of the four fixtures. The GP must be added to the
**same connected subgraph** as the standard annotation, and — because the
annotation is currently single-edge — the new edge's evidence must **match** the
existing edge's evidence metadata, or check #1 (`inconsistent_evidence`) would
flip it for a different reason.

Per fixture, add: one GP individual, one backbone edge (reified `owl:Axiom`), one
evidence node duplicating the existing standard edge's evidence
(same ECO type, and for `mf_occurs_in_anatomy_example` also the same
`contributor`/`source`):

| Fixture | Edge to add (into the standard subgraph) | Why valid |
|---|---|---|
| `mf_cc_relation_example` | `mf1 ─enabled_by(RO:0002333)→ GP` | enabled_by MF→GP backbone |
| `mf_bp_relation_example` | `mf1 ─enabled_by→ GP` | enabled_by MF→GP backbone |
| `mf_occurs_in_anatomy_example` | `mf1 ─enabled_by→ GP` (WormBase GP) | enabled_by; also makes #5 pass via a real backbone |
| `bp_cc_relation_example` | `GP ─acts_upstream_of_or_within(RO:0002264)→ bp1` | valid GP→BP relation (#4/#7) |

The **failing** annotation in each fixture is left unchanged; it stays
non-standard and additionally carries a harmless, untested `no_gp_at_all`.

For each edited fixture, verification must confirm: the standard annotation count
is unchanged (still 1 standard), the standard annotation's `failed_checks` is
empty, and the fixture's original target check still fires only on its intended
edge (e.g. `_flagged_props(...) == {RO_0001025}` unchanged).

## New fixture and tests

`resources/test/no_gp_at_all_example.ttl` — two independent single-edge
annotations:
- **A (no GP):** `root-MF (GO:0003674) ─is_active_in(RO:0002432)→ CC (GO:0005634)`.
  Valid relation (#6), no GP ⇒ its only failure is `no_gp_at_all`.
- **B (has GP):** `MF ─enabled_by(RO:0002333)→ MGI-GP`. Valid backbone, has a GP
  ⇒ standard, not flagged.

Tests in `tests/test_gocam_ttl.py`:

1. **`test_no_gp_at_all_flags_gp_less_annotation`** — on the new fixture,
   annotation A has `failed_checks == {"no_gp_at_all"}` and annotation B is
   standard (empty `failed_checks`, not flagged). Verify A's recorded set equals
   A's edge bnode ids (all edges recorded).
2. **`test_no_gp_at_all_in_criteria_report`** — `print_non_standard_annotation_failed_checks`
   on the new fixture emits at least one row whose reason column is
   `no_gp_at_all`.
3. **`test_no_gp_at_all_in_check_names`** — `"no_gp_at_all" in CHECK_NAMES` and
   `"no_gp_at_all" not in NESTING_ATTRIBUTABLE_CHECKS`.
4. **Fixture-edit regressions** — re-assert that the four edited fixtures keep
   exactly one standard annotation with empty `failed_checks`, and that the
   original target-check tests (`test_invalid_mf_cc_relation`,
   `test_invalid_mf_bp_relation`, `test_invalid_bp_cc_relation`,
   `test_gp_mf_relation_ignores_anatomy_target`) still pass unchanged.

The real model `62f58d8800003667` is not in the repo; tests are synthetic. It can
be dropped into `resources/test/` for a manual smoke check.

## What stays the same

- `print_non_standard_annotation_failed_checks` (generic over `failed_checks`).
- `--report-file` base columns and the byte-identical guarantee (the base report
  does not include per-check columns; `no_gp_at_all` only surfaces in the
  criteria-fail report and the extended stats report).
- All other checks and modes.

## Out of scope

- Scoping the check to multi-edge annotations or to a backbone (it applies to
  every annotation).
- Fetching/committing the real `62f58d8800003667` model.
- Suppressing `no_gp_at_all` when another GP-related check (`enabler_not_gp`,
  `invalid_gp_mf_relation`) also fires.

## Docs

Update `CLAUDE.md`'s "Filter Checks" section to document check #14
(`no_gp_at_all`): an annotation whose subgraph contains no gene product (per
`GP_NAMESPACE_KEYS`) is flagged; all edges of the annotation are recorded.

## Test Plan

```bash
# New + regression tests
source env/bin/activate && pytest tests/test_gocam_ttl.py -k "no_gp_at_all or invalid_mf_cc_relation or invalid_mf_bp_relation or invalid_bp_cc_relation or gp_mf_relation_ignores_anatomy" -v

# Full suite (no regressions)
source env/bin/activate && pytest -q
```
Expect all green; the four edited fixtures keep one standard annotation each; the
new fixture's annotation A is flagged `no_gp_at_all` and B is standard.
