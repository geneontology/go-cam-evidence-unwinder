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
```

## Architecture

### Core Components

**GoCamGraph** (`src/gocam_unwinder/gocam_ttl.py:72-430`)
- Main data structure representing a GO-CAM model
- Wraps an rdflib.Graph and extracts structured annotation information
- Key methods:
  - `parse_ttl()`: Class method to parse a TTL file into a GoCamGraph
  - `extract_standard_annotations()`: Identifies and groups connected edges into StandardAnnotation objects
  - `get_evidence_metadata()`: Extracts metadata signature from evidence individuals for grouping
  - `group_evidence_by_metadata()`: Groups evidence across edges by identical metadata
  - `split_evidence_and_write_ttl()`: Splits multi-evidence annotations by evidence groups
  - `filter_out_non_std_annotations()`: Filters based on structural patterns

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

**GoCamGraphBuilder** (`src/gocam_unwinder/gocam_ttl.py:432-478`)
- Factory class that parses GO-CAM models with GO ontology context
- Uses ontobio's GoAspector to determine if terms are molecular functions
- Filters out non-standard annotations based on aspect logic (multiple part_of edges from molecular functions)

### Key Algorithm: Standard Annotation Extraction

The `extract_standard_annotations()` method (lines 344-403) implements a union-find-like algorithm:

1. Iterates through all edges with evidence
2. Tracks which StandardAnnotation each individual URI belongs to via `individual_to_annotation` dict
3. When an edge connects two individuals:
   - If neither is in an annotation: create new annotation
   - If one is in an annotation: add to that annotation
   - If both are in different annotations: merge annotations
4. Uses `find_related_edges()` to recursively discover connected edges via GO-CAM relations

This ensures that all edges sharing individuals or transitively connected through the graph are grouped into the same StandardAnnotation.

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
- **SGD_S000004491.ttl**: Standard yeast gene model (15 evidence triples, 3 edges)
- **MGI_MGI_1335098.ttl**: Mouse model with occurs_in extensions (34 standard annotations)
- **MGI_MGI_1100089.ttl**: Mouse Tnfsf11 model with multi-edge multi-evidence annotations (29 standard annotations)
- **R-HSA-9937080.ttl**: Reactome pathway (should have 0 standard, 1 non-standard)
- **SYNGO_5371.ttl**: SynGO model (1 standard annotation)
- **61452e3d00000323.ttl**: Saccharomyces MAL loci model (10 standard annotations)

The test requires the GO ontology file at `target/go_20250601.json` (downloaded via Makefile).

**Test Functions:**
- `test_gocam_ttl()`: Tests basic parsing, annotation extraction, and splitting for multiple models
- `test_multi_edge_evidence_grouping()`: Tests evidence grouping logic for Issue #6, verifies that evidence with identical metadata across edges is properly grouped when splitting
