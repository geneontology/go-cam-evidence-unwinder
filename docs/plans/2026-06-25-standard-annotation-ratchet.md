# Standard-Annotation Ratchet Pipeline — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

> **Status:** LOCAL-ONLY work (branch `local/std-annot-ratchet`). Not to be pushed
> upstream for now; may be PR'd later. Do not push or open a PR without explicit
> instruction.
>
> **Progress:** **Tasks 1–8 done and green** (full suite **91 passed**, incl. the
> 42 existing tests). The ratchet is runnable end to end:
> `python -m gocam_unwinder.ratchet --jobs N` / `make ratchet[-smoke] JOBS=N`.
> Built `src/gocam_unwinder/ratchet/{core,rules,runner,engine,model_rules,unit_rules,cli,summary,__main__}.py`;
> a full `rules/` data set (5 model + 12 unit manifests, incl. 2 native `.rq`
> SPARQL rules); `gocam_ttl.py` refactored into per-check callables + `classify=`;
> parallel sharding (`--jobs N`, round-robin, ProcessPoolExecutor) with
> shard-level resume and exact merge. Smoke over `resources/test`: 25 models → 23
> (Phase A), 130 annotation units → 102 standard (Phase B); sharded `--jobs 4`
> gives the identical result. **All planned tasks complete.**
>
> **First full corpus run (2026-06-25, `--jobs 48` on a 96-core host, ~78 s):**
> 55,831 models → 46,071 candidate models (Phase A: −1,409 delete, −6,345 prefix,
> −1,988 R1 true-GO-CAM, −18 edge-less) → 250,865 annotation units → **238,750
> standard annotations** (Phase B; top filters: invalid_gp_cc_relation 7,288,
> inconsistent_evidence 2,252; invalid_bp_cc/invalid_mf_bp removed 0). ~1.1 G of
> per-stage manifests + reports.
>
> **Post-run additions (2026-06-25/26):** R1 wired to the live skyhook source
> (see R1 note); a self-contained HTML **dashboard** with explorable model axes
> (see *Dashboard* below), fed by a new per-model `model_stats.jsonl`. Full suite
> **97 passed**.

**Goal:** Build a two-level, monotonic "ratchet" mini-pipeline that takes the
noctua-models corpus (S0) and reduces it, one rule at a time, to the set of
*standard annotations* — emitting at each stage the surviving set (Sₙ) plus a
report of what that rule filtered out.

**Architecture:** Two phases. **Phase A (model-level)** applies cheap whole-model
gates — `modelstate`, filename prefix, and **R1 "not-a-true-GO-CAM"** (consume
pipeline-from-goa's published true-GO-CAM model-ID set) — to shrink the corpus
fast. **Phase B (annotation-unit-level)** extracts annotation units from the
surviving models and ratchets them through the existing 13 `gocam_ttl.py` checks,
re-packaged as independently-runnable, cost-ordered rule stages. Rules are stored
as a structured file set (`rules/NN-name/rule.yaml` + `query.rq` | python
entrypoint); a thin runner walks them in order, reads the prior stage's survivors,
and writes `Sₙ` survivors + `Sₙ` rejects + `reports/NN.jsonl`, then a roll-up
summary. Engine is **per-file in-process rdflib** (supports both SPARQL `.rq` and
Python predicates), reusing the existing `GoCamGraphBuilder` so ontology-aware
checks stay cheap.

**Tech Stack:** Python 3, rdflib (per-file parse + SPARQL 1.1), ontobio /
GoAspector (GO aspect), the existing `src/gocam_unwinder/gocam_ttl.py`
parsing + check logic, PyYAML rule manifests. No Blazegraph, no `arq`-per-file.

**Affected Areas:** New importable subpackage `src/gocam_unwinder/ratchet/`
(runner, rule loader, engine adapters); new top-level `rules/` data dir; small,
additive refactor exposing the 13 checks as per-check callables in `gocam_ttl.py`;
new `tests/`; a new `Makefile` ratchet target. Existing classification behavior is
unchanged (additive only).

---

## Context

This repo already determines, per annotation unit, whether it is a "standard
annotation" (splittable: one evidence per edge) via `filter_out_non_std_annotations()`
in `gocam_ttl.py`, running all 13 checks at once with no short-circuit. We want
to lift that logic into a **staged, monotonic pipeline** mirroring the
divide-and-conquer pattern in sibling repos `pipeline-from-goa` (numbered stage
dirs, sibling reject dirs, per-stage `reports/NN.jsonl`, roll-up summary) and
`noctua-models` (per-file SPARQL `.rq` QC, git-diff ratchet). The motivating
property: each stage carries forward a *shrinking* survivor set, so later stages
do strictly less work and the run gets faster as it proceeds.

Key domain facts established during scoping (see `docs/plans/` discussion and
CLAUDE.md rule docs):

- **Two levels, not one.** "Standard annotation" is an *annotation-unit* concept
  (a connected-component subgraph). "True GO-CAM" is a *whole-model* concept.
  Per the chosen design, Phase A ratchets models, Phase B ratchets units.
- **R1 = not-a-true-GO-CAM.** A True GO-CAM is a *production*, *pathway-like*
  model (≥3 activities connected by causal / shared-chemical edges), classified
  by `gocam-py/pipeline/filter_true_gocam_models.py`. By definition such a model
  is not a flat standard annotation. We **consume** the published true-GO-CAM set
  (pipeline-from-goa product `go-cams/json/`, ~2,008 model IDs; filename stem =
  model ID) rather than recompute it.
- **The expensive shared dependency** in Phase B is the connected-component
  annotation partition (union-find over edges sharing individuals). Four checks
  need it; everything else is per-edge.
- **RO-dependent rules skip (not fail-open) when RO is absent** — must replicate.

### Authoritative rule inventory (extracted; the prereq)

Model gates (Phase A) + 13 numbered unit checks + 1 internal invariant (Phase B),
in cost-tier order. TSV# is from the (never-committed) `std_annot_rules.tsv`,
reproduced in `docs/plans/2026-06-05-remaining-std-annot-criteria-design.md`.

| Phase / Stage | TSV# | Rule key | Category | Inputs | SPARQL feasibility |
|---|---|---|---|---|---|
| A · gate | — | `modelstate==delete` skip | model-gate | model graph | trivial |
| A · gate | — | `skip_prefix` (SYNGO, R-HSA) | model-gate | filename | n/a |
| A · gate (**R1**) | — | `not_true_gocam` | model-gate | published true-GO-CAM ID set | n/a (membership) |
| B · 1 cheap | 15 | `edge_without_evidence` | evidence | none | ✅ trivial |
| B · 1 cheap | 13 | `enabler_not_gp` | relation | GP allowlist | ⚠️ moderate |
| B · 2 GO-aspect | 3 | `invalid_gp_cc_relation` | relation | GO + GP | ⚠️ moderate |
| B · 2 GO-aspect | 6 | `invalid_mf_cc_relation` | relation | GO | ⚠️ moderate |
| B · 2 GO-aspect | 10 | `invalid_bp_cc_relation` | relation | GO + anatomy | ⚠️ moderate |
| B · 3 RO-closure | 1 | `mf_causal_mf` | relation | GO + RO:0002418 | ⚠️ moderate |
| B · 3 RO-closure | 4 | `invalid_gp_bp_relation` | relation | GO + GP + RO:0002264 | ⚠️ moderate |
| B · 3 RO-closure | 5 | `invalid_mf_bp_relation` | relation | GO + RO:0002264/0002418 | ⚠️ moderate |
| B · 4 partition | 11 | `multiple_mf_bp` | cardinality | GO + RO | 🔶 partial |
| B · 4 partition | 12 | `multiple_mf_anatomy` | cardinality | GO + anatomy | 🔶 partial |
| B · 4 partition | 2 | `invalid_gp_mf_relation` | relation | GO + GP | ❌ hard |
| B · 4 partition | — | `inconsistent_evidence` (internal) | evidence | none (grouping) | ❌ hard |
| informational | 7/8/9 | non-root nesting MF→BP/MF→CC/BP→BP | — | — | no check (extensions) |

## Constraints

- **Local-only.** All work on branch `local/std-annot-ratchet`; never push / PR
  without explicit instruction.
- **Reuse, don't fork.** Phase B rules must call the existing check logic in
  `gocam_ttl.py`, not reimplement it (the Hybrid decision). Ontology-aware checks
  stay in Python; only new/simple structural rules may be authored as `.rq`.
- **Monotonic.** Each stage reads *only* the prior stage's survivors; output sets
  only shrink. A stage may never resurrect a removed item.
- **Scale.** Must process ~55,831 models (~2 GB) without OOM: per-file streaming,
  embarrassingly parallel by shard, checkpointed via a survivor manifest.
- **Fidelity.** Per-unit rule verdicts must match `filter_out_non_std_annotations()`
  on the existing fixtures (no semantic drift). RO-dependent rules skip when RO
  absent.
- **Engine.** Per-file in-process rdflib only. Do **not** load the corpus into
  Blazegraph (it keeps the full journal hot and fights the shrink) and do **not**
  spawn `arq` per (file, rule) (JVM startup × 55k × N).

## Out of Scope

- Recomputing true-GO-CAM classification ourselves (we consume the published set;
  a local `gocam-py` recompute is a documented fallback only).
- Rewriting the ontology-aware checks as SPARQL.
- Blazegraph corpus loading / GPAD export.
- Changing existing `gocam_ttl.py` classification semantics or the unwinder CLI.
- Pushing, PR'ing, or any upstream publication (deferred).
- Parallel-shard execution can land as a follow-up (Task 8) — single-process
  correctness first.

---

## Tasks

### Task 1: Rule manifest schema + ordered loader

A rule lives in `rules/NN-name/` with a `rule.yaml`:

```yaml
id: edge_without_evidence      # stable key (matches failed_checks key where applicable)
tsv: 15                         # std_annot_rules.tsv number, or null
phase: unit                     # model | unit
stage: 1                        # cost tier (ordering within phase)
kind: python                    # python | sparql
entrypoint: edge_without_evidence   # python: callable name in ratchet.unit_rules; sparql: query.rq present
inputs: []                      # e.g. [go, ro, gp_allowlist, true_gocam_set]
description: "Every GO-CAM relation edge must carry a lego:evidence triple."
enabled: true
```

**Files:**
- Create: `src/gocam_unwinder/ratchet/__init__.py`
- Create: `src/gocam_unwinder/ratchet/rules.py` (`Rule` dataclass, `load_rules(rules_dir)`)
- Create: `rules/00-model-modelstate/rule.yaml`, `rules/01-edge-without-evidence/rule.yaml` (seed fixtures)
- Test: `tests/test_ratchet_rules.py`

**Step 1: Write the failing test**

```python
def test_load_rules_ordered_and_parsed(tmp_path):
    # Arrange: two rule dirs out of lexical order on disk
    _write_rule(tmp_path / "rules" / "10-b", id="b", phase="unit", stage=2, kind="python")
    _write_rule(tmp_path / "rules" / "00-a", id="a", phase="model", stage=0, kind="python")

    # Act
    rules = load_rules(tmp_path / "rules")

    # Assert: ordered by dir prefix; fields parsed
    assert [r.id for r in rules] == ["a", "b"]
    assert rules[0].phase == "model" and rules[1].kind == "python"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_ratchet_rules.py::test_load_rules_ordered_and_parsed -v`
Expected: FAIL with `ImportError`/`ModuleNotFoundError` for `ratchet.rules`.

**Step 3: Write minimal implementation**

`Rule` dataclass with the yaml fields; `load_rules()` globs `*/rule.yaml`, sorts
by parent dir name, parses with PyYAML, validates `phase`/`kind` enums, returns a
list. SPARQL rules require a sibling `query.rq`.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_ratchet_rules.py -v` — Expected: PASS

---

### Task 2: Ratchet runner core (the staging engine)

Generic over phase: given an input *set* (a manifest of survivor IDs + their
backing files) and a rule, partition into survivors / rejects, materialize
`SNN-<id>/` (survivors), `SNN-<id>.rejects.tsv`, and `reports/NN.jsonl`.

**Files:**
- Create: `src/gocam_unwinder/ratchet/runner.py` (`Stage`, `run_stage`, `Manifest`)
- Test: `tests/test_ratchet_runner.py`

**Step 1: Write the failing test**

```python
def test_run_stage_partitions_and_reports(tmp_path):
    # Arrange: a 4-item set + a trivial rule "keep ids that don't start with X"
    manifest = Manifest.of(["a1", "a2", "X3", "X4"])
    rule = _trivial_reject_rule(predicate=lambda item_id: item_id.startswith("X"),
                                reason="starts_with_X")

    # Act
    out = run_stage(rule, manifest, out_dir=tmp_path, stage_index=1)

    # Assert: survivors shrink, rejects + report record the reason
    assert out.survivors.ids == ["a1", "a2"]
    assert out.rejected_ids == ["X3", "X4"]
    report = _read_jsonl(tmp_path / "reports" / "01-starts_with_X.jsonl")
    assert {r["id"]: r["decision"] for r in report} == {
        "a1": "keep", "a2": "keep", "X3": "remove", "X4": "remove"}
    assert all(r["reason"] == "starts_with_X" for r in report if r["decision"] == "remove")
```

**Step 2: Run test to verify it fails** — Expected: FAIL (`run_stage` undefined).

**Step 3: Write minimal implementation**

`run_stage` iterates the input manifest, calls `rule.apply(item) -> Verdict(keep,
reason, detail)`, writes survivors manifest + rejects.tsv + jsonl, returns a
`StageResult`. Monotonic invariant asserted: `survivors ⊆ input`.

**Step 4: Run test to verify it passes** — Expected: PASS

---

### Task 3: Engine adapters — Python rule + SPARQL rule

Two `rule.apply` strategies behind one `Verdict` interface, both over a per-file
rdflib graph (lazily parsed, cached per item within a stage).

**Files:**
- Create: `src/gocam_unwinder/ratchet/engine.py` (`PythonRuleAdapter`, `SparqlRuleAdapter`)
- Test: `tests/test_ratchet_engine.py`

**Step 1: Write the failing test**

```python
def test_sparql_ask_rule_removes_on_match(tmp_path, edgeless_model_ttl):
    # ASK that is TRUE when the model has an OBO-namespace edge with no evidence
    rule = SparqlRuleAdapter(query_path=EDGE_NO_EV_RQ, remove_when="ask_true",
                             reason="edge_without_evidence")
    verdict = rule.apply(_item(edgeless_model_ttl))
    assert verdict.keep is False and verdict.reason == "edge_without_evidence"

def test_python_rule_uses_existing_check(monkeypatch, mgi_model_item):
    rule = PythonRuleAdapter(callable_=lambda ctx, item: Verdict.keep_all(),
                             reason="noop")
    assert rule.apply(mgi_model_item).keep is True
```

**Step 2: Run test to verify it fails** — Expected: FAIL.

**Step 3: Write minimal implementation**

- `SparqlRuleAdapter`: `rdflib.Graph().parse(item.path)`, run ASK/SELECT, map
  result to keep/remove per `remove_when`.
- `PythonRuleAdapter`: call a callable `(ctx, item) -> Verdict`, where `ctx`
  carries the shared `GoCamGraphBuilder` (GO/RO loaded once).

**Step 4: Run test to verify it passes** — Expected: PASS

---

### Task 4: Phase A model-level rules

**Files:**
- Create: `src/gocam_unwinder/ratchet/model_rules.py`
- Create: `rules/00-model-modelstate/`, `rules/01-model-skip-prefix/`,
  `rules/02-model-not-true-gocam/` (+ `rule.yaml`s)
- Test: `tests/test_ratchet_model_rules.py`

**Step 1: Write the failing tests**

```python
def test_modelstate_delete_removed(delete_state_item, prod_item):
    assert modelstate_gate(ctx=None, item=delete_state_item).keep is False
    assert modelstate_gate(ctx=None, item=prod_item).keep is True

def test_skip_prefix_removes_syngo_and_rhsa(item_named):
    gate = make_skip_prefix_gate(["SYNGO", "R-HSA"])
    assert gate(None, item_named("SYNGO_5371.ttl")).keep is False
    assert gate(None, item_named("MGI_MGI_1100089.ttl")).keep is True

def test_not_true_gocam_consumes_id_set(item_named, true_gocam_ids):
    # true_gocam_ids loaded from a fixture list (filename stems)
    gate = make_not_true_gocam_gate(true_gocam_ids={"605c...01"})
    assert gate(None, item_named("605c...01.ttl")).keep is False   # is a true GO-CAM -> removed
    assert gate(None, item_named("MGI_MGI_1100089.ttl")).keep is True
```

**Step 2: Run test to verify it fails** — Expected: FAIL.

**Step 3: Write minimal implementation**

- `modelstate_gate`: reuse `GoCamGraph.get_modelstate()`; remove if `== "delete"`.
- `make_skip_prefix_gate(prefixes)`: filename `startswith`.
- `make_not_true_gocam_gate(true_gocam_ids)`: remove if `item.model_id_stem in
  true_gocam_ids`. The ID set is loaded by `load_true_gocam_ids(source)`, which
  accepts the published skyhook base URL (default
  `https://skyhook.geneontology.io/pipeline-from-goa/main` →
  `reports/go-cam/02-filter.jsonl`, `status == "success"`), a `.jsonl` report, a
  local dir of `*.json` stems, or a newline id file. **Resolved** (see Notes).

**Step 4: Run test to verify it passes** — Expected: PASS

---

### Task 5: Phase B unit rules — expose the 13 checks as per-check callables

Additive refactor: split `filter_out_non_std_annotations()` so each check is a
standalone function `check_<key>(builder, annotation) -> set[bnode_id]` (the edges
it flags), and have the existing method call them in a loop (behavior preserved).
Then wrap each as a unit Rule whose `apply` removes an annotation unit if the check
flags ≥1 of its edges.

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py` (extract per-check callables; keep
  `filter_out_non_std_annotations()` delegating to them — no semantic change)
- Create: `src/gocam_unwinder/ratchet/unit_rules.py` (adapters mapping each check
  to a `Verdict` over an annotation unit)
- Create: `rules/1x-*/`..`rules/4x-*/` rule.yaml per check, staged by cost tier
- Test: `tests/test_ratchet_unit_rules.py`

**Step 1: Write the failing test**

```python
@pytest.mark.parametrize("model,check_key,expect_fail", [
    ("66c7d41500000016.ttl", "edge_without_evidence", True),
    ("MGI_MGI_1100089.ttl",  "edge_without_evidence", False),
    ("5b318d0900000481.ttl", "mf_causal_mf",          True),   # needs RO
    ("enabler_not_gp_example.ttl", "enabler_not_gp",  True),
])
def test_unit_rule_matches_existing_classification(builder_with_ro, model, check_key, expect_fail):
    units = parse_units(builder_with_ro, RES / model)
    verdicts = [unit_rule(check_key).apply(_unit_item(u)) for u in units]
    any_removed = any(v.keep is False for v in verdicts)
    assert any_removed == expect_fail
```

**Step 2: Run test to verify it fails** — Expected: FAIL (`unit_rule` undefined /
checks not yet extracted).

**Step 3: Write minimal implementation**

Extract each check block into `check_<key>()`; build `unit_rule(key)` mapping to
the right callable + reason; RO-dependent rules return `Verdict.skip()` (keep,
not flag) when `builder.ro_ontology is None`, replicating current gating.

**Step 4: Run test to verify it passes** — Expected: PASS

**Step 5: Run full test suite for regressions**

Run: `pytest -v` — Expected: all existing `gocam_ttl` tests still PASS (refactor
is behavior-preserving).

---

### Task 6: Two native `.rq` SPARQL rules (prove the hybrid path)

Author `edge_without_evidence` as a portable `.rq` ASK (it is the one trivial
case), and port `noctua-models/sparql/check-disconnected-individuals.rq` as an
example structural rule, to demonstrate `.rq` rules run under the same runner.

**Files:**
- Create: `rules/01-edge-without-evidence/query.rq` (kind: sparql variant)
- Create: `rules/90-disconnected-individuals/query.rq` + `rule.yaml`
- Test: `tests/test_ratchet_sparql_rules.py`

**Step 1: Write the failing test**

```python
def test_sparql_edge_without_evidence_matches_python(no_ev_item, ev_item):
    rq = SparqlRuleAdapter(EDGE_NO_EV_RQ, remove_when="ask_true",
                           reason="edge_without_evidence")
    assert rq.apply(no_ev_item).keep is False
    assert rq.apply(ev_item).keep is True
```

**Step 2–4:** Standard TDD cycle; the `.rq` mirrors the Python check’s OBO-namespace
edge filter.

---

### Task 7: Runner CLI + roll-up report + two-level wiring + Makefile

Chain Phase A → surviving models → Phase B over those; emit a roll-up summary
(per-stage in/out counts + cumulative shrink) and a Makefile target writing to
`target_ratchet_$(date +%Y%m%d)/`.

**Files:**
- Create: `src/gocam_unwinder/ratchet/cli.py` (`python -m gocam_unwinder.ratchet
  --models-dir … --rules-dir rules --out-dir target_ratchet_YYYYMMDD
  --true-gocam-dir … [--ro …] [--go …]`)
- Create: `src/gocam_unwinder/ratchet/summary.py` (merge `reports/*.jsonl` → TSV/MD)
- Modify: `Makefile` (add `ratchet` target)
- Test: `tests/test_ratchet_end_to_end.py`

**Step 1: Write the failing integration test**

```python
def test_end_to_end_two_level_shrink(tmp_path, fixture_models_dir):
    result = run_ratchet(models_dir=fixture_models_dir, rules_dir=RULES,
                         out_dir=tmp_path, go=GO_JSON, ro=RO_OWL,
                         true_gocam_ids={"R-HSA-9937080"})
    # Phase A removed the true-GO-CAM + delete-state models
    assert "R-HSA-9937080" not in result.phase_a_survivors
    # Monotonic: every stage count <= previous
    counts = [s.in_count for s in result.stages] + [result.stages[-1].out_count]
    assert counts == sorted(counts, reverse=True)
    # Roll-up summary exists and lists every stage
    assert (tmp_path / "summary.tsv").exists()
```

**Step 2: Run test to verify it fails** — Expected: FAIL.

**Step 3: Write implementation** — wire phases, summary, CLI, Makefile target.

**Step 4–5:** Run integration test, then `pytest -v` for full regression.

---

### Task 8 (follow-up): resume + parallel sharding

Checkpoint the survivor manifest per stage (skip completed stages on rerun);
shard the Phase B input across workers (`--jobs N`), merging per-shard
survivor/reject manifests. Deferred until single-process correctness is proven.

---

## Verification

```bash
# Full test suite (new ratchet tests + existing gocam_ttl regression)
pytest -v

# Smoke test over the bundled fixtures (no corpus download needed)
python -m gocam_unwinder.ratchet \
  --models-dir resources/test \
  --rules-dir rules \
  --out-dir /tmp/ratchet-smoke \
  --go target/go_20250601.json \
  --ro resources/test/ro_20250723.owl \
  --true-gocam-ids R-HSA-9937080

# Inspect the ratchet: each stage's survivors shrink; rejects carry reasons
column -t -s$'\t' /tmp/ratchet-smoke/summary.tsv
head /tmp/ratchet-smoke/reports/*.jsonl
```

**Expected final state:**
- [ ] All tests pass; existing `gocam_ttl` tests unchanged (behavior-preserving refactor).
- [ ] Per-unit rule verdicts match `filter_out_non_std_annotations()` on every fixture.
- [ ] Two-level run: Phase A shrinks models, Phase B shrinks units over survivors.
- [ ] Each stage emits survivors + rejects + `reports/NN.jsonl`; roll-up `summary.tsv` lists all stages with monotonically non-increasing counts.
- [ ] R1 consumes an external true-GO-CAM ID set (dir/file now; URL fallback documented).
- [ ] Rules are pure data in `rules/`; adding a rule needs no runner change.

---

## Notes

- **Behavior-preserving refactor first (Task 5).** The extraction of per-check
  callables must not change `filter_out_non_std_annotations()` output — keep it
  delegating. Run the existing suite before/after.
- **Cardinality + exemption checks (#11/#12/#2) and `inconsistent_evidence`** need
  the whole annotation unit, not a single edge — they live in Phase B stage 4 and
  operate on the unit the runner hands them; they cannot be authored as `.rq`.
- **RO gating:** `mf_causal_mf`, `invalid_gp_bp_relation`, `invalid_mf_bp_relation`
  must be *skipped* (item kept) when no RO is loaded — do not fail-open to "remove".
- **R1 sourcing — RESOLVED.** `--true-gocam-source` consumes the published
  pipeline-from-goa skyhook base (default
  `https://skyhook.geneontology.io/pipeline-from-goa/main`); `load_true_gocam_ids`
  appends `reports/go-cam/02-filter.jsonl` and keeps `status == "success"` ids
  (2,137 true GO-CAMs; `model_id` is the bare stem, matching our `.ttl` stems).
  Verified live. Also accepts a local dir / id-file / `.jsonl`. A browser-like
  User-Agent is required (skyhook is Cloudflare-fronted; default `Python-urllib`
  gets a 403). Local recompute via `gocam-py/pipeline/filter_true_gocam_models.py`
  remains a fallback if the published set is unavailable.
- **Monotonic invariant** is the correctness backbone: assert `survivors ⊆ input`
  in `run_stage`; the roll-up must show non-increasing counts.
- **Engine choice rationale** (do not revisit lightly): per-file rdflib is
  embarrassingly parallel and the shrinking survivor set directly reduces later
  work; Blazegraph keeps the full 2 GB journal hot regardless and fights the
  shrink; `arq`-per-file pays JVM startup × 55k × N. See
  `noctua-models/Makefile` (blazegraph) and `.github/workflows/check-models.yml`
  (arq) for the patterns we are deliberately *not* using at scale.
- **Reference patterns worth reading:** `pipeline-from-goa/scripts/gocam-processing.sh`
  (numbered stage dirs `01-…→05-…`, sibling reject dir `--pseudo-gocam-output-dir`,
  per-stage `reports/NN.jsonl`, roll-up `generate_log_summary.py`);
  `noctua-models/sparql/*.rq` (the `.rq` rule template: SELECT-DISTINCT violation
  enumerator, model-header attribution join, edges in both direct + reified form).

## Running at corpus scale

The full corpus (~55,831 TTLs, ~2 GB) is resource-hungry, so it's worth running on
a machine with many cores and ample RAM. It runs as an ordinary, non-root user —
the **generic, reproducible recipe** (`make corpus && make ratchet JOBS=N && make
ratchet-dashboard`, with the corpus / ontologies / groups.yaml auto-fetched and R1
via the public skyhook URL) is in the README's "Standard-Annotation Ratchet"
section, along with notes on sizing `JOBS` and driving the run non-interactively
over SSH.

- **Sizing `N`:** bounded by cores **and** RAM, since each worker loads its own
  ~0.7 GB GO copy. A 96-core / ~1 TiB host ran the whole corpus at `--jobs 48` in
  ~78 s.
- **GOTCHA — shard resume vs. fresh output:** each shard writes a `.done` marker,
  so re-running to the *same* `--out-dir` returns cached stage stats and does NOT
  regenerate per-item files. After a code change that adds/alters outputs (e.g.
  adding `model_stats.jsonl`), delete the out-dir first to force a fresh run.

## Dashboard (stats suite)

`gocam_unwinder.ratchet.dashboard` turns a run's `summary.tsv` + `rejects/*.tsv` +
`model_stats.jsonl` (+ `rules/` for descriptions, `groups.yaml` for group labels)
into ONE self-contained `dashboard.html` — data embedded inline, no external
libs/network, shareable by sending the file. `make ratchet-dashboard
RATCHET_OUT=<out>`.

Shows: the two-phase funnel (green/red shrink bars); a per-rule drill-down of the
top offending `predicate → target` patterns (example-model chips deep-link into
the Noctua editor); and **explorable model axes** — toggle by modelstate or
editorial group (providedBy resolved via groups.yaml), per-bucket models /
passing / unit pass-rate, a per-model percent-passing histogram, and a model list
linking to `<noctua_base>gomodel:<id>`. All tables are click-to-sort.

- Per-model stats come from `model_stats.jsonl` (one row per Phase B model:
  modelstate, raw providedBy group URIs, title, total/passing units), emitted by
  `cli._run_phase_b` and merged across shards. Group URI→label resolution happens
  at dashboard-build time, so the *run* needs no `groups.yaml`.
- Noctua editor URL form is `…/editor/graph/gomodel:<id>` (verified in the
  `noctua` repo); base is `--noctua-base` (default `noctua.geneontology.org`).
- **Findings (2026-06-25):** 44,454 / 46,071 candidate models pass (96.5%); 39,625
  are 100 % standard; production 45,330 / development 637; MGI 26,949 / ZFIN 10,129
  / SGD 7,832. The dominant unit filter `invalid_gp_cc_relation` (7,288) is
  overwhelmingly gene-products joined to protein complexes via `part_of` instead
  of `located_in` — a real curation signal.
