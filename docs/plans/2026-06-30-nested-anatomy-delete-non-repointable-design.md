---
title: Restrict nested-anatomy re-pointing to spatial chains; delete the rest — design
date: 2026-06-30
status: implemented
---

# Restrict nested-anatomy re-pointing; delete non-re-pointable chains — Design

## Goal

Stop `--fix-nested-anatomy` from silently mis-rewriting a nested anatomy extension
edge when its extension chain departs the backbone via a **non-containment**
relation. Concretely:

- **Re-point** (source-modifying fix) is now allowed **only** when the extension
  chain-start relation is `occurs_in`, `part_of`, or `is_active_in` **and** every
  other relation in the chain is `part_of`.
- Otherwise, when a both-anatomical nested edge is still fixer-qualified but its
  chain is not re-pointable, **delete** the nested edge instead (plus its reified
  axiom, its evidence individuals, and any individual it orphans).

Builds directly on [`2026-06-30-extension-start-node-denesting-design.md`](2026-06-30-extension-start-node-denesting-design.md),
which introduced re-pointing onto the extension-start individual and inheriting
"whatever relation that edge actually uses." This change adds the guard that "whatever
it uses" is only safe for spatial-containment relations.

## Motivation / reference example: `5c4605cc00000891`

Real Zebrafish (ZFIN) model
(`/Users/ebertdu/go/noctua-models/models/5c4605cc00000891.ttl`), four structurally
identical subgraphs. One subgraph (individual short-ids; `…N` = model individual
ending in N):

| edge | source | rel | target | role |
|---|---|---|---|---|
| `…893 → …892` | root MF (GO:0003674) | `enabled_by` (RO:0002333) | GP (ZFIN gene) | MF backbone |
| `…893 → …1956` | root MF (GO:0003674) | `causally_upstream_of_or_within` (RO:0002418) | BP (GO:0001708 cell fate specification) | **BP backbone → lead aspect = BP** |
| `…1956 → …894` | BP (GO:0001708) | `part_of` (BFO:0000050) | BP (GO:0021984 adenohypophysis development) | extension (BP→BP) |
| `…1956 → …902` | BP (GO:0001708) | `results_in_specification_of` (RO:0002356) | ZFA:0009204 (anatomy) | direct extension — **departs the backbone via RO:0002356** |
| `…902 → …903` | ZFA:0009204 (anatomy) | `part_of` (BFO:0000050) | ZFA:0005580 (anatomy) | **nested** both-anatomical extension |

The nested `…902 ─part_of→ …903` edge is both-anatomical and the annotation passes
both existing gates, so the extension-start-node fixer detected it and re-pointed it
onto the chain-start backbone individual (`…1956`), **inheriting the chain-start
relation** — which here is `results_in_specification_of`. The result was a fabricated
`cell fate specification ─results_in_specification_of→ ZFA:0005580` edge: wrong
semantics (the specification results in the *specific* region `…902`, not the broad
parent `…903`).

The chain departs the backbone via a relation the fixer's re-point logic was never
designed for. Re-pointing is only sound for spatial containment
(`X ─occurs_in→ a` + `a ─part_of→ b` ⟹ `X ─occurs_in→ b`); it is **not** sound for
`results_in_specification_of`. So such edges must not be re-pointed.

## Design decisions

Confirmed with the user (via clarifying questions) before implementation:

1. **Re-pointable predicate.** A nested anatomy edge's chain is re-pointable iff the
   **chain-start relation** (the direct extension edge leaving the backbone) is
   `occurs_in`, `part_of`, or `is_active_in`, **and** every *other* relation in the
   chain — the intermediate hops and the nested edge's own relation — is `part_of`.
2. **Deletion trigger = any non-re-pointable qualifying edge.** If a both-anatomical
   nested edge meets the base qualifications (nested, both endpoints anatomical, simple
   attachment, exactly one lead primary individual) but is **not** re-pointable — for
   *any* reason (bad chain-start relation OR a non-`part_of` relation later in the
   chain) — it is deleted rather than left unfixed.
3. **Deletion scope = the nested edge(s) only, plus orphan cleanup.** Delete the
   anatomy→anatomy nested edge (assertion + reified `owl:Axiom` bnode). **Keep** the
   direct backbone→anatomy extension (`…1956 ─results_in_specification_of→ …902`).
   Then remove any individual the deletion orphaned so nothing dangles — the deep
   anatomy target (`…903`) **and** the deleted edge's evidence individuals (the ECO
   nodes).
4. **Report an `Action` column.** `--nested-fix-report` gains a trailing `Action`
   column (`rewrite` / `delete`); `New Source` / `New Relation` are blank for
   `delete` rows.

## API

### Chain walk split into walk + start-wrapper (`GoCamGraphBuilder`)

`_extension_chain_start` previously discarded the intermediate relations. It is
refactored so the re-pointability check can see the full chain:

```python
def _walk_extension_chain(self, annot, edge):
    """Walk backwards from edge.source_uri to the backbone individual where the
    chain departs. Returns (start_uri, start_type, relations) — relations is the
    ordered property list, nearest-first, so relations[-1] is the chain-start
    (direct-extension) relation; the nested edge's own property is NOT included.
    Returns None on ambiguity (a traversed node has != 1 extension-edge
    predecessor, a cycle, or no backbone individual reached)."""

def _extension_chain_start(self, annot, edge):
    """Thin wrapper: returns (start_uri, start_type, relations[-1]) or None."""
```

`_extension_chain_start`'s public contract is unchanged (still returns
`(start_uri, start_type, relation_uri)` = the chain-start relation), so
`test_extension_chain_start` is untouched.

### Re-pointability predicate (`GoCamGraphBuilder`)

```python
def _nested_chain_is_repointable(self, edge, relations):
    """True iff relations[-1] (chain start) is occurs_in/part_of/is_active_in AND
    every other relation — relations[:-1] plus edge.property_uri — is part_of."""
    allowed_start = {str(self.rel_occurs_in), str(self.rel_part_of),
                     str(self.rel_is_active_in)}
    if str(relations[-1]) not in allowed_start:
        return False
    rest = list(relations[:-1]) + [edge.property_uri]
    return all(str(r) == str(self.rel_part_of) for r in rest)
```

### Edge deletion + orphan pruning (`GoCamGraph`)

```python
def delete_edge(self, bnode_id, source, property, target):
    """Remove the assertion triple AND the whole reified owl:Axiom bnode. Returns
    the axiom's evidence-individual URIs (captured before the bnode is removed) so
    the caller can add them to the orphan-prune candidates."""

def _individual_has_edges(self, ind):
    """True if ind is the object of any remaining triple, or the subject of any
    outgoing OBO-namespace relation assertion. Declaration triples (rdf:type,
    layout, providedBy, dates) do not count."""

def prune_orphan_individuals(self, candidate_uris):
    """Remove every candidate with no remaining edges (_individual_has_edges),
    deleting all triples with it as subject. Called once after all delete_edge()
    calls so multi-hop chains resolve in a single pass. Returns pruned URIs."""
```

## Algorithm change in `plan_nested_anatomy_fixes`

Unchanged: iterate annotations, `find_nested_extensions`, **both gates**
(`_anatomy_attachment_is_simple` and lead-primary-count == 1), and the per-edge
both-anatomical filter. `lead_aspect` / `primary_term` still kept in the instruction.

Changed: inside the per-edge loop, walk the chain, then branch on re-pointability and
stamp an `action`:

```python
walk = self._walk_extension_chain(annot, edge)
if walk is None:
    if warn: print("... ambiguous extension chain start")
    continue
start_uri, start_type, relations = walk
if self._nested_chain_is_repointable(edge, relations):
    action = "rewrite"
    new_source_uri, new_source_type, new_property = start_uri, start_type, relations[-1]
else:
    action = "delete"
    new_source_uri = new_source_type = new_property = None
plan.append({... "action": action, ...})
```

Instruction-dict keys gain **`action`**; for `delete` rows `new_source_uri` /
`new_property_uri` / `new_source_type` are `None`.

## Consumption change in `main()`

```python
orphan_candidates = []
for rec in nested_plan:
    if rec.get("action") == "delete":
        removed_evidence = gocam_graph.delete_edge(
            rec["bnode_id"], rec["old_source_uri"],
            rec["old_property_uri"], rec["target_uri"])
        orphan_candidates.append(rec["old_source_uri"])
        orphan_candidates.append(rec["target_uri"])
        orphan_candidates.extend(removed_evidence)
    else:
        gocam_graph.rewrite_edge_source_and_relation(...)   # unchanged
if orphan_candidates:
    gocam_graph.prune_orphan_individuals(orphan_candidates)
```

The source of a deleted edge (`…902`) survives because it is still the target of the
kept direct extension (`…1956 ─results_in_specification_of→ …902`); the deep target
(`…903`) and the deleted edge's evidence node are orphaned and pruned.

## Report change: `--nested-fix-report`

Header gains a trailing **`Action`** column:

```
Model ID, Title, Lead Aspect, Primary Term, Old Source, Old Relation, Target, New Source, New Relation, Action
```

For `delete` rows, `New Source` and `New Relation` are blank; `Action` is `delete`.
For `rewrite` rows the columns are exactly as before, `Action` = `rewrite`.

## Behavior on the reference model

Running the fixer on `5c4605cc00000891` produces **4 delete instructions** (one per
subgraph). Verified on the written output:

- 0 nested `ZFA ─part_of→ ZFA` edges remain.
- Orphaned deep targets (`…903`, `…907`, `…906`, `…914`) pruned — 0 triples each.
- The 4 deleted edges' evidence nodes (`…917`, `…927`, `…920`, `…923`) pruned.
- The 4 direct `results_in_specification_of` extensions survive, with their kept
  source individuals and their own evidence untouched; backbone `enabled_by` /
  causal evidence untouched.
- Idempotent: a second run over the output produces an empty plan and writes nothing.

## Testing

- **New fixture:** copied `5c4605cc00000891.ttl` into `resources/test/`.
- **Planner test** `test_plan_nested_anatomy_fixes_deletes_disallowed_start_relation`:
  4 instructions, all `action="delete"` with `None` new source/relation, matching the
  four `(old_source_uri, target_uri)` ZFA→ZFA pairs.
- **Deletion unit test** `test_delete_edge_removes_assertion_axiom_and_prunes_orphan`:
  `delete_edge` removes the assertion + whole axiom bnode and **returns** the evidence
  individual (`…917`); `prune_orphan_individuals` then drops the orphaned target
  (`…903`) and the evidence node (`…917`) while keeping the referenced source (`…902`)
  and the direct extension.
- **End-to-end** `test_nested_fix_report_records_deletions`: `main()` writes the
  report with a trailing `Action` column, 4 `delete` rows with blank New Source/New
  Relation, and the written model has the nested edges + orphaned targets + evidence
  individuals (`917/927/920/923`) removed while the direct extensions survive.
- **Schema regression** `test_nested_fix_report_has_new_source_column`: updated to the
  10-column header (`Action` last) and asserts the re-point row's `Action == "rewrite"`.
- **Regression:** `test_plan_nested_anatomy_fixes_bp/mf/cc`,
  `..._chain_start_differs_from_primary`, `..._skips_multi_boundary`,
  `..._warn_param`, `test_extension_chain_start`, `test_rewrite_edge_source_and_relation`,
  `test_remainders_report_fixable_column`, and the rest of the suite pass unchanged —
  every existing fixture's chain starts with `occurs_in`/`part_of` and is all-`part_of`
  thereafter, so it stays a `rewrite`. Full suite: **104 passed**.

## Out of scope

- Deleting the direct backbone→anatomy extension edge (scope decision 3 keeps it).
- Relation *composition* beyond inheriting the chain-start relation for re-points.
- Cleaning up orphaned individuals for the existing `rewrite` action (re-points don't
  orphan anything — the target is retained, only the source/relation change).
- Any GPAD-export changes.
