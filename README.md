# go-cam-evidence-unwinder
Find standard annotations with edges connected to multiple evidence nodes. The code will then duplicate ("unwind") the standard annotation for each evidence so that all edges have only have one evidence.

## Usage

### Analyzing Models

The tool can analyze individual GO-CAM models or entire folders of models, producing a tab-separated report of statistics:

```bash
# Analyze a single model
python src/gocam_unwinder/gocam_ttl.py \
  -m path/to/model.ttl \
  -o path/to/go.json

# Analyze a folder of models
python src/gocam_unwinder/gocam_ttl.py \
  -d path/to/models/folder \
  -o path/to/go.json
```

#### Model List Report

When analyzing models, the tool outputs a tab-separated report with the following columns:

- **Model ID**: The GO-CAM model identifier (e.g., `gomodel:12345678`)
- **Title**: The model's title/description
- **Standard Annotations**: Count of standard annotations found (annotation units with edges that share evidence nodes)
- **Non-Standard Annotations**: Count of non-standard annotations (e.g., Reactome pathway models with multiple `part_of` edges from molecular functions)
- **Multi-Evidence Annotations**: Count of standard annotations that have at least one edge connected to multiple evidence nodes
- **Mixed Annotation Type**: "Yes" if the model contains both standard and non-standard annotations, "No" otherwise

Example output:
```
Model ID                Title                                   Standard Annotations    Non-Standard Annotations    Multi-Evidence Annotations    Mixed Annotation Type
gomodel:SGD_S000004491  Yeast gene model                       3                       0                           1                             No
gomodel:R-HSA-9937080   Reactome pathway                       0                       1                           0                             No
gomodel:MGI_MGI_1335098 Mouse model with occurs_in extensions  34                      0                           10                            No
```

This report helps identify which models contain standard annotations that can be unwound (split by evidence) and which models contain non-standard structural patterns.

### Splitting Evidence (Unwinding)

To duplicate annotations so each edge has only one evidence node:

```bash
python src/gocam_unwinder/gocam_ttl.py \
  -m path/to/model.ttl \
  -o path/to/go.json \
  --split-evidence \
  --output-dir output/
```

The `--split-evidence` flag triggers the unwinding process:
- For each edge with multiple evidence nodes, the first evidence keeps the original blank node
- Additional evidence nodes get new blank nodes with suffixes (`-2`, `-3`, etc.)
- New individual URIs are created with matching suffixes
- Metadata (types, contributors, dates) is cloned to maintain provenance

### Separating Statistics and Split Messages

By default, statistics are written to stdout. To write statistics to a separate file and keep split evidence messages on stdout:

```bash
python src/gocam_unwinder/gocam_ttl.py \
  -d path/to/models/folder \
  -o path/to/go.json \
  --split-evidence \
  --output-dir output/ \
  --report-file statistics.tsv
```

This is useful when processing many models, as it prevents the statistics report from being mixed with the "Split evidence" progress messages.
