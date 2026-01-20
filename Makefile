# Configuration
DATE := $(shell date +%Y%m%d)
TARGET_DIR := target_$(DATE)
MODELS_DIR := /Users/ebertdu/go/noctua-models/models
MINERVA_CLI_DIR := /Users/ebertdu/go/minerva/minerva-cli
GO_ONTOLOGY := target/go_current.json
RO_ONTOLOGY := target/ro_current.owl
LEGO_JOURNAL := target/blazegraph-lego.jnl

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

# Default target
.PHONY: all test clean pipeline
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

# Full pipeline
pipeline: $(GPAD_DIFF)
	@echo "Pipeline complete. Results in $(TARGET_DIR)/"

# Step 1: Run the unwinder to create split models
$(MODELS_SPLIT): $(GO_ONTOLOGY) $(RO_ONTOLOGY)
	mkdir -p $(MODELS_SPLIT)
	python3 src/gocam_unwinder/gocam_ttl.py \
		-d $(MODELS_DIR) \
		-o $(GO_ONTOLOGY) \
		-r $(RO_ONTOLOGY) \
		--skip-prefix SYNGO \
		--skip-prefix R-HSA \
		--skip-prefix YeastPathways \
		--split-evidence \
		--output-dir $(MODELS_SPLIT) \
		--report-file $(REPORT_FILE) \
		--criteria-fail-report $(CRITERIA_FAIL_REPORT)
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

# Clean up generated files
clean:
	rm -rf $(TARGET_DIR)

# Clean all target directories
clean-all:
	rm -rf target_*