# Nested-Anatomy "Single Clean Attach Point" Gate Design

Branch: `nested-anatomy-extensions`.

## Problem

`--fix-nested-anatomy` (and, derived from it, the remainders report's `Fixable`
column) de-nests an extension edge whenever
`GoCamGraphBuilder.plan_nested_anatomy_fixes()` qualifies it: the edge is a nested
extension, **both** endpoints are anatomical (`_is_anatomical_structure()`), and
its annotation's lead aspect has exactly one primary individual. Each qualifying
edge is re-pointed onto the primary individual with `occurs_in` (MF/BP-led) or its
existing relation (CC-led).

That qualification is too permissive for richly-connected developmental
subgraphs. Model `5745387b00001376` ("mouse-Fat1-eye development") is the
motivating case — a single subgraph, lead aspect **BP**, primary BP individual
`GO:0003412` (ind `…1403`). The fixer rewrote four `part_of` edges:

```
GO:0005886 plasma membrane  ─part_of→ CL:0000066 epithelial cell
CL:0000066 epithelial cell  ─part_of→ UBERON:0001803
UBERON:0001803              ─part_of→ UBERON:0000965 lens
UBERON:0000965 lens         ─part_of→ UBERON:0000019 camera-type eye
```

re-pointing all four onto `GO:0003412` with `occurs_in`. But every terminal
anatomy node in that chain is *independently* tied to its own developmental BP:

```
GO:0043010 camera-type eye development    ─results_in_development_of→  UBERON:0000019 eye
GO:0002088 lens development               ─results_in_development_of→  UBERON:0000965 lens
GO:0003382 epithelial cell morphogenesis  ─results_in_morphogenesis_of→ CL:0000066
```

on top of a multi-BP `part_of` web (`GO:0070306` ⊂ `GO:0002088` ⊂ `GO:0043010`;
`GO:0003412` ⊂ `GO:0003382` ⊂ `GO:0002088`). The anatomy is not a simple "where
the activity happens" tail — it is woven into a developmental model, and
flattening it to `occurs_in` discards that meaning. These subgraphs should be
left alone for now (but still reported).

By contrast the canonical fixable case `5966411600000001` is
`BP ─occurs_in→ CL:0000202 ─part_of→ EMAPA:17597`, where `CL`/`EMAPA` connect to
the rest of the model only through that one `occurs_in` and carry no developmental
edges. (Confirmed: no current fixer-target fixture contains any `results_in_*`
relation.)

## Solution

Add a **single clean attach point** gate to `plan_nested_anatomy_fixes()`,
expressed in terms of the **anatomy region boundary**:

- **Anatomy region** — a connected component of anatomical individuals
  (`_is_anatomical_structure`: GO cellular components + anatomy-ontology terms
  CL/UBERON/EMAPA/…), joined to each other by **internal** edges (edges whose
  *both* endpoints are anatomical — the `part_of` chains).
- **Boundary edge** — an edge with **exactly one** anatomical endpoint; it links
  an anatomy region to a non-anatomy node (e.g. the primary `BP ─occurs_in→ CL`,
  or a stray `BP ─results_in_development_of→ eye`).
- **Rule** — an anatomy region is *simply attached* iff it has **at most one**
  distinct boundary edge. An annotation's nested-anatomy fixes are kept only if
  **every** anatomy region in the annotation is simply attached; otherwise the
  whole annotation is skipped (no fixes planned for any of its nested edges).

This is relation-agnostic: it catches developmental complexity (the
`results_in_*` edges add boundary edges) and any other weaving via relations we
did not enumerate, while leaving the simple single-attach chains fixable.

### Why the planner, not the report

`plan_nested_anatomy_fixes()` is the single source of truth for both the fixer
and the report's `Fixable` column (the column is sourced from
`{rec["bnode_id"] for rec in builder.plan_nested_anatomy_fixes(gocam, warn=False)}`).
Adding the gate there means:

1. `--fix-nested-anatomy` stops rewriting these edges, and
2. the remainders report's `Fixable` flips to `No` for them automatically —

while the nested edges **still appear as rows** in the report (the
`nested_<aspect>_extensions` bucket loop in `debug_non_standard.py` emits them
regardless of fixability). That satisfies "still report them; just don't fix
them." No change to `debug_non_standard.py`.

### Worked examples

| Model | Anatomy region | Boundary edges | Simple? | Result |
| --- | --- | --- | --- | --- |
| `5966411600000001` | `{CL:0000202, EMAPA:17597}` | `GO:0120045 ─occurs_in→ CL` (1) | yes | nested edge stays `Fixable=Yes` |
| `5745387b00001376` | `{plasma membrane, CL:0000066, UBERON:0001803, UBERON:0000965, UBERON:0000019}` | 1 primary `occurs_in` + 1 MF `occurs_in` + 3 `results_in_*` = **5** | no | all 4 nested edges become `Fixable=No`; fixer plans 0 |

### Multi-evidence note

`annot.edges` is keyed by axiom bnode, so a multi-evidence placement appears as
two bnode entries for the same `(source, relation, target)`. Boundary edges are
therefore counted as **distinct `(source_uri, property_uri, target_uri)`
triples**, so a duplicated single placement still counts as one boundary edge and
does not falsely disqualify a simple chain.

### RO note

The gate uses only `_is_anatomical_structure` (GO is_a closure + namespace keys);
it needs no RO ontology. Lead-aspect/backbone detection inside
`find_nested_extensions` behaves exactly as it does today (BP backbone falls back
to `part_of`-only without RO), so the gate is consistent with however the fixer
or the debug script is invoked — matching the existing `Fixable`-column RO
behavior.

## Changes

Only `src/gocam_unwinder/gocam_ttl.py` changes.

1. **New helper** `GoCamGraphBuilder._anatomy_attachment_is_simple(self, annot) -> bool`:

   ```python
   def _anatomy_attachment_is_simple(self, annot):
       # 1. anatomical flag per individual (from edge endpoint types)
       is_anat = {}
       for e in annot.edges.values():
           if e.source_uri is not None:
               is_anat[e.source_uri] = self._is_anatomical_structure(e.source_type)
           if e.target_uri is not None:
               is_anat[e.target_uri] = self._is_anatomical_structure(e.target_type)
       # 2. union-find over anatomical individuals joined by internal edges
       parent = {u: u for u, a in is_anat.items() if a}
       def find(x):
           while parent[x] != x:
               parent[x] = parent[parent[x]]; x = parent[x]
           return x
       def union(a, b):
           parent[find(a)] = find(b)
       for e in annot.edges.values():
           if is_anat.get(e.source_uri) and is_anat.get(e.target_uri):
               union(e.source_uri, e.target_uri)
       # 3. distinct boundary edges per region
       boundary = {}
       for e in annot.edges.values():
           s, t = is_anat.get(e.source_uri, False), is_anat.get(e.target_uri, False)
           if s != t:  # exactly one anatomical endpoint
               anat_node = e.source_uri if s else e.target_uri
               root = find(anat_node)
               boundary.setdefault(root, set()).add(
                   (e.source_uri, e.property_uri, e.target_uri))
       # 4. simple iff every region has <= 1 boundary edge
       return all(len(b) <= 1 for b in boundary.values())
   ```

2. **The gate** in `plan_nested_anatomy_fixes()`, immediately after the
   `lead, nested = find_nested_extensions(annot, self)` / `if not nested: continue`
   lines and before the primary-individual check:

   ```python
   if not self._anatomy_attachment_is_simple(annot):
       if warn:
           print(f"WARNING: skipping annotation in {gocam.model_id} "
                 f"({gocam.title}) — anatomy has multiple attachment points "
                 f"(complex subgraph)")
       continue
   ```

   `warn` is the existing kwarg; report generation and `compute_model_stats`
   already call the planner with `warn=False`, so this prints nothing during
   report runs.

## What stays the same

- `find_nested_extensions`, `get_primary_individuals`, `get_primary_go_terms`,
  `rewrite_edge_source_and_relation`, and the `--fix-nested-anatomy` CLI flow —
  unchanged. The gate only adds a `continue` before instructions are appended.
- `debug_non_standard.py` (the remainders report, including the `Fixable` column
  wiring) — unchanged; it consumes the planner.
- `compute_model_stats` fixable counts — unchanged code; they shift only because
  the planner now returns fewer instructions for complex subgraphs (the intended
  behavior).
- Every existing fixable fixture (`5966411600000001`, `mf_nested_anatomy_example`,
  `cc_nested_anatomy_example`, `mf_nested_anatomy_noev_example`,
  `mf_nested_anatomy_unfixable_example`) has a single-boundary anatomy region, so
  their fixability is unchanged.

## Out of Scope

- Reporting *why* an annotation is not fixable (which/how many boundary edges) —
  `Fixable` stays a single `Yes`/`No`.
- Per-edge or per-region granularity. Per the agreed decision, a single
  >1-boundary region disqualifies the **whole annotation** (conservative; in
  practice annotations have one anatomy region).
- Any change to the splitting pipeline (`--split-evidence`) or the main report
  (`gocam_ttl.py --report-file`).

## Test Plan

**New fixtures**

- `resources/test/nested_anatomy_multi_boundary_example.ttl` — synthetic, BP-led,
  minimal (all edges evidenced):
  - `rootMF (GO:0003674) ─enabled_by→ GP (MGI:109168)` (MF backbone)
  - `rootMF ─part_of→ BP1 (GO:0001654 eye development)` (BP backbone; primary BP
    individual)
  - `BP1 ─occurs_in→ CL:0000202` (direct placement; boundary #1)
  - `CL:0000202 ─part_of→ UBERON:0000019` (nested both-anatomical; the candidate)
  - `BP2 (GO:0043010 camera-type eye development) ─results_in_development_of (RO:0002296)→ UBERON:0000019`
    (the disqualifying boundary #2)

  The anatomy region `{CL:0000202, UBERON:0000019}` has 2 distinct boundary edges
  → not simple. `BP2` is reached only via `results_in_development_of`, so it is
  not a second BP backbone and the lead aspect stays BP with one primary
  individual — the new gate (not the primary-count check) is what skips it.

- `resources/test/5745387b00001376.ttl` — the real motivating model (copied from
  `noctua-models`), as a real-world regression.

**New tests** (`tests/test_gocam_ttl.py`)

- `test_anatomy_attachment_is_simple` — `True` for `5966411600000001`'s GO:0120045
  annotation; `False` for `nested_anatomy_multi_boundary_example` and for the
  `5745387b00001376` subgraph.
- `test_plan_nested_anatomy_fixes_skips_multi_boundary` —
  `plan_nested_anatomy_fixes(gocam)` returns `[]` for
  `nested_anatomy_multi_boundary_example` and for `5745387b00001376` (the
  previously-rewritten edges are absent from the plan).
- Extend `test_remainders_report_fixable_column` — assert a `5745387b00001376`
  nested row (e.g. `UBERON:0000965 lens ─part_of→ UBERON:0000019`) reads
  `Fixable=No`.

**Regression**

- `pytest -q` — full suite green. In particular the existing
  `test_plan_nested_anatomy_fixes_{bp,mf,cc}`,
  `test_compute_model_stats_fixable_standard`,
  `test_compute_model_stats_fixable_nonstandard`,
  `test_compute_model_stats_unfixable_nonstandard`, and the existing
  `Fixable=Yes` assertion in `test_remainders_report_fixable_column` are
  unaffected (all those fixtures are single-boundary).

**Smoke**

```bash
source env/bin/activate && python3 debug_non_standard.py resources/test/ \
  -o target/go_20250601.json -r resources/test/ro_20250723.owl \
  --no-label-api --tsv-output /tmp/remainders.tsv
# every 5745387b00001376 nested row reads Fixable=No; 5966411600000001 CL->EMAPA reads Yes
awk -F'\t' 'NR>1 && $1 ~ /5745387b00001376/ {print $7"\t"$4" -["$5"]-> "$6}' /tmp/remainders.tsv
```

## Docs

Update `CLAUDE.md`:
- `plan_nested_anatomy_fixes` description — add the attachment gate (a region with
  >1 boundary edge skips the whole annotation).
- `--fix-nested-anatomy` mode paragraph — note that complex (multi-attach-point)
  anatomy subgraphs are left unchanged.
- Remainders-report `Fixable` paragraph — note that a both-anatomical nested edge
  can read `No` because its annotation's anatomy region has multiple attachment
  points (a new reason alongside the existing ambiguous-primary-individual one).
- Test-fixture list — add `nested_anatomy_multi_boundary_example.ttl` and
  `5745387b00001376.ttl`, and the new test functions.
