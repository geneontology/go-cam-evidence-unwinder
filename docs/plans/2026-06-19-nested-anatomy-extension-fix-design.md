# Flatten Nested Anatomy Extensions into TTL Fixes Design

Branch: `nested-anatomy-extensions`.

## Problem

`debug_non_standard.py` buckets annotations into `nested_<aspect>_extensions`
rows (see `find_nested_extensions`): an annotation has a *nested* extension when
one of its extension edges (`GoCamGraphBuilder.get_extension_edges`) has a
`source_type` that is **not** the lead aspect's primary GO term
(`get_primary_go_terms` + `pick_lead_aspect`, priority BP > CC > MF).

A common shape is an anatomy chain hanging off the primary term, e.g. in
`resources/test/5966411600000001.ttl` (GO:0120045 BP annotation):

```
root-MF (…0003) ─enabled_by→ GP (…0002)          # MF backbone
root-MF (…0003) ─part_of→    BP (…0004)          # BP backbone  → primary BP individual = …0004
BP (…0004)      ─part_of→    BP GO:0007605 (…0007)
BP (…0004)      ─occurs_in→  CL:0000202 (…0009)   # direct extension (source = primary), kept
CL:0000202 (…0009) ─part_of→ EMAPA:17597 (…0010)  # NESTED: source CL ≠ primary, both anatomy
```

The last edge is *nested*: the anatomy target (`EMAPA:17597`) hangs off an
intermediate anatomy node (`CL:0000202`) rather than off the primary term. We
want these anatomy targets attached **directly** to the annotation's primary
term so they are plain (non-nested) extensions.

## Solution

A new `--fix-nested-anatomy` mode on `gocam_ttl.py`. For each annotation, find
extension edges where **both** endpoints are anatomical structures
(`_is_anatomical_structure()` → anatomy ontologies *plus* GO cellular
components) **and** whose `source_type` is not the lead-aspect primary GO term
(i.e. genuinely nested). Rewrite each such edge **in place** so:

- its source individual becomes the annotation's **primary individual**, and
- its relation becomes `occurs_in` (BFO:0000066) when the lead aspect is MF or
  BP, or stays the edge's existing relation (typically `part_of`) when the lead
  aspect is CC.

`target`, evidence, dates, and contributors are preserved. Each qualifying edge
is de-nested independently (so deeper chains flatten edge-by-edge), and
intermediate placements that are themselves valid direct extensions (e.g.
`BP ─occurs_in→ CL`, whose source is *not* anatomy) are left untouched — nothing
is pruned.

For the worked example the `CL ─part_of→ EMAPA` edge becomes
`BP (…0004) ─occurs_in→ EMAPA (…0010)`; `BP ─occurs_in→ CL` and the `CL`
individual are retained.

### Separation of concerns

Detection lives on `GoCamGraphBuilder` (it already owns `get_extension_edges`,
`get_primary_go_terms`, `_is_anatomical_structure`); the low-level graph
mutation lives on `GoCamGraph` (alongside `update_evidence_date`, `clone_bnode`,
`write_ttl`). The builder produces a *rewrite plan*; `GoCamGraph` applies it.
This preserves the existing dependency direction (builder knows the graph, not
vice-versa) and keeps the pure graph-editor free of ontology classification.

### Change 1: promote shared detection helpers into the library

Move `ASPECT_PRIORITY`, `pick_lead_aspect`, and `find_nested_extensions` from
`debug_non_standard.py` into `gocam_ttl.py`:

- `ASPECT_PRIORITY = ("BP", "CC", "MF")` — module constant.
- `pick_lead_aspect(primary_terms)` — module function (no builder needed).
- `find_nested_extensions(annot, builder)` — module function (unchanged
  signature; still takes the builder). Kept as a free function rather than a
  builder method so its `(annot, builder)` call sites in `debug_non_standard.py`
  need no change beyond the import source.

`debug_non_standard.py` re-imports them so its existing call sites — and the
`from debug_non_standard import pick_lead_aspect` used by
`test_mgi_2182965_lead_aspect_is_mf` — keep working:

```python
from gocam_unwinder.gocam_ttl import (
    GoCamGraphBuilder,
    collect_model_files,
    load_skip_filenames,
    pick_lead_aspect,
    find_nested_extensions,
)
```

### Change 2: new `GoCamGraphBuilder.get_primary_individuals(annot)`

Mirror of `get_primary_go_terms`, but collects the backbone *individual* URI per
aspect instead of the type URI. Same `_backbone_role` dispatch:

- MF role → `edge.source_uri` (the MF individual)
- BP role → `edge.target_uri` (the BP individual)
- CC role → `edge.target_uri` (the CC individual)

Returns `dict[aspect → list[URIRef]]`; aspect keys absent when no backbone match.
Lists are length 1 in well-formed data (longer surfaces anomalies, like
`get_primary_go_terms`).

### Change 3: new `GoCamGraphBuilder.plan_nested_anatomy_fixes(gocam)`

The planner. Iterates **all** annotations
(`gocam.standard_annotations + gocam.non_standard_annotations`, matching the
debug script's nested-extension scan). For each annotation:

1. `lead, nested = find_nested_extensions(annot, self)`; skip if `lead is None`
   or `nested` is empty.
2. `primaries = self.get_primary_individuals(annot).get(lead, [])`. Require
   exactly one primary individual; if `len(primaries) != 1`, skip this
   annotation and emit a warning (anomalous shape — ambiguous attach point).
3. Keep only nested edges where **both** `source_type` and `target_type`
   satisfy `self._is_anatomical_structure(...)`.
4. New relation per lead aspect: `occurs_in` (BFO:0000066) when `lead in
   {"MF", "BP"}`, else the edge's existing `property_uri`.
5. Emit a rewrite instruction dict per qualifying edge. It carries both the
   *individual* URIs needed for the graph mutation and the *type* URIs needed for
   label resolution in the report:

```python
{
    "model_id": gocam.model_id,
    "title": gocam.title,
    "lead_aspect": lead,
    "primary_term": primary_term,        # get_primary_go_terms(annot)[lead][0]
    "bnode_id": edge.bnode_id,
    # individual URIs — used by the mutation
    "old_source_uri": edge.source_uri,
    "old_property_uri": edge.property_uri,
    "target_uri": edge.target_uri,
    "new_source_uri": primary_individual,  # the one get_primary_individuals[lead]
    "new_property_uri": new_property,      # occurs_in (MF/BP) or edge.property_uri (CC)
    # type URIs — used only for report labels
    "old_source_type": edge.source_type,   # the intermediate anatomy term
    "target_type": edge.target_type,
}
```

`primary_term` is `get_primary_go_terms(annot)[lead][0]` — the lead aspect's
primary GO term URI, which is also the type of `new_source_uri` (the primary
individual). Returns the list of instruction dicts (empty when nothing
qualifies).

### Change 4: new `GoCamGraph.rewrite_edge_source_and_relation(...)`

The mutation. Signature:

```python
def rewrite_edge_source_and_relation(
    self, bnode_id, old_source, old_property, target, new_source, new_property
):
```

Updates **both** representations of the edge:

- **Assertion triple:** `self.g.remove((old_source, old_property, target))`,
  then `self.g.add((new_source, new_property, target))`.
- **OWL axiom bnode** (only if it exists — defensive, in case a bare assertion
  was never reified): on `rdflib.term.BNode(bnode_id)`, swap
  `owl:annotatedSource` (`old_source` → `new_source`) and `owl:annotatedProperty`
  (`old_property` → `new_property`). `owl:annotatedTarget`, `lego:evidence`,
  dates, and contributors are untouched.

OWL constants reuse `rdflib.namespace.OWL` as elsewhere in the file.

### Change 5: CLI wiring in `main()`

Add:

- `--fix-nested-anatomy` (flag, store_true) — enable the mode.
- reuse existing `--output-dir` for the fixed-model output directory.
- `--nested-fix-report PATH` — optional TSV of every rewritten edge.

Flow per model (only when `--fix-nested-anatomy`):

```
gocam = builder.parse_ttl(ttl)
if gocam.modelstate == "delete": skip
plan = builder.plan_nested_anatomy_fixes(gocam)
if not plan: continue                       # only changed models are written
for r in plan:
    gocam.rewrite_edge_source_and_relation(
        r["bnode_id"], r["old_source_uri"], r["old_property_uri"],
        r["target_uri"], r["new_source_uri"], r["new_property_uri"])
    report_records.append(r)
gocam.write_ttl(<output-dir>/<original filename>)
# after the loop, if --nested-fix-report: write TSV (labels via term_label)
```

### Report columns

`Model ID, Title, Lead Aspect, Primary Term, Old Source, Old Relation, Target,
New Relation`

Labels are resolved via `builder.term_label()` on the *type* URIs in the
instruction dict (mirrors `--date-change-report`):

- `Primary Term` = `term_label(primary_term)` — also the new source's type, so
  no separate "New Source" column is needed (it would always equal Primary Term).
- `Old Source` = `term_label(old_source_type)` — the intermediate anatomy term.
- `Old Relation` = `term_label(old_property_uri)`.
- `Target` = `term_label(target_type)` — unchanged by the rewrite.
- `New Relation` = `term_label(new_property_uri)` — `occurs_in` or the kept
  relation.

## What stays the same

- The classification pipeline (`parse_ttl`, `filter_out_non_std_annotations`)
  and all `failed_checks` logic — the fixer reads annotations after
  classification but does not change how they are classified.
- `--split-evidence` and every other existing mode and flag.
- `debug_non_standard.py` behavior and its TSV schema — only its import source
  for the three promoted helpers changes.

## Out of Scope

- Re-classifying or re-validating the rewritten models (no second
  `filter_out_non_std_annotations` pass on the fixed graph).
- Pruning intermediate anatomy nodes or their direct-extension edges — only the
  qualifying nested edges are rewritten; the model is otherwise preserved.
- Annotations with 0 or >1 primary individual for the lead aspect — skipped with
  a warning, not fixed.
- Nested extensions whose endpoints are not both anatomical structures (e.g. a
  nested BP→BP or MF→GP chain) — out of this fix's scope.
- Combining `--fix-nested-anatomy` with `--split-evidence` in one pass — the
  modes are independent; the fixer is invoked on its own.

## Test Plan

New tests in `tests/test_gocam_ttl.py` (require `target/go_20250601.json`;
RO-independent cases need no RO, but the fixture run mirrors existing tests):

1. **`test_get_primary_individuals`** — on `5966411600000001.ttl`, the GO:0120045
   annotation returns `{"MF": [<…0003>], "BP": [<…0004>]}` (each length 1, `CC`
   absent). Confirms the individual (not type) is returned.

2. **`test_rewrite_edge_source_and_relation`** — low-level mutation on a parsed
   graph: after rewriting one edge, the new `(new_source, new_property, target)`
   assertion triple is present, the old `(old_source, old_property, target)`
   triple is gone, the axiom bnode's `annotatedSource`/`annotatedProperty` are
   swapped, and `annotatedTarget` + `lego:evidence` are preserved.

3. **`test_plan_nested_anatomy_fixes`** — on `5966411600000001.ttl`, the plan
   contains exactly one instruction: the `CL:0000202 ─part_of→ EMAPA:17597` edge,
   with `new_source_uri == …0004` (primary BP individual), `new_property_uri ==
   occurs_in`, `lead_aspect == "BP"`. The `BP ─occurs_in→ CL` edge is **not** in
   the plan (source not anatomy).

4. **Aspect-relation branches** — a synthetic MF-led fixture (nested anatomy edge
   under an `MF ─enabled_by→ GP` annotation) yields `occurs_in`; a synthetic
   CC-led fixture (nested anatomy edge under a `? ─located_in/is_active_in→ CC`
   annotation) **keeps** the original relation (`part_of`). New fixtures added to
   `resources/test/` only if no existing model has the exact shape.

5. **Regression** — `debug_non_standard.py` still imports successfully and its
   `pick_lead_aspect` / `find_nested_extensions` resolve from the new location;
   `test_mgi_2182965_lead_aspect_is_mf` (which imports `pick_lead_aspect` from
   `debug_non_standard`) still passes.

Run: `pytest tests/test_gocam_ttl.py -v`.

Smoke-test the CLI against the fixtures:

```bash
python src/gocam_unwinder/gocam_ttl.py -d resources/test/ \
  -o target/go_20250601.json -r resources/test/ro_20250723.owl \
  --fix-nested-anatomy --output-dir /tmp/nested_fix_out \
  --nested-fix-report /tmp/nested_fix_report.tsv
```

Expect `5966411600000001.ttl` written to the output dir with the rewritten edge,
and one corresponding row in the report TSV.
