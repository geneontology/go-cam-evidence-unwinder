---
title: De-nest nested anatomy extensions onto the extension-start node — design
date: 2026-06-30
status: draft
---

# De-nest onto the extension-start node — Design

## Goal

Change `--fix-nested-anatomy` so a nested anatomy extension edge is re-pointed onto
the node where its extension **chain departs from the backbone** (the "extension
starting individual"), instead of always the lead-aspect **primary** individual.

In every model seen so far the two coincide, so this is invisible until the chain
starts at a backbone node other than the lead primary. `5fb9cc0600000760` (Xenbase,
`/Users/ebertdu/go/noctua-models/models/5fb9cc0600000760.ttl`) is the first such
case and the reference example for this design.

## Reference example: `5fb9cc0600000760`

A single connected subgraph (individual short-ids; `…N` = the model individual ending in N):

| edge | source | rel | target | role |
|---|---|---|---|---|
| `…761 → …762` | root MF (GO:0003674) | `enabled_by` (RO:0002333) | GP (Xenbase gene) | MF backbone |
| `…761 → …764` | root MF (GO:0003674) | `part_of` (BFO:0000050) | BP (GO:0003401 axis elongation) | **BP backbone → lead aspect = BP** |
| `…761 → …766` | root MF (GO:0003674) | `occurs_in` (BFO:0000066) | CC (GO:0005771 multivesicular body) | direct extension — **departs from the root MF** |
| `…766 → …768` | CC (GO:0005771 multivesicular body) | `part_of` (BFO:0000050) | CC (GO:0005770 late endosome) | **nested** both-anatomical extension |

Today `plan_nested_anatomy_fixes` re-points the nested edge (`…766 ─part_of→ …768`)
onto the **lead-aspect primary individual** — the BP `…764` — producing
`axis elongation ─occurs_in→ late endosome`. The correct target is the node where
the extension chain departs from the backbone: the **root MF `…761`**, producing
`root MF ─occurs_in→ late endosome`.

## The concept: "extension starting individual"

For a nested anatomy edge, the **extension starting individual** is the backbone
individual where the edge's extension chain departs from the backbone — found by
walking *backwards* through extension edges from the nested edge's source until a
**backbone individual** is reached.

A *backbone individual* is any endpoint of a backbone edge (`_backbone_role(edge)
is not None`): the MF and GP of `MF ─enabled_by→ GP`, the MF and BP of
`root-MF ─part_of→ BP`, the GP and CC of `GP ─located_in/is_active_in→ CC`.

The new relation is the relation of the **direct extension edge** — the edge that
leaves the backbone individual toward the chain. This is the boundary-leaving edge
the walk traverses *last* (`occurs_in` for the MF/BP-led examples, `part_of` for the
CC-led one), and is semantically sound: `X ─occurs_in→ a` + `a ─part_of→ b` ⟹
`X ─occurs_in→ b`; likewise for `part_of`.

### Why a chain-walk and not the anatomy-region boundary edge

A boundary-edge definition (non-anatomical endpoint of the anatomy region's single
boundary edge) is wrong for **CC-led** annotations: the primary CC is *itself*
anatomical (`_is_anatomical_structure` is `True` for GO cellular components), so it
is absorbed into the anatomy region and the region's only boundary edge is
`GP ─located_in→ CC` — which would re-point the nested edge onto the **GP**, not the
CC. The chain-walk stops at the CC (a backbone individual) and is correct in every
aspect:

| example | nested edge | chain-walk → start (rel of leaving edge) |
|---|---|---|
| `5fb9cc0600000760` (BP-led) | `mvb ─part_of→ late endosome` | **root MF `…761`** (≠ BP primary `…764`) — `occurs_in` |
| `5966411600000001` (BP-led) | `CL ─part_of→ EMAPA` | BP `…0004` (= primary) — `occurs_in` |
| `mf_nested_anatomy_example` (MF-led) | `CL ─part_of→ EMAPA` | MF `mf1` (= primary) — `occurs_in` |
| `cc_nested_anatomy_example` (CC-led) | `CL ─part_of→ EMAPA` | CC `cc1` (= primary, **not** the GP) — `part_of` |

Only `5fb9cc0600000760` changes; the other three already attach to the chain-start
node because there the chain departs from the lead primary.

## Design decisions (from brainstorming)

1. **New relation = the chain-start (direct extension) edge's relation.** Generalizes
   the current `occurs_in`-for-MF/BP / keep-for-CC heuristic and reproduces every
   existing fixture's output.
2. **Minimal scope.** Both existing gates stay **unchanged**:
   - `_anatomy_attachment_is_simple(annot)` (every anatomy region ≤ 1 boundary edge), and
   - lead aspect has exactly 1 primary individual.
   Only the attach **target** (source + relation) changes. This preserves all
   current behavior, including `57c82fad00000252`'s nucleus edge staying
   `Fixable=No` (its lead primary is ambiguous, so it is still gated out).

   `_anatomy_attachment_is_simple` is **not** refactored — the chain-walk does not
   reuse its boundary computation, so the gate is left exactly as-is.

## API

### New helper on `GoCamGraphBuilder`

```python
def _extension_chain_start(self, annot: StandardAnnotation,
                           edge: StandardAnnotationEdge):
    """
    Walk the extension chain backwards from `edge.source_uri` to the backbone
    individual where the chain departs from the backbone.

    Returns (start_uri, start_type, relation_uri):
      start_uri:     the backbone individual the chain attaches to
      start_type:    that individual's type node (for the report's New Source col)
      relation_uri:  the property of the direct extension edge that leaves the
                     backbone individual toward the chain

    Returns None when the chain start is ambiguous: a traversed node has != 1
    extension-edge predecessor, a cycle is hit, or no backbone individual is
    reached. Callers skip such edges.
    """
```

Algorithm:

```python
extension_edges = self.get_extension_edges(annot)
backbone_individuals = set()
for e in annot.edges.values():
    if self._backbone_role(e) is not None:
        backbone_individuals.add(e.source_uri)
        backbone_individuals.add(e.target_uri)
uri_to_type = {}
for e in annot.edges.values():
    uri_to_type[e.source_uri] = e.source_type
    uri_to_type[e.target_uri] = e.target_type

current = edge.source_uri
relation = None
visited = set()
while current not in backbone_individuals:
    if current in visited:
        return None                       # cycle
    visited.add(current)
    preds = [e for e in extension_edges if e.target_uri == current]
    if len(preds) != 1:
        return None                        # ambiguous / dead-end
    relation = preds[0].property_uri       # relation of edge leaving preds[0].source
    current = preds[0].source_uri
if relation is None:
    return None                            # edge.source was already a backbone node
return current, uri_to_type.get(current), relation
```

The loop exits with `current` = the backbone individual and `relation` = the
property of the edge whose source is that individual (the direct extension edge).

## Algorithm change in `plan_nested_anatomy_fixes`

Unchanged: iterate annotations, `find_nested_extensions`, **both gates**
(`_anatomy_attachment_is_simple` and lead-primary-count == 1), and the per-edge
both-anatomical filter. `lead_aspect` / `primary_term` are still computed and kept in
the instruction dict.

Changed: inside the per-edge loop, replace the two lines that set the new
source/relation with a chain-walk lookup, and add `new_source_type`:

```python
# before
if lead in ("MF", "BP"):
    new_property = self.rel_occurs_in
else:
    new_property = edge.property_uri
... "new_source_uri": primary_individual, "new_property_uri": new_property ...

# after
start = self._extension_chain_start(annot, edge)
if start is None:
    if warn:
        print(f"WARNING: skipping nested edge in {gocam.model_id} "
              f"({gocam.title}) — ambiguous extension chain start")
    continue
new_source_uri, new_source_type, new_property = start
... "new_source_uri": new_source_uri, "new_property_uri": new_property,
    "new_source_type": new_source_type ...
```

Instruction-dict keys gain **`new_source_type`**; all existing keys are unchanged.
`primary_individual` is no longer used to set the source (the gate that produced it
stays).

## Report change: `--nested-fix-report`

The report has no "New Source" column today because the source was always the lead
primary (already shown as "Primary Term"). It can now differ, so add **New Source**
(label of `new_source_type`) between `Target` and `New Relation`, so the row reads as
the actual rewrite: `(Old Source, Old Relation, Target) → (New Source, New Relation, Target)`.

New header:

```
Model ID, Title, Lead Aspect, Primary Term, Old Source, Old Relation, Target, New Source, New Relation
```

## Testing

- **New fixture:** copy `5fb9cc0600000760.ttl` into `resources/test/`.
- **Helper unit test** `test_extension_chain_start`: on `5fb9cc0600000760`,
  `_extension_chain_start(annot, nested_edge)` returns the root MF `…761` and
  `occurs_in`; on `cc_nested_anatomy_example` it returns the CC `cc1` and `part_of`
  (NOT the GP).
- **Planner test** `test_plan_nested_anatomy_fixes_chain_start_differs_from_primary`:
  on `5fb9cc0600000760`, `plan_nested_anatomy_fixes` yields exactly one instruction —
  `old_source_uri = …766`, `target_uri = …768`,
  **`new_source_uri = …761` (root MF, NOT the BP primary `…764`)**,
  `new_property_uri = occurs_in`. Assert
  `new_source_uri != get_primary_individuals(annot)["BP"][0]`.
- **Remainders `Fixable`:** extend `test_remainders_report_fixable_column` to assert
  `5fb9cc0600000760`'s `multivesicular body ─part of→ late endosome` row is
  `Fixable=Yes` (bucket `nested_bp_extensions`).
- **Regression:** existing `test_plan_nested_anatomy_fixes_bp/mf/cc`,
  `test_anatomy_attachment_is_simple`, `test_plan_nested_anatomy_fixes_skips_multi_boundary`,
  `test_plan_nested_anatomy_fixes_warn_param`, and the rest of the suite must pass
  unchanged — their chain-start node already equals the asserted `new_source_uri` and
  their leaving-edge relation already equals the asserted `new_property_uri`.

## Out of scope

- Dropping the lead-primary-count gate (would expand the fixer to ambiguous-primary
  annotations such as `57c82fad00000252`).
- Relation *composition* beyond inheriting the direct extension edge's relation.
- Any GPAD-export changes.
