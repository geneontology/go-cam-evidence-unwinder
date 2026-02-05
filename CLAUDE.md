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
- Multi-Evidence Annotations - Count of standard annotations with >1 evidence on any edge
- Mixed Annotation Type - "Yes" if model has both standard and non-standard annotations
- MF-causal->MF Edges - Count of causal edges between molecular functions (in non-standard)
- Model State - Model state from `http://geneontology.org/lego/modelstate` (e.g., "production", "development")
- Groups - Pipe-separated list of contributing groups from `http://purl.org/pav/providedBy` (resolved to labels if `--groups-yaml` provided, e.g., "MGI", "ZFIN", "SGD")
- Multi-Evidence GO Terms - Pipe-separated list of resolved GO term labels from multi-evidence annotations (excludes URIs and CURIEs that couldn't be resolved to labels)

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
  --criteria-fail-report failures.tsv
```

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
- Key methods:
  - `parse_ttl()`: Parses a TTL file, extracts model metadata (including modelstate and groups with label resolution), and applies filtering
  - `uri_is_molecular_function()`: Checks if a URI is a molecular function using GoAspector
  - `uri_is_causal_relation()`: Checks if a URI is a causal relation (descendant of RO:0002418)
  - `term_label()`: Looks up human-readable labels for GO/RO/BFO terms from stored ontologies
  - `filter_out_non_std_annotations()`: Applies filtering checks and tracks failures
  - `print_non_standard_annotation_failed_checks()`: Outputs TSV report of failed checks with term labels

**`load_groups_lookup(groups_yaml_path)`** (`src/gocam_unwinder/gocam_ttl.py:65-88`)
- Loads groups.yaml from go-site and creates a URI → label lookup dictionary
- The groups.yaml file contains entries like: `{id: "http://informatics.jax.org", label: "MGI"}`
- Returns dict mapping group URIs to their labels (e.g., `{"http://informatics.jax.org": "MGI"}`)

### Key Algorithm: Standard Annotation Extraction

The `extract_standard_annotations()` method (lines 334-393) implements a union-find-like algorithm:

1. Iterates through all edges with evidence
2. Tracks which StandardAnnotation each individual URI belongs to via `individual_to_annotation` dict
3. When an edge connects two individuals:
   - If neither is in an annotation: create new annotation
   - If one is in an annotation: add to that annotation
   - If both are in different annotations: merge annotations
4. Uses `find_related_edges()` to recursively discover connected edges via GO-CAM relations

The `find_related_edges()` method (lines 396-425) looks up already-extracted edges by bnode ID rather than creating new ones, which preserves the `evidence_uris` that were populated during `extract_edges()`.

This ensures that all edges sharing individuals or transitively connected through the graph are grouped into the same StandardAnnotation with their evidence data intact.

### Standard Annotation Filtering

The `filter_out_non_std_annotations()` method applies three filtering checks to every annotation. All checks are run on each annotation (no short-circuiting), and results are tracked per-edge in the `StandardAnnotation.failed_checks` dict.

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

2. **Multiple part_of edges from molecular functions** (`multiple_mf_part_of`):
   - Filters out annotations containing more than one MF-part_of->? edge within their annotation subgraph
   - When failed, only the part_of edges are recorded (not all edges)
   - This prevents complex pathway models from being classified as standard annotations

3. **Causal relation edges between two molecular function nodes** (`mf_causal_mf`, requires RO ontology):
   - If an RO ontology file is provided, filters out annotations containing causal relation edges (descendants of RO:0002418 "causally upstream of or within") where both source and target are molecular functions
   - When failed, only the MF-causal->MF edges are recorded
   - Causal relations include: directly positively regulates (RO:0002629), directly negatively regulates (RO:0002630), etc.
   - This prevents MF-to-MF causal chains from being classified as standard annotations

#### Reporting

The `print_non_standard_annotation_failed_checks()` method outputs details about which edges failed which checks for non-standard annotations.

### Evidence Splitting Logic

The evidence splitting process now groups evidence by metadata to handle multi-edge annotations correctly (Issue #6):

#### Evidence Metadata Grouping

The `get_evidence_metadata()` method (lines 102-127) extracts a metadata signature from each evidence individual:
- Collects values for predicates in `PREDICATES_TO_COPY` (type, contributor, date, created, dateAccepted, providedBy, comment)
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
2. For each evidence group:
   - Group 0 (first): keeps original blank nodes and individuals, removes extra evidence
   - Groups 1+ (subsequent): creates new blank nodes with suffix "-2", "-3", etc.
   - Creates new individual URIs with same suffix for all edges in the group
   - Reuses individual URIs across edges in the same group (via `individual_mapping`)
   - Clones metadata (types, contributors, dates) to new nodes using `PREDICATES_TO_COPY`
   - Adds only the evidence belonging to this group

**Example:** If an annotation has 2 edges with evidence [A, B] and [C, D] respectively, where metadata(A) == metadata(C) and metadata(B) == metadata(D):
- Group 0: Edge 1 with evidence A + Edge 2 with evidence C (original nodes)
- Group 1: Edge 1 with evidence B + Edge 2 with evidence D (new nodes with "-2" suffix)

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
- **SYNGO_5371.ttl**: SynGO model (single-edge annotations pass consistency check)
- **5b318d0900000481.ttl**: Human kinase activation template model with MF-to-MF causal edges
  - Contains GO:0004672 (protein kinase activity) → RO:0002629 (directly positively regulates) → GO:0003700 (DNA-binding transcription factor activity)
  - Used to test MF-causal->MF filtering when RO ontology is provided

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
