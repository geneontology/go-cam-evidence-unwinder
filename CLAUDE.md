# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GO-CAM Evidence Unwinder is a Python tool that processes Gene Ontology Causal Activity Models (GO-CAM) in RDF/TTL format. The tool identifies "standard annotations" (annotation units with edges connected to multiple evidence nodes) and can optionally "unwind" them by duplicating the annotation for each evidence, ensuring all edges have only one evidence node.

## Development Commands

### Setup
```bash
# Create and activate virtual environment (recommended)
python3 -m venv env
source env/bin/activate  # On macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Install package in development mode
pip install -e .
```

### Testing
```bash
# Download required GO ontology for tests (only needed once)
make target/go_20250601.json

# Run all tests
make test
# OR
pytest

# Run specific test file
pytest tests/test_gocam_ttl.py

# Run specific test function
pytest tests/test_gocam_ttl.py::test_gocam_ttl -v
```

### Pipeline (Makefile)

The Makefile provides targets for running the full evidence-splitting pipeline:

```bash
# Run full pipeline (split models, create journals, export GPADs, diff)
make pipeline

# Download ontologies and metadata
make target/go_current.json
make target/ro_current.owl
make target/groups.yaml

# Run individual pipeline steps (outputs to target_YYYYMMDD/)
make target_$(date +%Y%m%d)/models_split           # Step 1: Run unwinder
make target_$(date +%Y%m%d)/models_split_orig      # Step 2: Copy originals
make target_$(date +%Y%m%d)/blazegraph-dev.jnl     # Step 3a: Dev journal (split)
make target_$(date +%Y%m%d)/blazegraph-prod.jnl    # Step 3b: Prod journal (orig)
make target_$(date +%Y%m%d)/gpad_export_dev.gpad   # Steps 4a+5a: Dev GPAD
make target_$(date +%Y%m%d)/gpad_export_prod.gpad  # Steps 4b+5b: Prod GPAD
make target_$(date +%Y%m%d)/gpad_diff.txt          # Step 6: GPAD diff

# Independent transformations of the source corpus (own output dirs, no conflict
# with the split pipeline; run on their own)
make non_std                                       # Remainders report (debug_non_standard.py)
make fix_nested_anatomy                            # De-nest anatomy extensions -> target_YYYYMMDD/models_nested_fixed/

# Clean up
make clean      # Remove today's target directory
make clean-all  # Remove all target_* directories
```

**Pipeline inputs** (in `target/`):
- `go_current.json` - GO ontology (JSON format)
- `ro_current.owl` - RO ontology (OWL format)
- `groups.yaml` - Groups metadata from go-site for resolving group URIs to labels

**Pipeline outputs** (in `target_YYYYMMDD/`):
- `models_split/` - Split GO-CAM models (one evidence per edge)
- `models_split_orig/` - Original models (for comparison)
- `blazegraph-dev.jnl` - Blazegraph journal with split models
- `blazegraph-prod.jnl` - Blazegraph journal with original models
- `gpad_export_dev.gpad` - GPAD export from split models
- `gpad_export_prod.gpad` - GPAD export from original models
- `gpad_diff.txt` - Diff between prod and dev GPADs
- `noctua_models_graph_counts_YYYYMMDD.tsv` - Statistics report
- `models_split_criteria_failures_YYYYMMDD.tsv` - Criteria failure report

**Statistics report columns** (`--report-file`):
- Model ID, Title - Model identifier and title
- Standard Annotations - Count of annotations passing all checks
- Non-Standard Annotations - Count of annotations failing one or more checks
- Multi-Evidence Annotations - Count of **all** annotations (standard **and** non-standard) with >1 evidence on any edge
- Mixed Annotation Type - "Yes" if model has both standard and non-standard annotations
- MF-causal->MF Edges - Count of causal edges between molecular functions (in non-standard)
- Edges w/o Evidence - Count of edges (OWL axioms with GO-CAM relations) that have no `lego:evidence` triple
- Model State - Model state from `http://geneontology.org/lego/modelstate` (e.g., "production", "development")
- Groups - Pipe-separated list of contributing groups from `http://purl.org/pav/providedBy` (resolved to labels if `--groups-yaml` provided, e.g., "MGI", "ZFIN", "SGD")
- Multi-Evidence GO Terms - Pipe-separated list of resolved GO term labels from multi-evidence annotations. **Collected only from *standard* (splittable) multi-evidence annotations** — so this is blank for a model whose multi-evidence annotations are all non-standard, even when the "Multi-Evidence Annotations" count (which includes non-standard) is non-zero. Excludes URIs and CURIEs that couldn't be resolved to labels.

**Note:** Models with `modelstate == "delete"` are automatically skipped during processing.

### Running the Tool

The main script is `src/gocam_unwinder/gocam_ttl.py` and can be run directly:

```bash
# Analyze a single model
python src/gocam_unwinder/gocam_ttl.py \
  -m path/to/model.ttl \
  -o target/go_20250601.json

# Analyze a folder of models
python src/gocam_unwinder/gocam_ttl.py \
  -d path/to/models/folder \
  -o target/go_20250601.json

# Split evidence (unwind annotations) and save to output directory
python src/gocam_unwinder/gocam_ttl.py \
  -m path/to/model.ttl \
  -o target/go_20250601.json \
  --split-evidence \
  --output-dir output/

# Analyze with RO ontology and generate criteria failure report
python src/gocam_unwinder/gocam_ttl.py \
  -m path/to/model.ttl \
  -o target/go_20250601.json \
  -r target/ro_current.owl \
  --criteria-fail-report failures.tsv

# Skip files starting with specific prefixes (e.g., SYNGO and Reactome models)
python src/gocam_unwinder/gocam_ttl.py \
  -d path/to/models/folder \
  -o target/go_20250601.json \
  --skip-prefix SYNGO \
  --skip-prefix R-HSA

# Full pipeline with groups.yaml for resolving group URIs to labels
python src/gocam_unwinder/gocam_ttl.py \
  -d path/to/models/folder \
  -o target/go_current.json \
  -r target/ro_current.owl \
  --groups-yaml target/groups.yaml \
  --split-evidence \
  --output-dir output/ \
  --report-file report.tsv \
  --criteria-fail-report failures.tsv \
  --date-change-report date_changes.tsv

# Fix nested anatomy extensions (de-nest anatomy targets onto the primary term)
# Writes only the models that were changed to --output-dir; --nested-fix-report is optional.
# Independent of --split-evidence — combining the two in one invocation is a hard error
# (the CLI exits with a message). Run them as separate passes / Makefile targets.
python src/gocam_unwinder/gocam_ttl.py \
  -d path/to/models/folder \
  -o target/go_current.json \
  -r target/ro_current.owl \
  --fix-nested-anatomy \
  --output-dir output/ \
  --nested-fix-report nested_fixes.tsv
```

**`--fix-nested-anatomy` mode:** For each annotation, finds extension edges where **both** endpoints are anatomical structures (anatomy ontologies or GO cellular components) and whose source is not the lead-aspect primary GO term (i.e. genuinely *nested*, such as `BP ─occurs_in→ CL ─part_of→ EMAPA`), and rewrites each so its source becomes the annotation's **primary individual** and its relation becomes `occurs_in` (MF/BP-led) or stays as-is (CC-led, typically `part_of`). Each qualifying edge is de-nested independently; intermediate direct extensions (e.g. `BP ─occurs_in→ CL`) are kept. Only models with at least one rewrite are written. The fix is idempotent (a second run is a no-op). `--nested-fix-report` columns: Model ID, Title, Lead Aspect, Primary Term, Old Source, Old Relation, Target, New Relation. This mode is an **independent** transformation of the source models: it reads the source corpus directly (not the split output) and is mutually exclusive with `--split-evidence` in a single invocation (combining them is a hard error). The Makefile exposes it as the standalone `fix_nested_anatomy` target, which writes to its own `$(TARGET_DIR)/models_nested_fixed` directory — so running it alongside the split pipeline never conflicts.

## Planning

When writing implementation plans, use the template at `docs/plans/PLAN-TEMPLATE.md`. Save plans to `docs/plans/YYYY-MM-DD-<feature-name>.md`.

## Architecture

### Helper Functions

**`get_relation_descendants(ro_graph, root_relation_uri)`** (`src/gocam_unwinder/gocam_ttl.py:26-59`)
- Extracts all descendants of a given relation from an already-parsed RO ontology graph
- Uses BFS traversal of `rdfs:subPropertyOf` to find child relations
- Takes an `rdflib.Graph` (not a file path) to avoid redundant parsing
- Returns a set of URIs (as strings) including the root and all descendants

### Core Components

**GoCamGraph** (`src/gocam_unwinder/gocam_ttl.py:113-542`)
- Main data structure representing a GO-CAM model
- Wraps an rdflib.Graph and extracts structured annotation information
- Key properties:
  - `model_id`: Model URI (e.g., "http://model.geneontology.org/MGI_MGI_1100089")
  - `title`: Model title
  - `modelstate`: Model state from `http://geneontology.org/lego/modelstate` (e.g., "production", "development", "delete")
  - `groups`: List of contributing groups from `http://purl.org/pav/providedBy` (resolved to labels if lookup available)
- Key methods:
  - `get_model_id()`, `get_title()`, `get_modelstate()`, `get_groups()`: Extract model-level metadata
  - `extract_standard_annotations()`: Identifies and groups connected edges into StandardAnnotation objects
  - `get_evidence_metadata()`: Extracts metadata signature from evidence individuals for grouping
  - `group_evidence_by_metadata()`: Groups evidence across edges by identical metadata
  - `split_evidence_and_write_ttl()`: Splits multi-evidence annotations by evidence groups
  - `rewrite_edge_source_and_relation(bnode_id, old_source, old_property, target, new_source, new_property)`: Low-level mutation that re-points an edge's source individual and relation, updating **both** the assertion triple and its reified `owl:Axiom` blank node (annotatedTarget, evidence, dates, contributors preserved; the axiom bnode is updated only if it exists). Used by the `--fix-nested-anatomy` mode to de-nest anatomy extensions onto the primary individual

**StandardAnnotation** (`src/gocam_unwinder/gocam_ttl.py:43-68`)
- Represents a connected component of edges forming a single annotation unit
- Contains a dictionary of edges (keyed by bnode ID) and a set of individuals
- Edges are connected via their source/target URIs to form annotation graphs

**StandardAnnotationEdge** (`src/gocam_unwinder/gocam_ttl.py:22-41`)
- Represents a single RDF axiom (blank node) with:
  - Source and target URIs (individuals)
  - Property URI (relation)
  - List of evidence URIs
  - Source and target types (GO terms, etc.)

**GoCamGraphBuilder** (`src/gocam_unwinder/gocam_ttl.py:545-680`)
- Factory class that parses GO-CAM models with GO ontology context
- Constructor: `GoCamGraphBuilder(ontology_path, ro_ontology_path=None, groups_yaml_path=None)`
- Stores parsed ontologies and lookups for reuse:
  - `self.ontology`: GO ontology (via ontobio) for term lookups and MF classification
  - `self.ro_ontology`: RO ontology as rdflib.Graph (if provided) for causal relation hierarchy and labels
  - `self.groups_lookup`: Dict mapping group URIs to labels (from groups.yaml, if provided)
- Class constants:
  - `GP_NAMESPACE_KEYS`: Allowlist of gene-product namespace keys (mgi, sgd, uniprot, etc.)
  - `CAUSALLY_UPSTREAM_OF_OR_WITHIN`: Root URI for the causal relation family (RO:0002418)
  - `ACTS_UPSTREAM_OF_OR_WITHIN`: Root URI for the acts-upstream family (RO:0002264), used for checks #4 and #5
  - `ROOT_GO_TERMS`: Set of the three GO aspect root PURLs (MF GO:0003674, BP GO:0008150, CC GO:0005575)
  - `ANATOMY_NAMESPACE_KEYS`: Set of anatomy-ontology namespace keys (cl, uberon, emapa, wbbt, fbbt, zfa, ma, po)
  - `COMPLEX_CC_ROOT` / `ANATOMICAL_CC_ROOTS`: CURIEs of the top CC is_a branches used by `_cc_branch` for the #3 GP→CC relation split (`GO:0032991`; `GO:0110165`, `GO:0044423`)
- Cached relation URIs (set in `__init__`): `rel_enabled_by`, `rel_contributes_to`, `rel_has_input`, `rel_has_output`, `rel_part_of`, `rel_located_in`, `rel_is_active_in`, `rel_occurs_in`; also `acts_upstream_relations` (set of RO:0002264 descendants, empty without RO) and `mf_bp_valid_relations` (combined set of valid MF→BP relations for the #11 cardinality check)
- Key methods:
  - `parse_ttl()`: Parses a TTL file, extracts model metadata (including modelstate and groups with label resolution), and applies filtering
  - `uri_is_molecular_function()`: Checks if a URI is a molecular function using GoAspector
  - `uri_is_biological_process()`: Checks if a URI is a biological process using GoAspector
  - `uri_is_cellular_component()`: Checks if a URI is a cellular component using GoAspector
  - `uri_is_causal_relation()`: Checks if a URI is a causal relation (descendant of RO:0002418)
  - `_gene_product_namespace_key()`: Maps an entity type URI to a gene-product namespace key (e.g. `http://identifiers.org/mgi/...` → `mgi`, the compact form `http://identifiers.org/PomBase:...` → `pombase`, ComplexPortal host URLs → `complexportal`, `obo/PR_...` → `pr`), or `None`. Used with the `GP_NAMESPACE_KEYS` class constant (allowlist) to identify gene products in the `invalid_gp_mf_relation` check
  - `_go_aspect()`: Returns `"MF"` | `"BP"` | `"CC"` | `None` for an individual's type node; MF resolution is `owl:complementOf`-aware via `_resolve_mf_type()`
  - `_is_root_go_term()`: Returns `True` if the type node is one of the three GO aspect root terms
  - `_is_anatomical_structure()`: Returns `True` if the type is a GO CC or has a namespace key in `ANATOMY_NAMESPACE_KEYS`; used by the #12 cardinality check
  - `_cc_branch()`: Returns `"complex"` | `"anatomical"` | `None` — buckets a GO cellular component into its top is_a branch (protein-containing complex `GO:0032991` vs cellular anatomical structure `GO:0110165` / virion component `GO:0044423`). Drives the #3 `invalid_gp_cc_relation` branch split via the rule table's `tgt_cc_branch` constraint
  - `_category()`: Returns the disjoint endpoint category (`"MF"` | `"BP"` | `"CC"` | `"GP"` | `"ANATOMY"` | `None`) for use in the relation-validity rule table
  - `_build_relation_rules()`: Builds the declarative relation-validity rule table (list of dicts) for checks #3/#4/#5/#6/#10; RO-dependent rules (#4, #5) are appended only when an RO ontology is loaded
  - `_matches_relation_rule()`: Tests whether an edge matches a rule's src/tgt categories and root/nonroot constraints
  - `term_label()`: Looks up human-readable labels for GO/RO/BFO terms from stored ontologies
  - `filter_out_non_std_annotations()`: Applies all filtering checks and tracks failures
  - `print_non_standard_annotation_failed_checks()`: Outputs TSV report of failed checks with term labels
  - `_backbone_role()`: Returns the gene-product → MF/BP/CC backbone role of an edge (`"MF"` | `"BP"` | `"CC"` | `None`). MF = `enabled_by` with an MF source; BP = a relation in `self.mf_bp_valid_relations` (`part_of` ∪ the `acts_upstream_of_or_within` RO:0002264 family ∪ the `causally_upstream_of_or_within` RO:0002418 family) from a **root** MF (GO:0003674) to a BP; CC = `located_in`/`is_active_in` to a CC. The root-MF gate on the BP rule means a *specific* (non-root) MF `─part_of→` BP is an extension, not a BP backbone — so such annotations stay MF-led (the BP is contextual). Only the canonical "BP-only" pattern (unknown/root MF `part_of` **or** causally upstream of a BP) counts as a BP backbone. The relation set is shared with the #5 `invalid_mf_bp_relation` check so they cannot diverge; without an RO ontology it falls back to `part_of` only (the upstream/causal families require RO). Shared by `get_extension_edges()` and `get_primary_go_terms()`
  - `get_extension_edges()`: Returns the edges of a `StandardAnnotation` that are annotation extensions (i.e., `_backbone_role()` is `None`)
  - `get_primary_go_terms()`: Returns a dict mapping aspect (`"MF"`/`"BP"`/`"CC"`) to the list of primary GO term URIs identified via `_backbone_role()` (MF→source_type, BP/CC→target_type). The remainders-report "lead aspect" is picked from these keys by priority BP > CC > MF (module-level `pick_lead_aspect`), so the root-MF BP gate keeps specific-MF annotations MF-led
  - `get_primary_individuals()`: Mirror of `get_primary_go_terms()` that returns the primary *individual* URIs per aspect (MF→source_uri, BP/CC→target_uri) via the same `_backbone_role()` dispatch. The two methods are byte-for-byte parallel traversals, so for a given aspect their lists are index-aligned. Used by `plan_nested_anatomy_fixes()` to find which individual a nested edge re-points onto
  - `plan_nested_anatomy_fixes(gocam)`: Builds the rewrite plan for the `--fix-nested-anatomy` mode. For every annotation (standard and non-standard), finds nested extension edges (`find_nested_extensions()`) whose source and target types are **both** anatomical structures (`_is_anatomical_structure()`), and emits one instruction dict per edge re-pointing it onto the lead aspect's primary individual. New relation is `occurs_in` (BFO:0000066) when the lead aspect is MF or BP, or the edge's existing relation (typically `part_of`) when CC. Annotations whose lead aspect has 0 or >1 primary individual are skipped with a warning. Instruction-dict keys: `model_id, title, lead_aspect, primary_term, bnode_id, old_source_uri, old_property_uri, target_uri, new_source_uri, new_property_uri, old_source_type, target_type`. Consumed by `GoCamGraph.rewrite_edge_source_and_relation()` in `main()`

**Module-level helpers** (`src/gocam_unwinder/gocam_ttl.py`) — `ASPECT_PRIORITY = ("BP", "CC", "MF")`, `pick_lead_aspect(primary_terms)` (first aspect in `ASPECT_PRIORITY` present in the dict, or `None`), and `find_nested_extensions(annot, builder)` (returns `(lead_aspect, nested_edges)`, where `nested_edges` are extension edges whose `source_type` is not in the lead aspect's primary URI set). These were promoted from `debug_non_standard.py` (which now imports them) so both the `--fix-nested-anatomy` fixer and the remainders-report triage script share one definition.

**Remainders report** (`debug_non_standard.py --tsv-output`) — a triage TSV with one row per bucketed edge. Columns: `Model ID, Title, Bucket, Source, Predicate, Target, Fixable, ECO Codes, Groups`. `Bucket` is the edge's triage class (`nested_mf_extensions` / `nested_bp_extensions` / `nested_cc_extensions`, `multi_mf_same_bp`, `multi_bp_same_mf`, `extension_eco_differs`). `Fixable` is `Yes`/`No` indicating whether `--fix-nested-anatomy` would actually rewrite that edge. It is sourced from `builder.plan_nested_anatomy_fixes(gocam, warn=False)` bnode-id membership (the set is computed **once per model**, then every `tsv_rows.append` site sets `"Yes" if <edge bnode> in fixable_bnodes else "No"`), so it cannot diverge from the fixer and captures the planner's full qualification — both endpoints anatomical **and** the annotation's lead aspect having exactly one primary individual. Consequently a both-anatomical nested edge can still read `No` when its annotation has an ambiguous (0 or >1) attach point (e.g. `57c82fad00000252`'s `nucleus ─part_of→ WBbt`). `warn=False` suppresses the planner's per-annotation skip warnings during report generation (matching `compute_model_stats`). Tested by `test_remainders_report_fixable_column`, which invokes `debug_non_standard.main()` end-to-end against `resources/test/` (reusing the session `builder` via monkeypatch) and asserts the column on three ground-truth rows.

**`load_groups_lookup(groups_yaml_path)`** (`src/gocam_unwinder/gocam_ttl.py:65-88`)
- Loads groups.yaml from go-site and creates a URI → label lookup dictionary
- The groups.yaml file contains entries like: `{id: "http://informatics.jax.org", label: "MGI"}`
- Returns dict mapping group URIs to their labels (e.g., `{"http://informatics.jax.org": "MGI"}`)

### Key Algorithm: Standard Annotation Extraction

The `extract_standard_annotations()` method (lines 334-393) implements a union-find-like algorithm:

1. Calls `extract_edges()` which discovers all OWL axiom edges — both those with evidence and those without (Issue #14). Edges without evidence are filtered to only include OBO namespace relations (`http://purl.obolibrary.org/obo/`), excluding non-GO-CAM axioms like `rdf:type`, `rdfs:label`, and `oboInOwl#id`. Included edges get empty `evidence_uris` lists but still participate in subgraph assembly.
2. Tracks which StandardAnnotation each individual URI belongs to via `individual_to_annotation` dict
3. When an edge connects two individuals:
   - If neither is in an annotation: create new annotation
   - If one is in an annotation: add to that annotation
   - If both are in different annotations: merge annotations
4. Uses `find_related_edges()` to recursively discover connected edges via GO-CAM relations

The `find_related_edges()` method (lines 396-425) looks up already-extracted edges by bnode ID rather than creating new ones, which preserves the `evidence_uris` that were populated during `extract_edges()`.

This ensures that all edges sharing individuals or transitively connected through the graph are grouped into the same StandardAnnotation with their evidence data intact.

### Standard Annotation Filtering

The `filter_out_non_std_annotations()` method applies filtering checks to every annotation. All checks are run on each annotation (no short-circuiting), and results are tracked per-edge in the `StandardAnnotation.failed_checks` dict.

#### Failed Checks Tracking

Each `StandardAnnotation` has a `failed_checks` attribute:
- Dict mapping check name to set of edge bnode IDs that triggered the failure
- Example: `{"mf_causal_mf": {"bnode123", "bnode456"}, "inconsistent_evidence": {"bnode123", "bnode789"}}`
- Empty dict means the annotation passed all checks (is a standard annotation)

#### Filter Checks

1. **Evidence Consistency Check** (`inconsistent_evidence`):
   - For multi-edge annotations, verifies that all edges have evidence with matching metadata
   - Uses `group_evidence_by_metadata()` to group evidence across edges
   - Ensures each evidence group has exactly one evidence from each edge
   - Single-edge annotations always pass this check
   - When failed, all edges in the annotation are recorded
   - **Passing example**: 2 edges with evidence [A, B] and [C, D], where metadata(A) == metadata(C) and metadata(B) == metadata(D) → 2 evidence groups, each with evidence from both edges
   - **Failing example**: 2 edges with evidence [A, B] and [C] → evidence group for A has no match from edge 2, inconsistent

2. **Multiple MF→BP edges** (`multiple_mf_bp`):
   - Filters out annotations containing more than one MF→BP edge where the relation is in `part_of` (BFO:0000050) ∪ the `acts_upstream_of_or_within` (RO:0002264) family ∪ the `causally_upstream_of_or_within` (RO:0002418) family
   - When failed, only the qualifying MF→BP edges are recorded (not all edges)
   - This prevents complex pathway models from being classified as standard annotations
   - Previously named `multiple_mf_part_of`; broadened to cover all standard MF→BP relation types

3. **Causal relation edges between two molecular function nodes** (`mf_causal_mf`, requires RO ontology):
   - If an RO ontology file is provided, filters out annotations containing causal relation edges (descendants of RO:0002418 "causally upstream of or within") where both source and target are molecular functions
   - When failed, only the MF-causal->MF edges are recorded
   - Causal relations include: directly positively regulates (RO:0002629), directly negatively regulates (RO:0002630), etc.
   - This prevents MF-to-MF causal chains from being classified as standard annotations

4. **Edges without evidence** (`edge_without_evidence`):
   - Flags any edge in the annotation that has no `lego:evidence` triple
   - When failed, only the no-evidence edges are recorded (not all edges)
   - This prevents annotations with incomplete provenance from being classified as standard

5. **Invalid gene-product ↔ MF relation** (`invalid_gp_mf_relation`):
   - Validates the relation on edges connecting a molecular function (MF) to a gene product (GP). A GP is identified by a namespace **allowlist** (`GP_NAMESPACE_KEYS`) via `_gene_product_namespace_key()`, sourced from the `mod_id_space` values in go-site `metadata/goex.yaml` plus ComplexPortal and PR (HGNC intentionally excluded). Anatomy/ontology targets (EMAPA, WBbt, CL, UBERON, ...) are **not** gene products and are ignored by this check (Issue #22 — the prior `{GO, RO, BFO}` blocklist misclassified them as GPs).
   - Valid backbone relations: `enabled_by` (MF as source) or `contributes_to` / RO:0002326 (MF as target). NOT-qualified MFs are recognized via `owl:complementOf` class expressions (`_resolve_mf_type()`).
   - `has_input` (RO:0002233) and `has_output` (RO:0002234) are allowed MF→GP extension relations, but **only in the MF-as-source direction** — they are accepted (neither flagged nor counted as a backbone). In the GP→MF direction only `contributes_to` is valid.
   - An annotation passes if it has at least one valid backbone edge. If it has no valid backbone, every invalid gene-product↔MF edge is recorded.
   - MF↔MF edges are out of scope here (handled by `mf_causal_mf`).

6. **Invalid GP→CC relation** (`invalid_gp_cc_relation`):
   - A gene product (GP) connected to a non-root GO cellular component (CC) must use a relation that depends on the CC's subhierarchy (`_cc_branch`):
     - **protein-containing complex** (`GO:0032991` is_a subtree) → must be `part_of` (BFO:0000050)
     - **cellular anatomical structure** (`GO:0110165`) / **virion component** (`GO:0044423`) is_a subtree → must be `located_in` (RO:0001025) OR `is_active_in` (RO:0002432)
   - The branch is determined by `_cc_branch()` via the GO is_a closure (the same closure `GoAspector` uses for aspect classification), exposed to the rule table as the `tgt_cc_branch` constraint (`"complex"` / `"anatomical"`). Implemented as two rows in `_build_relation_rules` that share the `invalid_gp_cc_relation` key.
   - When failed, only the offending edge is recorded

7. **Invalid GP→BP relation** (`invalid_gp_bp_relation`, requires RO ontology):
   - A gene product connected to any BP must use a relation in the `acts_upstream_of_or_within` (RO:0002264) family (the 11-member descendant set)
   - When failed, only the offending edge is recorded
   - Omitted when no RO ontology is loaded

8. **Invalid root-MF→BP relation** (`invalid_mf_bp_relation`, requires RO ontology):
   - A root MF individual connected to a non-root BP must use `part_of` OR a relation in the `acts_upstream_of_or_within` (RO:0002264) family OR the `causally_upstream_of_or_within` (RO:0002418) family
   - The RO:0002418 family is included because the canonical MOD "BP-only annotation" (unknown/root MF causally upstream of a BP) uses it and must stay standard/splittable
   - When failed, only the offending edge is recorded
   - Omitted when no RO ontology is loaded

9. **Invalid root-MF→CC relation** (`invalid_mf_cc_relation`):
   - A root MF individual connected to a non-root GO CC must use `is_active_in` (RO:0002432)
   - When failed, only the offending edge is recorded

10. **Invalid BP→CC/anatomy relation** (`invalid_bp_cc_relation`):
    - A BP individual connected to a GO CC or anatomy-ontology term (CL, UBERON, EMAPA, etc.) must use `occurs_in` (BFO:0000066)
    - When failed, only the offending edge is recorded

11. **Multiple MF→anatomy edges** (`multiple_mf_anatomy`):
    - Flags annotations where a single MF individual has more than one outgoing edge to an anatomical structure (GO CC or anatomy-ontology term such as CL, UBERON, EMAPA, WBbt, etc.)
    - When failed, all the qualifying MF→anatomy edges are recorded

12. **Enabler is not a gene product** (`enabler_not_gp`):
    - The target of an `enabled_by` edge (MF→enabler) must be a gene product (in `GP_NAMESPACE_KEYS`). Flags annotations where the enabler is a non-GP entity such as a GO term or ChEBI chemical.
    - When failed, the offending `enabled_by` edge is recorded

13. **No gene product in subgraph** (`no_gp_at_all`):
    - Flags annotations whose subgraph contains no gene product — no individual whose type resolves to a `GP_NAMESPACE_KEYS` namespace via `_gene_product_namespace_key()` (scanning every edge's `source_type`/`target_type`). A standard annotation links a gene product to GO; a subgraph that is, e.g., a bare anatomy or chemical placement has no GP and is non-standard. Complements `enabler_not_gp`/`invalid_gp_mf_relation`, which only fire on edges that already touch a GP-shaped or `enabled_by` endpoint.
    - When failed, **all** edges of the annotation are recorded (annotation-level failure, like `inconsistent_evidence`).

**Backbone-only gate:** The relation-validity checks 6–10 above (`invalid_gp_cc_relation`, `invalid_gp_bp_relation`, `invalid_mf_bp_relation`, `invalid_mf_cc_relation`, `invalid_bp_cc_relation`) validate the annotation **backbone** only. An edge is validated only if its relation is in `GoCamGraphBuilder.backbone_relations` — the recognized backbone/placement relations (`located_in`, `is_active_in`, `occurs_in`, `part_of`, plus the `acts_upstream_of_or_within` RO:0002264 and `causally_upstream_of_or_within` RO:0002418 families). Edges using any other relation are annotation **extensions** (e.g. `BP ─results_in_development_of→ anatomy`) and are left informational, not flagged. A *misused* placement relation (e.g. `located_in` on a `BP→CC` edge) is in `backbone_relations` and is still flagged. `occurs_in` is a recognized placement relation, so a `root-MF ─occurs_in→ CC` edge is validated by `invalid_mf_cc_relation` (the MF→CC placement must be `is_active_in`).

**Note:** TSV rules #7, #8, #9 from `std_annot_rules.tsv` are informational only — they produce no `failed_checks` entry and do not affect standard/non-standard classification.

#### Reporting

The `print_non_standard_annotation_failed_checks()` method outputs details about which edges failed which checks for non-standard annotations.

### Evidence Splitting Logic

The evidence splitting process now groups evidence by metadata to handle multi-edge annotations correctly (Issue #6):

#### Evidence Metadata Grouping

The `get_evidence_metadata()` method (lines 102-127) extracts a metadata signature from each evidence individual:
- Collects values for predicates in `PREDICATES_TO_COPY`, **excluding date predicates** (`dc:date`, `dcterms:created`, `dcterms:dateAccepted`) so that evidence differing only in dates can be grouped together (Issue #15)
- Also includes `evidence-with` and `source` predicates
- Returns a hashable tuple that uniquely identifies evidence with identical metadata

The `group_evidence_by_metadata()` method (lines 129-178) groups evidence across edges in a standard annotation:
1. Collects metadata signatures for all evidence individuals in the annotation
2. Groups evidence URIs by their metadata signature
3. For each metadata group, identifies which evidence from each edge belongs to that group
4. Returns a mapping: `group_index -> {edge_bnode_id -> [evidence_uris]}`

This ensures that evidence representing the same "evidence event" across different edges stay together.

#### Splitting Algorithm

The `split_evidence_and_write_ttl()` method (lines 180-255) implements the actual splitting:

1. For each standard annotation, get evidence groups via `group_evidence_by_metadata()`
2. Update `dc:date` to the most recent value across all evidence in each group, but only when dates actually differ (Issue #15). Updates both evidence individuals and BNode axioms. Prints a report line for each update: `Date updated to {date} for {model_id} ({title})`. Uses `get_most_recent_date()` and `update_evidence_date()` helper methods. Collects per-edge date change records (with source_type, property_uri, target_type URIs) for reporting
3. For each evidence group:
   - Group 0 (first): keeps original blank nodes and individuals, removes extra evidence
   - Groups 1+ (subsequent): creates new blank nodes with suffix "-2", "-3", etc.
   - Creates new individual URIs with same suffix for all edges in the group
   - Reuses individual URIs across edges in the same group (via `individual_mapping`)
   - Clones metadata (types, contributors, dates) to new nodes using `PREDICATES_TO_COPY`
   - Adds only the evidence belonging to this group

**Example:** If an annotation has 2 edges with evidence [A, B] and [C, D] respectively, where metadata(A) == metadata(C) and metadata(B) == metadata(D):
- Group 0: Edge 1 with evidence A + Edge 2 with evidence C (original nodes)
- Group 1: Edge 1 with evidence B + Edge 2 with evidence D (new nodes with "-2" suffix)

The method returns a list of date change record dicts (with keys: model_id, title, original_date, new_date, source_type, property_uri, target_type) for edges where `dc:date` was updated. In `main()`, these records are resolved to human-readable labels via `term_label()` and written to a TSV file if `--date-change-report` is specified. Report columns: Model ID, Title, Original Date, New Date, Source, Predicate, Target.

This maintains provenance while ensuring one-to-one edge-to-evidence relationships and correct evidence grouping across edges.

### Testing

Tests use real GO-CAM model examples in `resources/test/`:
- **MGI_MGI_1100089.ttl**: Mouse Tnfsf11 model with multi-edge multi-evidence annotations
  - **Positive test case**: Has annotations with consistent evidence across edges (passes new filter)
  - Contains at least one 2-edge annotation where evidence metadata matches across edges
- **61452e3d00000323.ttl**: Saccharomyces MAL loci model
  - **Negative test case**: Has annotations with inconsistent evidence across edges (filtered out)
  - Multi-edge annotations do not have matching evidence metadata
- **R-HSA-9937080.ttl**: Reactome pathway (0 standard, 1 non-standard)
- **SYNGO_5371.ttl**: SynGO model. Its annotations pass the evidence-consistency check, but it has a `root-MF ─occurs_in→ CC` (presynapse) edge, which the `invalid_mf_cc_relation` check (#6) flags as non-standard — the MF→CC placement relation must be `is_active_in`, not `occurs_in` (so the model is now classified non-standard)
- **5b318d0900000481.ttl**: Human kinase activation template model with MF-to-MF causal edges
  - Contains GO:0004672 (protein kinase activity) → RO:0002629 (directly positively regulates) → GO:0003700 (DNA-binding transcription factor activity)
  - Used to test MF-causal->MF filtering when RO ontology is provided
- **66c7d41500000016.ttl**: Human NRXN1B-CBLN1-GRID2 trans-synaptic model with 1 evidence-less causal edge
  - Contains RO:0002407 (indirectly positively regulates) edge between two MF nodes with no evidence
  - Used to test that edges without evidence are included in annotation subgraph assembly (Issue #14)
- **57c82fad00000252.ttl**: C. elegans SAB neuron synaptogenesis model with 3 evidence-less OBO relation edges
  - Regression test for OBO namespace filter in no-evidence edge extraction (Issue #14)
  - Without the filter, non-GO-CAM axiom edges (oboInOwl#id, rdfs:label) cause incorrect subgraph splitting
- **67369e7600005491.ttl**: Mouse Hnf4aos model with a subgraph (causally_upstream_of_or_within -> GO:0006954 inflammatory response) where all 3 edges have no evidence
  - Used to test `edge_without_evidence` filter on an annotation with zero total evidence
- **MGI_MGI_1101770.ttl**: Mouse Ring1 model with date-differing evidence across edges (Issue #15)
  - enabled_by edge has evidence dated 2006-08-09, causally_upstream_of edge has evidence dated 2023-02-13
  - Evidence is otherwise identical (same ECO, PMID, contributor) — only dates and dcterms:created presence differ
  - Used to test date-tolerant evidence grouping and date update during splitting
- **5966411600000001.ttl**: Mouse stereocilium maintenance model with the `GO:0120045` BP annotation as a 5-edge subgraph
  - 2 backbone edges (`MF─enabled_by→GP`, `root-MF─part_of→BP` — the MF is the root term GO:0003674, so this is a true BP backbone) plus 3 extension edges including the chain `BP─occurs_in→CL─part_of→EMAPA`
  - Used to test `get_extension_edges()` (backbone vs. extension classification across all three GO aspects), `get_primary_individuals()`, `rewrite_edge_source_and_relation()`, and the BP-led case of `plan_nested_anatomy_fixes()` — the nested `CL:0000202─part_of→EMAPA:17597` edge de-nests onto the primary BP individual (`...0004`) with `occurs_in`
- **mf_occurs_in_anatomy_example.ttl**: Synthetic single-edge annotation `MF ─occurs_in→ WBbt:0006796` (anatomy) (Issue #22)
  - Regression fixture for `invalid_gp_mf_relation`: under the old `{GO, RO, BFO}` blocklist the anatomy target was misclassified as a GP and falsely flagged; the `GP_NAMESPACE_KEYS` allowlist now correctly ignores it
- **mf_to_gp_has_input_output_example.ttl**: Synthetic model with two single-edge annotations, `MF ─has_input→ GP` and `MF ─has_output→ GP` (MGI gene products) (Issue #22)
  - Used to test that `has_input`/`has_output` are allowed MF→GP extension relations (not flagged by `invalid_gp_mf_relation`)
- **bp_cc_relation_example.ttl**: Synthetic model with a passing `BP─occurs_in→CC` and a failing `BP─located_in→CL` edge
  - Used to test `invalid_bp_cc_relation` check (#10): only the wrong-relation edge is flagged
- **gp_cc_relation_example.ttl**: Synthetic model with five single-edge GP→CC annotations covering both #3 branches: `GP─located_in→anatomical-CC` (pass), `GP─part_of→anatomical-CC` (fail), `GP─part_of→complex` (pass), `GP─located_in→complex` (fail), `GP─is_active_in→anatomical-CC` (pass)
  - Used to test `invalid_gp_cc_relation` check (#3): a GP must be `part_of` a protein-containing complex (`GO:0032991`) but `located_in`/`is_active_in` a cellular anatomical structure (`GO:0110165`)/virion component (`GO:0044423`)
- **gp_bp_relation_example.ttl**: Synthetic model with a passing `GP─acts_upstream_of_or_within→BP` and a failing `GP─part_of→BP` edge
  - Used to test `invalid_gp_bp_relation` check (#4, RO-dependent): only the wrong-relation edge is flagged
- **mf_bp_relation_example.ttl**: Synthetic model with a passing `root-MF─causally_upstream_of_or_within→BP` and a failing `root-MF─located_in→BP` edge
  - Used to test `invalid_mf_bp_relation` check (#5, RO-dependent): only the wrong-relation edge is flagged
  - Also used by `test_causal_root_mf_to_bp_is_backbone` to confirm the causal edge is recognized as a BP backbone by `_backbone_role()`
- **mf_cc_relation_example.ttl**: Synthetic model with a passing `root-MF─is_active_in→CC` and a failing `root-MF─located_in→CC` edge
  - Used to test `invalid_mf_cc_relation` check (#6): only the wrong-relation edge is flagged
- **multi_mf_bp_example.ttl**: Synthetic annotation where one MF connects to two BPs via `part_of` and `acts_upstream_of_or_within`
  - Used to test `multiple_mf_bp` cardinality check (#11): both MF→BP edges are flagged
- **multi_mf_anatomy_example.ttl**: Synthetic annotation where one MF connects to a GO CC and a CL anatomy term
  - Used to test `multiple_mf_anatomy` cardinality check (#12): both MF→anatomy edges are flagged
- **enabler_not_gp_example.ttl**: Synthetic model with a passing `MF─enabled_by→MGI-GP` and a failing `MF─enabled_by→CHEBI` edge
  - Used to test `enabler_not_gp` check (#13): only the ChEBI-enabled edge is flagged
- **MGI_MGI_2182965.ttl**: Mouse Tifa model with a single annotation whose backbone is a *specific* MF (`GO:0005515` protein binding) `─enabled_by→` the Tifa gene product, plus a `specific-MF ─part_of→ BP` (`GO:0043123`) edge
  - Used to test the root-MF gate on the BP backbone (`_backbone_role`): the specific MF keeps the annotation MF-led (lead aspect MF, not BP), so it buckets as `nested_mf_extensions` rather than `nested_bp_extensions` in the remainders report
- **mf_nested_anatomy_example.ttl**: Synthetic MF-led model — `MF(GO:0004672)─enabled_by→GP`, `MF─occurs_in→CL:0000202` (direct extension), and the nested `CL:0000202─part_of→EMAPA:17597`
  - Used to test the MF-led branch of `plan_nested_anatomy_fixes()`: the nested edge re-points onto the primary MF individual with `occurs_in`
- **cc_nested_anatomy_example.ttl**: Synthetic CC-led model — `GP─located_in→CC(GO:0005634)`, `CC─part_of→CL:0000202` (direct extension), and the nested `CL:0000202─part_of→EMAPA:17597`
  - Used to test the CC-led branch of `plan_nested_anatomy_fixes()`: the nested edge re-points onto the primary CC individual but **keeps** `part_of` (CC-led does not switch to `occurs_in`)

The test requires the GO ontology file at `target/go_20250601.json` (downloaded via Makefile). The MF-causal->MF test also requires `resources/test/ro_20250723.owl`.

**Test Functions:**
- `test_gocam_ttl()`: Tests filtering logic with positive (MGI_MGI_1100089) and negative (61452e3d00000323) test cases for evidence consistency check
- `test_multi_edge_evidence_grouping()`: Tests evidence grouping logic for Issue #6, verifies that:
  - Evidence with identical metadata across edges is grouped correctly
  - Splitting creates one annotation per evidence group
  - Each split annotation maintains 2-edge structure with 1 evidence per edge
  - All split annotations pass the evidence consistency check
- `test_mf_causal_mf_filtering()`: Tests MF-to-MF causal relation filtering, verifies that:
  - RO causal relations are loaded from the RO ontology
  - RO:0002629 (directly positively regulates) is recognized as a causal relation
  - Annotations with MF-causal->MF edges are filtered out as non-standard
  - Filtering only applies when RO ontology is provided
- `test_print_non_standard_annotation_failed_checks()`: Tests TSV report output, verifies that:
  - Output has correct 6-column TSV format (model ID, title, reason, source, predicate, object)
  - Failure reasons are valid check names
  - GO term labels are resolved to human-readable form (not just CURIEs)
  - Output is de-duplicated (no duplicate rows)
- `test_print_non_standard_annotation_failed_checks_multiple_reasons()`: Tests reporting with inconsistent_evidence failures:
  - Verifies correct format for models with evidence consistency failures
  - Confirms inconsistent_evidence check failures are properly reported
- `test_edges_without_evidence()`: Tests that edges without evidence are included in annotation subgraph assembly (Issue #14):
  - Verifies model 66c7d41500000016 is parsed as one annotation subgraph (not two)
  - Verifies the no-evidence causal edge is present in the annotation with empty evidence_uris
  - Verifies the combined annotation is classified as non-standard
- `test_edges_without_evidence_report_column()`: Tests counting of edges without evidence:
  - Model 66c7d41500000016 should have exactly 1 edge without evidence
  - Model MGI_MGI_1100089 should have 0 edges without evidence
- `test_no_evidence_edge_gocam_relations_filter()`: Regression test for OBO namespace filter in no-evidence edge extraction:
  - Verifies model 57c82fad00000252 is parsed as 0 standard + 1 non-standard annotation (not 3+1)
  - Confirms non-GO-CAM axiom edges (oboInOwl#id, rdfs:label) are filtered out during extraction
- `test_edge_without_evidence_filter()`: Tests that annotations with no-evidence edges get `edge_without_evidence` failed check:
  - Uses model 66c7d41500000016 which has 1 no-evidence causal edge
  - Verifies the no-evidence edge's bnode ID is recorded in failed_checks
  - Verifies model MGI_MGI_1100089 (all edges have evidence) is unaffected
- `test_edge_without_evidence_all_edges_no_evidence()`: Tests all-no-evidence subgraph using model 67369e7600005491:
  - Verifies the GO:0006954 (inflammatory response) subgraph with 3 no-evidence edges is non-standard
  - All 3 no-evidence edges are flagged in `failed_checks["edge_without_evidence"]`
- `test_date_tolerant_evidence_grouping()`: Tests date-tolerant evidence grouping for Issue #15:
  - Evidence differing only in dates (dc:date, dcterms:created) is grouped together
  - Annotation with date-differing evidence is classified as standard
  - Evidence grouping produces correct number of groups with evidence from all edges
- `test_date_update_on_split()`: Tests date update during evidence splitting for Issue #15:
  - After splitting, all evidence nodes have the most recent dc:date (2023-02-13)
  - Verifies the split output file has updated dates
- `test_date_change_report()`: Tests date change record collection during splitting for Issue #15:
  - Verifies split returns date change records with model ID, title, dates, and edge type URIs
  - Verifies original and new dates differ, and new date is the most recent (2023-02-13)
  - Verifies edge labels can be resolved via `term_label()`
- `test_get_extension_edges()`: Tests `get_extension_edges()` backbone-vs-extension classification on the 5-edge GO:0120045 annotation in 5966411600000001.ttl:
  - Returns exactly the 3 expected extension edges, identified by `(predicate, source_type, target_type)` tuples
  - Confirms the 2 remaining backbone edges (MF-enabled_by-GP and root-MF-part_of-BP) are not in the result
  - Searches both `standard_annotations` and `non_standard_annotations` since the method works regardless of classification
- `test_gene_product_namespace_key()`: Unit test for `_gene_product_namespace_key()` (Issue #22):
  - Maps MOD / ComplexPortal / PR URIs (including MGI's double-prefixed form and the PomBase compact-colon form `identifiers.org/PomBase:...`) to the expected `GP_NAMESPACE_KEYS` keys
  - Confirms anatomy/ontology terms (EMAPA, WBbt, CL, UBERON, GO, RO, BFO, CHEBI) and HGNC resolve to keys absent from the allowlist, and non-URIRef nodes return `None`
- `test_gp_mf_relation_ignores_anatomy_target()`: Verifies an `MF ─occurs_in→ WBbt` anatomy edge (mf_occurs_in_anatomy_example.ttl) is not flagged `invalid_gp_mf_relation` (Issue #22 regression)
- `test_gp_mf_relation_allows_mf_to_gp_has_input_output()`: Verifies `MF ─has_input/has_output→ GP` edges (mf_to_gp_has_input_output_example.ttl) are not flagged `invalid_gp_mf_relation` (Issue #22)
- `test_relation_infrastructure()`: Verifies the new shared constants and cached relation URIs are populated correctly: `acts_upstream_relations` contains RO:0002264 and its descendants, `ROOT_GO_TERMS` has all three aspect roots, `ANATOMY_NAMESPACE_KEYS` includes `cl`/`uberon`, and all `rel_*` cached URIs resolve correctly
- `test_go_aspect()`: Unit test for `_go_aspect()`: MF/BP/CC GO terms return the correct aspect, non-GO/anatomy/GP entities return `None`
- `test_is_root_go_term()`: Unit test for `_is_root_go_term()`: the three root terms return `True`; non-root GO terms, BNodes, and `None` return `False`
- `test_is_anatomical_structure()`: Unit test for `_is_anatomical_structure()`: GO CCs, CL, UBERON, EMAPA return `True`; GP, ChEBI, MF, BP return `False`
- `test_category()`: Unit test for `_category()`: MF/BP/CC GO terms, GP (MGI/PR), and anatomy (CL) return the correct category string; ChEBI and `None` return `None`
- `test_invalid_bp_cc_relation()`: Tests that only the wrong-relation `BP─located_in→CL` edge in bp_cc_relation_example.ttl is flagged under `invalid_bp_cc_relation`
- `test_invalid_gp_cc_relation()`: Tests the #3 CC-subhierarchy split on gp_cc_relation_example.ttl via the per-edge `_flagged_sigs` helper — only `GP─part_of→anatomical-CC` and `GP─located_in→complex` are flagged; `located_in`/`is_active_in`→anatomical and `part_of`→complex pass
- `test_cc_branch()`: Unit test for `_cc_branch()`: protein-containing complex terms (incl. the `GO:0032991` root, reflexive) return `"complex"`; cellular anatomical structures, the `GO:0110165` root, and virion component `GO:0044423` return `"anatomical"`; non-CC GO terms, GPs, BNodes, the bare CC root `GO:0005575`, and `None` return `None`
- `test_invalid_gp_bp_relation()`: Tests that only the wrong-relation `GP─part_of→BP` edge in gp_bp_relation_example.ttl is flagged under `invalid_gp_bp_relation`
- `test_invalid_gp_bp_relation_skipped_without_ro()`: Verifies that `invalid_gp_bp_relation` is not recorded when no RO ontology is loaded
- `test_invalid_mf_bp_relation()`: Tests that `part_of`/`causally_upstream_of_or_within` MF→BP edges in real fixtures pass, and only the wrong-relation `root-MF─located_in→BP` edge in mf_bp_relation_example.ttl is flagged
- `test_invalid_mf_cc_relation()`: Tests that only the wrong-relation `root-MF─located_in→CC` edge in mf_cc_relation_example.ttl is flagged under `invalid_mf_cc_relation`
- `test_relation_rules_ignore_extension_edges()`: Regression test for the backbone-only gate. Verifies `MGI_MGI_1100089.ttl` keeps its full 28 standard annotations and that its `BP→anatomy` extension edges (`results_in_development_of` RO:0002296, `results_in_acquisition_of_features_of` RO:0002315, `acts_on_population_of` RO:0012003) are NOT flagged by any relation-validity check
- `test_multiple_mf_bp()`: Tests that both MF→BP edges in multi_mf_bp_example.ttl are flagged under `multiple_mf_bp` (the new key), and the old `multiple_mf_part_of` key is absent
- `test_multiple_mf_anatomy()`: Tests that both MF→anatomy edges in multi_mf_anatomy_example.ttl are flagged under `multiple_mf_anatomy`
- `test_enabler_not_gp()`: Tests that only the ChEBI-enabled edge in enabler_not_gp_example.ttl is flagged under `enabler_not_gp`
- `test_mgi_2182965_lead_aspect_is_mf()`: Tests that MGI_MGI_2182965's single annotation has remainders-report lead aspect MF (not BP), via `pick_lead_aspect(builder.get_primary_go_terms(annot))` (imported from `debug_non_standard.py`, which now re-exports it from `gocam_ttl.py`). Regression for the root-MF BP gate: the specific MF (`GO:0005515`) `─part_of→` BP must not make BP win
- `test_mgi_2182965_specific_mf_part_of_bp_is_not_backbone()`: Tests that for MGI_MGI_2182965 `get_primary_go_terms()` returns only an `MF` key (primary `GO:0005515`, no `BP`) and that the specific-MF `─part_of→` BP edge appears in `get_extension_edges()` — confirming `_backbone_role()`'s root-MF gate classifies it as an extension
- `test_causal_root_mf_to_bp_is_backbone()`: Tests that a `root-MF ─causally_upstream_of_or_within (RO:0002418)→ BP` edge (the passing causal edge in mf_bp_relation_example.ttl) is a BP backbone — `get_primary_go_terms()` registers the BP primary (`GO:0006954`) and the edge is not in `get_extension_edges()`. Confirms `_backbone_role()` accepts the full `mf_bp_valid_relations` set, not just `part_of`
- `test_get_primary_individuals()`: Tests that `get_primary_individuals()` on 5966411600000001.ttl's GO:0120045 annotation returns `{"MF": [...0003], "BP": [...0004]}` (individual URIs, not types) and no `"CC"` key
- `test_rewrite_edge_source_and_relation()`: Tests the low-level `GoCamGraph.rewrite_edge_source_and_relation()` on the `CL─part_of→EMAPA` edge in 5966411600000001.ttl: after re-pointing onto the BP individual (`...0004`) with `occurs_in`, the new assertion triple is present and the old gone, the `owl:Axiom` bnode's `annotatedSource`/`annotatedProperty` are swapped, and `annotatedTarget` + `lego:evidence` are preserved
- `test_plan_nested_anatomy_fixes_bp()`: BP-led real fixture (5966411600000001.ttl) — `plan_nested_anatomy_fixes()` yields exactly one instruction: the `CL─part_of→EMAPA` edge re-pointed onto the primary BP individual (`...0004`) with `occurs_in`; the direct `BP─occurs_in→CL` extension is not in the plan
- `test_plan_nested_anatomy_fixes_mf()`: MF-led synthetic fixture (mf_nested_anatomy_example.ttl) — the nested edge is re-pointed onto the primary MF individual with `occurs_in`
- `test_plan_nested_anatomy_fixes_cc()`: CC-led synthetic fixture (cc_nested_anatomy_example.ttl) — the nested edge is re-pointed onto the primary CC individual but **keeps** `part_of`
- `test_remainders_report_fixable_column()`: Tests the remainders report's `Fixable` column. Invokes `debug_non_standard.main()` end-to-end against `resources/test/` (monkeypatching `debug_non_standard.GoCamGraphBuilder` to the session `builder` to avoid re-parsing the GO ontology, and `sys.argv`), parses the TSV, and asserts the header has `Fixable` immediately after `Target` and three ground-truth rows: `5966411600000001`'s `CL:0000202 ─part_of→ EMAPA:17597` is `Yes`; `multi_mf_anatomy_example`'s `identical protein binding ─RO:0001025→ CL` (non-anatomy MF source) is `No`; and `57c82fad00000252`'s `nucleus ─part_of→ WBbt:0005396` is `No` despite both endpoints being anatomical (its annotation has an ambiguous primary individual, so the planner skips it — the design-intent guard that the column defers to `plan_nested_anatomy_fixes`, not a naive both-anatomical check)
