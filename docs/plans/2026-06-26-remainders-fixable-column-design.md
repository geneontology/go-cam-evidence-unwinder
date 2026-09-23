# Remainders Report "Fixable" Column Design

Branch: `nested-anatomy-extensions`.

## Problem

The remainders report (`debug_non_standard.py --tsv-output`) emits one TSV row
**per nested extension edge** for the `nested_<aspect>_extensions` buckets
(`Model ID, Title, Bucket, Source, Predicate, Target, ECO Codes, Groups`). A
nested extension is any extension edge whose `source_type` is not the lead
aspect's primary GO term (`find_nested_extensions`).

But `--fix-nested-anatomy` only rewrites a *subset* of those edges — the ones
`GoCamGraphBuilder.plan_nested_anatomy_fixes()` qualifies: the edge must be a
nested extension **and** have **both** endpoints anatomical
(`_is_anatomical_structure()` → GO cellular component or an anatomy-ontology term
such as CL/UBERON/EMAPA) **and** belong to an annotation whose lead aspect has
exactly one primary individual (the `len(primary_individuals) != 1` skip).

So in the current report, the rows the fixer will actually rewrite — e.g.
`CL —[part_of]→ EMAPA` — sit indistinguishably alongside rows it will never
touch, such as `ubiquitin-protein transferase activity —[occurs in]→ nucleus`
(MF source, not anatomy) or `RNA polymerase II, core complex —[has part]→
UniProtKB:P24928` (gene-product target, not anatomy). There is no way to tell,
from the report, which nested rows `--fix-nested-anatomy` covers.

## Solution

Add a single `Fixable` column (values `Yes`/`No`) to the remainders TSV,
indicating whether the `--fix-nested-anatomy` code will rewrite that edge.

**Single source of truth.** Rather than re-implement the both-endpoints-anatomical
test in `debug_non_standard.py` (which could drift from the fixer), ask the
fixer's own planner. Once per model, after parsing and the `delete` skip:

```python
fixable_bnodes = {rec["bnode_id"] for rec in builder.plan_nested_anatomy_fixes(gocam)}
```

`plan_nested_anatomy_fixes()` already encodes the complete qualification (nested
extension ∧ both endpoints anatomical ∧ exactly one primary individual for the
lead aspect). An edge is therefore `Fixable` iff its `bnode_id` is in
`fixable_bnodes`. This guarantees the column can never disagree with what the
fixer actually rewrites — including the rare anomaly where a both-anatomical edge
is *not* fixed because its annotation has an ambiguous (0 or >1) attach point.

**Applied uniformly.** Every `tsv_rows.append(...)` gets
`"Yes" if edge.bnode_id in fixable_bnodes else "No"`. The non-nested buckets
(`multi_mf_same_bp`, `multi_bp_same_mf`, `extension_eco_differs`) emit edges that
are never in the plan, so they correctly read `No` — honest, since
`--fix-nested-anatomy` will not touch them. No bucket-specific special-casing.

**Column placement.** Right after `Target`, so the fixability reads with the edge
it describes:

```
Model ID · Title · Bucket · Source · Predicate · Target · Fixable · ECO Codes · Groups
```

### Worked examples (after the change)

| Source | Predicate | Target | Fixable |
| --- | --- | --- | --- |
| CL:0000202 | part_of | EMAPA:17597 | Yes |
| ubiquitin-protein transferase activity | occurs in | nucleus | No |
| RNA polymerase II, core complex | has part | UniProtKB:P24928 | No |

### RO note

When the debug script is run **without** `-r`, `plan_nested_anatomy_fixes`
detects fewer BP backbones (the `acts_upstream_of_or_within` / causal families
require RO), exactly as the fixer would under the same config — so the `Fixable`
column still matches reality. The report stays consistent with however the fixer
is invoked.

## Changes

Only `debug_non_standard.py` changes. No change to `gocam_ttl.py` or the fixer.

1. **Per-model fixable set.** In the `for ttl_path in ttl_files:` loop, after
   `gocam = builder.parse_ttl(ttl_path)` and the `modelstate == "delete"` skip
   (and after `compute_model_stats`, anywhere before the first `tsv_rows.append`),
   add:

   ```python
   fixable_bnodes = {rec["bnode_id"]
                     for rec in builder.plan_nested_anatomy_fixes(gocam, warn=False)}
   ```

   `plan_nested_anatomy_fixes` is an existing `GoCamGraphBuilder` method, already
   in scope via the `builder` already used in this loop — no new import. Pass
   `warn=False` so report generation doesn't re-emit the planner's
   ambiguous-attach-point warnings once per model (matches the existing
   `compute_model_stats` call, which already invokes the planner with
   `warn=False`).

2. **Header.** Insert `"Fixable"` between `"Target"` and `"ECO Codes"`:

   ```python
   tsv_headers = ["Model ID", "Title", "Bucket", "Source", "Predicate", "Target",
                  "Fixable", "ECO Codes", "Groups"]
   ```

3. **Row tuples.** Each of the three `tsv_rows.append(...)` sites currently emits
   `(model_id, title, bucket, src, rel, tgt, eco_codes, groups)`. Insert the
   fixable value in the same position as the header (after `tgt`,
   before `eco_codes`):

   - **Nested buckets** loop (`for edge in nested_edges:`): the loop variable is
     `edge`, so `"Yes" if edge.bnode_id in fixable_bnodes else "No"`.
   - **`multiple_mf_bp`** sub-classification loop (`for bnode_id in
     annot.failed_checks["multiple_mf_bp"]:` → `edge = annot.edges[bnode_id]`):
     use `bnode_id` (or `edge.bnode_id`) for the membership test.
   - **`extension_eco_differs`** loop (`for edge in annot.edges.values():`): use
     `edge.bnode_id`.

   All three sites already have an edge object (and thus a `bnode_id`) in scope.

## What stays the same

- `plan_nested_anatomy_fixes`, `find_nested_extensions`, and the fixer
  (`--fix-nested-anatomy`) — unchanged. This is a read-only consumer of the plan.
- The bucketing logic, the console (`print`) output, and the per-model stats
  report (`--stats-output`) — unchanged.
- All other TSV columns and their order (only `Fixable` is inserted).

## Out of Scope

- Splitting the `nested_<aspect>_extensions` buckets into anatomy vs. non-anatomy
  bucket names — the `Fixable` column makes that distinction filterable without
  changing bucket identity.
- Reporting *why* a nested edge is not fixable (which endpoint failed, or the
  >1-primary-individual skip) — out of scope; `Fixable` is a single Yes/No.
- Any change to the fixer's behavior or the main pipeline report
  (`gocam_ttl.py --report-file`).

## Test Plan

1. **Full suite regression** — `pytest -q` still passes (the change is confined
   to the debug script; no `gocam_ttl.py` behavior changes).

2. **Integration test the report (primary).** A pure predicate test would only
   re-exercise `plan_nested_anatomy_fixes` (already covered by
   `test_plan_nested_anatomy_fixes_{bp,mf,cc}`) and would pass *before* this
   change — so it is not a real failing-first anchor for the new wiring. Instead,
   add one pytest (in `tests/test_gocam_ttl.py`) that invokes
   `debug_non_standard.main()` against `resources/test/` writing to a temp TSV,
   then parses the TSV and asserts:
   - the header is exactly `[..., "Target", "Fixable", "ECO Codes", "Groups"]`
     (column present and positioned immediately after `Target`);
   - the `5966411600000001` `CL:0000202 —part_of→ EMAPA:17597` row is `Fixable=Yes`;
   - the `multi_mf_anatomy_example` `identical protein binding —RO:0001025→ CL`
     row (non-anatomy MF source) is `Fixable=No`;
   - the `57c82fad00000252` `nucleus —part_of→ WBbt:0005396` row is `Fixable=No`
     even though both endpoints are anatomical — the planner skips its annotation
     for an ambiguous (≠1) primary individual, which a naive both-anatomical check
     would get wrong.

   To keep it fast, the test reuses the session-scoped `builder` fixture by
   monkeypatching `debug_non_standard.GoCamGraphBuilder` (so `main()` does not
   re-parse the GO ontology) while still exercising the full report-writing path.
   This fails before the change (no `Fixable` column) and passes after.

3. **Smoke test the report (secondary).** Run the debug script on the test
   fixtures and eyeball the new column end-to-end:

   ```bash
   source env/bin/activate && python3 debug_non_standard.py resources/test/ \
     -o target/go_20250601.json -r resources/test/ro_20250723.owl \
     --no-label-api --tsv-output /tmp/remainders.tsv
   ```

   Then verify, on `/tmp/remainders.tsv`:
   - The header contains `Fixable` immediately after `Target`.
   - The `5966411600000001` `CL ... part_of ... EMAPA` nested row has
     `Fixable == "Yes"`.
   - At least one nested row whose source/target is not both-anatomical (e.g. a
     row with a non-anatomical MF source, or a gene-product target) has
     `Fixable == "No"`.
