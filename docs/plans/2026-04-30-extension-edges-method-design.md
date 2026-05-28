---
title: get_extension_edges() — design
date: 2026-04-30
status: draft
---

# `get_extension_edges()` — Design

## Goal

Add a method that, given a `StandardAnnotation`, returns the subset of its edges that are GO/GO-CAM **annotation extensions** — edges that add context beyond the base/primary gene-product → MF/BP/CC backbone of the annotation.

Reference example: the `GO:0120045` (stereocilium maintenance) BP annotation in `/Users/ebertdu/go/noctua-models/models/5966411600000001.ttl`. Its connected subgraph contains 5 edges; 2 form the BP backbone, and 3 are extensions (including a multi-hop chain `BP ─occurs_in→ CL ─part_of→ EMAPA`).

## Definition of "extension"

The **base/primary backbone** for each GO aspect is:

| Aspect | Backbone edges |
|---|---|
| MF | `MF ─enabled_by→ GP` (1 edge) |
| BP | `MF ─enabled_by→ GP` + `MF ─part_of→ BP` (2 edges) |
| CC | `GP ─located_in→ CC` *or* `GP ─is_active_in→ CC` (1 edge) |

A **`StandardAnnotation`** is a connected subgraph and may simultaneously hold backbones for multiple aspects (e.g., MF + BP + CC for the same gene product). The set of backbone edges across all aspects is the union of the matches above.

An **extension edge** is any edge in the annotation subgraph that is *not* in the backbone set. Multi-hop extensions (e.g., `CL ─part_of→ EMAPA` reached via the BP node) are included — they are simply edges that fail to match any backbone pattern.

## API

### Location

Add as a method on **`GoCamGraphBuilder`** (in `src/gocam_unwinder/gocam_ttl.py`). This matches the existing convention: `filter_out_non_std_annotations()` lives on the builder because it requires GO ontology aspect classification, and so does this method.

```python
def get_extension_edges(self, annot: StandardAnnotation) -> list[StandardAnnotationEdge]:
    """
    Return edges in `annot` that are annotation extensions —
    edges that fall outside the gene-product → MF/BP/CC backbone.
    """
```

### Supporting additions

`GoCamGraphBuilder` only has `uri_is_molecular_function()`. We add:

- `uri_is_biological_process(uri)` — wraps `self.go_aspector.is_biological_process(curie)`
- `uri_is_cellular_component(uri)` — wraps `self.go_aspector.is_cellular_component(curie)`

Both follow the same shape as the existing MF helper: contract the URI to a CURIE, return `False` if it's not a `GO:` term, otherwise delegate to `GoAspector`.

## Algorithm

For each edge in `annot.edges.values()`, classify as backbone if it matches any of:

1. **MF backbone**: `predicate == enabled_by (RO:0002333)` AND `uri_is_molecular_function(source_type)`
2. **BP backbone (the MF→BP edge)**: `predicate == part_of (BFO:0000050)` AND `uri_is_molecular_function(source_type)` AND `uri_is_biological_process(target_type)`
3. **CC backbone**: `predicate ∈ {located_in (RO:0001025), is_active_in (RO:0002432)}` AND `uri_is_cellular_component(target_type)`

Note: rule 1 covers the `MF ─enabled_by→ GP` edge that is also part of the BP backbone — no separate rule needed for it.

Return all edges whose `bnode_id` is *not* in the matched set, preserving the order of `annot.edges`.

### Relation URI resolution

Reuse the existing pattern: `URIRef(relations.lookup_label("enabled by"))` etc. Verified at design time that all four relation labels (`enabled by`, `part of`, `located in`, `is active in`) resolve via `ontobio.rdfgen.relations.lookup_label()`.

## Walk-through on the reference example

For the `GO:0120045` annotation in `5966411600000001.ttl`, the 5 edges classify as:

| bnode | source | predicate | target | classification |
|---|---|---|---|---|
| t1866543 | MF (GO:0003674) | enabled_by | GP (MGI:2139535) | **backbone** (rule 1) |
| t1866542 | MF (GO:0003674) | part_of | BP (GO:0120045) | **backbone** (rule 2) |
| t1866544 | BP (GO:0120045) | part_of | GO:0007605 | extension (source not MF) |
| t1866545 | BP (GO:0120045) | occurs_in | CL:0000202 | extension (predicate not enabled_by/located_in/is_active_in) |
| t1866546 | CL:0000202 | part_of | EMAPA:17597 | extension (source not MF) |

`get_extension_edges(annot)` returns `[t1866544, t1866545, t1866546]`.

## Behavior on edge cases

- **No backbone matched** (e.g., a non-standard annotation with broken structure): all edges are returned as extensions. The method is descriptive, not prescriptive — no exception raised.
- **Annotation with only an MF backbone** (single-edge `GP ←enabled_by─ MF` annotation): returns `[]`.
- **Multiple aspects in one subgraph**: each aspect's backbone is recognized independently; their union is the backbone set.

## Testing

Add a test in `tests/test_gocam_ttl.py`:

- `test_get_extension_edges()` — uses `5966411600000001.ttl`, locates the annotation containing GO:0120045, and asserts that `get_extension_edges()` returns exactly the 3 non-backbone edges (matched by predicate + source/target types).

The test requires the model file. Since the codebase tests use `resources/test/`, copy `5966411600000001.ttl` there for test self-containment.

## Out of scope

- Per-aspect grouping of extensions (Option C from brainstorming) — not requested for this first cut.
- Generating GPAD col-16 strings from extensions.
- Validation of extension well-formedness (e.g., warning on unexpected predicates).