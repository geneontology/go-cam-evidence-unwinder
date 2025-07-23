
%/go_20250601.json:
	mkdir -p $*
	wget https://release.geneontology.org/2025-06-01/ontology/go.json -O $@

test: target/go_20250601.json
	pytest