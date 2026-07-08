# go-cam-evidence-unwinder
Find standard annotations with edges connected to multiple evidence nodes. The code will then duplicate ("unwind") the standard annotation for each evidence so that all edges have only have one evidence.

## Project Google drive
[Evidence unwinding project](https://drive.google.com/drive/u/0/folders/1ORulffGbEQANu8-jViGJaFn-mPMpQRfs)

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

## Standard-Annotation Ratchet

The `local/std-annot-ratchet` branch adds a two-level, monotonic pipeline that
filters the whole [noctua-models](https://github.com/geneontology/noctua-models)
corpus down to *standard annotations*, emitting a surviving set + a "what got
filtered out" report at each stage, plus a self-contained, shareable HTML
dashboard. Filtering rules live as data in `rules/`. Design, the rule inventory,
and findings are in
[`docs/plans/2026-06-25-standard-annotation-ratchet.md`](docs/plans/2026-06-25-standard-annotation-ratchet.md).

### Running it over the full corpus

This works as an ordinary, **non-root user** — on your laptop, or (for the ~55k
model corpus) on a powerful box you can SSH into. Everything writes under your
own checkout (`target_*/`) and a sibling `noctua-models/`; nothing needs a system
install or elevated privileges.

```bash
# 0. Get a checkout (e.g. on the remote box you SSH into).
git clone https://github.com/geneontology/go-cam-evidence-unwinder.git
cd go-cam-evidence-unwinder

# 1. A virtualenv in your own space, editable install.
python3 -m venv env && . env/bin/activate
pip install -r requirements.txt && pip install -e .

# 2. Fetch the corpus (shallow clone, ~2 GB) as a sibling ../noctua-models.
make corpus

# 3. Run the ratchet, sharded. JOBS ~= cores minus headroom on a shared box.
#    Auto-downloads the GO and RO ontologies on first run.
make ratchet JOBS=8

# 4. Build the shareable dashboard (auto-downloads groups.yaml).
make ratchet-dashboard
```

Open `target_YYYYMMDD/dashboard.html` in a browser — it's a single self-contained
file, so you can copy it off the remote host and share it as-is. For a quick,
corpus-free sanity check, `make ratchet-smoke`.

Notes for remote / restricted hosts:

- **Sizing `JOBS`:** each shard worker loads its own ~0.7 GB GO copy, so pick
  `JOBS` from the box's cores **and** free RAM. (A 96-core / 1 TiB host ran the
  full corpus at `JOBS=48` in ~80 s.) Shards resume across runs via a `.done`
  marker.
- **R1 source:** "not a true GO-CAM" defaults to the public
  `https://skyhook.geneontology.io/pipeline-from-goa/main`. On a host that mirrors
  skyhook locally, skip the network by pointing at the local file:
  `make ratchet TRUE_GOCAM_SOURCE=/path/to/pipeline-from-goa/main/reports/go-cam/02-filter.jsonl`.
- **Existing corpus checkout:** set `NOCTUA_MODELS_DIR=/path/to/noctua-models`
  instead of running `make corpus`.
- **Any ordinary login account works:** run under your own account and stage the
  checkout, venv, and `target_*/` outputs wherever you can write (`$HOME` or
  `/tmp`). No root, `sudo`, or shared service account is needed.
- **Driving it non-interactively (a script, or your own coding agent over SSH):**
  the agent runs on your local machine and drives the remote host over SSH. On
  your local machine, generate a dedicated, passphrase-less key just for this
  (never a personal key) and authorize its public half on *your own* account on
  the remote host:

  ```bash
  ssh-keygen -t ed25519 -N '' -C std-annot-ratchet-agent -f ~/.ssh/ratchet-agent
  # then append ~/.ssh/ratchet-agent.pub to <you>@<host>:~/.ssh/authorized_keys
  ```

  Each remote command runs in a fresh non-login shell, so activate the venv in the
  command itself:

  ```bash
  ssh -i ~/.ssh/ratchet-agent <you>@<host> \
    'cd <checkout> && . env/bin/activate && make ratchet JOBS=48'
  ```

  Nothing about this is committed to the repo — only your key's public half lives
  in your remote `~/.ssh/authorized_keys`.
