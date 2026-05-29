import io
import pytest
import rdflib
from gocam_unwinder.gocam_ttl import GoCamGraph, GoCamGraphBuilder

ontology_file = "target/go_20250601.json"  # TODO: Make this GitHub-friendly, maybe LFS

def test_gocam_ttl():
    builder = GoCamGraphBuilder(ontology_file)

    # Positive test case: MGI_MGI_1100089 has consistent evidence across edges
    gocam_graph = builder.parse_ttl("resources/test/MGI_MGI_1100089.ttl")  # Tnfsf11
    test_individual = rdflib.term.URIRef(
        'http://model.geneontology.org/MGI_MGI_1100089/95094c4f-335a-4e08-9840-b42760a96357')
    std_annot = gocam_graph.get_standard_annotation_by_individual(test_individual)
    assert std_annot is not None, "Test individual should belong to a standard annotation"
    assert len(std_annot.edges) == 3
    # With the new evidence consistency check, we expect fewer standard annotations
    assert len(gocam_graph.standard_annotations) >= 1
    assert len(gocam_graph.non_standard_annotations) >= 0

    # Test evidence consistency for multi-edge annotation
    multi_edge_individual = rdflib.term.URIRef(
        'http://model.geneontology.org/MGI_MGI_1100089/ad099715-8779-4315-b3cd-77a1c25a6177')
    multi_edge_annot = gocam_graph.get_standard_annotation_by_individual(multi_edge_individual)
    assert multi_edge_annot is not None, "Multi-edge annotation should pass evidence consistency check"
    assert len(multi_edge_annot.edges) == 2, "Multi-edge annotation should have 2 edges"

    # Negative test case: 61452e3d00000323 does NOT have consistent evidence across edges
    gocam_graph = builder.parse_ttl("resources/test/61452e3d00000323.ttl")  # MAL loci in Saccharomyces
    test_individual = rdflib.term.URIRef('http://model.geneontology.org/61452e3d00000323/61452e3d00000330')
    std_annot = gocam_graph.get_standard_annotation_by_individual(test_individual)
    # This annotation should now be filtered out as non-standard due to inconsistent evidence
    assert std_annot is None, "61452e3d00000323 annotation should be filtered as non-standard due to inconsistent evidence"
    # Most or all annotations should be non-standard now
    assert len(gocam_graph.non_standard_annotations) >= 1

    # Other test models
    gocam_graph = builder.parse_ttl("resources/test/R-HSA-9937080.ttl")  # Reactome
    assert len(gocam_graph.standard_annotations) == 0
    assert len(gocam_graph.non_standard_annotations) == 1

    gocam_graph = builder.parse_ttl("resources/test/SYNGO_5371.ttl")  # SynGO
    # Single edge annotations are always consistent
    assert len(gocam_graph.standard_annotations) >= 0


def test_multi_edge_evidence_grouping():
    """
    Test that evidence with identical metadata across multiple edges
    is properly grouped when splitting.

    Issue #6: Evidence individuals that have identical data on the same
    standard annotation subgraph but on different edges need to be grouped
    together so that newly created multi-edge subgraphs retain the correct
    group of evidence individuals.
    """
    builder = GoCamGraphBuilder(ontology_file)
    gocam_graph = builder.parse_ttl("resources/test/MGI_MGI_1100089.ttl")

    # Find the standard annotation with the multi-edge multi-evidence issue
    # This annotation has source individual ad099715-8779-4315-b3cd-77a1c25a6177
    test_individual = rdflib.term.URIRef(
        'http://model.geneontology.org/MGI_MGI_1100089/ad099715-8779-4315-b3cd-77a1c25a6177')
    std_annot = gocam_graph.get_standard_annotation_by_individual(test_individual)

    # Should have 2 edges (enabled_by and causally_upstream_of)
    assert len(std_annot.edges) == 2

    # Test evidence grouping
    evidence_groups = gocam_graph.group_evidence_by_metadata(std_annot)

    # Should have 2 evidence groups (one for 2003-09-12 evidence, one for 2013-08-27)
    assert len(evidence_groups) == 2

    # Each group should have evidence from both edges
    for group_index, group_edges in evidence_groups.items():
        assert len(group_edges) == 2, f"Group {group_index} should have evidence from both edges"
        # Each edge in the group should have exactly 1 evidence
        for edge_id, evidence_uris in group_edges.items():
            assert len(evidence_uris) == 1, f"Each edge should have 1 evidence in group {group_index}"

    # Now split and verify the output
    gocam_graph.split_evidence_and_write_ttl("target/MGI_MGI_1100089_split.ttl")

    # Reload the split model
    gocam_graph_split = builder.parse_ttl("target/MGI_MGI_1100089_split.ttl")

    # After splitting, the multi-evidence annotation should be duplicated
    # Find the split annotations - look for the original individual and the -2 suffix version
    original_annot = gocam_graph_split.get_standard_annotation_by_individual(test_individual)
    split_individual = rdflib.term.URIRef(str(test_individual) + "-2")
    split_annot = gocam_graph_split.get_standard_annotation_by_individual(split_individual)

    # Both should exist and pass the evidence consistency check
    assert original_annot is not None, "Original annotation should exist"
    assert split_annot is not None, "Split annotation with -2 suffix should exist"

    # Both should have 2 edges (same structure, different evidence)
    assert len(original_annot.edges) == 2
    assert len(split_annot.edges) == 2

    # Each edge in each annotation should have exactly 1 evidence
    for edge in original_annot.edges.values():
        assert len(edge.evidence_uris) == 1, "Original annotation edges should have 1 evidence each"

    for edge in split_annot.edges.values():
        assert len(edge.evidence_uris) == 1, "Split annotation edges should have 1 evidence each"

    # Verify evidence consistency
    assert gocam_graph_split.has_consistent_evidence_across_edges(original_annot), "Original should have consistent evidence"
    assert gocam_graph_split.has_consistent_evidence_across_edges(split_annot), "Split should have consistent evidence"

    # Test MGI_MGI_1927246 (Zfp326 model)
    gocam_graph = builder.parse_ttl("resources/test/MGI_MGI_1927246.ttl")
    assert len(gocam_graph.standard_annotations) == 5
    gocam_graph.split_evidence_and_write_ttl("target/MGI_MGI_1927246_test.ttl")

    # Verify the split created the -2 individual, which is the fibroblast for a part_of extension
    gocam_graph_split = builder.parse_ttl("target/MGI_MGI_1927246_test.ttl")
    split_individual = rdflib.term.URIRef('http://model.geneontology.org/MGI_MGI_1927246/a2f2216c-0cf5-4436-8a75-b3aa41974936-2')
    split_annot = gocam_graph_split.get_standard_annotation_by_individual(split_individual)
    assert split_annot is not None, "MGI_MGI_1927246 should have -2 split individual"


def test_mf_causal_mf_filtering():
    """
    Test that annotations with causal relation edges between two molecular function
    nodes are filtered out as non-standard.

    The model 5b318d0900000481.ttl contains a GO:0004672 (protein kinase activity)
    node with an RO:0002629 (directly positively regulates) edge to a GO:0003700
    (DNA-binding transcription factor activity) node. Both are molecular functions,
    so any annotation containing this edge should be filtered out when the RO
    ontology is provided.
    """
    ro_ontology_file = "resources/test/ro_20250723.owl"

    # Without RO ontology - MF-causal->MF check is not applied
    builder_no_ro = GoCamGraphBuilder(ontology_file)
    gocam_graph_no_ro = builder_no_ro.parse_ttl("resources/test/5b318d0900000481.ttl")

    # Count annotations that would be affected by the MF-causal->MF check
    # The model has multiple MF-to-MF causal edges
    total_annotations_no_ro = len(gocam_graph_no_ro.standard_annotations) + len(gocam_graph_no_ro.non_standard_annotations)

    # With RO ontology - MF-causal->MF annotations should be filtered out
    builder_with_ro = GoCamGraphBuilder(ontology_file, ro_ontology_file)
    gocam_graph_with_ro = builder_with_ro.parse_ttl("resources/test/5b318d0900000481.ttl")

    # Verify RO causal relations were loaded
    assert len(builder_with_ro.causal_relations) > 0, "Causal relations should be loaded from RO ontology"

    # Verify RO:0002629 (directly positively regulates) is in the causal relations set
    assert "http://purl.obolibrary.org/obo/RO_0002629" in builder_with_ro.causal_relations, \
        "RO:0002629 should be a descendant of causally_upstream_of_or_within"

    # The individual 5b318d0900000505 is a GO:0004672 (MF) with RO:0002629 edge to
    # 5b318d0900000503 which is GO:0003700 (MF) - this should be filtered out
    mf_causal_mf_individual = rdflib.term.URIRef(
        'http://model.geneontology.org/5b318d0900000481/5b318d0900000505')

    # With RO ontology, this annotation should be in non_standard_annotations
    std_annot = gocam_graph_with_ro.get_standard_annotation_by_individual(mf_causal_mf_individual)
    assert std_annot is None, "MF-causal->MF annotation should be filtered out as non-standard"

    # Verify it's in non_standard_annotations
    non_std_annot = None
    for annot in gocam_graph_with_ro.non_standard_annotations:
        if mf_causal_mf_individual in annot.individuals:
            non_std_annot = annot
            break
    assert non_std_annot is not None, "MF-causal->MF annotation should be in non_standard_annotations"

    # The total number of annotations should be the same (just categorized differently)
    total_annotations_with_ro = len(gocam_graph_with_ro.standard_annotations) + len(gocam_graph_with_ro.non_standard_annotations)
    assert total_annotations_with_ro == total_annotations_no_ro, \
        "Total annotation count should be the same with or without RO ontology"

    # More annotations should be non-standard when RO is provided (due to MF-causal->MF filtering)
    assert len(gocam_graph_with_ro.non_standard_annotations) >= len(gocam_graph_no_ro.non_standard_annotations), \
        "Should have at least as many non-standard annotations when MF-causal->MF filtering is applied"


def test_print_non_standard_annotation_failed_checks():
    """
    Test that print_non_standard_annotation_failed_checks outputs correct TSV format
    with model ID, title, failure reason, and term labels for source, predicate, object.
    """
    ro_ontology_file = "resources/test/ro_20250723.owl"
    builder = GoCamGraphBuilder(ontology_file, ro_ontology_file)
    gocam_graph = builder.parse_ttl("resources/test/5b318d0900000481.ttl")

    # Verify we have non-standard annotations to report
    assert len(gocam_graph.non_standard_annotations) > 0, "Should have non-standard annotations"

    # Capture output using StringIO
    output = io.StringIO()
    builder.print_non_standard_annotation_failed_checks(gocam_graph, output)

    # Get the output and parse it
    output_content = output.getvalue()
    lines = output_content.strip().split('\n') if output_content.strip() else []

    # Should have at least one line of output
    assert len(lines) > 0, "Should output at least one failure row"

    # Verify TSV format - each line should have 6 tab-separated columns
    for line in lines:
        cols = line.split('\t')
        assert len(cols) == 6, f"Each line should have 6 columns, got {len(cols)}: {line}"

        model_id, title, failure_reason, source_label, prop_label, target_label = cols

        # Model ID should be the model URI
        assert "5b318d0900000481" in model_id, f"Model ID should contain model identifier: {model_id}"

        # Title should be non-empty
        assert title, "Title should not be empty"

        # Failure reason should be one of the known check names
        assert failure_reason in ["inconsistent_evidence", "multiple_mf_part_of", "mf_causal_mf", "edge_without_evidence"], \
            f"Unknown failure reason: {failure_reason}"

        # Labels should be non-empty (either term labels or CURIEs)
        assert source_label, "Source label should not be empty"
        assert prop_label, "Property label should not be empty"
        assert target_label, "Target label should not be empty"

    # Check for mf_causal_mf failures specifically
    mf_causal_lines = [l for l in lines if "mf_causal_mf" in l]
    assert len(mf_causal_lines) > 0, "Should have at least one mf_causal_mf failure"

    # Verify term labels are resolved (not just CURIEs) for GO terms
    # The model has protein kinase activity (GO:0004672) -> DNA-binding transcription factor activity (GO:0003700)
    for line in mf_causal_lines:
        cols = line.split('\t')
        source_label, prop_label, target_label = cols[3], cols[4], cols[5]

        # At least one of the GO term labels should be resolved to human-readable form
        # (not just GO:XXXXXXX format)
        go_labels = [source_label, target_label]
        has_resolved_label = any(not label.startswith("GO:") for label in go_labels)
        assert has_resolved_label, f"At least one GO term should have a resolved label: {go_labels}"

    # Verify de-duplication: no duplicate rows
    assert len(lines) == len(set(lines)), "Output should not contain duplicate rows"


def test_print_non_standard_annotation_failed_checks_multiple_reasons():
    """
    Test that print_non_standard_annotation_failed_checks correctly reports
    annotations that fail multiple checks.
    """
    # Use a model that has annotations failing the inconsistent_evidence check
    builder = GoCamGraphBuilder(ontology_file)
    gocam_graph = builder.parse_ttl("resources/test/61452e3d00000323.ttl")

    # Verify we have non-standard annotations
    assert len(gocam_graph.non_standard_annotations) > 0, "Should have non-standard annotations"

    # Capture output
    output = io.StringIO()
    builder.print_non_standard_annotation_failed_checks(gocam_graph, output)

    output_content = output.getvalue()
    lines = output_content.strip().split('\n') if output_content.strip() else []

    # Should have output for inconsistent_evidence failures
    assert len(lines) > 0, "Should have failure output"

    # Verify all lines have correct format
    for line in lines:
        cols = line.split('\t')
        assert len(cols) == 6, f"Each line should have 6 columns: {line}"

    # Check for inconsistent_evidence failures
    inconsistent_lines = [l for l in lines if "inconsistent_evidence" in l]
    assert len(inconsistent_lines) > 0, "Should have inconsistent_evidence failures"


def test_edges_without_evidence():
    """
    Test that edges without evidence are included in annotation subgraph assembly.

    Issue #14: Model 66c7d41500000016 has a causal edge (RO:0002407, "indirectly
    positively regulates") between two MF nodes that has no evidence. Without
    handling this, the model is incorrectly parsed as two separate annotation
    subgraphs instead of one.

    The model has 12 OWL axiom edges total, 11 with evidence and 1 without.
    The no-evidence edge connects individual ...17 (GO:0030545, receptor ligand
    activity) to ...25 (GO:0004971, AMPA glutamate receptor activity).
    """
    ro_ontology_file = "resources/test/ro_20250723.owl"
    builder = GoCamGraphBuilder(ontology_file, ro_ontology_file)
    gocam_graph = builder.parse_ttl("resources/test/66c7d41500000016.ttl")

    # The two MF individuals that are bridged by the no-evidence causal edge
    mf_source = rdflib.term.URIRef(
        'http://model.geneontology.org/66c7d41500000016/66c7d41500000017')
    mf_target = rdflib.term.URIRef(
        'http://model.geneontology.org/66c7d41500000016/66c7d41500000025')

    # Both individuals should be in the SAME annotation subgraph
    # (not split into two separate subgraphs)
    all_annotations = gocam_graph.standard_annotations + gocam_graph.non_standard_annotations
    source_annot = None
    target_annot = None
    for annot in all_annotations:
        if mf_source in annot.individuals:
            source_annot = annot
        if mf_target in annot.individuals:
            target_annot = annot

    assert source_annot is not None, "MF source individual should be in an annotation"
    assert target_annot is not None, "MF target individual should be in an annotation"
    assert source_annot is target_annot, \
        "Both MF individuals should be in the SAME annotation (connected via no-evidence edge)"

    # The combined annotation should be non-standard (due to mf_causal_mf or inconsistent_evidence)
    assert source_annot in gocam_graph.non_standard_annotations, \
        "The combined annotation should be non-standard"

    # Verify the no-evidence edge is present in the annotation's edges
    no_evidence_edge_found = False
    for edge in source_annot.edges.values():
        if (edge.source_uri == mf_source and edge.target_uri == mf_target and
                str(edge.property_uri) == "http://purl.obolibrary.org/obo/RO_0002407"):
            no_evidence_edge_found = True
            assert len(edge.evidence_uris) == 0, "The bridging edge should have no evidence"
            break
    assert no_evidence_edge_found, "The no-evidence causal edge should be in the annotation"


def test_edges_without_evidence_report_column():
    """
    Test that the report includes a column counting edges without evidence.
    Issue #14: Report out models having edges without evidence.
    """
    ro_ontology_file = "resources/test/ro_20250723.owl"
    builder = GoCamGraphBuilder(ontology_file, ro_ontology_file)
    gocam_graph = builder.parse_ttl("resources/test/66c7d41500000016.ttl")

    # Count edges without evidence across all annotations
    no_evidence_count = 0
    all_annotations = gocam_graph.standard_annotations + gocam_graph.non_standard_annotations
    for annot in all_annotations:
        for edge in annot.edges.values():
            if len(edge.evidence_uris) == 0:
                no_evidence_count += 1

    # Model 66c7d41500000016 has exactly 1 edge without evidence
    assert no_evidence_count == 1, f"Expected 1 edge without evidence, got {no_evidence_count}"

    # Also verify a model with all edges having evidence reports 0
    builder_no_ro = GoCamGraphBuilder(ontology_file)
    gocam_graph_all_ev = builder_no_ro.parse_ttl("resources/test/MGI_MGI_1100089.ttl")

    no_evidence_count_all = 0
    all_annotations_all = gocam_graph_all_ev.standard_annotations + gocam_graph_all_ev.non_standard_annotations
    for annot in all_annotations_all:
        for edge in annot.edges.values():
            if len(edge.evidence_uris) == 0:
                no_evidence_count_all += 1

    assert no_evidence_count_all == 0, f"MGI_MGI_1100089 should have 0 edges without evidence, got {no_evidence_count_all}"


def test_no_evidence_edge_gocam_relations_filter():
    """
    Regression test: model 57c82fad00000252 should be parsed as 1 non-standard annotation.

    Without the GOCAM_RELATIONS filter in extract_edges()'s second pass,
    non-GO-CAM axiom edges (like rdf:type reifications without evidence) are
    extracted and fed into the union-find, causing the model to be incorrectly
    split into 4 subgraphs (3 standard + 1 non-standard).
    """
    ro_ontology_file = "resources/test/ro_20250723.owl"
    builder = GoCamGraphBuilder(ontology_file, ro_ontology_file)
    gocam_graph = builder.parse_ttl("resources/test/57c82fad00000252.ttl")

    std_count = len(gocam_graph.standard_annotations)
    non_std_count = len(gocam_graph.non_standard_annotations)

    assert std_count == 0, \
        f"Expected 0 standard annotations, got {std_count}"
    assert non_std_count == 1, \
        f"Expected 1 non-standard annotation, got {non_std_count}"


def test_edge_without_evidence_filter():
    """
    Test that annotations containing edges without evidence are marked non-standard
    with the 'edge_without_evidence' failed check.

    Model 66c7d41500000016 has a causal edge (RO:0002407) with no evidence.
    The annotation containing this edge should have 'edge_without_evidence' in
    its failed_checks, with the no-evidence edge's bnode ID recorded.
    """
    ro_ontology_file = "resources/test/ro_20250723.owl"
    builder = GoCamGraphBuilder(ontology_file, ro_ontology_file)
    gocam_graph = builder.parse_ttl("resources/test/66c7d41500000016.ttl")

    # The two MF individuals bridged by the no-evidence causal edge
    mf_source = rdflib.term.URIRef(
        'http://model.geneontology.org/66c7d41500000016/66c7d41500000017')

    # Find the annotation containing mf_source
    target_annot = None
    for annot in gocam_graph.non_standard_annotations:
        if mf_source in annot.individuals:
            target_annot = annot
            break
    assert target_annot is not None, "Annotation should be in non_standard_annotations"

    # The annotation should have 'edge_without_evidence' in its failed_checks
    assert "edge_without_evidence" in target_annot.failed_checks, \
        f"Expected 'edge_without_evidence' in failed_checks, got: {list(target_annot.failed_checks.keys())}"

    # The failed check should contain exactly the no-evidence edge's bnode ID
    no_ev_edge_bnodes = target_annot.failed_checks["edge_without_evidence"]
    assert len(no_ev_edge_bnodes) == 1, \
        f"Expected 1 edge without evidence, got {len(no_ev_edge_bnodes)}"

    # Verify the flagged edge is actually the one without evidence
    for edge in target_annot.edges.values():
        if edge.bnode_id in no_ev_edge_bnodes:
            assert len(edge.evidence_uris) == 0, "Flagged edge should have no evidence"

    # Also verify that a model with all edges having evidence does NOT get this check
    builder_no_ro = GoCamGraphBuilder(ontology_file)
    gocam_graph_all_ev = builder_no_ro.parse_ttl("resources/test/MGI_MGI_1100089.ttl")
    for annot in gocam_graph_all_ev.standard_annotations:
        assert "edge_without_evidence" not in (annot.failed_checks or {}), \
            "Annotations with all edges having evidence should not fail this check"


def test_edge_without_evidence_all_edges_no_evidence():
    """
    Test that an annotation where ALL edges lack evidence is marked non-standard
    with the 'edge_without_evidence' failed check.

    Model 67369e7600005491 contains a subgraph with 3 edges (RO:0002333, BFO:0000066,
    RO:0002418) connecting 4 individuals including GO:0006954 (inflammatory response),
    where all edges have no evidence. This tests the case where an entire annotation
    has zero evidence, not just a single bridging edge.
    """
    ro_ontology_file = "resources/test/ro_20250723.owl"
    builder = GoCamGraphBuilder(ontology_file, ro_ontology_file)
    gocam_graph = builder.parse_ttl("resources/test/67369e7600005491.ttl")

    # Find the individual typed as GO:0006954 (inflammatory response)
    inflammatory_response_type = rdflib.term.URIRef(
        'http://purl.obolibrary.org/obo/GO_0006954')

    target_individual = None
    for s, p, o in gocam_graph.g.triples((None, rdflib.RDF.type, inflammatory_response_type)):
        if str(s).startswith('http://model.geneontology.org/67369e7600005491'):
            target_individual = s
            break
    assert target_individual is not None, "Should find an individual typed GO:0006954"

    # Find the annotation containing this individual
    target_annot = None
    all_annotations = gocam_graph.standard_annotations + gocam_graph.non_standard_annotations
    for annot in all_annotations:
        if target_individual in annot.individuals:
            target_annot = annot
            break
    assert target_annot is not None, "GO:0006954 individual should be in an annotation"

    # The annotation should be non-standard
    assert target_annot in gocam_graph.non_standard_annotations, \
        "Annotation with all-no-evidence edges should be non-standard"

    # It should have the edge_without_evidence failed check
    assert "edge_without_evidence" in target_annot.failed_checks, \
        f"Expected 'edge_without_evidence' in failed_checks, got: {list(target_annot.failed_checks.keys())}"

    # Every edge without evidence in this annotation should be in the failed set
    no_ev_edge_bnodes = target_annot.failed_checks["edge_without_evidence"]
    actual_no_ev_edges = [e for e in target_annot.edges.values() if len(e.evidence_uris) == 0]
    assert len(no_ev_edge_bnodes) == len(actual_no_ev_edges), \
        f"All {len(actual_no_ev_edges)} no-evidence edges should be flagged, got {len(no_ev_edge_bnodes)}"
    assert len(actual_no_ev_edges) == 3, "Should have 3 edges without evidence"


def test_date_tolerant_evidence_grouping():
    """
    Test that evidence differing only in dc:date is grouped together.

    Issue #15: Model MGI_MGI_1101770 has an annotation where the enabled_by
    edge has evidence dated 2006-08-09 and the causally_upstream_of edge has
    evidence dated 2023-02-13. The evidence is otherwise identical (same ECO,
    same PMID, same contributor). These should be grouped together, and the
    date should be updated to the most recent (2023-02-13).
    """
    builder = GoCamGraphBuilder(ontology_file)
    gocam_graph = builder.parse_ttl("resources/test/MGI_MGI_1101770.ttl")

    # Individual a9c5f5d3 is the Ring1 MF activity with date-differing evidence
    test_individual = rdflib.term.URIRef(
        'http://model.geneontology.org/MGI_MGI_1101770/a9c5f5d3-a960-420d-b052-1d264074a901')

    # This annotation should be classified as standard (not filtered out)
    std_annot = gocam_graph.get_standard_annotation_by_individual(test_individual)
    assert std_annot is not None, \
        "Date-differing evidence annotation should be standard (not filtered out)"
    assert len(std_annot.edges) == 2, "Annotation should have 2 edges"

    # Evidence grouping should produce 2 groups (one per PMID/ECO pair)
    evidence_groups = gocam_graph.group_evidence_by_metadata(std_annot)
    assert len(evidence_groups) == 2, \
        f"Should have 2 evidence groups, got {len(evidence_groups)}"

    # Each group should have evidence from both edges
    for group_index, group_edges in evidence_groups.items():
        assert len(group_edges) == 2, \
            f"Group {group_index} should have evidence from both edges"


def test_date_update_on_split():
    """
    Test that after splitting, evidence nodes are updated to the most recent dc:date.

    Issue #15: When evidence is grouped across edges that have different dates,
    the split output should use the most recent date for all evidence in the group.
    """
    builder = GoCamGraphBuilder(ontology_file)
    gocam_graph = builder.parse_ttl("resources/test/MGI_MGI_1101770.ttl")

    # Split and write
    gocam_graph.split_evidence_and_write_ttl("target/MGI_MGI_1101770_split.ttl")

    # Reload the split model
    split_graph = rdflib.Graph()
    split_graph.parse("target/MGI_MGI_1101770_split.ttl", format="turtle")

    # Find all evidence URIs attached to the annotation edges for individual a9c5f5d3
    # The evidence should all have the most recent date (2023-02-13)
    date_pred = rdflib.namespace.DC.date
    evidence_pred = rdflib.URIRef("http://geneontology.org/lego/evidence")

    # Collect all evidence URIs from edges that reference individual a9c5f5d3
    test_individual = rdflib.URIRef(
        'http://model.geneontology.org/MGI_MGI_1101770/a9c5f5d3-a960-420d-b052-1d264074a901')
    evidence_uris = set()
    for bnode, _, _ in split_graph.triples((None, rdflib.namespace.OWL.annotatedSource, test_individual)):
        for _, _, ev_uri in split_graph.triples((bnode, evidence_pred, None)):
            evidence_uris.add(ev_uri)
    for bnode, _, _ in split_graph.triples((None, rdflib.namespace.OWL.annotatedTarget, test_individual)):
        for _, _, ev_uri in split_graph.triples((bnode, evidence_pred, None)):
            evidence_uris.add(ev_uri)

    assert len(evidence_uris) > 0, "Should find evidence URIs for the test individual"

    # All evidence should have dc:date = "2023-02-13" (the most recent)
    for ev_uri in evidence_uris:
        dates = list(split_graph.objects(ev_uri, date_pred))
        assert len(dates) == 1, f"Evidence {ev_uri} should have exactly 1 dc:date, got {len(dates)}"
        assert str(dates[0]) == "2023-02-13", \
            f"Evidence {ev_uri} should have date 2023-02-13, got {str(dates[0])}"


def test_date_change_report():
    """
    Test that splitting produces date change records with edge info.

    Issue #15: Records should include model ID, title, old date, new date,
    and source/predicate/target type URIs for each updated edge.
    """
    builder = GoCamGraphBuilder(ontology_file)
    gocam_graph = builder.parse_ttl("resources/test/MGI_MGI_1101770.ttl")

    # Split and collect date change records
    date_change_records = gocam_graph.split_evidence_and_write_ttl("target/MGI_MGI_1101770_split.ttl")

    assert len(date_change_records) >= 1, "Should have at least 1 date change record"

    for rec in date_change_records:
        assert "MGI_MGI_1101770" in rec["model_id"]
        assert rec["original_date"] != rec["new_date"], "Original and new dates should differ"
        assert rec["new_date"] == "2023-02-13", "New date should be the most recent"
        assert rec["source_type"] is not None, "Source type should not be None"
        assert rec["property_uri"] is not None, "Property URI should not be None"
        assert rec["target_type"] is not None, "Target type should not be None"

        # Verify labels can be resolved
        source_label = builder.term_label(rec["source_type"])
        pred_label = builder.term_label(rec["property_uri"])
        target_label = builder.term_label(rec["target_type"])
        assert source_label, "Source label should not be empty"
        assert pred_label, "Predicate label should not be empty"
        assert target_label, "Target label should not be empty"


def test_get_extension_edges():
    """
    Test that get_extension_edges() returns the non-backbone edges of a
    StandardAnnotation. Uses the GO:0120045 (stereocilium maintenance)
    annotation in 5966411600000001.ttl, which has 5 edges total:
    - 2 backbone edges (MF-enabled_by->GP, MF-part_of->BP)
    - 3 extension edges (BP-part_of->BP, BP-occurs_in->CL, CL-part_of->EMAPA)
    """
    builder = GoCamGraphBuilder(ontology_file)
    gocam_graph = builder.parse_ttl("resources/test/5966411600000001.ttl")

    # Locate the annotation containing the GO:0120045 individual.
    # The annotation may be in either standard or non_standard list.
    bp_individual = rdflib.term.URIRef(
        'http://model.geneontology.org/5966411600000001/5966411600000004')
    target_annot = None
    for annot in gocam_graph.standard_annotations + gocam_graph.non_standard_annotations:
        if bp_individual in annot.individuals:
            target_annot = annot
            break
    assert target_annot is not None, \
        "Annotation containing the GO:0120045 individual should exist"

    # Sanity: the annotation in this model has 5 edges
    assert len(target_annot.edges) == 5, \
        f"Expected 5 edges in the GO:0120045 annotation, got {len(target_annot.edges)}"

    # Call the method under test
    extensions = builder.get_extension_edges(target_annot)

    # Should return exactly 3 extension edges
    assert len(extensions) == 3, \
        f"Expected 3 extension edges, got {len(extensions)}"

    # Verify each returned edge by (predicate, source_type, target_type) tuple.
    # bnode IDs are not stable across parses — use type triples instead.
    actual_tuples = {
        (str(e.property_uri), str(e.source_type), str(e.target_type))
        for e in extensions
    }
    expected_tuples = {
        # BP -part_of-> BP (GO:0120045 -> GO:0007605)
        ("http://purl.obolibrary.org/obo/BFO_0000050",
         "http://purl.obolibrary.org/obo/GO_0120045",
         "http://purl.obolibrary.org/obo/GO_0007605"),
        # BP -occurs_in-> CL (GO:0120045 -> CL:0000202)
        ("http://purl.obolibrary.org/obo/BFO_0000066",
         "http://purl.obolibrary.org/obo/GO_0120045",
         "http://purl.obolibrary.org/obo/CL_0000202"),
        # CL -part_of-> EMAPA (CL:0000202 -> EMAPA:17597)
        ("http://purl.obolibrary.org/obo/BFO_0000050",
         "http://purl.obolibrary.org/obo/CL_0000202",
         "http://purl.obolibrary.org/obo/EMAPA_17597"),
    }
    assert actual_tuples == expected_tuples, \
        f"Extension edges differ.\n  expected: {expected_tuples}\n  got:      {actual_tuples}"

    # Verify the 2 backbone edges (MF-enabled_by->GP, MF-part_of->BP) are NOT in the result
    extension_bnodes = {e.bnode_id for e in extensions}
    backbone_tuples_seen = set()
    for edge in target_annot.edges.values():
        if edge.bnode_id in extension_bnodes:
            continue
        backbone_tuples_seen.add(
            (str(edge.property_uri), str(edge.source_type), str(edge.target_type)))
    expected_backbone = {
        # MF -enabled_by-> GP
        ("http://purl.obolibrary.org/obo/RO_0002333",
         "http://purl.obolibrary.org/obo/GO_0003674",
         "http://identifiers.org/mgi/MGI:2139535"),
        # MF -part_of-> BP
        ("http://purl.obolibrary.org/obo/BFO_0000050",
         "http://purl.obolibrary.org/obo/GO_0003674",
         "http://purl.obolibrary.org/obo/GO_0120045"),
    }
    assert backbone_tuples_seen == expected_backbone, \
        f"Backbone edges differ.\n  expected: {expected_backbone}\n  got:      {backbone_tuples_seen}"


def test_get_primary_go_terms():
    """
    Test that get_primary_go_terms() returns the primary GO term URIs of an
    annotation, grouped by aspect ("MF", "BP", "CC"). Uses the GO:0120045
    (stereocilium maintenance) annotation in 5966411600000001.ttl, which has:
      - MF backbone (MF-enabled_by-GP)   -> primary MF = GO:0003674
      - BP backbone (MF-part_of-BP)      -> primary BP = GO:0120045
      - No CC backbone                   -> "CC" key absent
    """
    builder = GoCamGraphBuilder(ontology_file)
    gocam_graph = builder.parse_ttl("resources/test/5966411600000001.ttl")

    # Locate the annotation containing the GO:0120045 individual.
    bp_individual = rdflib.term.URIRef(
        'http://model.geneontology.org/5966411600000001/5966411600000004')
    target_annot = None
    for annot in gocam_graph.standard_annotations + gocam_graph.non_standard_annotations:
        if bp_individual in annot.individuals:
            target_annot = annot
            break
    assert target_annot is not None, \
        "Annotation containing the GO:0120045 individual should exist"

    primaries = builder.get_primary_go_terms(target_annot)

    # Expect exactly the two aspects present
    assert set(primaries.keys()) == {"MF", "BP"}, \
        f"Expected aspects {{'MF', 'BP'}}, got {set(primaries.keys())}"

    # Each aspect's list should have exactly 1 URI for this annotation
    assert len(primaries["MF"]) == 1, \
        f"Expected 1 primary MF URI, got {len(primaries['MF'])}: {primaries['MF']}"
    assert len(primaries["BP"]) == 1, \
        f"Expected 1 primary BP URI, got {len(primaries['BP'])}: {primaries['BP']}"

    # Verify the actual URIs
    assert str(primaries["MF"][0]) == "http://purl.obolibrary.org/obo/GO_0003674", \
        f"Primary MF should be GO:0003674, got {primaries['MF'][0]}"
    assert str(primaries["BP"][0]) == "http://purl.obolibrary.org/obo/GO_0120045", \
        f"Primary BP should be GO:0120045, got {primaries['BP'][0]}"

    # CC key should be absent (no CC backbone in this annotation)
    assert "CC" not in primaries, \
        f"Expected no 'CC' key, got primaries={primaries}"


# ---------------------------------------------------------------------------
# _resolve_mf_type() helper tests (Task 2)
# ---------------------------------------------------------------------------

def test_resolve_mf_type_direct_uri():
    builder = GoCamGraphBuilder(ontology_file)
    mf_uri = rdflib.URIRef("http://purl.obolibrary.org/obo/GO_0042802")  # identical protein binding (MF)
    g = rdflib.Graph()
    assert builder._resolve_mf_type(mf_uri, g) == mf_uri


def test_resolve_mf_type_non_mf_uri_returns_none():
    builder = GoCamGraphBuilder(ontology_file)
    bp_uri = rdflib.URIRef("http://purl.obolibrary.org/obo/GO_0006954")  # inflammatory response (BP)
    g = rdflib.Graph()
    assert builder._resolve_mf_type(bp_uri, g) is None


def test_resolve_mf_type_complement_of_mf():
    builder = GoCamGraphBuilder(ontology_file)
    g = rdflib.Graph()
    g.parse(data='''
        @prefix owl: <http://www.w3.org/2002/07/owl#> .
        @prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
        _:mf_indiv rdf:type [ rdf:type owl:Class ;
                              owl:complementOf <http://purl.obolibrary.org/obo/GO_0042802> ] .
    ''', format="ttl")
    # Find the bnode that's the value of rdf:type
    bnode = None
    for _, _, o in g.triples((None, rdflib.RDF.type, None)):
        if isinstance(o, rdflib.BNode):
            bnode = o
            break
    assert bnode is not None, "Test setup: expected a bnode class expression"
    resolved = builder._resolve_mf_type(bnode, g)
    assert resolved == rdflib.URIRef("http://purl.obolibrary.org/obo/GO_0042802")


# ---------------------------------------------------------------------------
# invalid_gp_mf_relation check tests (Task 3)
# ---------------------------------------------------------------------------

def test_gp_mf_relation_allows_enables():
    builder = GoCamGraphBuilder(ontology_file)
    gocam = builder.parse_ttl("resources/test/MGI_MGI_1100089.ttl")
    for annot in gocam.standard_annotations + gocam.non_standard_annotations:
        assert "invalid_gp_mf_relation" not in annot.failed_checks, (
            f"enabled_by-based annotation incorrectly flagged: {annot.failed_checks}"
        )


def test_gp_mf_relation_allows_contributes_to():
    builder = GoCamGraphBuilder(ontology_file)
    gocam = builder.parse_ttl("resources/test/contributes_to_example.ttl")
    flagged = [
        a for a in gocam.non_standard_annotations
        if "invalid_gp_mf_relation" in a.failed_checks
    ]
    assert flagged == [], f"contributes_to incorrectly flagged: {flagged}"
    # Should remain standard
    assert len(gocam.standard_annotations) == 1


def test_gp_mf_relation_rejects_other_predicate():
    builder = GoCamGraphBuilder(ontology_file)
    gocam = builder.parse_ttl("resources/test/invalid_gp_mf_relation_example.ttl")
    flagged_edges = set()
    for a in gocam.non_standard_annotations:
        flagged_edges |= a.failed_checks.get("invalid_gp_mf_relation", set())
    assert len(flagged_edges) >= 1, (
        "Annotation using a non-allowed GP-MF predicate should be flagged"
    )
    # And should land in non_standard_annotations
    assert len(gocam.non_standard_annotations) == 1
    assert len(gocam.standard_annotations) == 0


# ---------------------------------------------------------------------------
# TSV reporter integration (Task 4)
# ---------------------------------------------------------------------------

def test_print_non_standard_annotation_failed_checks_includes_gp_mf_relation():
    builder = GoCamGraphBuilder(ontology_file)
    gocam = builder.parse_ttl("resources/test/invalid_gp_mf_relation_example.ttl")
    buf = io.StringIO()
    builder.print_non_standard_annotation_failed_checks(gocam, buf)
    contents = buf.getvalue()
    assert "invalid_gp_mf_relation" in contents
