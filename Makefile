# Use bash so recipes can run `set -o pipefail` (the system make is GNU 3.81, which
# silently ignores .SHELLFLAGS). Recipes that pipe a command into `tee` prepend
# `set -o pipefail` so a crash in `python3 ... | tee log` is not hidden by tee's
# exit 0 (which made `make` report "Pipeline complete" on a failed run).
SHELL := /bin/bash

# Configuration
DATE := $(shell date +%Y%m%d)
TARGET_DIR := target_$(DATE)
MODELS_DIR := /Users/ebertdu/go/noctua-models/models
MINERVA_CLI_DIR := /Users/ebertdu/go/minerva/minerva-cli
GO_ONTOLOGY := target/go_current.json
RO_ONTOLOGY := target/ro_current.owl
GROUPS_YAML := target/groups.yaml
LEGO_JOURNAL := target/blazegraph-lego.jnl

# Optional list of .ttl filenames to skip (one per line), e.g. true GO-CAMs.
# Produce via gocam-py's fetch_true_go_cams.sh, then extract success model IDs as <id>.ttl.
# Usage: make pipeline SKIP_LIST=path/to/true_gocam_skip_list.txt
SKIP_LIST ?=

# Whether to pass --split-evidence to the unwinder. Defaults to enabled.
# Usage: make pipeline SPLIT_EVIDENCE=     (disable)
#        make pipeline SPLIT_EVIDENCE=1    (enable, default)
SPLIT_EVIDENCE ?= 1

# Output directories and files
MODELS_SPLIT := $(TARGET_DIR)/models_split
MODELS_SPLIT_ORIG := $(TARGET_DIR)/models_split_orig
BLAZEGRAPH_DEV := $(TARGET_DIR)/blazegraph-dev.jnl
BLAZEGRAPH_PROD := $(TARGET_DIR)/blazegraph-prod.jnl
GPAD_EXPORT_DEV := $(TARGET_DIR)/gpad_export_dev
GPAD_EXPORT_PROD := $(TARGET_DIR)/gpad_export_prod
GPAD_DEV := $(TARGET_DIR)/gpad_export_dev.gpad
GPAD_PROD := $(TARGET_DIR)/gpad_export_prod.gpad
GPAD_DIFF := $(TARGET_DIR)/gpad_diff.txt
REPORT_FILE := $(TARGET_DIR)/noctua_models_graph_counts_$(DATE).tsv
CRITERIA_FAIL_REPORT := $(TARGET_DIR)/models_split_criteria_failures_$(DATE).tsv
DATE_CHANGE_REPORT := $(TARGET_DIR)/date_changes_$(DATE).tsv
NON_STD_REPORT := $(TARGET_DIR)/remainders_report_$(DATE).tsv
NON_STD_LOG := $(TARGET_DIR)/remainders_report_$(DATE).log
MODELS_NESTED_FIXED := $(TARGET_DIR)/models_nested_fixed
NESTED_FIX_REPORT := $(TARGET_DIR)/nested_fixes_$(DATE).tsv
NESTED_FIX_LOG := $(TARGET_DIR)/nested_fixes_$(DATE).log

# Google Drive folder for published report sheets (override on the CLI if needed)
GDRIVE_FOLDER_ID ?= 1ORulffGbEQANu8-jViGJaFn-mPMpQRfs

# Default target
.PHONY: all test clean pipeline fix_nested_anatomy
all: pipeline

# Run tests
test: target/go_20250601.json
	pytest

# Download GO ontology (specific version for tests)
%/go_20250601.json:
	mkdir -p $*
	wget https://release.geneontology.org/2025-06-01/ontology/go.json -O $@

# Download current GO ontology
target/go_current.json:
	mkdir -p target
	wget https://release.geneontology.org/2025-06-01/ontology/go.json -O $@

# Download current RO ontology
target/ro_current.owl:
	mkdir -p target
	wget http://purl.obolibrary.org/obo/ro.owl -O $@

# Download groups.yaml for resolving group URIs to labels
target/groups.yaml:
	mkdir -p target
	wget https://raw.githubusercontent.com/geneontology/go-site/master/metadata/groups.yaml -O $@

# Full pipeline
pipeline: $(GPAD_DIFF)
	@echo "Pipeline complete. Results in $(TARGET_DIR)/"

# Just split evidence, don't run GPAD diff
models_split: $(MODELS_SPLIT)
	@echo "Pipeline complete. Results in $(TARGET_DIR)/"

# Step 1: Run the unwinder to create split models
$(MODELS_SPLIT): $(GO_ONTOLOGY) $(RO_ONTOLOGY) $(GROUPS_YAML)
	mkdir -p $(MODELS_SPLIT)
	set -o pipefail; python3 src/gocam_unwinder/gocam_ttl.py \
		-d $(MODELS_DIR) \
		-o $(GO_ONTOLOGY) \
		-r $(RO_ONTOLOGY) \
		--groups-yaml $(GROUPS_YAML) \
		--skip-prefix SYNGO \
		--skip-prefix R-HSA \
		--skip-prefix YeastPathways \
		$(if $(SKIP_LIST),--skip-file $(SKIP_LIST),) \
		$(if $(SPLIT_EVIDENCE),--split-evidence,) \
		--output-dir $(MODELS_SPLIT) \
		--report-file $(REPORT_FILE) \
		--criteria-fail-report $(CRITERIA_FAIL_REPORT) \
		--date-change-report $(DATE_CHANGE_REPORT) \
		| tee $(TARGET_DIR)/gocam_ttl.log
	touch $@

# Step 2: Copy original models that were split for comparison
$(MODELS_SPLIT_ORIG): $(MODELS_SPLIT)
	mkdir -p $(MODELS_SPLIT_ORIG)
	@for file in $(MODELS_SPLIT)/*; do \
		basename=$$(basename "$$file"); \
		original="$(MODELS_DIR)/$$basename"; \
		if [ -f "$$original" ]; then \
			cp "$$original" $(MODELS_SPLIT_ORIG)/; \
		fi; \
	done
	touch $@

# Step 3a: Create dev blazegraph journal (split models)
$(BLAZEGRAPH_DEV): $(MODELS_SPLIT)
	rm -f $(BLAZEGRAPH_DEV)
	cd $(MINERVA_CLI_DIR) && \
	MINERVA_CLI_MEMORY=12G bin/minerva-cli.sh \
		--import-owl-models \
		-j $(CURDIR)/$(BLAZEGRAPH_DEV) \
		-f $(CURDIR)/$(MODELS_SPLIT)

# Step 3b: Create prod blazegraph journal (original models)
$(BLAZEGRAPH_PROD): $(MODELS_SPLIT_ORIG)
	rm -f $(BLAZEGRAPH_PROD)
	cd $(MINERVA_CLI_DIR) && \
	MINERVA_CLI_MEMORY=12G bin/minerva-cli.sh \
		--import-owl-models \
		-j $(CURDIR)/$(BLAZEGRAPH_PROD) \
		-f $(CURDIR)/$(MODELS_SPLIT_ORIG)

# Step 4a: Export GPAD from dev journal
$(GPAD_EXPORT_DEV): $(BLAZEGRAPH_DEV)
	mkdir -p $(GPAD_EXPORT_DEV)
	cd $(MINERVA_CLI_DIR) && \
	MINERVA_CLI_MEMORY=12G bin/minerva-cli.sh \
		--lego-to-gpad-sparql \
		-i $(CURDIR)/$(BLAZEGRAPH_DEV) \
		-ontojournal $(CURDIR)/$(LEGO_JOURNAL) \
		--gpad-output $(CURDIR)/$(GPAD_EXPORT_DEV)
	touch $@

# Step 4b: Export GPAD from prod journal
$(GPAD_EXPORT_PROD): $(BLAZEGRAPH_PROD)
	mkdir -p $(GPAD_EXPORT_PROD)
	cd $(MINERVA_CLI_DIR) && \
	MINERVA_CLI_MEMORY=12G bin/minerva-cli.sh \
		--lego-to-gpad-sparql \
		-i $(CURDIR)/$(BLAZEGRAPH_PROD) \
		-ontojournal $(CURDIR)/$(LEGO_JOURNAL) \
		--gpad-output $(CURDIR)/$(GPAD_EXPORT_PROD)
	touch $@

# Step 5a: Sort and dedupe dev GPAD
$(GPAD_DEV): $(GPAD_EXPORT_DEV)
	cat $(GPAD_EXPORT_DEV)/* | grep -v gpa-version | sort | uniq > $(TARGET_DIR)/gpad_export_dev.unsorted.gpad
	python3 sort_gpad_col12.py -f $(TARGET_DIR)/gpad_export_dev.unsorted.gpad > $@

# Step 5b: Sort and dedupe prod GPAD
$(GPAD_PROD): $(GPAD_EXPORT_PROD)
	cat $(GPAD_EXPORT_PROD)/* | grep -v gpa-version | sort | uniq > $(TARGET_DIR)/gpad_export_prod.unsorted.gpad
	python3 sort_gpad_col12.py -f $(TARGET_DIR)/gpad_export_prod.unsorted.gpad > $@

# Step 6: Generate GPAD diff
$(GPAD_DIFF): $(GPAD_PROD) $(GPAD_DEV)
	diff $(GPAD_PROD) $(GPAD_DEV) > $@ || true
	@echo "GPAD diff written to $@"

$(NON_STD_REPORT): $(GO_ONTOLOGY) $(RO_ONTOLOGY) $(GROUPS_YAML)
	mkdir -p $(TARGET_DIR)
	set -o pipefail; python3 debug_non_standard.py \
		$(MODELS_DIR) \
		-o $(GO_ONTOLOGY) \
		-r $(RO_ONTOLOGY) \
		--skip-prefix SYNGO \
		--skip-prefix R-HSA \
		--skip-prefix YeastPathways \
		$(if $(SKIP_LIST),--skip-file $(SKIP_LIST),) \
		--groups-yaml $(GROUPS_YAML) \
		--tsv-output $@ | tee $(NON_STD_LOG)

.PHONY: non_std
non_std: $(NON_STD_REPORT)

# Fix nested anatomy extensions: de-nest anatomy targets onto the annotation's
# primary term. This is an INDEPENDENT transformation of the source models -- it
# reads $(MODELS_DIR) directly (not the split output) and writes only the changed
# models to its own output dir, so it never conflicts with `models_split`. The CLI
# forbids combining --fix-nested-anatomy with --split-evidence in one invocation;
# run this target separately from the split pipeline.
$(MODELS_NESTED_FIXED): $(GO_ONTOLOGY) $(RO_ONTOLOGY) $(GROUPS_YAML)
	mkdir -p $(MODELS_NESTED_FIXED)
	set -o pipefail; python3 src/gocam_unwinder/gocam_ttl.py \
		-d $(MODELS_DIR) \
		-o $(GO_ONTOLOGY) \
		-r $(RO_ONTOLOGY) \
		--groups-yaml $(GROUPS_YAML) \
		--skip-prefix SYNGO \
		--skip-prefix R-HSA \
		--skip-prefix YeastPathways \
		$(if $(SKIP_LIST),--skip-file $(SKIP_LIST),) \
		--fix-nested-anatomy \
		--output-dir $(MODELS_NESTED_FIXED) \
		--nested-fix-report $(NESTED_FIX_REPORT) \
		| tee $(NESTED_FIX_LOG)
	touch $@

.PHONY: fix_nested_anatomy
fix_nested_anatomy: $(MODELS_NESTED_FIXED)
	@echo "Nested anatomy fixes written to $(MODELS_NESTED_FIXED)/ (report: $(NESTED_FIX_REPORT))"

# Publish whichever of the three reports exist in $(TARGET_DIR) to Google Drive as
# Sheets via tsv2sheet. Push-only: does not build the reports. Missing reports are
# skipped; a failed upload aborts. Use DATE=YYYYMMDD to target a specific run's folder.
.PHONY: push-reports
push-reports:
	@set -e; \
	push() { \
		if [ -f "$$1" ]; then \
			echo "Pushing $$1 -> Google Drive folder $(GDRIVE_FOLDER_ID) as \"$$2\""; \
			tsv2sheet --folder-id $(GDRIVE_FOLDER_ID) --title "$$2" "$$1"; \
		else \
			echo "Skipping $$1 (not found)"; \
		fi; \
	}; \
	push "$(REPORT_FILE)" "Standard annotation model stats $(DATE)"; \
	push "$(CRITERIA_FAIL_REPORT)" "Standard annotation criteria failures $(DATE)"; \
	push "$(NON_STD_REPORT)" "Non-standard annotation remainders $(DATE)"; \
	push "$(NESTED_FIX_REPORT)" "Nested anatomical extensions fixed $(DATE)"

# Clean up generated files
clean:
	rm -rf $(TARGET_DIR)

# Clean all target directories
clean-all:
	rm -rf target_*