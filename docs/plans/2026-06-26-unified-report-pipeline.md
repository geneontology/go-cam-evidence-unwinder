# Unified Report Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate every report from a single `gocam_ttl.py` pass that parses each model once, centralizing the CLI on `gocam_ttl.py` and retiring `debug_non_standard.py`.

**Architecture:** Move the remainders bucketing logic into a new package module `src/gocam_unwinder/remainders_report.py` exposing a per-model `collect_model_remainders()`. Refactor `gocam_ttl.py`'s inline `__main__` block into a callable `main()`, make the extended stats report the default `--report-file` content (with `--basic-report` to opt out), and add `--remainders-report` so the single per-model loop emits all analysis reports. The three analysis reports are computed from the pristine parsed graph before any split/fix mutation, so their content is mutation-independent.

**Tech Stack:** Python 3, rdflib, ontobio, pytest, GNU make.

**Affected Areas:** CLI args + `main()` in `src/gocam_unwinder/gocam_ttl.py`; new `src/gocam_unwinder/remainders_report.py`; `debug_non_standard.py` (deleted); `tests/test_gocam_ttl.py`; `Makefile`; `CLAUDE.md`.

---

## Context

The model corpus is parsed twice today: `gocam_ttl.py` writes the base stats + criteria failures (and optionally splits/fixes), while `debug_non_standard.py` re-parses to write the extended stats + bucketed remainders report. The bucketing logic lives only inside `debug_non_standard.py`'s `main()`, so it can't be reached from the primary entry point. This plan unifies everything behind one `gocam_ttl.py` invocation. See the companion design at `docs/plans/2026-06-26-unified-report-pipeline-design.md`.

Key prior facts verified during design:
- `gocam_ttl.py` has **no** `main()` function — its logic is inline under `if __name__ == "__main__":` (currently around line 1812).
- `find_nested_extensions`, `pick_lead_aspect`, `ASPECT_PRIORITY`, `collect_model_files`, `load_skip_filenames`, `ModelStats`, `CHECK_NAMES` are module-level in `src/gocam_unwinder/gocam_ttl.py`.
- The shared test `builder` fixture (`tests/conftest.py`) is session-scoped with `resolve_labels_api=False`, so anatomy labels resolve to CURIEs (e.g. `CL:0000202`, predicate `part of`).
- The project is installed editable (`pip install -e .`), so `gocam_unwinder.*` is importable regardless of cwd; a repo-root script like the old `debug_non_standard.py` is not. This is why the new module belongs **inside** the package.

## Constraints

- **No git operations.** Per the user's standing instruction, this plan contains **no** `git add`/`git commit` steps; the user manages git manually. The per-task checkpoint is the passing test run.
- Must not change report **column contents** — only which report `--report-file` defaults to (extended) and the new `--remainders-report` output (identical columns to the old `debug_non_standard.py --tsv-output`).
- `--split-evidence` and `--fix-nested-anatomy` stay mutually exclusive in one invocation (keep the existing hard error).
- Remainders/criteria/stats must be computed from the pristine parsed graph (before any mutation), preserving today's order: parse → reports → split-or-fix.
- Tests must keep passing offline (no OLS API) via the session `builder` fixture monkeypatch.

## Out of Scope

- The GPAD export pipeline (Blazegraph / minerva-cli) and its targets.
- The remainders bucketing rules themselves (lifted verbatim).
- Merging split + fix into one invocation.
- De-duplicating the two `plan_nested_anatomy_fixes` calls per model (extended stats + remainders each call it). Noted as a possible later optimization.

---

## Tasks

### Task 1: New module `remainders_report.py` with `collect_model_remainders`

Lift the bucketing helpers out of `debug_non_standard.py` into a focused package module with a per-model entry point.

**Files:**
- Create: `src/gocam_unwinder/remainders_report.py`
- Test: `tests/test_gocam_ttl.py`

- [ ] **Step 1: Write the failing test**

Add this test function to `tests/test_gocam_ttl.py` (anywhere among the existing tests, e.g. just before `def _read_remainders_tsv`):

```python
def test_collect_model_remainders_fixable_row(builder):
    """collect_model_remainders bucket the canonical nested anatomy edge and
    marks it Fixable=Yes, matching what --fix-nested-anatomy would rewrite."""
    from gocam_unwinder.remainders_report import collect_model_remainders

    gocam = builder.parse_ttl("resources/test/5966411600000001.ttl")
    rows, bucket_hits = collect_model_remainders(builder, gocam)

    # Tuple layout: (model_id, title, bucket, source, predicate, target,
    #                fixable, eco_codes, groups)
    match = [r for r in rows
             if r[0].endswith("5966411600000001")
             and r[3] == "CL:0000202" and r[4] == "part of"
             and r[5] == "EMAPA:17597"]
    assert match, "expected the CL:0000202 -part of-> EMAPA:17597 nested row"
    assert match[0][6] == "Yes"  # Fixable column

    assert any(b == "nested_bp_extensions" for (b, _) in bucket_hits)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gocam_ttl.py::test_collect_model_remainders_fixable_row -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gocam_unwinder.remainders_report'`

- [ ] **Step 3: Write the module**

Create `src/gocam_unwinder/remainders_report.py` with exactly this content:

```python
#!/usr/bin/env python3
"""Remainders report: bucket non-standard / nested-extension GO-CAM annotations.

Lifted from the former debug_non_standard.py so the bucketing logic is reachable
from gocam_ttl.py's single-parse main() (the centralized CLI entry point). The
per-model entry point is collect_model_remainders(); gocam_ttl.main() calls it
inside its existing model loop, so the corpus is parsed only once.
"""

import rdflib

from gocam_unwinder.gocam_ttl import find_nested_extensions


REMAINDERS_HEADER = ["Model ID", "Title", "Bucket", "Source", "Predicate",
                     "Target", "Fixable", "ECO Codes", "Groups"]

# Order buckets appear in the end-of-run summary.
_SUMMARY_BUCKETS = (
    "nested_mf_extensions", "nested_bp_extensions", "nested_cc_extensions",
    "multi_mf_same_bp", "multi_bp_same_mf", "extension_eco_differs",
    "unclassified",
)


def classify_multiple_mf_bp(annot):
    """Sub-classify a multiple_mf_bp failure.

    Returns "same_bp", "same_mf", or None (ambiguous).
    """
    flagged_bnodes = annot.failed_checks.get("multiple_mf_bp", set())
    if not flagged_bnodes:
        return None

    source_types = set()
    target_uris = set()
    for bnode_id in flagged_bnodes:
        edge = annot.edges[bnode_id]
        source_types.add(edge.source_type)
        target_uris.add(edge.target_uri)

    if len(target_uris) == 1 and len(source_types) > 1:
        return "same_bp"
    if len(source_types) == 1 and len(target_uris) > 1:
        return "same_mf"
    # Also check by type rather than individual URI
    target_types = {annot.edges[b].target_type for b in flagged_bnodes}
    if len(target_types) == 1 and len(source_types) > 1:
        return "same_bp"
    source_uris = {annot.edges[b].source_uri for b in flagged_bnodes}
    if len(source_uris) == 1 and len(target_types) > 1:
        return "same_mf"

    return None


def has_differing_eco_types(annot, gocam):
    """True if edges in the annotation have evidence with different ECO types."""
    eco_sets_per_edge = []
    for edge in annot.edges.values():
        eco_types = set()
        for ev_uri in edge.evidence_uris:
            for rdf_type in gocam.g.objects(ev_uri, rdflib.RDF.type):
                if rdf_type != rdflib.OWL.NamedIndividual:
                    eco_types.add(rdf_type)
        eco_sets_per_edge.append(eco_types)

    if len(eco_sets_per_edge) < 2:
        return False
    first = eco_sets_per_edge[0]
    return any(s != first for s in eco_sets_per_edge[1:])


def get_eco_types_for_annot(annot, gocam):
    """Return the set of ECO type URIs across all evidence in an annotation."""
    eco_types = set()
    for edge in annot.edges.values():
        for ev_uri in edge.evidence_uris:
            for rdf_type in gocam.g.objects(ev_uri, rdflib.RDF.type):
                if rdf_type != rdflib.OWL.NamedIndividual:
                    eco_types.add(rdf_type)
    return eco_types


def collect_model_remainders(builder, gocam):
    """Build the remainders TSV rows for one already-parsed model.

    Returns (rows, bucket_hits):
      - rows: list of 9-tuples matching REMAINDERS_HEADER (one per bucketed edge)
      - bucket_hits: list of (bucket_name, model_id) at annotation granularity,
        for the end-of-run summary.

    Mirrors the per-model loop of the former debug_non_standard.main(): the same
    nested-extension / multiple_mf_bp / extension_eco_differs bucketing, the same
    Fixable column (sourced from plan_nested_anatomy_fixes so it cannot diverge
    from --fix-nested-anatomy), and the same "unclassified" catch-all for
    non-standard annotations that fall in no other bucket.
    """
    rows = []
    bucket_hits = []

    groups = "|".join(gocam.groups) if gocam.groups else ""

    # Edges --fix-nested-anatomy would actually rewrite (computed once per model);
    # warn=False keeps report generation quiet (matches compute_model_stats).
    fixable_bnodes = {rec["bnode_id"]
                      for rec in builder.plan_nested_anatomy_fixes(gocam, warn=False)}

    def label(uri):
        return builder.term_label(uri) if uri else "?"

    # Buckets 1a/1b/1c: nested_<aspect>_extensions over ALL annotations (std + non-std).
    all_annots = gocam.standard_annotations + gocam.non_standard_annotations
    for annot in all_annots:
        lead_aspect, nested_edges = find_nested_extensions(annot, builder)
        if not nested_edges:
            continue
        bucket_name = f"nested_{lead_aspect.lower()}_extensions"
        bucket_hits.append((bucket_name, gocam.model_id))
        eco_codes = "|".join(sorted(label(e) for e in get_eco_types_for_annot(annot, gocam)))
        for edge in nested_edges:
            fixable = "Yes" if edge.bnode_id in fixable_bnodes else "No"
            rows.append((gocam.model_id, gocam.title, bucket_name,
                         label(edge.source_type), label(edge.property_uri),
                         label(edge.target_type), fixable, eco_codes, groups))

    # Buckets 2-4 + unclassified: non-standard annotations only.
    for annot in gocam.non_standard_annotations:
        classified = False
        eco_codes = "|".join(sorted(label(e) for e in get_eco_types_for_annot(annot, gocam)))

        # Bucket 2 & 3: multiple_mf_bp sub-classification
        if "multiple_mf_bp" in annot.failed_checks:
            shape = classify_multiple_mf_bp(annot)
            bucket_name = {"same_bp": "multi_mf_same_bp",
                           "same_mf": "multi_bp_same_mf"}.get(shape)
            if bucket_name:
                classified = True
                bucket_hits.append((bucket_name, gocam.model_id))
                for bnode_id in annot.failed_checks["multiple_mf_bp"]:
                    edge = annot.edges[bnode_id]
                    fixable = "Yes" if bnode_id in fixable_bnodes else "No"
                    rows.append((gocam.model_id, gocam.title, bucket_name,
                                 label(edge.source_type), label(edge.property_uri),
                                 label(edge.target_type), fixable, eco_codes, groups))

        # Bucket 4: inconsistent_evidence with different ECOs (only if no other failures)
        if annot.failed_checks.keys() == {"inconsistent_evidence"} and \
                has_differing_eco_types(annot, gocam):
            classified = True
            bucket_hits.append(("extension_eco_differs", gocam.model_id))
            for edge in annot.edges.values():
                fixable = "Yes" if edge.bnode_id in fixable_bnodes else "No"
                rows.append((gocam.model_id, gocam.title, "extension_eco_differs",
                             label(edge.source_type), label(edge.property_uri),
                             label(edge.target_type), fixable, eco_codes, groups))

        if not classified:
            bucket_hits.append(("unclassified", gocam.model_id))

    return rows, bucket_hits


def write_remainders_tsv(path, rows):
    """Write the remainders TSV (header + rows) to path."""
    with open(path, "w") as f:
        f.write("\t".join(REMAINDERS_HEADER) + "\n")
        for row in rows:
            f.write("\t".join(str(v) for v in row) + "\n")


def print_bucket_summary(bucket_hits, total_non_std):
    """Print the end-of-run bucket summary to stdout."""
    print("=== Bucket Summary ===")
    for name in _SUMMARY_BUCKETS:
        entries = [m for (b, m) in bucket_hits if b == name]
        models = len(set(entries))
        print(f"  {name + ':':<30s} {len(entries):>4d} annotations across {models:>4d} models")
    print(f"  {'total non-standard:':<30s} {total_non_std:>4d}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gocam_ttl.py::test_collect_model_remainders_fixable_row -v`
Expected: PASS

---

### Task 2: Extract `def main()` in `gocam_ttl.py`

Pure refactor: wrap the inline `__main__` body in a callable `main()` so tests can drive it and later tasks can extend it.

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py` (the `if __name__ == "__main__":` block, ~line 1812 to end)
- Test: `tests/test_gocam_ttl.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gocam_ttl.py`. First ensure the module + `ModelStats` are importable — at the top of the file, change the existing import line:

```python
from gocam_unwinder.gocam_ttl import GoCamGraph, GoCamGraphBuilder
```

to:

```python
from gocam_unwinder.gocam_ttl import GoCamGraph, GoCamGraphBuilder, ModelStats
from gocam_unwinder import gocam_ttl
```

Then add the test:

```python
def test_main_callable(builder, tmp_path, monkeypatch):
    """gocam_ttl.main() runs end-to-end on a single model and writes a TSV with
    a header row to --report-file."""
    out = tmp_path / "stats.tsv"
    monkeypatch.setattr("gocam_unwinder.gocam_ttl.GoCamGraphBuilder",
                        lambda *a, **k: builder)
    monkeypatch.setattr(sys, "argv", [
        "gocam_ttl.py", "-m", "resources/test/MGI_MGI_1100089.ttl",
        "-o", "target/go_20250601.json",
        "-r", "resources/test/ro_20250723.owl",
        "--no-label-api", "--report-file", str(out),
    ])

    gocam_ttl.main()

    lines = out.read_text().splitlines()
    assert lines, "report file should have content"
    assert lines[0].split("\t")[0] == "Model ID"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gocam_ttl.py::test_main_callable -v`
Expected: FAIL with `AttributeError: module 'gocam_unwinder.gocam_ttl' has no attribute 'main'`

- [ ] **Step 3: Refactor the `__main__` block into `main()`**

In `src/gocam_unwinder/gocam_ttl.py`, change the line:

```python
if __name__ == "__main__":
    args = parser.parse_args()
```

to:

```python
def main():
    args = parser.parse_args()
```

(The body — everything from `args = parser.parse_args()` through the final `if fail_report_file: fail_report_file.close()` — is already indented one level, so it now sits correctly inside `main()`.)

Then append at the very end of the file:

```python


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gocam_ttl.py::test_main_callable -v`
Expected: PASS

- [ ] **Step 5: Run full suite for regressions**

Run: `pytest -q`
Expected: All tests PASS (the extraction is behavior-preserving).

---

### Task 3: Extended stats by default + `--basic-report`

Make `--report-file` write the extended report by default; `--basic-report` reverts to the 11-column base report.

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py` (argparse block ~line 23; `main()` header-print and per-model row)
- Test: `tests/test_gocam_ttl.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gocam_ttl.py`:

```python
def test_report_file_extended_by_default(builder, tmp_path, monkeypatch):
    """--report-file writes the extended header by default."""
    out = tmp_path / "stats.tsv"
    monkeypatch.setattr("gocam_unwinder.gocam_ttl.GoCamGraphBuilder",
                        lambda *a, **k: builder)
    monkeypatch.setattr(sys, "argv", [
        "gocam_ttl.py", "-m", "resources/test/MGI_MGI_1100089.ttl",
        "-o", "target/go_20250601.json",
        "-r", "resources/test/ro_20250723.owl",
        "--no-label-api", "--report-file", str(out),
    ])

    gocam_ttl.main()

    header = out.read_text().splitlines()[0].split("\t")
    assert header == ModelStats.extended_header()


def test_report_file_basic_with_flag(builder, tmp_path, monkeypatch):
    """--basic-report switches --report-file back to the base header."""
    out = tmp_path / "stats.tsv"
    monkeypatch.setattr("gocam_unwinder.gocam_ttl.GoCamGraphBuilder",
                        lambda *a, **k: builder)
    monkeypatch.setattr(sys, "argv", [
        "gocam_ttl.py", "-m", "resources/test/MGI_MGI_1100089.ttl",
        "-o", "target/go_20250601.json",
        "-r", "resources/test/ro_20250723.owl",
        "--no-label-api", "--basic-report", "--report-file", str(out),
    ])

    gocam_ttl.main()

    header = out.read_text().splitlines()[0].split("\t")
    assert header == ModelStats.base_header()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_gocam_ttl.py::test_report_file_extended_by_default tests/test_gocam_ttl.py::test_report_file_basic_with_flag -v`
Expected: `test_report_file_extended_by_default` FAILS (header is base, not extended); `test_report_file_basic_with_flag` FAILS with `error: unrecognized arguments: --basic-report` (a SystemExit from argparse).

- [ ] **Step 3: Add the flag and switch the report content**

In the module-level argparse block, immediately after the `--report-file` line (currently line 23), add:

```python
parser.add_argument('--basic-report', action='store_true',
                    help="Write the basic 11-column stats report instead of the default extended report")
```

In `main()`, replace:

```python
    # Always print statistics header
    print("\t".join(ModelStats.base_header()), file=output)
```

with:

```python
    # Statistics report: extended by default, base columns with --basic-report.
    extended = not args.basic_report
    stats_header = ModelStats.extended_header() if extended else ModelStats.base_header()
    print("\t".join(stats_header), file=output)
```

In the per-model loop, replace:

```python
        # Compute per-model statistics (base fields only for the frozen report)
        stats = go_cam_graph_builder.compute_model_stats(
            gocam_graph, "gomodel:" + model_id)
```

with:

```python
        # Compute per-model statistics (extended fields unless --basic-report)
        stats = go_cam_graph_builder.compute_model_stats(
            gocam_graph, "gomodel:" + model_id, extended=extended)
```

and replace:

```python
        print("\t".join(stats.to_base_row()), file=output)
```

with:

```python
        row = stats.to_extended_row() if extended else stats.to_base_row()
        print("\t".join(row), file=output)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gocam_ttl.py::test_report_file_extended_by_default tests/test_gocam_ttl.py::test_report_file_basic_with_flag -v`
Expected: PASS

- [ ] **Step 5: Run full suite for regressions**

Run: `pytest -q`
Expected: All tests PASS.

---

### Task 4: Wire `--remainders-report` into `main()` and migrate its test

Add the `--remainders-report` flag and emit the bucketed remainders TSV from the single per-model loop. Repoint the existing end-to-end test off `debug_non_standard`.

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py` (argparse + `main()` loop)
- Modify: `tests/test_gocam_ttl.py` (`test_remainders_report_fixable_column`)

- [ ] **Step 1: Update the failing test**

In `tests/test_gocam_ttl.py`, replace the body of `test_remainders_report_fixable_column` **above** the `header, rows = _read_remainders_tsv(str(out))` line. Specifically, replace this block:

```python
    # Reuse the session-scoped builder (skip re-parsing the GO ontology) while
    # still exercising main()'s full report-writing path. main() looks up
    # GoCamGraphBuilder as a module global, so patching the module attribute
    # makes it return our shared builder regardless of the constructor args.
    monkeypatch.setattr(debug_non_standard, "GoCamGraphBuilder",
                        lambda *a, **k: builder)
    monkeypatch.setattr(sys, "argv", [
        "debug_non_standard.py", "resources/test/",
        "-o", "target/go_20250601.json",
        "-r", "resources/test/ro_20250723.owl",
        "--no-label-api",
        "--tsv-output", str(out),
    ])

    debug_non_standard.main()
```

with:

```python
    # Reuse the session-scoped builder (skip re-parsing the GO ontology) while
    # still exercising gocam_ttl.main()'s full report-writing path. main() looks
    # up GoCamGraphBuilder as a module global, so patching the module attribute
    # makes it return our shared builder regardless of the constructor args.
    monkeypatch.setattr("gocam_unwinder.gocam_ttl.GoCamGraphBuilder",
                        lambda *a, **k: builder)
    monkeypatch.setattr(sys, "argv", [
        "gocam_ttl.py", "-d", "resources/test/",
        "-o", "target/go_20250601.json",
        "-r", "resources/test/ro_20250723.owl",
        "--no-label-api",
        "--remainders-report", str(out),
    ])

    gocam_ttl.main()
```

Leave all assertions (header equality, `yes_row`, `no_row`, `skip_row`, `complex_row`) unchanged.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gocam_ttl.py::test_remainders_report_fixable_column -v`
Expected: FAIL with `error: unrecognized arguments: --remainders-report` (argparse SystemExit), because the flag doesn't exist yet.

- [ ] **Step 3: Add the flag and wire it into the loop**

In the module-level argparse block, immediately after the `--criteria-fail-report` line (currently line 24), add:

```python
parser.add_argument('--remainders-report', help="Output TSV file for the bucketed remainders report (non-standard / nested-extension triage)")
```

In `main()`, find the accumulator setup:

```python
    all_date_change_records = []
    all_nested_fix_records = []
```

and replace it with:

```python
    all_date_change_records = []
    all_nested_fix_records = []

    # Remainders report accumulators. Imported here (not at module top) to avoid
    # an import cycle: remainders_report imports find_nested_extensions from this
    # module, which is only fully defined once this module finishes loading.
    from gocam_unwinder import remainders_report
    remainders_rows = []
    remainders_bucket_hits = []
    total_non_std = 0
```

In the per-model loop, immediately after the stats row is printed:

```python
        row = stats.to_extended_row() if extended else stats.to_base_row()
        print("\t".join(row), file=output)
```

add:

```python
        if args.remainders_report:
            rows, hits = remainders_report.collect_model_remainders(
                go_cam_graph_builder, gocam_graph)
            remainders_rows.extend(rows)
            remainders_bucket_hits.extend(hits)
            total_non_std += len(gocam_graph.non_standard_annotations)
```

After the loop, immediately before the `# Write date change report if requested` block, add:

```python
    # Write remainders report if requested
    if args.remainders_report:
        remainders_report.write_remainders_tsv(args.remainders_report, remainders_rows)
        remainders_report.print_bucket_summary(remainders_bucket_hits, total_non_std)
        print(f"\nRemainders report written to {args.remainders_report} "
              f"({len(remainders_rows)} rows)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gocam_ttl.py::test_remainders_report_fixable_column -v`
Expected: PASS

- [ ] **Step 5: Run full suite for regressions**

Run: `pytest -q`
Expected: All tests PASS.

---

### Task 5: Retire `debug_non_standard.py`

Repoint the last `debug_non_standard` import, drop the repo-root sys.path shim, and delete the file.

**Files:**
- Modify: `tests/test_gocam_ttl.py` (top-of-file imports)
- Delete: `debug_non_standard.py`

- [ ] **Step 1: Confirm the only remaining runtime references**

Run: `grep -rn "debug_non_standard" tests/ src/ Makefile`
Expected: matches only in `tests/test_gocam_ttl.py` (the sys.path shim + `pick_lead_aspect` import) and `Makefile` (handled in Task 6). `CLAUDE.md` is handled in Task 7.

- [ ] **Step 2: Remove the shim and repoint the import**

In `tests/test_gocam_ttl.py`, delete these lines (currently 9-15):

```python
# debug_non_standard.py lives at the repo root (not under src/ or tests/), so
# make the repo root importable for the remainders-report bucketing helpers.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from debug_non_standard import pick_lead_aspect
import debug_non_standard
```

and replace them with:

```python
from gocam_unwinder.gocam_ttl import pick_lead_aspect
```

(`import os` and `import sys` stay — `os` is still used at the `collect_model_files` tests and `sys` at the `sys.argv` monkeypatches. The `gocam_ttl` and `ModelStats` imports were already added in Task 2.)

- [ ] **Step 3: Delete the obsolete script**

Run: `rm debug_non_standard.py`

- [ ] **Step 4: Run full suite to verify**

Run: `pytest -q`
Expected: All tests PASS, including `test_mgi_2182965_lead_aspect_is_mf` (now importing `pick_lead_aspect` from `gocam_unwinder.gocam_ttl`).

- [ ] **Step 5: Confirm no stale references remain**

Run: `grep -rn "debug_non_standard" tests/ src/`
Expected: no output.

---

### Task 6: Update the Makefile to the single-pass pipeline

One `gocam_ttl.py` run now emits stats (extended) + criteria + remainders; the dedicated `debug_non_standard.py` target is removed.

**Files:**
- Modify: `Makefile`

- [ ] **Step 1: Point `REPORT_FILE` at extended content and drop the extended/standalone-remainders vars**

Replace this block (currently lines 40-42):

```makefile
NON_STD_REPORT := $(TARGET_DIR)/remainders_report_$(DATE).tsv
NON_STD_LOG := $(TARGET_DIR)/remainders_report_$(DATE).log
NON_STD_STATS_REPORT := $(TARGET_DIR)/noctua_models_graph_counts_extended_$(DATE).tsv
```

with:

```makefile
NON_STD_REPORT := $(TARGET_DIR)/remainders_report_$(DATE).tsv
```

(`REPORT_FILE` on line 37 keeps its name `noctua_models_graph_counts_$(DATE).tsv` but now carries the extended columns by default.)

- [ ] **Step 2: Add `--remainders-report` to the single split/report run**

In the `$(MODELS_SPLIT)` recipe, change:

```makefile
		--report-file $(REPORT_FILE) \
		--criteria-fail-report $(CRITERIA_FAIL_REPORT) \
		--date-change-report $(DATE_CHANGE_REPORT) \
		| tee $(TARGET_DIR)/gocam_ttl.log
```

to:

```makefile
		--report-file $(REPORT_FILE) \
		--criteria-fail-report $(CRITERIA_FAIL_REPORT) \
		--remainders-report $(NON_STD_REPORT) \
		--date-change-report $(DATE_CHANGE_REPORT) \
		| tee $(TARGET_DIR)/gocam_ttl.log
```

- [ ] **Step 3: Replace the standalone `debug_non_standard.py` target with an alias**

Replace this block (currently lines 173-188):

```makefile
$(NON_STD_REPORT): $(GO_ONTOLOGY) $(RO_ONTOLOGY) $(GROUPS_YAML)
	mkdir -p $(TARGET_DIR)
	set -o pipefail; python3 debug_non_standard.py \
		$(MODELS_DIR) \
		-o $(GO_ONTOLOGY) \
		-r $(RO_ONTOLOGY) \
		--skip-prefix SYNGO \
		--skip-prefix R-HSA \
		--skip-prefix YeastPathways \
		$(if $(SKIP_LIST),--skip-file $(SKIP_LIST),) \
		--groups-yaml $(GROUPS_YAML) \
		--tsv-output $@ \
		--stats-output $(NON_STD_STATS_REPORT) | tee $(NON_STD_LOG)

.PHONY: non_std
non_std: $(NON_STD_REPORT)
```

with:

```makefile
# Remainders + extended stats are now emitted by the single $(MODELS_SPLIT) run
# (gocam_ttl.py --remainders-report). `non_std` is kept as a convenience alias.
.PHONY: non_std
non_std: $(MODELS_SPLIT)
	@echo "Remainders report written to $(NON_STD_REPORT)"
```

- [ ] **Step 4: Make `fix_nested_anatomy` emit the analysis reports too (4 reports)**

In the `$(MODELS_NESTED_FIXED)` recipe, change:

```makefile
		--fix-nested-anatomy \
		--output-dir $(MODELS_NESTED_FIXED) \
		--nested-fix-report $(NESTED_FIX_REPORT) \
		| tee $(NESTED_FIX_LOG)
```

to:

```makefile
		--fix-nested-anatomy \
		--output-dir $(MODELS_NESTED_FIXED) \
		--nested-fix-report $(NESTED_FIX_REPORT) \
		--report-file $(REPORT_FILE) \
		--criteria-fail-report $(CRITERIA_FAIL_REPORT) \
		--remainders-report $(NON_STD_REPORT) \
		| tee $(NESTED_FIX_LOG)
```

- [ ] **Step 5: Drop the defunct "Extended model stats" push**

In the `push-reports` recipe, change:

```makefile
	push "$(REPORT_FILE)" "Standard annotation model stats $(DATE)"; \
	push "$(CRITERIA_FAIL_REPORT)" "Standard annotation criteria failures $(DATE)"; \
	push "$(NON_STD_REPORT)" "Non-standard annotation remainders $(DATE)"; \
	push "$(NON_STD_STATS_REPORT)" "Extended model stats $(DATE)"; \
	push "$(NESTED_FIX_REPORT)" "Nested anatomical extensions fixed $(DATE)"
```

to:

```makefile
	push "$(REPORT_FILE)" "Model stats (extended) $(DATE)"; \
	push "$(CRITERIA_FAIL_REPORT)" "Standard annotation criteria failures $(DATE)"; \
	push "$(NON_STD_REPORT)" "Non-standard annotation remainders $(DATE)"; \
	push "$(NESTED_FIX_REPORT)" "Nested anatomical extensions fixed $(DATE)"
```

- [ ] **Step 6: Sanity-check the Makefile parses and wiring resolves**

Run: `make -n non_std DATE=29990101 2>&1 | grep -E "gocam_ttl.py|remainders-report|debug_non_standard"`
Expected: shows the `gocam_ttl.py ... --remainders-report ...` invocation and **no** `debug_non_standard` reference.

Run: `grep -n "NON_STD_STATS_REPORT\|NON_STD_LOG\|debug_non_standard" Makefile`
Expected: no output.

---

### Task 7: Update `CLAUDE.md`

Bring the docs in line with the centralized CLI. No tests; verification is a grep + a read-through.

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the Makefile/pipeline section**

In the "Pipeline (Makefile)" command list and "Pipeline outputs" notes:
- Note that the single `gocam_ttl.py` run now also emits the remainders report and that `REPORT_FILE` (`noctua_models_graph_counts_$(DATE).tsv`) holds the **extended** columns by default.
- Replace the `make non_std` comment `# Remainders report (debug_non_standard.py)` with `# Remainders report alias (now produced by the single gocam_ttl.py run)`.
- Remove references to `noctua_models_graph_counts_extended_*.tsv` as a separate output.

- [ ] **Step 2: Update the "Running the Tool" section**

Add `--remainders-report` and `--basic-report` to the documented flags, and state that `--report-file` is extended by default. Add a brief report-flag table mirroring the design doc (stats / criteria / remainders always available; date-change with `--split-evidence`; nested-fix with `--fix-nested-anatomy`).

- [ ] **Step 3: Rewrite the Remainders-report and module references**

- Change the "**Remainders report**" paragraph lead-in from ``debug_non_standard.py --tsv-output`` to ``gocam_ttl.py --remainders-report`` and note the logic lives in `src/gocam_unwinder/remainders_report.py` (functions `collect_model_remainders`, `classify_multiple_mf_bp`, `has_differing_eco_types`, `get_eco_types_for_annot`, `write_remainders_tsv`, `print_bucket_summary`).
- In the "**Module-level helpers**" paragraph, change "promoted from `debug_non_standard.py` (which now imports them)" to reflect that `find_nested_extensions` / `pick_lead_aspect` live in `gocam_ttl.py` and are imported by `remainders_report.py`.
- Update the two test descriptions: `test_remainders_report_fixable_column` (now drives `gocam_ttl.main()` with `--remainders-report`) and `test_mgi_2182965_lead_aspect_is_mf` (imports `pick_lead_aspect` from `gocam_unwinder.gocam_ttl`). Add the new tests: `test_collect_model_remainders_fixable_row`, `test_main_callable`, `test_report_file_extended_by_default`, `test_report_file_basic_with_flag`.

- [ ] **Step 4: Verify no stale references**

Run: `grep -n "debug_non_standard\|stats-output\|tsv-output\|noctua_models_graph_counts_extended" CLAUDE.md`
Expected: no output (every reference updated to the new CLI).

---

## Verification

After all tasks are complete:

```bash
# Full test suite (requires target/go_20250601.json and resources/test/ro_20250723.owl)
pytest -q

# The new + migrated tests specifically
pytest tests/test_gocam_ttl.py -k "remainders or main_callable or report_file or lead_aspect_is_mf" -v

# Manual smoke test: one parse emits all three analysis reports
python3 src/gocam_unwinder/gocam_ttl.py \
  -d resources/test/ \
  -o target/go_20250601.json \
  -r resources/test/ro_20250723.owl \
  --no-label-api \
  --report-file /tmp/stats.tsv \
  --criteria-fail-report /tmp/criteria.tsv \
  --remainders-report /tmp/remainders.tsv
head -1 /tmp/stats.tsv        # extended header (Nested MF Extensions ... fail:*)
head -1 /tmp/remainders.tsv   # Model ID ... Target  Fixable  ECO Codes  Groups

# Basic report still available
python3 src/gocam_unwinder/gocam_ttl.py \
  -m resources/test/MGI_MGI_1100089.ttl \
  -o target/go_20250601.json -r resources/test/ro_20250723.owl \
  --no-label-api --basic-report --report-file /tmp/basic.tsv
head -1 /tmp/basic.tsv        # 11-column base header

# Makefile dry-run shows the unified invocation, no debug_non_standard
make -n non_std DATE=29990101 2>&1 | grep -E "gocam_ttl.py|--remainders-report"
grep -rn "debug_non_standard" . --include="*.py" --include="Makefile" --include="*.md" | grep -v docs/plans
```

**Expected final state:**
- [ ] All tests pass (`pytest -q`).
- [ ] `debug_non_standard.py` is deleted; no `.py`/`Makefile`/`CLAUDE.md` runtime references remain.
- [ ] One `gocam_ttl.py` invocation produces stats (extended by default) + criteria + remainders from a single parse.
- [ ] `--basic-report` reverts the stats report to the 11 base columns.
- [ ] `--fix-nested-anatomy` run yields 4 reports; a plain run yields 3.
- [ ] `--split-evidence` evidence unwinding still works (unchanged path + date-change report).

---

## Notes

- **Import cycle:** `remainders_report.py` imports `find_nested_extensions` from `gocam_ttl.py`; `gocam_ttl.main()` therefore imports `remainders_report` **locally inside `main()`**, not at module top — importing it at the top would run while `find_nested_extensions` is still undefined and raise `ImportError`. This mirrors the working pattern the old `debug_non_standard.py` used.
- **Mutation-independence invariant:** the three analysis reports are computed before any split/fix mutation in the loop, so `make pipeline` (split) and `make fix_nested_anatomy` write byte-identical stats/criteria/remainders files — re-running one after the other is harmless. Keep the loop order parse → reports → split/fix.
- **Double `plan_nested_anatomy_fixes` per model:** with the default extended stats and `--remainders-report`, the planner runs twice per model (once in `compute_model_stats(extended=True)`, once in `collect_model_remainders`). Correct but slightly redundant; a future optimization could compute `fixable_bnodes` once and thread it through. Out of scope here.
- **CURIE labels in tests:** the session `builder` has `resolve_labels_api=False`, so anatomy terms resolve to CURIEs (`CL:0000202`, `EMAPA:17597`) and `BFO:0000050` resolves to `part of` — the exact strings the remainders assertions expect.
- Related reading: `tests/conftest.py` (the `builder` fixture), `src/gocam_unwinder/gocam_ttl.py` `compute_model_stats` (the extended fields), and the design doc `docs/plans/2026-06-26-unified-report-pipeline-design.md`.
