# Ratchet rules

Each subdirectory is one rule in the standard-annotation ratchet. The runner
(`gocam_unwinder.ratchet`) discovers them and applies them **in directory-name
order** — the zero-padded `NN-` prefix makes lexical order equal execution order.
Adding a rule needs no code change to the runner; only `kind: python` rules need
a registered entrypoint.

## Layout

```
rules/
  00-model-modelstate/      rule.yaml                 (Phase A, python)
  01-edge-without-evidence/ rule.yaml  query.rq       (Phase B, sparql)
  ...
```

## `rule.yaml` fields

| field         | required | meaning |
|---------------|----------|---------|
| `id`          | yes | stable rule key (matches a `failed_checks` key where one exists); used in reports + output filenames |
| `phase`       | yes | `model` (Phase A, whole-model) or `unit` (Phase B, annotation unit) |
| `kind`        | yes | `python` (registered predicate) or `sparql` (sibling `query.rq`) |
| `stage`       | no  | cost tier; ordering hint within a phase (default 0) |
| `tsv`         | no  | `std_annot_rules.tsv` number, if any |
| `entrypoint`  | python only | callable name in the rule registry, signature `(ctx, item) -> Verdict` |
| `remove_when` | sparql only | `ask_true` (default) \| `ask_false` \| `select_nonempty` |
| `inputs`      | no  | declared deps, e.g. `[go, ro, gp_allowlist, true_gocam_set]` |
| `description` | no  | human-readable; surfaced in the roll-up report |
| `enabled`     | no  | set `false` to skip the rule without deleting it (default `true`) |

## Engine

Rules run **per model file, in-process with rdflib** — no Blazegraph, no `arq`
subprocess. SPARQL rules are plain SPARQL 1.1 and stay engine-portable (they run
unchanged under rdflib, arq, or Blazegraph), following the `noctua-models/sparql/*.rq`
template. Ontology-aware checks (GO aspect, RO closure) are `kind: python` and
reuse the existing `gocam_ttl` logic via the shared `ctx` (a `GoCamGraphBuilder`).

See `docs/plans/2026-06-25-standard-annotation-ratchet.md` for the full rule
inventory and design.
