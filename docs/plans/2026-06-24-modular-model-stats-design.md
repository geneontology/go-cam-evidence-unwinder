# Modularize Per-Model Statistics for Reuse Design

Branch: `nested-anatomy-extensions`.

## Problem

The per-model statistics report (the `--report-file` / stdout TSV) is computed
**inline** in `main()` (`src/gocam_unwinder/gocam_ttl.py:1574-1631`). It is not a
function, so it cannot be reused, and two concerns are tangled into the block:

1. `std_multi_evidence_count` is computed there as a side effect but is also read
   later (line 1634) to decide whether to split (`if args.split_evidence and
   std_multi_evidence_count >= 1`).
2. The criteria-fail-report call (`print_non_standard_annotation_failed_checks`,
   line 1624) is interleaved with the stats computation but is a separate
   concern.

`debug_non_standard.py` cannot produce this model-level row at all — it only has
its own per-edge "bucket" TSV. We want the same per-model stats available there,
plus several **new** columns for triage, without disturbing the existing
`--report-file` output (its 11 columns are documented in `CLAUDE.md` and feed the
Makefile's `noctua_models_graph_counts_*.tsv`).

## Solution

Extract the inline block into a `ModelStats` dataclass plus one builder method
that *computes* (never prints) the stats. `main()` and `debug_non_standard.py`
both call it and format their own TSV rows. New triage columns are added as
extra fields on `ModelStats`, surfaced only in an **extended** row used by the
new `debug_non_standard.py` report; the `--report-file` row stays frozen.

### Separation of concerns

- **Computing** lives on `GoCamGraphBuilder.compute_model_stats(gocam)` (the
  builder already owns `term_label`, `find_nested_extensions`,
  `get_primary_go_terms`, `get_primary_individuals`, `plan_nested_anatomy_fixes`).
  It returns a `ModelStats` and has no I/O.
- **Formatting** lives on `ModelStats` (`base_header`/`to_base_row` and
  `extended_header`/`to_extended_row`), so both call sites stay DRY and the
  "Yes"/"No", pipe-join, and label-join formatting exists in exactly one place.
- The `print_non_standard_annotation_failed_checks` call **stays in `main()`** —
  it is not part of stats computation.

### Change 1: module-level constants

Add near the top of `gocam_ttl.py` (with the other module-level helpers):

```python
# Canonical order of all standard-annotation failure-check names. Fixed so the
# extended stats report has stable columns even when a model has zero of a
# given failure. Mirrors the checks in filter_out_non_std_annotations.
CHECK_NAMES = (
    "inconsistent_evidence",
    "multiple_mf_bp",
    "mf_causal_mf",
    "edge_without_evidence",
    "invalid_gp_mf_relation",
    "invalid_gp_cc_relation",
    "invalid_gp_bp_relation",
    "invalid_mf_bp_relation",
    "invalid_mf_cc_relation",
    "invalid_bp_cc_relation",
    "multiple_mf_anatomy",
    "enabler_not_gp",
)

# The only failed-checks a both-anatomical nested extension edge can contribute
# to: a relation-validity check never fires on an anatomy->anatomy edge (no rule
# has an anatomy source category), so the de-nesting fix accounts for the whole
# of a non-standard annotation's failures only when every failing check is one of
# these. Used to define a "fixable" non-standard annotation.
NESTING_ATTRIBUTABLE_CHECKS = frozenset({
    "inconsistent_evidence",
    "edge_without_evidence",
})
```

### Change 2: `ModelStats` dataclass

A `@dataclass` holding every per-model field. Base fields reproduce today's 11
columns exactly; the rest are the new triage fields.

```python
@dataclass
class ModelStats:
    # --- base fields (today's --report-file columns) ---
    model_id: str
    title: str
    standard_count: int
    non_standard_count: int
    multi_evidence_count: int            # std + non-std (the existing column)
    mixed_annotation_type: bool
    mf_causal_edge_count: int            # existing "MF-causal->MF Edges" (EDGE count)
    no_evidence_edge_count: int
    modelstate: str
    groups: list                         # list[str], already resolved to labels
    multi_evidence_go_terms: list        # sorted list[str], already resolved labels

    # --- needed by main() but not a report column ---
    std_multi_evidence_count: int        # std-only; drives the --split-evidence decision

    # --- new extended fields (debug stats report only) ---
    nested_mf_count: int = 0             # annotations whose lead aspect is MF w/ a nested ext
    nested_bp_count: int = 0
    nested_cc_count: int = 0
    fixable_standard_count: int = 0      # standard annots the de-nesting fixer would rewrite
    fixable_nonstandard_count: int = 0   # non-std annots, only-nested-failures rule (below)
    failure_counts: dict = field(default_factory=lambda: {n: 0 for n in CHECK_NAMES})
```

Formatting methods:

```python
    BASE_HEADERS = ["Model ID", "Title", "Standard Annotations",
        "Non-Standard Annotations", "Multi-Evidence Annotations",
        "Mixed Annotation Type", "MF-causal->MF Edges", "Edges w/o Evidence",
        "Model State", "Groups", "Multi-Evidence GO Terms"]

    @classmethod
    def base_header(cls):  return list(cls.BASE_HEADERS)

    def to_base_row(self):
        return [self.model_id, self.title, str(self.standard_count),
            str(self.non_standard_count), str(self.multi_evidence_count),
            "Yes" if self.mixed_annotation_type else "No",
            str(self.mf_causal_edge_count), str(self.no_evidence_edge_count),
            self.modelstate or "", "|".join(self.groups),
            "|".join(self.multi_evidence_go_terms)]

    @classmethod
    def extended_header(cls):
        return cls.base_header() + [
            "Nested MF Extensions", "Nested BP Extensions", "Nested CC Extensions",
            "Fixable (Standard)", "Fixable (Non-Standard)",
        ] + [f"fail:{n}" for n in CHECK_NAMES]

    def to_extended_row(self):
        return self.to_base_row() + [
            str(self.nested_mf_count), str(self.nested_bp_count),
            str(self.nested_cc_count), str(self.fixable_standard_count),
            str(self.fixable_nonstandard_count),
        ] + [str(self.failure_counts[n]) for n in CHECK_NAMES]
```

`model_id` is emitted verbatim by these methods. `main()` currently builds the
"Model ID" cell as `"gomodel:" + model_id`, where `model_id` is the **filename**
stem (`filename.split(".")[0]`) — *not* `gocam.model_id` (the full URI). Because
the stem comes from the file path, not the graph, the caller passes the exact
display string in (Change 3); `ModelStats.model_id` stores it verbatim and the
formatter stays convention-free. This keeps `--report-file` byte-identical.

### Change 3: `GoCamGraphBuilder.compute_model_stats(gocam, model_id, extended=False)`

Returns a fully-populated `ModelStats`. Lifts the existing inline computation
verbatim for the base fields. The `extended` flag gates the new block so the
`--report-file` path computes (and costs) exactly what it does today.

`model_id` is the exact "Model ID" cell the caller wants (e.g.
`"gomodel:" + filename_stem`); the method does not derive it from `gocam`.

```python
def compute_model_stats(self, gocam, model_id, extended=False):
    std = gocam.standard_annotations
    nonstd = gocam.non_standard_annotations
    all_annots = std + nonstd

    # --- base fields (lifted from main() lines 1574-1631) ---
    std_multi = sum(1 for a in std if a.has_muliple_evidence())
    multi_ev_terms = set()
    for a in std:
        if a.has_muliple_evidence():
            for edge in a.edges.values():
                for t in (edge.source_type, edge.target_type):
                    if t:
                        label = self.term_label(t)
                        if label and not label.startswith("http") and ":" not in label:
                            multi_ev_terms.add(label)
    nonstd_multi = sum(1 for a in nonstd if a.has_muliple_evidence())
    mf_causal_edges = sum(len(a.failed_checks.get("mf_causal_mf", set())) for a in nonstd)
    no_ev_edges = sum(1 for a in all_annots
                      for e in a.edges.values() if not e.evidence_uris)

    stats = ModelStats(
        model_id=model_id,                            # caller-supplied display cell
        title=gocam.title,
        standard_count=len(std),
        non_standard_count=len(nonstd),
        multi_evidence_count=std_multi + nonstd_multi,
        mixed_annotation_type=bool(std) and bool(nonstd),
        mf_causal_edge_count=mf_causal_edges,
        no_evidence_edge_count=no_ev_edges,
        modelstate=gocam.modelstate,
        groups=list(gocam.groups or []),
        multi_evidence_go_terms=sorted(multi_ev_terms),
        std_multi_evidence_count=std_multi,
    )
    if not extended:
        return stats

    # --- extended block (debug stats report) ---
    for a in all_annots:
        for name in a.failed_checks:
            if name in stats.failure_counts:
                stats.failure_counts[name] += 1

    nested = {"MF": 0, "BP": 0, "CC": 0}
    for a in all_annots:
        lead, nested_edges = find_nested_extensions(a, self)
        if nested_edges:
            nested[lead] += 1
    stats.nested_mf_count, stats.nested_bp_count, stats.nested_cc_count = (
        nested["MF"], nested["BP"], nested["CC"])

    # Reuse the real planner so "fixable" cannot diverge from what the fixer does.
    fixer_bnodes = {r["bnode_id"]
                    for r in self.plan_nested_anatomy_fixes(gocam, warn=False)}
    for a in all_annots:
        if not any(bnid in fixer_bnodes for bnid in a.edges):
            continue
        if not a.failed_checks:                                    # standard
            stats.fixable_standard_count += 1
        elif set(a.failed_checks) <= NESTING_ATTRIBUTABLE_CHECKS:  # only-nested
            stats.fixable_nonstandard_count += 1
    return stats
```

**`warn=False`.** `plan_nested_anatomy_fixes` prints a warning for annotations
with 0 or >1 primary individual. Computing stats should be silent, so a `warn`
parameter (default `True`, preserving the fixer's behavior) is added and passed
`False` here. This is the only change to the existing planner.

**Cost.** The extended block runs `find_nested_extensions` and the planner per
model. Both are cheap relative to `parse_ttl` + ontology lookups, and they run
only when `extended=True` (i.e. only in the debug stats report).

### Change 4: rewire `main()` (`--report-file` frozen)

Replace the inline block (`gocam_ttl.py:1574-1631`) with:

```python
    # model_id is the existing filename stem: filename.split(".")[0]
    stats = go_cam_graph_builder.compute_model_stats(
        gocam_graph, "gomodel:" + model_id)            # extended=False
    if criteria_fail_output:
        go_cam_graph_builder.print_non_standard_annotation_failed_checks(
            gocam_graph, report_file=criteria_fail_output)
    print("\t".join(stats.to_base_row()), file=output)
```

The header line (line 1547-1548) becomes `ModelStats.base_header()`. The split
decision at line 1634 reads `stats.std_multi_evidence_count`. Output is
byte-identical to today.

### Change 5: `debug_non_standard.py` gains `--stats-output`

Add a `--stats-output PATH` argument. In the existing model loop (after the
`modelstate == "delete"` skip), accumulate a stats object into a list, deriving
the display id from the file path to match `main()`'s convention:

```python
    model_id = "gomodel:" + os.path.basename(ttl_path).split(".")[0]
    all_stats.append(builder.compute_model_stats(gocam, model_id, extended=True))
```

After the loop, if `--stats-output` is set, write the extended TSV:

```python
    if args.stats_output:
        with open(args.stats_output, "w") as f:
            f.write("\t".join(ModelStats.extended_header()) + "\n")
            for s in all_stats:
                f.write("\t".join(s.to_extended_row()) + "\n")
```

The existing per-edge bucket TSV (`--tsv-output`) and console summary are
untouched.

## The "fixable" rule (precise)

An annotation is a **fixer target** when `plan_nested_anatomy_fixes` would
rewrite ≥1 of its edges (detected by bnode-id membership in the planner output —
single source of truth, no reimplementation).

- A **standard** annotation (`failed_checks` empty) that is a fixer target
  counts toward `fixable_standard_count`.
- A **non-standard** annotation counts toward `fixable_nonstandard_count` iff it
  is a fixer target **and** every key in `failed_checks` is in
  `NESTING_ATTRIBUTABLE_CHECKS` (`{inconsistent_evidence, edge_without_evidence}`).
  Any other failing check is a real, un-repaired problem, so the annotation is
  not fixable by de-nesting alone.

`NESTING_ATTRIBUTABLE_CHECKS` is the single knob for this definition. Its members
are justified: a both-anatomical nested edge (`CL ─part_of→ EMAPA`) is never
flagged by any relation-validity check (no rule has an anatomy source category),
so the only checks such an edge can contribute to are evidence consistency
(annotation-wide) and missing evidence (on the nested edge itself).

## Column layouts

**Base** (`--report-file`, unchanged — 11 columns):

`Model ID, Title, Standard Annotations, Non-Standard Annotations,
Multi-Evidence Annotations, Mixed Annotation Type, MF-causal->MF Edges,
Edges w/o Evidence, Model State, Groups, Multi-Evidence GO Terms`

**Extended** (`debug_non_standard.py --stats-output`): the 11 base columns, then

`Nested MF Extensions, Nested BP Extensions, Nested CC Extensions,
Fixable (Standard), Fixable (Non-Standard),
fail:inconsistent_evidence, fail:multiple_mf_bp, fail:mf_causal_mf,
fail:edge_without_evidence, fail:invalid_gp_mf_relation, fail:invalid_gp_cc_relation,
fail:invalid_gp_bp_relation, fail:invalid_mf_bp_relation, fail:invalid_mf_cc_relation,
fail:invalid_bp_cc_relation, fail:multiple_mf_anatomy, fail:enabler_not_gp`

Per-check counts are **annotation-level** — the number of non-standard
annotations with ≥1 failure of that check. (Note: the base `MF-causal->MF Edges`
column counts *edges* and is unchanged; `fail:mf_causal_mf` is the annotation
count, so the two are intentionally different metrics.)

## What stays the same

- `--report-file` / stdout output: byte-identical (same header, same 11 columns,
  same `gomodel:` prefix).
- The classification pipeline (`parse_ttl`, `filter_out_non_std_annotations`) and
  all `failed_checks` logic.
- `--split-evidence`, `--fix-nested-anatomy`, and every other mode/flag.
- `debug_non_standard.py`'s per-edge bucket TSV (`--tsv-output`) and console
  summary.
- `plan_nested_anatomy_fixes` behavior when called without `warn=False` (the
  fixer path).

## Out of scope

- Adding the new columns to `--report-file` (frozen by decision).
- Introducing a tracked `failed_checks` entry for nested anatomy (the structure
  is detected via `find_nested_extensions`, not the classifier — unchanged).
- Re-validating or re-classifying models after a fix.
- Changing how "fixable" is defined beyond the `NESTING_ATTRIBUTABLE_CHECKS`
  knob.

## Test Plan

New tests in `tests/test_gocam_ttl.py` (require `target/go_20250601.json`;
extended-field tests that exercise the BP backbone also need
`resources/test/ro_20250723.owl` via the existing `builder` fixture):

1. **`test_compute_model_stats_base_matches_legacy`** — on a known fixture (e.g.
   `MGI_MGI_1100089.ttl`), `compute_model_stats(gocam).to_base_row()` equals the
   row the inline block produced, and `base_header()` equals the current
   `headers` list. Regression that the extraction is byte-identical.

2. **`test_compute_model_stats_split_count`** — `std_multi_evidence_count` on
   `MGI_MGI_1100089.ttl` is ≥1 (so the split decision is unchanged); existing
   split tests continue to pass.

3. **`test_model_stats_nested_bucket_counts`** — on `5966411600000001.ttl`
   (`extended=True`), `nested_bp_count == 1`, `nested_mf_count == 0`,
   `nested_cc_count == 0`, matching the debug script's bucketing.

4. **`test_model_stats_failure_counts`** — on a model with a single known failure
   type (e.g. `SYNGO_5371.ttl` → `failure_counts["invalid_mf_cc_relation"] >= 1`;
   `66c7d41500000016.ttl` → `failure_counts["edge_without_evidence"] >= 1`), with
   every `CHECK_NAMES` key present (0 where absent).

5. **`test_model_stats_fixable_standard`** — on a fixture whose nested-anatomy
   annotation is standard and a fixer target (the MF/CC synthetic fixtures, or
   `5966411600000001.ttl` if its GO:0120045 annotation is standard),
   `fixable_standard_count >= 1` and `fixable_nonstandard_count == 0`.

6. **`test_model_stats_fixable_nonstandard_only_nested`** — a fixture with a
   non-standard annotation that is a fixer target whose only failed check is in
   `NESTING_ATTRIBUTABLE_CHECKS` (likely a new synthetic fixture: a both-anatomy
   nested edge with **no evidence**, so `edge_without_evidence` is the sole
   failure) counts toward `fixable_nonstandard_count`; a sibling fixture that
   adds an unrelated real failure (e.g. a bad `root-MF ─located_in→ CC`) does
   **not** count.

7. **`test_debug_stats_output`** — running `debug_non_standard.py` with
   `--stats-output` writes a TSV whose header is `ModelStats.extended_header()`
   with one row per non-delete model, while `--tsv-output` (bucket report) is
   unchanged.

Run: `pytest tests/test_gocam_ttl.py -v`.
