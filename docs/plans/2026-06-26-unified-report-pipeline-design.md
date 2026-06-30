# Unified Report Pipeline — Design

**Status:** Approved (2026-06-26)
**Companion plan:** `docs/plans/2026-06-26-unified-report-pipeline.md`

## Problem

Today the model corpus is parsed **twice** across two CLI entry points:

- `gocam_ttl.py` (`__main__`): parses → writes the **base** stats report (`--report-file`),
  criteria failures, and optionally splits (`--split-evidence` → date-change report) or fixes
  (`--fix-nested-anatomy` → nested-fix report). Split & fix are mutually exclusive in one run.
- `debug_non_standard.py` (`main`): parses **again** → writes the **extended** stats report
  (`--stats-output`) and the bucketed **remainders** report (`--tsv-output`).

The Makefile runs these as separate targets (`models_split` / `non_std`), so a full report set
requires two corpus parses, and the CLI surface is split across two scripts. The bucketing logic
that builds the remainders report lives only inside `debug_non_standard.py`'s `main()`, so it
cannot be reached from the primary entry point.

## Goal

One `gocam_ttl.py` invocation parses each model **once** and produces every report. Centralize the
CLI on `gocam_ttl.py`; stop shelling out to `debug_non_standard.py`. Make the extended stats report
the default `--report-file` content. Retain evidence unwinding (`--split-evidence`).

## Architecture — one parse, reports always-on, mutation optional

A single `gocam_ttl.py` run parses each model once. In that one loop it emits whichever analysis
reports were requested, then optionally performs **one** mutation pass.

| Report | Flag | When emitted |
|---|---|---|
| Model stats (extended by default) | `--report-file` | always (or stdout if omitted) |
| Criteria failures | `--criteria-fail-report` | always |
| Remainders (bucketed) | `--remainders-report` *(new)* | always |
| Date changes | `--date-change-report` | only with `--split-evidence` |
| Nested-anatomy fixes | `--nested-fix-report` | only with `--fix-nested-anatomy` |

`--split-evidence` and `--fix-nested-anatomy` stay mutually exclusive in a single invocation (the
existing hard error is kept).

**Report count:** default run (no mutation) = 3 analysis reports; `--fix-nested-anatomy` = 4
(adds nested-fix); `--split-evidence` = 4 (adds date-change).

**Key invariant — analysis reports are mutation-independent.** The three analysis reports (stats,
criteria, remainders) are computed from the pristine parsed graph *before* any split/fix mutation,
which always happens later in the loop. Therefore their content is byte-identical across all three
modes (none / split / fix). Consequences:

- `make pipeline` (which runs `--split-evidence`) and `make fix_nested_anatomy` can both emit the
  same analysis report filenames with no clobbering risk — same bytes either way.
- The order inside the per-model loop must be: parse → compute stats / criteria / remainders →
  *then* split **or** fix.

## Components

### New module: `src/gocam_unwinder/remainders_report.py`

Lives **inside the package** (not the repo root where `debug_non_standard.py` sat): the project is
an editable install, so `gocam_unwinder.*` is importable regardless of cwd, whereas a repo-root
module is not when the Makefile runs `python3 src/gocam_unwinder/gocam_ttl.py`. Houses the
bucketing/classification logic, lifted verbatim from `debug_non_standard.py`:

- **Moved helpers:** `classify_multiple_mf_bp(annot)`, `has_differing_eco_types(annot, gocam)`,
  `get_eco_types_for_annot(annot, gocam)`.
- **`collect_model_remainders(builder, gocam) -> (rows, bucket_hits)`** — runs the full bucketing
  (nested `<aspect>` extensions, `multi_mf_same_bp`, `multi_bp_same_mf`, `extension_eco_differs`,
  `unclassified`) for one already-parsed model. Returns the model's TSV rows plus annotation-level
  `bucket_hits` (a list of `(bucket_name, model_id)` for the summary). This is the unit that slots
  into `gocam_ttl`'s existing per-model loop, so the corpus is parsed once. Computes `fixable_bnodes`
  once via `builder.plan_nested_anatomy_fixes(gocam, warn=False)`, exactly as today.
- **`REMAINDERS_HEADER`** = `["Model ID", "Title", "Bucket", "Source", "Predicate", "Target",
  "Fixable", "ECO Codes", "Groups"]`.
- **`write_remainders_tsv(path, rows)`** and **`print_bucket_summary(bucket_hits)`**.

The chatty per-model / per-edge `print()` debug lines from the old script are **dropped**; only the
end-of-run bucket summary is printed (to stdout, so the Makefile `tee` still captures it in the log).

`debug_non_standard.py` is **deleted**.

### `gocam_ttl.py`

- **Extract `def main():`** from the inline `if __name__ == "__main__":` block, then
  `if __name__ == "__main__": main()`. Required so the test can drive it, and good structure.
- **Extended stats by default:** `extended = not args.basic_report`; write
  `ModelStats.extended_header()` / `to_extended_row()` unless `--basic-report` (then base).
- **Integrate remainders** into the existing per-model loop: when `--remainders-report` is set, call
  `remainders_report.collect_model_remainders(builder, gocam)` and accumulate rows + bucket hits;
  after the loop, `write_remainders_tsv` + `print_bucket_summary`. `remainders_report` is imported
  **locally inside `main()`** (not at module top) to avoid an import cycle — `remainders_report`
  imports `find_nested_extensions` from `gocam_ttl`, which is only fully defined once `gocam_ttl`
  finishes loading.
- **New flags:** `--remainders-report PATH`, `--basic-report` (store_true).

### CLI flag surface (final)

Unchanged: `-m -d -l -o -r --output-dir --skip-prefix --skip-file --groups-yaml --no-label-api
--split-evidence --fix-nested-anatomy --criteria-fail-report --date-change-report --nested-fix-report`.

Changed: `--report-file` now defaults to **extended** content.

New: `--remainders-report PATH`, `--basic-report`.

### Makefile

- `$(MODELS_SPLIT)` step (the single `gocam_ttl.py` run, already part of `pipeline`) gains
  `--remainders-report $(NON_STD_REPORT)`. So `make pipeline` produces all analysis reports +
  split models + GPAD from one corpus parse.
- `REPORT_FILE` now holds extended content (`NON_STD_STATS_REPORT` → `REPORT_FILE`). The separate
  `NON_STD_STATS_REPORT` variable, the `$(NON_STD_REPORT)` target that shelled out to
  `debug_non_standard.py`, and the `non_std` phony are removed.
- `fix_nested_anatomy` target gains the three analysis-report flags → 4 reports for that run.
- `push-reports` drops the defunct "Extended model stats" push (the main stats sheet is now extended).

### Tests

- `test_remainders_report_fixable_column`: monkeypatch `gocam_unwinder.gocam_ttl.GoCamGraphBuilder`
  + `sys.argv` (`--remainders-report`), call `gocam_ttl.main()`; same TSV assertions.
- `test_mgi_2182965_lead_aspect_is_mf`: import `pick_lead_aspect` from `gocam_unwinder.gocam_ttl`
  (where it already lives) instead of `debug_non_standard`.
- Remove `import debug_non_standard`.

### Docs

Update `CLAUDE.md`: replace `debug_non_standard.py` references with `remainders_report.py` and the
unified `--remainders-report` flag; document the report-flag table, the `--basic-report` default flip,
and the new module's API.

## Out of scope

- Changing the GPAD export pipeline (Blazegraph / minerva-cli) or the remainders bucketing rules.
- Changing report column contents (only which report `--report-file` defaults to).
- Merging split + fix into one invocation (they stay mutually exclusive).

## Decisions recorded

- Verbose per-model/per-edge stdout from the old debug script is dropped; concise bucket summary kept.
- Per the global instruction "Do not git commit or git add", design/plan docs are written but **not**
  committed.
