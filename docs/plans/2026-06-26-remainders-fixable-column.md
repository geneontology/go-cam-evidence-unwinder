# Remainders Report "Fixable" Column Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `Fixable` (Yes/No) column to the remainders report (`debug_non_standard.py --tsv-output`) that marks, for each edge row, whether `--fix-nested-anatomy` would actually rewrite that edge.

**Architecture:** The column value is sourced from the fixer's own planner — once per model, `fixable_bnodes = {rec["bnode_id"] for rec in builder.plan_nested_anatomy_fixes(gocam, warn=False)}` — so it can never diverge from what the fixer rewrites (this also captures the planner's `!=1 primary individual` skip, where a both-anatomical edge is still *not* fixable). Each TSV row's `Fixable` cell is `"Yes" if edge.bnode_id in fixable_bnodes else "No"`. Only `debug_non_standard.py` changes; the fixer and `gocam_ttl.py` are untouched.

**Tech Stack:** Python 3, `rdflib`, `ontobio`. Tests use `pytest`.

**Design doc:** `docs/plans/2026-06-26-remainders-fixable-column-design.md`

> **Commits:** This repo's owner stages and commits manually. Do **not** run `git add` or `git commit`. Where this plan says "Checkpoint," run the listed verification and stop for the owner to review/commit.

---

## File Structure

- **Modify** `debug_non_standard.py`:
  - Add a per-model `fixable_bnodes` set inside the `for ttl_path in ttl_files:` loop (after `groups = ...`, before the nested-extension loop).
  - Insert `"Fixable"` into `tsv_headers` (between `"Target"` and `"ECO Codes"`).
  - Insert the per-edge `fixable` value into the row tuple at all three `tsv_rows.append(...)` sites (nested-extension loop, `multiple_mf_bp` loop, `extension_eco_differs` loop), in the same position as the header.
- **Modify** `tests/test_gocam_ttl.py`:
  - Add `import debug_non_standard` near the existing `from debug_non_standard import pick_lead_aspect` (line 14).
  - Append two small TSV helpers and one integration test.

No new files. No change to `src/gocam_unwinder/gocam_ttl.py`.

---

## Task 1: Add the `Fixable` column to the remainders report (TDD)

The only new behavior lives in `debug_non_standard.main()`'s report-writing path: a new header column and a per-edge Yes/No value tied to `plan_nested_anatomy_fixes`. The test invokes `main()` end-to-end against `resources/test/` (reusing the session-scoped `builder` via monkeypatch, to avoid re-parsing the GO ontology) and asserts the column on three ground-truth rows.

**Files:**
- Test: `tests/test_gocam_ttl.py` (add import at line 14; append helpers + test at end of file)
- Modify: `debug_non_standard.py` (loop body ~lines 139–158, 194–201, 208–213; header line 233)

- [ ] **Step 1: Add the module import to the test file**

In `tests/test_gocam_ttl.py`, immediately after the existing line 14 `from debug_non_standard import pick_lead_aspect`, add:

```python
import debug_non_standard
```

(The `sys.path.insert(0, _REPO_ROOT)` above line 14 already makes the repo-root module importable.)

- [ ] **Step 2: Write the failing integration test**

Append to the end of `tests/test_gocam_ttl.py`:

```python
def _read_remainders_tsv(path):
    """Parse a remainders TSV into (header_list, list_of_row_dicts)."""
    with open(path) as f:
        lines = [ln.rstrip("\n") for ln in f if ln.strip()]
    header = lines[0].split("\t")
    rows = [dict(zip(header, ln.split("\t"))) for ln in lines[1:]]
    return header, rows


def _find_remainders_row(rows, model_suffix, source, predicate, target):
    """Return the first row matching the given edge, asserting it exists."""
    matches = [r for r in rows
               if r["Model ID"].endswith(model_suffix)
               and r["Source"] == source
               and r["Predicate"] == predicate
               and r["Target"] == target]
    assert matches, (
        f"expected a remainders row for {model_suffix}: "
        f"{source} -[{predicate}]-> {target}")
    return matches[0]


def test_remainders_report_fixable_column(builder, tmp_path, monkeypatch):
    """The remainders TSV gains a `Fixable` column (immediately after `Target`)
    whose Yes/No value matches exactly what --fix-nested-anatomy
    (plan_nested_anatomy_fixes) would rewrite -- including the planner's
    `!=1 primary individual` skip, which a naive both-anatomical test would miss.
    """
    out = tmp_path / "remainders.tsv"

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

    header, rows = _read_remainders_tsv(str(out))

    # Column exists and sits immediately after Target.
    assert header == ["Model ID", "Title", "Bucket", "Source", "Predicate",
                      "Target", "Fixable", "ECO Codes", "Groups"]

    # Fixable nested anatomy edge (the canonical CL -part_of-> EMAPA) -> Yes.
    yes_row = _find_remainders_row(
        rows, "5966411600000001", "CL:0000202", "part of", "EMAPA:17597")
    assert yes_row["Fixable"] == "Yes"

    # Non-anatomy nested edge (MF source) -> No.
    no_row = _find_remainders_row(
        rows, "multi_mf_anatomy_example",
        "identical protein binding", "RO:0001025", "CL:0000066")
    assert no_row["Fixable"] == "No"

    # Design-intent guard: BOTH endpoints anatomical, but the annotation has an
    # ambiguous (!=1) primary individual, so the planner skips it -> No.
    skip_row = _find_remainders_row(
        rows, "57c82fad00000252", "nucleus", "part of", "WBbt:0005396")
    assert skip_row["Fixable"] == "No"
```

- [ ] **Step 3: Run the test to verify it fails**

Run:
```bash
source env/bin/activate && pytest tests/test_gocam_ttl.py::test_remainders_report_fixable_column -v
```
Expected: FAIL on the `assert header == [...]` line — the pre-change header is `[... "Target", "ECO Codes", "Groups"]` with no `"Fixable"` entry.

- [ ] **Step 4: Add the per-model `fixable_bnodes` set**

In `debug_non_standard.py`, inside the `for ttl_path in ttl_files:` loop, the block currently reads (lines 136–143):

```python
        model_id = "gomodel:" + os.path.basename(ttl_path).split(".")[0]
        all_stats.append(builder.compute_model_stats(gocam, model_id, extended=True))

        groups = "|".join(gocam.groups) if gocam.groups else ""

        # Buckets 1a/1b/1c: nested_<aspect>_extensions — check ALL annotations (std + non-std).
        # An annotation lands in the single aspect bucket of its lead aspect (BP > CC > MF).
        all_annots = gocam.standard_annotations + gocam.non_standard_annotations
```

Insert the `fixable_bnodes` computation between the `groups = ...` line and the `# Buckets 1a/1b/1c` comment, so the block becomes:

```python
        model_id = "gomodel:" + os.path.basename(ttl_path).split(".")[0]
        all_stats.append(builder.compute_model_stats(gocam, model_id, extended=True))

        groups = "|".join(gocam.groups) if gocam.groups else ""

        # Edges that --fix-nested-anatomy would actually rewrite (set of bnode IDs).
        # Reuse the real planner so the "Fixable" column can never diverge from the
        # fixer; warn=False keeps report generation quiet (matches compute_model_stats).
        fixable_bnodes = {rec["bnode_id"]
                          for rec in builder.plan_nested_anatomy_fixes(gocam, warn=False)}

        # Buckets 1a/1b/1c: nested_<aspect>_extensions — check ALL annotations (std + non-std).
        # An annotation lands in the single aspect bucket of its lead aspect (BP > CC > MF).
        all_annots = gocam.standard_annotations + gocam.non_standard_annotations
```

- [ ] **Step 5: Add `Fixable` to the TSV header**

In `debug_non_standard.py`, change the header (line 233) from:

```python
        tsv_headers = ["Model ID", "Title", "Bucket", "Source", "Predicate", "Target", "ECO Codes", "Groups"]
```

to:

```python
        tsv_headers = ["Model ID", "Title", "Bucket", "Source", "Predicate", "Target", "Fixable", "ECO Codes", "Groups"]
```

- [ ] **Step 6: Insert the `fixable` value at the nested-extension append site**

In `debug_non_standard.py`, the nested-extension loop currently reads (lines 152–158):

```python
            for edge in nested_edges:
                src = builder.term_label(edge.source_type) if edge.source_type else "?"
                rel = builder.term_label(edge.property_uri) if edge.property_uri else "?"
                tgt = builder.term_label(edge.target_type) if edge.target_type else "?"
                print(f"             {src} —[{rel}]→ {tgt}")
                tsv_rows.append((gocam.model_id, gocam.title, bucket_name,
                                 src, rel, tgt, eco_codes, groups))
```

Change it to (add the `fixable` line, insert `fixable` after `tgt`):

```python
            for edge in nested_edges:
                src = builder.term_label(edge.source_type) if edge.source_type else "?"
                rel = builder.term_label(edge.property_uri) if edge.property_uri else "?"
                tgt = builder.term_label(edge.target_type) if edge.target_type else "?"
                fixable = "Yes" if edge.bnode_id in fixable_bnodes else "No"
                print(f"             {src} —[{rel}]→ {tgt}")
                tsv_rows.append((gocam.model_id, gocam.title, bucket_name,
                                 src, rel, tgt, fixable, eco_codes, groups))
```

- [ ] **Step 7: Insert the `fixable` value at the `multiple_mf_bp` append site**

In `debug_non_standard.py`, the `multiple_mf_bp` loop currently reads (lines 195–201):

```python
                    for bnode_id in annot.failed_checks["multiple_mf_bp"]:
                        edge = annot.edges[bnode_id]
                        tsv_rows.append((gocam.model_id, gocam.title, bucket_name,
                                         builder.term_label(edge.source_type) if edge.source_type else "?",
                                         builder.term_label(edge.property_uri) if edge.property_uri else "?",
                                         builder.term_label(edge.target_type) if edge.target_type else "?",
                                         eco_codes, groups))
```

Change it to (add the `fixable` line, insert `fixable` after the target label):

```python
                    for bnode_id in annot.failed_checks["multiple_mf_bp"]:
                        edge = annot.edges[bnode_id]
                        fixable = "Yes" if bnode_id in fixable_bnodes else "No"
                        tsv_rows.append((gocam.model_id, gocam.title, bucket_name,
                                         builder.term_label(edge.source_type) if edge.source_type else "?",
                                         builder.term_label(edge.property_uri) if edge.property_uri else "?",
                                         builder.term_label(edge.target_type) if edge.target_type else "?",
                                         fixable, eco_codes, groups))
```

- [ ] **Step 8: Insert the `fixable` value at the `extension_eco_differs` append site**

In `debug_non_standard.py`, the `extension_eco_differs` loop currently reads (lines 208–213):

```python
                    for edge in annot.edges.values():
                        tsv_rows.append((gocam.model_id, gocam.title, "extension_eco_differs",
                                         builder.term_label(edge.source_type) if edge.source_type else "?",
                                         builder.term_label(edge.property_uri) if edge.property_uri else "?",
                                         builder.term_label(edge.target_type) if edge.target_type else "?",
                                         eco_codes, groups))
```

Change it to (add the `fixable` line, insert `fixable` after the target label):

```python
                    for edge in annot.edges.values():
                        fixable = "Yes" if edge.bnode_id in fixable_bnodes else "No"
                        tsv_rows.append((gocam.model_id, gocam.title, "extension_eco_differs",
                                         builder.term_label(edge.source_type) if edge.source_type else "?",
                                         builder.term_label(edge.property_uri) if edge.property_uri else "?",
                                         builder.term_label(edge.target_type) if edge.target_type else "?",
                                         fixable, eco_codes, groups))
```

- [ ] **Step 9: Run the test to verify it passes**

Run:
```bash
source env/bin/activate && pytest tests/test_gocam_ttl.py::test_remainders_report_fixable_column -v
```
Expected: PASS.

If the header assertion still fails, recheck Step 5. If `_find_remainders_row` raises for the `5966411600000001` row, confirm the RO ontology path resolved (the BP backbone needs RO) — the row only appears as `nested_bp_extensions` when RO is loaded.

- [ ] **Step 10: Checkpoint** — stop for review/commit.

---

## Task 2: Full-suite regression + end-to-end smoke

Confirm nothing else changed and the column reads correctly across the whole fixture set.

**Files:** none (verification only).

- [ ] **Step 1: Run the full suite**

Run:
```bash
source env/bin/activate && pytest -q
```
Expected: all tests PASS (the existing suite plus the new `test_remainders_report_fixable_column`). No other test changes behavior — the edit is confined to `debug_non_standard.py`'s report writer.

- [ ] **Step 2: End-to-end smoke of the report**

Run:
```bash
source env/bin/activate && python3 debug_non_standard.py resources/test/ \
  -o target/go_20250601.json -r resources/test/ro_20250723.owl \
  --no-label-api --tsv-output /tmp/remainders_fixable.tsv >/dev/null 2>&1; echo "exit=$?"
echo "=== header ==="; head -1 /tmp/remainders_fixable.tsv
echo "=== Fixable distribution among nested rows ==="
awk -F'\t' 'NR>1 && $3 ~ /^nested_/ {print $7}' /tmp/remainders_fixable.tsv | sort | uniq -c
echo "=== sample Yes/No rows ==="
awk -F'\t' 'NR>1 && $3 ~ /^nested_/ {print $7"\t"$1"\t"$4" -["$5"]-> "$6}' /tmp/remainders_fixable.tsv | sort | head -12
```
Expected:
- `exit=0`.
- Header is `Model ID  Title  Bucket  Source  Predicate  Target  Fixable  ECO Codes  Groups` (Fixable is the 7th column).
- The distribution shows both `Yes` and `No` among nested rows (per the ground-truth scan: the CL/EMAPA, CL:0000222/EMAPA:16752, SYNGO synapse→UBERON, and the synthetic `*_nested_anatomy_example` rows are `Yes`; the gene-product-target, BP-source, and ambiguous-primary rows are `No`).
- The `5966411600000001` `CL:0000202 part of EMAPA:17597` row shows `Yes`; the `multi_mf_anatomy_example` and `57c82fad00000252 nucleus part of WBbt:0005396` rows show `No`.

- [ ] **Step 3: Checkpoint** — stop for review/commit.

---

## Verification

After all tasks:

```bash
# Full suite
source env/bin/activate && pytest -q

# Report smoke (Fixable column present and populated)
source env/bin/activate && python3 debug_non_standard.py resources/test/ \
  -o target/go_20250601.json -r resources/test/ro_20250723.owl \
  --no-label-api --tsv-output /tmp/remainders_fixable.tsv
head -1 /tmp/remainders_fixable.tsv
```

Expected final state:
- [ ] All tests pass, including `test_remainders_report_fixable_column`.
- [ ] The remainders TSV header is `Model ID, Title, Bucket, Source, Predicate, Target, Fixable, ECO Codes, Groups`.
- [ ] Every row carries a `Fixable` value of `Yes`/`No`; `Yes` iff the edge's bnode is in `plan_nested_anatomy_fixes(gocam, warn=False)` for its model — including the ambiguous-primary skip (both-anatomical but `No`).
- [ ] `src/gocam_unwinder/gocam_ttl.py` and the `--fix-nested-anatomy` fixer are unchanged.

---

## Notes

- **Why `warn=False`:** `plan_nested_anatomy_fixes` prints a warning per annotation skipped for `!=1` primary individuals when `warn=True` (the default). The report path doesn't act on those warnings and would otherwise emit them once per model; `compute_model_stats` already calls the planner with `warn=False` for the same reason.
- **Why tie to the planner instead of an `_is_anatomical_structure` check on both endpoints:** the planner additionally skips annotations whose lead aspect has 0 or >1 primary individual. `57c82fad00000252`'s `nucleus —part_of→ WBbt` edge is both-anatomical yet not rewritten for exactly this reason; the `Fixable` column reflects reality only by deferring to the planner.
- **Why reuse the session `builder` in the test (monkeypatch):** `main()` constructs a fresh `GoCamGraphBuilder`, which re-parses the (slow) GO ontology JSON. Patching `debug_non_standard.GoCamGraphBuilder` to return the session-scoped fixture keeps the test fast while still exercising the full `main()` report-writing path (header, all three append sites, file write).
- **Files worth reading before starting:**
  - `docs/plans/2026-06-26-remainders-fixable-column-design.md` — the design.
  - `debug_non_standard.py` — the model loop (130–218) and TSV writer (231–238).
  - `src/gocam_unwinder/gocam_ttl.py:1313` — `plan_nested_anatomy_fixes` (record schema, `warn` kwarg) and `:1440` — the `fixer_bnodes` pattern in `compute_model_stats`.
  - `tests/conftest.py` — the session-scoped `builder` fixture and its ontology paths.
```
