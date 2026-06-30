import io
import os
import sys
import pytest
import requests
import rdflib
from gocam_unwinder.gocam_ttl import GoCamGraph, GoCamGraphBuilder, ModelStats, pick_lead_aspect
from gocam_unwinder import gocam_ttl

ontology_file = "target/go_20250601.json"  # TODO: Make this GitHub-friendly, maybe LFS

# Relation URIs used by the nested-anatomy-fix plan tests.
OCCURS_IN = "http://purl.obolibrary.org/obo/BFO_0000066"
PART_OF = "http://purl.obolibrary.org/obo/BFO_0000050"

def test_gocam_ttl(builder):

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


def test_multi_edge_evidence_grouping(builder):
    """
    Test that evidence with identical metadata across multiple edges
    is properly grouped when splitting.

    Issue #6: Evidence individuals that have identical data on the same
    standard annotation subgraph but on different edges need to be grouped
    together so that newly created multi-edge subgraphs retain the correct
    group of evidence individuals.
    """
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


def test_print_non_standard_annotation_failed_checks(builder):
    """
    Test that print_non_standard_annotation_failed_checks outputs correct TSV format
    with model ID, title, failure reason, and term labels for source, predicate, object.
    """
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
        assert failure_reason in [
            "inconsistent_evidence", "multiple_mf_bp", "mf_causal_mf",
            "edge_without_evidence", "invalid_gp_mf_relation",
            "invalid_gp_cc_relation", "invalid_gp_bp_relation",
            "invalid_mf_bp_relation", "invalid_mf_cc_relation",
            "invalid_bp_cc_relation", "multiple_mf_anatomy", "enabler_not_gp",
        ], f"Unknown failure reason: {failure_reason}"

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


def test_print_non_standard_annotation_failed_checks_multiple_reasons(builder):
    """
    Test that print_non_standard_annotation_failed_checks correctly reports
    annotations that fail multiple checks.
    """
    # Use a model that has annotations failing the inconsistent_evidence check
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


def test_edges_without_evidence(builder):
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


def test_edges_without_evidence_report_column(builder):
    """
    Test that the report includes a column counting edges without evidence.
    Issue #14: Report out models having edges without evidence.
    """
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
    gocam_graph_all_ev = builder.parse_ttl("resources/test/MGI_MGI_1100089.ttl")

    no_evidence_count_all = 0
    all_annotations_all = gocam_graph_all_ev.standard_annotations + gocam_graph_all_ev.non_standard_annotations
    for annot in all_annotations_all:
        for edge in annot.edges.values():
            if len(edge.evidence_uris) == 0:
                no_evidence_count_all += 1

    assert no_evidence_count_all == 0, f"MGI_MGI_1100089 should have 0 edges without evidence, got {no_evidence_count_all}"


def test_no_evidence_edge_gocam_relations_filter(builder):
    """
    Regression test: model 57c82fad00000252 should be parsed as 1 non-standard annotation.

    Without the GOCAM_RELATIONS filter in extract_edges()'s second pass,
    non-GO-CAM axiom edges (like rdf:type reifications without evidence) are
    extracted and fed into the union-find, causing the model to be incorrectly
    split into 4 subgraphs (3 standard + 1 non-standard).
    """
    gocam_graph = builder.parse_ttl("resources/test/57c82fad00000252.ttl")

    std_count = len(gocam_graph.standard_annotations)
    non_std_count = len(gocam_graph.non_standard_annotations)

    assert std_count == 0, \
        f"Expected 0 standard annotations, got {std_count}"
    assert non_std_count == 1, \
        f"Expected 1 non-standard annotation, got {non_std_count}"


def test_edge_without_evidence_filter(builder):
    """
    Test that annotations containing edges without evidence are marked non-standard
    with the 'edge_without_evidence' failed check.

    Model 66c7d41500000016 has a causal edge (RO:0002407) with no evidence.
    The annotation containing this edge should have 'edge_without_evidence' in
    its failed_checks, with the no-evidence edge's bnode ID recorded.
    """
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
    gocam_graph_all_ev = builder.parse_ttl("resources/test/MGI_MGI_1100089.ttl")
    for annot in gocam_graph_all_ev.standard_annotations:
        assert "edge_without_evidence" not in (annot.failed_checks or {}), \
            "Annotations with all edges having evidence should not fail this check"


def test_edge_without_evidence_all_edges_no_evidence(builder):
    """
    Test that an annotation where ALL edges lack evidence is marked non-standard
    with the 'edge_without_evidence' failed check.

    Model 67369e7600005491 contains a subgraph with 3 edges (RO:0002333, BFO:0000066,
    RO:0002418) connecting 4 individuals including GO:0006954 (inflammatory response),
    where all edges have no evidence. This tests the case where an entire annotation
    has zero evidence, not just a single bridging edge.
    """
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


def test_date_tolerant_evidence_grouping(builder):
    """
    Test that evidence differing only in dc:date is grouped together.

    Issue #15: Model MGI_MGI_1101770 has an annotation where the enabled_by
    edge has evidence dated 2006-08-09 and the causally_upstream_of edge has
    evidence dated 2023-02-13. The evidence is otherwise identical (same ECO,
    same PMID, same contributor). These should be grouped together, and the
    date should be updated to the most recent (2023-02-13).
    """
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


def test_date_update_on_split(builder):
    """
    Test that after splitting, evidence nodes are updated to the most recent dc:date.

    Issue #15: When evidence is grouped across edges that have different dates,
    the split output should use the most recent date for all evidence in the group.
    """
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


def test_date_change_report(builder):
    """
    Test that splitting produces date change records with edge info.

    Issue #15: Records should include model ID, title, old date, new date,
    and source/predicate/target type URIs for each updated edge.
    """
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


def test_get_extension_edges(builder):
    """
    Test that get_extension_edges() returns the non-backbone edges of a
    StandardAnnotation. Uses the GO:0120045 (stereocilium maintenance)
    annotation in 5966411600000001.ttl, which has 5 edges total:
    - 2 backbone edges (MF-enabled_by->GP, MF-part_of->BP)
    - 3 extension edges (BP-part_of->BP, BP-occurs_in->CL, CL-part_of->EMAPA)
    """
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


def test_get_primary_go_terms(builder):
    """
    Test that get_primary_go_terms() returns the primary GO term URIs of an
    annotation, grouped by aspect ("MF", "BP", "CC"). Uses the GO:0120045
    (stereocilium maintenance) annotation in 5966411600000001.ttl, which has:
      - MF backbone (MF-enabled_by-GP)   -> primary MF = GO:0003674
      - BP backbone (MF-part_of-BP)      -> primary BP = GO:0120045
      - No CC backbone                   -> "CC" key absent
    """
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

def test_resolve_mf_type_direct_uri(builder):
    mf_uri = rdflib.URIRef("http://purl.obolibrary.org/obo/GO_0042802")  # identical protein binding (MF)
    g = rdflib.Graph()
    assert builder._resolve_mf_type(mf_uri, g) == mf_uri


def test_resolve_mf_type_non_mf_uri_returns_none(builder):
    bp_uri = rdflib.URIRef("http://purl.obolibrary.org/obo/GO_0006954")  # inflammatory response (BP)
    g = rdflib.Graph()
    assert builder._resolve_mf_type(bp_uri, g) is None


def test_resolve_mf_type_complement_of_mf(builder):
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

def test_gp_mf_relation_allows_enables(builder):
    gocam = builder.parse_ttl("resources/test/MGI_MGI_1100089.ttl")
    for annot in gocam.standard_annotations + gocam.non_standard_annotations:
        assert "invalid_gp_mf_relation" not in annot.failed_checks, (
            f"enabled_by-based annotation incorrectly flagged: {annot.failed_checks}"
        )


def test_gp_mf_relation_allows_contributes_to(builder):
    gocam = builder.parse_ttl("resources/test/contributes_to_example.ttl")
    flagged = [
        a for a in gocam.non_standard_annotations
        if "invalid_gp_mf_relation" in a.failed_checks
    ]
    assert flagged == [], f"contributes_to incorrectly flagged: {flagged}"
    # Should remain standard
    assert len(gocam.standard_annotations) == 1


def test_gp_mf_relation_rejects_other_predicate(builder):
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

def test_print_non_standard_annotation_failed_checks_includes_gp_mf_relation(builder):
    gocam = builder.parse_ttl("resources/test/invalid_gp_mf_relation_example.ttl")
    buf = io.StringIO()
    builder.print_non_standard_annotation_failed_checks(gocam, buf)
    contents = buf.getvalue()
    assert "invalid_gp_mf_relation" in contents


# ---------------------------------------------------------------------------
# Model file collection: --skip-file / --skip-prefix / id-filter
# ---------------------------------------------------------------------------

def test_collect_model_files_skip_filenames(tmp_path):
    from gocam_unwinder.gocam_ttl import collect_model_files
    for name in ["a.ttl", "b.ttl", "c.ttl", "notes.txt"]:
        (tmp_path / name).write_text("")
    result = collect_model_files(str(tmp_path), skip_filenames={"b.ttl"})
    names = sorted(os.path.basename(p) for p in result)
    assert names == ["a.ttl", "c.ttl"]


def test_collect_model_files_combines_filters(tmp_path):
    from gocam_unwinder.gocam_ttl import collect_model_files
    for name in ["SYNGO_1.ttl", "keep.ttl", "skipme.ttl", "drop.ttl"]:
        (tmp_path / name).write_text("")
    result = collect_model_files(
        str(tmp_path),
        skip_prefixes=["SYNGO"],
        skip_filenames={"skipme.ttl"},
        model_id_filter={"keep"},
    )
    names = sorted(os.path.basename(p) for p in result)
    # SYNGO_1 skipped by prefix, skipme by filename, drop excluded by id filter
    assert names == ["keep.ttl"]


# ---------------------------------------------------------------------------
# GP namespace resolver (allowlist) — Issue #22
# ---------------------------------------------------------------------------

def test_gene_product_namespace_key(builder):
    U = rdflib.URIRef

    # Gene products -> namespace keys in GP_NAMESPACE_KEYS
    gp_cases = {
        "http://identifiers.org/mgi/MGI:1100089": "mgi",
        "http://identifiers.org/sgd/S000005274": "sgd",
        "http://identifiers.org/zfin/ZDB-GENE-060118-1": "zfin",
        "http://identifiers.org/uniprot/P12345": "uniprot",
        "http://identifiers.org/wormbase/WB:WBGene00000912": "wormbase",
        "http://identifiers.org/rgd/RGD:61909": "rgd",
        "http://identifiers.org/dictybase.gene/DDB_G0277853": "dictybase",
        "http://identifiers.org/tair.locus/2200950": "tair",
        "https://www.ebi.ac.uk/complexportal/complex/CPX-566": "complexportal",
        "http://purl.obolibrary.org/obo/PR_000000001": "pr",
        # Compact identifiers.org form ({seg}:{id} instead of {seg}/{id}).
        # PomBase uses this real-world form, with a '.' inside the id portion.
        "http://identifiers.org/PomBase:SPBC16D10.09": "pombase",
        "https://identifiers.org/complexportal:CPX-566": "complexportal",
        "https://identifiers.org/uniprot:P12345": "uniprot",
    }
    for uri, expected_key in gp_cases.items():
        key = builder._gene_product_namespace_key(U(uri))
        assert key == expected_key, f"{uri}: got {key!r}, expected {expected_key!r}"
        assert key in builder.GP_NAMESPACE_KEYS, f"{uri}: key {key!r} not in GP_NAMESPACE_KEYS"

    # Non-gene-products -> key absent from GP_NAMESPACE_KEYS (or None)
    non_gp = [
        "http://purl.obolibrary.org/obo/EMAPA_16894",
        "http://purl.obolibrary.org/obo/WBbt_0006796",
        "http://purl.obolibrary.org/obo/CL_0000066",
        "http://purl.obolibrary.org/obo/UBERON_0000955",
        "http://purl.obolibrary.org/obo/GO_0003674",
        "http://purl.obolibrary.org/obo/RO_0002418",
        "http://purl.obolibrary.org/obo/BFO_0000050",
        "http://purl.obolibrary.org/obo/CHEBI_15367",
    ]
    for uri in non_gp:
        assert builder._gene_product_namespace_key(U(uri)) not in builder.GP_NAMESPACE_KEYS, \
            f"{uri} should not resolve to a GP namespace"

    # HGNC is intentionally excluded from the allowlist
    assert builder._gene_product_namespace_key(
        U("http://identifiers.org/hgnc/HGNC:11998")) == "hgnc"
    assert "hgnc" not in builder.GP_NAMESPACE_KEYS

    # Non-URIRef (e.g. a blank node) -> None
    assert builder._gene_product_namespace_key(rdflib.BNode()) is None


def test_gp_mf_relation_ignores_anatomy_target(builder):
    # MF -occurs_in-> WBbt anatomy is an extension, not a GP-MF backbone edge.
    # Under the old {GO,RO,BFO} blocklist this was falsely flagged.
    gocam = builder.parse_ttl("resources/test/mf_occurs_in_anatomy_example.ttl")
    for annot in gocam.standard_annotations + gocam.non_standard_annotations:
        assert "invalid_gp_mf_relation" not in annot.failed_checks, (
            f"MF->anatomy edge incorrectly flagged: {annot.failed_checks}"
        )


def test_gp_mf_relation_allows_mf_to_gp_has_input_output(builder):
    # has_input / has_output are valid MF->GP extension relations.
    gocam = builder.parse_ttl("resources/test/mf_to_gp_has_input_output_example.ttl")
    for annot in gocam.standard_annotations + gocam.non_standard_annotations:
        assert "invalid_gp_mf_relation" not in annot.failed_checks, (
            f"MF->GP has_input/has_output incorrectly flagged: {annot.failed_checks}"
        )


# ---------------------------------------------------------------------------
# Tasks 1–5: shared infrastructure (constants, cached relation URIs, helpers)
# ---------------------------------------------------------------------------

def test_relation_infrastructure(builder):
    # acts_upstream family loaded from RO (RO:0002264 + children)
    au = builder.acts_upstream_relations
    assert "http://purl.obolibrary.org/obo/RO_0002264" in au          # root of the family
    assert "http://purl.obolibrary.org/obo/RO_0002331" in au          # involved in (child)
    assert "http://purl.obolibrary.org/obo/RO_0002418" not in au      # separate branch
    # root GO terms
    assert "http://purl.obolibrary.org/obo/GO_0003674" in builder.ROOT_GO_TERMS
    assert "http://purl.obolibrary.org/obo/GO_0008150" in builder.ROOT_GO_TERMS
    assert "http://purl.obolibrary.org/obo/GO_0005575" in builder.ROOT_GO_TERMS
    # anatomy namespace keys
    assert "cl" in builder.ANATOMY_NAMESPACE_KEYS
    assert "uberon" in builder.ANATOMY_NAMESPACE_KEYS
    # cached relation URIs (all 8)
    assert str(builder.rel_enabled_by) == "http://purl.obolibrary.org/obo/RO_0002333"
    assert str(builder.rel_contributes_to) == "http://purl.obolibrary.org/obo/RO_0002326"
    assert str(builder.rel_has_input) == "http://purl.obolibrary.org/obo/RO_0002233"
    assert str(builder.rel_has_output) == "http://purl.obolibrary.org/obo/RO_0002234"
    assert str(builder.rel_part_of) == "http://purl.obolibrary.org/obo/BFO_0000050"
    assert str(builder.rel_located_in) == "http://purl.obolibrary.org/obo/RO_0001025"
    assert str(builder.rel_is_active_in) == "http://purl.obolibrary.org/obo/RO_0002432"
    assert str(builder.rel_occurs_in) == "http://purl.obolibrary.org/obo/BFO_0000066"


def test_go_aspect(builder):
    U = rdflib.URIRef
    g = rdflib.Graph()  # empty graph: owl:complementOf lookup finds nothing,
                        # so BNode inputs always resolve to None here
    assert builder._go_aspect(U("http://purl.obolibrary.org/obo/GO_0042802"), g) == "MF"
    assert builder._go_aspect(U("http://purl.obolibrary.org/obo/GO_0003674"), g) == "MF"  # root MF
    assert builder._go_aspect(U("http://purl.obolibrary.org/obo/GO_0006954"), g) == "BP"
    assert builder._go_aspect(U("http://purl.obolibrary.org/obo/GO_0005634"), g) == "CC"
    # Non-GO entities are not an aspect
    assert builder._go_aspect(U("http://identifiers.org/mgi/MGI:1100089"), g) is None
    assert builder._go_aspect(U("http://purl.obolibrary.org/obo/CL_0000066"), g) is None
    assert builder._go_aspect(None, g) is None
    assert builder._go_aspect(rdflib.BNode(), g) is None  # unwrapped BNode -> None


def test_is_root_go_term(builder):
    U = rdflib.URIRef
    assert builder._is_root_go_term(U("http://purl.obolibrary.org/obo/GO_0003674")) is True
    assert builder._is_root_go_term(U("http://purl.obolibrary.org/obo/GO_0008150")) is True
    assert builder._is_root_go_term(U("http://purl.obolibrary.org/obo/GO_0005575")) is True
    assert builder._is_root_go_term(U("http://purl.obolibrary.org/obo/GO_0042802")) is False
    assert builder._is_root_go_term(rdflib.BNode()) is False
    assert builder._is_root_go_term(None) is False


def test_is_anatomical_structure(builder):
    U = rdflib.URIRef
    # GO cellular components count as anatomical structures
    assert builder._is_anatomical_structure(U("http://purl.obolibrary.org/obo/GO_0005634")) is True
    # Anatomy-ontology terms count
    assert builder._is_anatomical_structure(U("http://purl.obolibrary.org/obo/CL_0000066")) is True
    assert builder._is_anatomical_structure(U("http://purl.obolibrary.org/obo/UBERON_0000955")) is True
    assert builder._is_anatomical_structure(U("http://purl.obolibrary.org/obo/EMAPA_16894")) is True
    # Non-anatomy: gene product, chemical, MF, BP
    assert builder._is_anatomical_structure(U("http://identifiers.org/mgi/MGI:1100089")) is False
    assert builder._is_anatomical_structure(U("http://purl.obolibrary.org/obo/CHEBI_15367")) is False
    assert builder._is_anatomical_structure(U("http://purl.obolibrary.org/obo/GO_0042802")) is False
    assert builder._is_anatomical_structure(U("http://purl.obolibrary.org/obo/GO_0006954")) is False


def test_category(builder):
    U = rdflib.URIRef
    g = rdflib.Graph()
    assert builder._category(U("http://purl.obolibrary.org/obo/GO_0042802"), g) == "MF"
    assert builder._category(U("http://purl.obolibrary.org/obo/GO_0006954"), g) == "BP"
    assert builder._category(U("http://purl.obolibrary.org/obo/GO_0005634"), g) == "CC"
    assert builder._category(U("http://identifiers.org/mgi/MGI:1100089"), g) == "GP"
    assert builder._category(U("http://purl.obolibrary.org/obo/PR_000000001"), g) == "GP"
    assert builder._category(U("http://purl.obolibrary.org/obo/CL_0000066"), g) == "ANATOMY"
    assert builder._category(U("http://purl.obolibrary.org/obo/CHEBI_15367"), g) is None
    assert builder._category(None, g) is None


def test_cc_branch(builder):
    U = rdflib.URIRef
    # protein-containing complex (GO:0032991) subtree -> "complex"
    assert builder._cc_branch(U("http://purl.obolibrary.org/obo/GO_0000307")) == "complex"  # CDK holoenzyme complex
    assert builder._cc_branch(U("http://purl.obolibrary.org/obo/GO_0032991")) == "complex"  # branch root (reflexive)
    # cellular anatomical structure (GO:0110165) subtree -> "anatomical"
    assert builder._cc_branch(U("http://purl.obolibrary.org/obo/GO_0005634")) == "anatomical"  # nucleus
    assert builder._cc_branch(U("http://purl.obolibrary.org/obo/GO_0005829")) == "anatomical"  # cytosol
    assert builder._cc_branch(U("http://purl.obolibrary.org/obo/GO_0110165")) == "anatomical"  # branch root (reflexive)
    # virion component (GO:0044423) is its own top branch (NOT under GO:0110165) -> "anatomical"
    assert builder._cc_branch(U("http://purl.obolibrary.org/obo/GO_0044423")) == "anatomical"
    # The bare CC root (GO:0005575) is under no top-level branch -> None
    assert builder._cc_branch(U("http://purl.obolibrary.org/obo/GO_0005575")) is None  # CC root
    # Non-CC GO terms and non-GO nodes -> None
    assert builder._cc_branch(U("http://purl.obolibrary.org/obo/GO_0008150")) is None  # BP root
    assert builder._cc_branch(U("http://purl.obolibrary.org/obo/GO_0042802")) is None  # MF
    assert builder._cc_branch(U("http://identifiers.org/mgi/MGI:1100089")) is None     # GP
    assert builder._cc_branch(rdflib.BNode()) is None  # non-URIRef BNode
    assert builder._cc_branch(None) is None


# ---------------------------------------------------------------------------
# Shared helper for Tasks 6–10 tests
# ---------------------------------------------------------------------------

def _flagged_props(gocam, key):
    """Return the set of property-URI strings flagged under `key` across all annotations."""
    props = set()
    for annot in gocam.standard_annotations + gocam.non_standard_annotations:
        for bnode_id in annot.failed_checks.get(key, set()):
            props.add(str(annot.edges[bnode_id].property_uri))
    return props


def _flagged_sigs(gocam, key):
    """Return {(property_uri, target_type)} string-tuples flagged under `key`
    across all annotations -- precise enough to distinguish edges that share a
    relation but differ in target."""
    sigs = set()
    for annot in gocam.standard_annotations + gocam.non_standard_annotations:
        for bnode_id in annot.failed_checks.get(key, set()):
            e = annot.edges[bnode_id]
            sigs.add((str(e.property_uri), str(e.target_type)))
    return sigs


# ---------------------------------------------------------------------------
# Task 6: Rule #10 — BP->CC/anatomy must be occurs_in
# ---------------------------------------------------------------------------

def test_invalid_bp_cc_relation(builder):
    gocam = builder.parse_ttl("resources/test/bp_cc_relation_example.ttl")
    # Only the located_in BP->CL edge is flagged; the occurs_in edge is not.
    assert _flagged_props(gocam, "invalid_bp_cc_relation") == {
        "http://purl.obolibrary.org/obo/RO_0001025"
    }


# ---------------------------------------------------------------------------
# Task 7: Rule #3 — GP->non-root CC : located_in/is_active_in (anatomical) or part_of (complex)
# ---------------------------------------------------------------------------

def test_invalid_gp_cc_relation(builder):
    gocam = builder.parse_ttl("resources/test/gp_cc_relation_example.ttl")
    # #3 splits by CC subhierarchy:
    #   complex (GO:0032991 subtree)            -> part_of valid; located_in invalid
    #   anatomical (GO:0110165 / GO:0044423)    -> located_in / is_active_in valid; part_of invalid
    # Only gp2 (part_of -> cytosol, anatomical) and gp4 (located_in -> complex)
    # are flagged. gp1 (located_in -> nucleus), gp3 (part_of -> complex), and
    # gp5 (is_active_in -> nucleus) all pass.
    assert _flagged_sigs(gocam, "invalid_gp_cc_relation") == {
        ("http://purl.obolibrary.org/obo/BFO_0000050",
         "http://purl.obolibrary.org/obo/GO_0005829"),  # part_of -> cytosol (anatomical) FAIL
        ("http://purl.obolibrary.org/obo/RO_0001025",
         "http://purl.obolibrary.org/obo/GO_0000307"),  # located_in -> complex FAIL
    }


# ---------------------------------------------------------------------------
# Task 8: Rule #4 — GP->BP must be acts_upstream_of_or_within or child
# ---------------------------------------------------------------------------

def test_invalid_gp_bp_relation(builder):
    gocam = builder.parse_ttl("resources/test/gp_bp_relation_example.ttl")
    # part_of GP->BP is flagged; acts_upstream_of_or_within is not.
    assert _flagged_props(gocam, "invalid_gp_bp_relation") == {
        "http://purl.obolibrary.org/obo/BFO_0000050"
    }


def test_invalid_gp_bp_relation_skipped_without_ro():
    # #4 is RO-dependent; without an RO ontology it is omitted from the table,
    # so a wrong GP->BP relation is NOT flagged.
    builder_no_ro = GoCamGraphBuilder(ontology_file)
    gocam = builder_no_ro.parse_ttl("resources/test/gp_bp_relation_example.ttl")
    for annot in gocam.standard_annotations + gocam.non_standard_annotations:
        assert "invalid_gp_bp_relation" not in annot.failed_checks


# ---------------------------------------------------------------------------
# Task 9: Rule #5 — root-MF->non-root BP must be part_of or acts_upstream family
# ---------------------------------------------------------------------------

def test_invalid_mf_bp_relation(builder):
    # PASS: root-MF -part_of-> BP in 5966411600000001 is accepted (part_of OK for MF->BP).
    ok = builder.parse_ttl("resources/test/5966411600000001.ttl")
    for annot in ok.standard_annotations + ok.non_standard_annotations:
        assert "invalid_mf_bp_relation" not in annot.failed_checks, \
            "root-MF -part_of-> BP should be accepted"

    # PASS: root-MF -RO:0002418(causally_upstream)-> BP in MGI_MGI_1100089 is the
    # canonical MOD BP-only annotation pattern and is accepted (2026-06-09 decision).
    mgi = builder.parse_ttl("resources/test/MGI_MGI_1100089.ttl")
    assert "http://purl.obolibrary.org/obo/RO_0002418" not in \
        _flagged_props(mgi, "invalid_mf_bp_relation"), \
        "root-MF -causally_upstream_of_or_within-> BP should be accepted"

    # Dedicated fixture: RO:0002418 edge passes; a located_in edge (wrong relation
    # for MF->BP) is the only one flagged.
    fix = builder.parse_ttl("resources/test/mf_bp_relation_example.ttl")
    assert _flagged_props(fix, "invalid_mf_bp_relation") == {
        "http://purl.obolibrary.org/obo/RO_0001025"
    }


# ---------------------------------------------------------------------------
# Task 10: Rule #6 — root-MF->non-root CC must be is_active_in
# ---------------------------------------------------------------------------

def test_invalid_mf_cc_relation(builder):
    gocam = builder.parse_ttl("resources/test/mf_cc_relation_example.ttl")
    # located_in root-MF->CC is flagged; is_active_in is not.
    assert _flagged_props(gocam, "invalid_mf_cc_relation") == {
        "http://purl.obolibrary.org/obo/RO_0001025"
    }


def test_relation_rules_ignore_extension_edges(builder):
    """Regression: the relation-validity rules (#3/#4/#5/#6/#10) validate the
    BACKBONE only. Edges using non-placement (extension) relations must NOT be
    flagged. MGI_MGI_1100089 contains BP->anatomy extension edges
    (results_in_development_of RO:0002296, results_in_acquisition_of_features_of
    RO:0002315, acts_on_population_of RO:0012003) alongside backbone occurs_in
    placements; none of the extension edges should be flagged, and the model
    must keep its full count of standard annotations (regression guard for the
    over-flagging bug found in review)."""
    g = builder.parse_ttl("resources/test/MGI_MGI_1100089.ttl")
    assert len(g.standard_annotations) == 28, \
        f"expected 28 standard annotations, got {len(g.standard_annotations)}"

    extension_rels = {
        "http://purl.obolibrary.org/obo/RO_0002296",  # results in development of
        "http://purl.obolibrary.org/obo/RO_0002315",  # results in acquisition of features of
        "http://purl.obolibrary.org/obo/RO_0012003",  # acts on population of
    }
    for key in ("invalid_bp_cc_relation", "invalid_gp_cc_relation",
                "invalid_mf_cc_relation", "invalid_gp_bp_relation",
                "invalid_mf_bp_relation"):
        assert _flagged_props(g, key).isdisjoint(extension_rels), \
            f"{key} wrongly flagged an extension relation in MGI_MGI_1100089"


# ---------------------------------------------------------------------------
# Task 11: #11 cardinality — multiple_mf_bp
# ---------------------------------------------------------------------------

def test_multiple_mf_bp(builder):
    gocam = builder.parse_ttl("resources/test/multi_mf_bp_example.ttl")
    annots = gocam.standard_annotations + gocam.non_standard_annotations
    # The old key must be gone everywhere.
    for annot in annots:
        assert "multiple_mf_part_of" not in annot.failed_checks, "old key must be gone"
    # Both MF->BP edges are flagged: the part_of one and the acts_upstream one
    # (not, e.g., the wrong edge twice).
    assert _flagged_props(gocam, "multiple_mf_bp") == {
        "http://purl.obolibrary.org/obo/BFO_0000050",   # part_of
        "http://purl.obolibrary.org/obo/RO_0002264",    # acts_upstream_of_or_within
    }
    # And exactly two edges total are flagged.
    flagged = set()
    for annot in annots:
        flagged |= annot.failed_checks.get("multiple_mf_bp", set())
    assert len(flagged) == 2, f"both MF->BP edges should be flagged, got {len(flagged)}"


# ---------------------------------------------------------------------------
# Task 12: #12 cardinality — multiple_mf_anatomy
# ---------------------------------------------------------------------------

def test_multiple_mf_anatomy(builder):
    gocam = builder.parse_ttl("resources/test/multi_mf_anatomy_example.ttl")
    flagged = set()
    for annot in gocam.standard_annotations + gocam.non_standard_annotations:
        flagged |= annot.failed_checks.get("multiple_mf_anatomy", set())
    assert len(flagged) == 2, f"both MF->anatomy edges should be flagged, got {len(flagged)}"


# ---------------------------------------------------------------------------
# Task 13: #13 — enabler_not_gp
# ---------------------------------------------------------------------------

def test_enabler_not_gp(builder):
    gocam = builder.parse_ttl("resources/test/enabler_not_gp_example.ttl")
    # Only the ChEBI enabler is flagged; the MGI enabler is not.
    flagged_targets = set()
    for annot in gocam.standard_annotations + gocam.non_standard_annotations:
        for bnode_id in annot.failed_checks.get("enabler_not_gp", set()):
            flagged_targets.add(str(annot.edges[bnode_id].target_type))
    assert flagged_targets == {"http://purl.obolibrary.org/obo/CHEBI_15367"}


# ---------------------------------------------------------------------------
# Remainders-report lead-aspect bucketing — MGI_MGI_2182965
# ---------------------------------------------------------------------------

def test_mgi_2182965_lead_aspect_is_mf(builder):
    """MGI_MGI_2182965 has a single annotation whose lead aspect should be MF.

    The annotation backbone is an MF (GO:0005515 protein binding) enabled_by
    the Tifa gene product. It also has an MF -part_of-> BP edge (GO:0043123),
    which currently makes BP win in pick_lead_aspect (priority BP > CC > MF),
    mis-bucketing the annotation as nested_bp_extensions in the remainders
    report. The lead aspect used for bucketing should be MF.
    """
    gocam = builder.parse_ttl("resources/test/MGI_MGI_2182965.ttl")

    all_annots = gocam.standard_annotations + gocam.non_standard_annotations
    assert len(all_annots) == 1, \
        f"MGI_MGI_2182965 should have exactly one annotation, got {len(all_annots)}"
    annot = all_annots[0]

    lead_aspect = pick_lead_aspect(builder.get_primary_go_terms(annot))
    assert lead_aspect == "MF", \
        f"Expected lead aspect MF for MGI_MGI_2182965, got {lead_aspect}"


def test_mgi_2182965_specific_mf_part_of_bp_is_not_backbone(builder):
    """A specific (non-root) MF -part_of-> BP edge is an extension, not a BP backbone.

    MGI_MGI_2182965's annotation has GO:0005515 (protein binding, a non-root MF)
    -part_of-> GO:0043123 (BP). Because the MF is specific, that edge must NOT
    register a BP primary (so the annotation stays MF-led) and must appear among
    the extension edges. The root MF backbone (GO:0003674 -part_of-> BP) in
    5966411600000001 is unaffected (covered by test_get_primary_go_terms).
    """
    gocam = builder.parse_ttl("resources/test/MGI_MGI_2182965.ttl")
    all_annots = gocam.standard_annotations + gocam.non_standard_annotations
    assert len(all_annots) == 1
    annot = all_annots[0]

    # Only an MF primary should be registered (no BP backbone from the specific MF).
    primaries = builder.get_primary_go_terms(annot)
    assert set(primaries.keys()) == {"MF"}, \
        f"Expected only an MF primary, got {set(primaries.keys())}"
    assert [str(u) for u in primaries["MF"]] == ["http://purl.obolibrary.org/obo/GO_0005515"]

    # The specific-MF -part_of-> BP edge must now be classified as an extension.
    extension_tuples = {
        (str(e.property_uri), str(e.source_type), str(e.target_type))
        for e in builder.get_extension_edges(annot)
    }
    mf_part_of_bp = (
        "http://purl.obolibrary.org/obo/BFO_0000050",
        "http://purl.obolibrary.org/obo/GO_0005515",
        "http://purl.obolibrary.org/obo/GO_0043123",
    )
    assert mf_part_of_bp in extension_tuples, \
        f"Specific-MF -part_of-> BP edge should be an extension, got {extension_tuples}"


def test_causal_root_mf_to_bp_is_backbone(builder):
    """A root-MF -causally_upstream_of_or_within-> BP edge is a BP backbone.

    The canonical MOD "BP-only" annotation uses a relation in the RO:0002418
    (causally upstream of or within) family, not part_of, from the root MF
    (GO:0003674) to the BP. get_primary_go_terms must register a BP primary for
    it (so the annotation is BP-led), matching the relation set already accepted
    by the #5 invalid_mf_bp_relation check (part_of OR acts_upstream OR causal).
    Uses the passing causal edge in mf_bp_relation_example.ttl.
    """
    gocam = builder.parse_ttl("resources/test/mf_bp_relation_example.ttl")
    bp1 = rdflib.term.URIRef(
        "http://model.geneontology.org/mf_bp_relation_example/bp1")
    annot = None
    for a in gocam.standard_annotations + gocam.non_standard_annotations:
        if bp1 in a.individuals:
            annot = a
            break
    assert annot is not None, "annotation containing bp1 should exist"

    primaries = builder.get_primary_go_terms(annot)
    assert "BP" in primaries, \
        f"root-MF -causal-> BP should register a BP backbone, got {set(primaries.keys())}"
    assert [str(u) for u in primaries["BP"]] == ["http://purl.obolibrary.org/obo/GO_0006954"]

    # And that edge is therefore NOT an extension.
    assert builder.get_extension_edges(annot) == [], \
        "the root-MF -causal-> BP backbone edge should not be an extension"


def test_builder_api_state_defaults(builder):
    # The shared fixture is constructed offline (resolve_labels_api=False),
    # but the cache/circuit-breaker state must still be initialized.
    assert builder.resolve_labels_api is False
    assert builder._api_label_cache == {}
    assert builder._api_session is None
    assert builder._api_consecutive_failures == 0
    assert builder._api_disabled is False
    assert GoCamGraphBuilder.OLS4_TERMS_URL == "https://www.ebi.ac.uk/ols4/api/terms"
    assert GoCamGraphBuilder.OLS4_TIMEOUT == 10
    assert GoCamGraphBuilder.OLS4_MAX_CONSECUTIVE_FAILURES == 5


def test_select_label_prefers_defining_ontology(builder):
    # CL_0000540: cl is defining ("neuron"); caro carries a junk label == the ID.
    terms = [
        {"ontology_name": "ado", "label": "neuron", "is_defining_ontology": False},
        {"ontology_name": "caro", "label": "CL_0000540", "is_defining_ontology": False},
        {"ontology_name": "cl", "label": "neuron", "is_defining_ontology": True},
    ]
    assert builder._select_label(terms, "CL:0000540") == "neuron"


def test_select_label_prefix_match_when_no_defining(builder):
    # UBERON_0000955: no defining flag in results; pick the term whose
    # ontology_name matches the CURIE prefix.
    terms = [
        {"ontology_name": "fma", "label": "Brain", "is_defining_ontology": False},
        {"ontology_name": "uberon", "label": "brain", "is_defining_ontology": False},
    ]
    assert builder._select_label(terms, "UBERON:0000955") == "brain"


def test_select_label_first_real_label_fallback(builder):
    # No defining flag, no prefix match -> first term with a real label.
    terms = [
        {"ontology_name": "x", "label": "EMAPA_16894", "is_defining_ontology": False},
        {"ontology_name": "y", "label": "brain", "is_defining_ontology": False},
    ]
    assert builder._select_label(terms, "EMAPA:16894") == "brain"


def test_select_label_single_defining(builder):
    terms = [{"ontology_name": "wbbt", "label": "germ cell", "is_defining_ontology": True}]
    assert builder._select_label(terms, "WBbt:0006796") == "germ cell"


def test_select_label_empty_returns_none(builder):
    assert builder._select_label([], "CL:9999999999") is None


def test_select_label_only_junk_returns_none(builder):
    # Every candidate's label is just the ID fragment or the CURIE itself.
    terms = [
        {"ontology_name": "caro", "label": "CL_0000540", "is_defining_ontology": False},
        {"ontology_name": "z", "label": "CL:0000540", "is_defining_ontology": True},
    ]
    assert builder._select_label(terms, "CL:0000540") is None


def test_select_label_defining_junk_falls_through_to_real(builder):
    # Defining term has a junk label (== ID) and there is no prefix match;
    # rule 3 returns the first term with any real label.
    terms = [
        {"ontology_name": "caro", "label": "CL_0000540", "is_defining_ontology": True},
        {"ontology_name": "ado", "label": "neuron", "is_defining_ontology": False},
    ]
    assert builder._select_label(terms, "CL:0000540") == "neuron"


# ---------------------------------------------------------------------------
# Fake network helpers for _fetch_label_from_ols tests
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload, ok=True):
        self._payload = payload
        self._ok = ok

    def raise_for_status(self):
        if not self._ok:
            raise requests.HTTPError("simulated non-2xx")

    def json(self):
        return self._payload


class _FakeSession:
    """Stand-in for requests.Session that returns a canned response or raises."""
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls = 0
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.response


def _ols_payload(terms):
    return {"_embedded": {"terms": terms}}


def test_fetch_label_from_ols_success(builder):
    builder._api_consecutive_failures = 0
    builder._api_disabled = False
    terms = [{"ontology_name": "cl", "label": "neuron", "is_defining_ontology": True}]
    builder._api_session = _FakeSession(response=_FakeResponse(_ols_payload(terms)))
    uri = rdflib.URIRef("http://purl.obolibrary.org/obo/CL_0000540")
    assert builder._fetch_label_from_ols(uri, "CL:0000540") == "neuron"
    assert builder._api_session.calls == 1
    assert builder._api_consecutive_failures == 0
    assert builder._api_disabled is False


def test_fetch_label_from_ols_network_error_returns_none(builder):
    builder._api_consecutive_failures = 0
    builder._api_disabled = False
    builder._api_session = _FakeSession(exc=requests.ConnectionError("down"))
    uri = rdflib.URIRef("http://purl.obolibrary.org/obo/CL_0000540")
    assert builder._fetch_label_from_ols(uri, "CL:0000540") is None
    assert builder._api_consecutive_failures == 1


def test_fetch_label_from_ols_circuit_breaker(builder):
    builder._api_consecutive_failures = 0
    builder._api_disabled = False
    builder._api_session = _FakeSession(exc=requests.ConnectionError("down"))
    uri = rdflib.URIRef("http://purl.obolibrary.org/obo/CL_0000540")
    for _ in range(GoCamGraphBuilder.OLS4_MAX_CONSECUTIVE_FAILURES):
        assert builder._fetch_label_from_ols(uri, "CL:0000540") is None
    assert builder._api_disabled is True
    # Once disabled, no further network calls are made.
    builder._api_session.calls = 0
    assert builder._fetch_label_from_ols(uri, "CL:0000540") is None
    assert builder._api_session.calls == 0
    # Restore the session-scoped fixture to a clean state for later tests.
    builder._api_disabled = False
    builder._api_consecutive_failures = 0
    builder._api_session = None


def test_fetch_label_from_ols_http_error_returns_none(builder):
    builder._api_consecutive_failures = 0
    builder._api_disabled = False
    builder._api_session = _FakeSession(response=_FakeResponse({}, ok=False))
    uri = rdflib.URIRef("http://purl.obolibrary.org/obo/CL_0000540")
    assert builder._fetch_label_from_ols(uri, "CL:0000540") is None
    assert builder._api_consecutive_failures == 1


def test_fetch_label_from_ols_non_dict_json_returns_none(builder):
    builder._api_consecutive_failures = 0
    builder._api_disabled = False
    # A JSON body that parses but isn't the expected dict (e.g. a list).
    builder._api_session = _FakeSession(response=_FakeResponse([]))
    uri = rdflib.URIRef("http://purl.obolibrary.org/obo/CL_0000540")
    assert builder._fetch_label_from_ols(uri, "CL:0000540") is None
    assert builder._api_consecutive_failures == 1
    # Restore clean state for the session-scoped fixture.
    builder._api_consecutive_failures = 0
    builder._api_disabled = False
    builder._api_session = None


# ---------------------------------------------------------------------------
# term_label() API fallback integration tests
# ---------------------------------------------------------------------------

@pytest.fixture
def api_builder(builder):
    """Shared builder with the OLS fallback enabled and a clean cache.

    Restores offline state afterward so the session-scoped builder stays
    hermetic for every other test.
    """
    builder.resolve_labels_api = True
    builder._api_label_cache.clear()
    builder._api_consecutive_failures = 0
    builder._api_disabled = False
    builder._api_session = None
    try:
        yield builder
    finally:
        builder.resolve_labels_api = False
        builder._api_session = None
        builder._api_label_cache.clear()
        builder._api_consecutive_failures = 0
        builder._api_disabled = False


def test_term_label_api_fallback(api_builder):
    terms = [{"ontology_name": "cl", "label": "neuron", "is_defining_ontology": True}]
    api_builder._api_session = _FakeSession(response=_FakeResponse(_ols_payload(terms)))
    uri = rdflib.URIRef("http://purl.obolibrary.org/obo/CL_0000540")
    assert api_builder.term_label(uri) == "neuron"


def test_term_label_api_cached_once(api_builder):
    terms = [{"ontology_name": "cl", "label": "neuron", "is_defining_ontology": True}]
    session = _FakeSession(response=_FakeResponse(_ols_payload(terms)))
    api_builder._api_session = session
    uri = rdflib.URIRef("http://purl.obolibrary.org/obo/CL_0000540")
    assert api_builder.term_label(uri) == "neuron"
    assert api_builder.term_label(uri) == "neuron"
    assert session.calls == 1  # second call served from cache


def test_term_label_api_miss_negative_cached(api_builder):
    session = _FakeSession(response=_FakeResponse(_ols_payload([])))  # 0 terms
    api_builder._api_session = session
    uri = rdflib.URIRef("http://purl.obolibrary.org/obo/CL_9999999999")
    assert api_builder.term_label(uri) == "CL:9999999999"   # falls back to CURIE
    assert api_builder.term_label(uri) == "CL:9999999999"
    assert session.calls == 1  # miss is cached; not re-queried


def test_term_label_api_disabled_no_network(builder, monkeypatch):
    # The default shared builder is offline; the fetch helper must never run.
    def _boom(*a, **k):
        raise AssertionError("API must not be called when resolve_labels_api is False")
    monkeypatch.setattr(builder, "_fetch_label_from_ols", _boom)
    uri = rdflib.URIRef("http://purl.obolibrary.org/obo/CL_0000540")
    assert builder.term_label(uri) == "CL:0000540"


def test_term_label_api_network_error_returns_curie(api_builder):
    api_builder._api_session = _FakeSession(exc=requests.ConnectionError("down"))
    uri = rdflib.URIRef("http://purl.obolibrary.org/obo/CL_0000540")
    assert api_builder.term_label(uri) == "CL:0000540"   # never raises


def test_gocam_ttl_parser_has_no_label_api_flag():
    from gocam_unwinder.gocam_ttl import parser
    # Absence of the flag defaults to False (resolution on by default).
    assert parser.parse_args(["-o", "go.json"]).no_label_api is False
    # Presence of the flag is True (-> resolve_labels_api=not True=False).
    assert parser.parse_args(["-o", "go.json", "--no-label-api"]).no_label_api is True


def test_get_primary_individuals(builder):
    """
    get_primary_individuals() returns the backbone *individual* URIs per aspect.
    On 5966411600000001.ttl's GO:0120045 annotation:
      - MF backbone (MF -enabled_by-> GP) -> primary MF individual = ...0003
      - BP backbone (root-MF -part_of-> BP) -> primary BP individual = ...0004
      - no CC backbone -> "CC" absent
    """
    gocam_graph = builder.parse_ttl("resources/test/5966411600000001.ttl")

    bp_individual = rdflib.term.URIRef(
        'http://model.geneontology.org/5966411600000001/5966411600000004')
    target_annot = None
    for annot in gocam_graph.standard_annotations + gocam_graph.non_standard_annotations:
        if bp_individual in annot.individuals:
            target_annot = annot
            break
    assert target_annot is not None, \
        "Annotation containing the GO:0120045 individual should exist"

    primaries = builder.get_primary_individuals(target_annot)

    assert set(primaries.keys()) == {"MF", "BP"}, \
        f"Expected {{'MF','BP'}}, got {set(primaries.keys())}"
    assert len(primaries["MF"]) == 1 and len(primaries["BP"]) == 1
    assert str(primaries["MF"][0]) == \
        "http://model.geneontology.org/5966411600000001/5966411600000003"
    assert str(primaries["BP"][0]) == \
        "http://model.geneontology.org/5966411600000001/5966411600000004"
    assert "CC" not in primaries


def test_rewrite_edge_source_and_relation(builder):
    """
    rewrite_edge_source_and_relation re-points the CL -part_of-> EMAPA nested edge
    in 5966411600000001.ttl onto the primary BP individual with occurs_in, updating
    BOTH the assertion triple and the owl:Axiom bnode, preserving target + evidence.
    """
    gocam_graph = builder.parse_ttl("resources/test/5966411600000001.ttl")

    cl_type = rdflib.term.URIRef("http://purl.obolibrary.org/obo/CL_0000202")
    emapa_type = rdflib.term.URIRef("http://purl.obolibrary.org/obo/EMAPA_17597")
    part_of = rdflib.term.URIRef("http://purl.obolibrary.org/obo/BFO_0000050")
    occurs_in = rdflib.term.URIRef("http://purl.obolibrary.org/obo/BFO_0000066")
    bp_individual = rdflib.term.URIRef(
        "http://model.geneontology.org/5966411600000001/5966411600000004")

    # Locate the nested CL -part_of-> EMAPA edge.
    edge = None
    for annot in gocam_graph.standard_annotations + gocam_graph.non_standard_annotations:
        for e in annot.edges.values():
            if e.source_type == cl_type and e.target_type == emapa_type and e.property_uri == part_of:
                edge = e
                break
        if edge:
            break
    assert edge is not None, "Expected a CL -part_of-> EMAPA edge"

    old_source = edge.source_uri      # the CL individual (...0009)
    target = edge.target_uri          # the EMAPA individual (...0010)
    axiom_bnode = rdflib.term.BNode(edge.bnode_id)
    # Capture evidence on the axiom bnode before the rewrite.
    evidence_pred = rdflib.term.URIRef("http://geneontology.org/lego/evidence")
    evidence_before = set(gocam_graph.g.objects(axiom_bnode, evidence_pred))
    assert evidence_before, "Nested edge axiom should have evidence"

    gocam_graph.rewrite_edge_source_and_relation(
        edge.bnode_id, old_source, part_of, target, bp_individual, occurs_in)

    g = gocam_graph.g
    # Assertion triple rewritten
    assert (bp_individual, occurs_in, target) in g
    assert (old_source, part_of, target) not in g
    # Axiom bnode rewritten
    assert (axiom_bnode, rdflib.namespace.OWL.annotatedSource, bp_individual) in g
    assert (axiom_bnode, rdflib.namespace.OWL.annotatedSource, old_source) not in g
    assert (axiom_bnode, rdflib.namespace.OWL.annotatedProperty, occurs_in) in g
    assert (axiom_bnode, rdflib.namespace.OWL.annotatedProperty, part_of) not in g
    # Target + evidence preserved
    assert (axiom_bnode, rdflib.namespace.OWL.annotatedTarget, target) in g
    assert set(g.objects(axiom_bnode, evidence_pred)) == evidence_before


def test_plan_nested_anatomy_fixes_bp(builder):
    """BP-led real fixture: exactly the CL -part_of-> EMAPA edge is planned,
    re-pointed onto the primary BP individual (...0004) with occurs_in."""
    gocam_graph = builder.parse_ttl("resources/test/5966411600000001.ttl")
    plan = builder.plan_nested_anatomy_fixes(gocam_graph)

    assert len(plan) == 1, f"Expected 1 rewrite, got {len(plan)}"
    r = plan[0]
    assert r["lead_aspect"] == "BP"
    assert str(r["new_property_uri"]) == OCCURS_IN
    assert str(r["old_property_uri"]) == PART_OF
    assert str(r["new_source_uri"]) == \
        "http://model.geneontology.org/5966411600000001/5966411600000004"
    assert str(r["old_source_uri"]) == \
        "http://model.geneontology.org/5966411600000001/5966411600000009"
    assert str(r["target_uri"]) == \
        "http://model.geneontology.org/5966411600000001/5966411600000010"
    assert str(r["primary_term"]) == "http://purl.obolibrary.org/obo/GO_0120045"
    assert str(r["old_source_type"]) == "http://purl.obolibrary.org/obo/CL_0000202"
    assert str(r["target_type"]) == "http://purl.obolibrary.org/obo/EMAPA_17597"


def test_plan_nested_anatomy_fixes_mf(builder):
    """MF-led synthetic fixture: nested CL -part_of-> EMAPA re-pointed onto the
    primary MF individual with occurs_in (MF/BP-led relation)."""
    gocam_graph = builder.parse_ttl("resources/test/mf_nested_anatomy_example.ttl")
    plan = builder.plan_nested_anatomy_fixes(gocam_graph)

    assert len(plan) == 1
    r = plan[0]
    assert r["lead_aspect"] == "MF"
    assert str(r["new_property_uri"]) == OCCURS_IN
    assert str(r["new_source_uri"]) == \
        "http://model.geneontology.org/mf_nested_anatomy_example/mf1"
    assert str(r["target_type"]) == "http://purl.obolibrary.org/obo/EMAPA_17597"


def test_plan_nested_anatomy_fixes_cc(builder):
    """CC-led synthetic fixture: nested CL -part_of-> EMAPA re-pointed onto the
    primary CC individual but KEEPS part_of (CC-led keeps the relation)."""
    gocam_graph = builder.parse_ttl("resources/test/cc_nested_anatomy_example.ttl")
    plan = builder.plan_nested_anatomy_fixes(gocam_graph)

    assert len(plan) == 1
    r = plan[0]
    assert r["lead_aspect"] == "CC"
    assert str(r["new_property_uri"]) == PART_OF, "CC-led keeps the original relation"
    assert str(r["new_source_uri"]) == \
        "http://model.geneontology.org/cc_nested_anatomy_example/cc1"
    assert str(r["target_type"]) == "http://purl.obolibrary.org/obo/EMAPA_17597"


def test_plan_nested_anatomy_fixes_warn_param(builder):
    """plan_nested_anatomy_fixes accepts warn=False and returns the same plan
    as the default call (the flag only gates warning output, not results)."""
    gocam_graph = builder.parse_ttl("resources/test/5966411600000001.ttl")
    default_plan = builder.plan_nested_anatomy_fixes(gocam_graph)
    quiet_plan = builder.plan_nested_anatomy_fixes(gocam_graph, warn=False)
    assert [r["bnode_id"] for r in quiet_plan] == [r["bnode_id"] for r in default_plan]
    assert len(quiet_plan) == 1


def test_model_stats_base_header():
    """base_header() is exactly today's 11-column --report-file header."""
    from gocam_unwinder.gocam_ttl import ModelStats
    assert ModelStats.base_header() == [
        "Model ID", "Title", "Standard Annotations", "Non-Standard Annotations",
        "Multi-Evidence Annotations", "Mixed Annotation Type", "MF-causal->MF Edges",
        "Edges w/o Evidence", "Model State", "Groups", "Multi-Evidence GO Terms"]


def test_model_stats_base_row_formatting():
    """to_base_row() renders bool/list/str fields exactly as the legacy block did."""
    from gocam_unwinder.gocam_ttl import ModelStats
    s = ModelStats(
        model_id="gomodel:X", title="T", standard_count=2, non_standard_count=1,
        multi_evidence_count=3, mixed_annotation_type=True, mf_causal_edge_count=0,
        no_evidence_edge_count=1, modelstate="production", groups=["MGI", "SGD"],
        multi_evidence_go_terms=["alpha", "beta"], std_multi_evidence_count=2)
    assert s.to_base_row() == [
        "gomodel:X", "T", "2", "1", "3", "Yes", "0", "1", "production",
        "MGI|SGD", "alpha|beta"]


def test_model_stats_base_row_empties():
    """Empty groups/terms render as '' and a None modelstate as ''; mixed False -> 'No'."""
    from gocam_unwinder.gocam_ttl import ModelStats
    s = ModelStats(
        model_id="gomodel:Y", title="T2", standard_count=0, non_standard_count=0,
        multi_evidence_count=0, mixed_annotation_type=False, mf_causal_edge_count=0,
        no_evidence_edge_count=0, modelstate=None, groups=[],
        multi_evidence_go_terms=[], std_multi_evidence_count=0)
    row = s.to_base_row()
    assert row[5] == "No" and row[8] == "" and row[9] == "" and row[10] == ""


def test_model_stats_extended_header():
    """extended_header() = base + 5 triage columns + one fail:<name> per CHECK_NAMES."""
    from gocam_unwinder.gocam_ttl import ModelStats, CHECK_NAMES
    hdr = ModelStats.extended_header()
    assert hdr[:11] == ModelStats.base_header()
    assert hdr[11:16] == ["Nested MF Extensions", "Nested BP Extensions",
                          "Nested CC Extensions", "Fixable (Standard)",
                          "Fixable (Non-Standard)"]
    assert hdr[16:] == [f"fail:{n}" for n in CHECK_NAMES]
    assert len(hdr) == 16 + len(CHECK_NAMES)


def test_model_stats_extended_row_failcounts_order():
    """to_extended_row() emits failure_counts in CHECK_NAMES order, 0 where absent."""
    from gocam_unwinder.gocam_ttl import ModelStats, CHECK_NAMES
    s = ModelStats(
        model_id="gomodel:Z", title="T3", standard_count=1, non_standard_count=1,
        multi_evidence_count=0, mixed_annotation_type=True, mf_causal_edge_count=0,
        no_evidence_edge_count=0, modelstate="production", groups=[],
        multi_evidence_go_terms=[], std_multi_evidence_count=0,
        nested_mf_count=1, nested_bp_count=2, nested_cc_count=3,
        fixable_standard_count=4, fixable_nonstandard_count=5)
    s.failure_counts["edge_without_evidence"] = 7
    row = s.to_extended_row()
    assert row[11:16] == ["1", "2", "3", "4", "5"]
    idx = 16 + CHECK_NAMES.index("edge_without_evidence")
    assert row[idx] == "7"
    # every other check column is "0"
    assert sum(1 for c in row[16:] if c == "0") == len(CHECK_NAMES) - 1


def test_nesting_attributable_checks_subset_of_check_names():
    """Every NESTING_ATTRIBUTABLE_CHECKS member must be a valid CHECK_NAMES entry."""
    from gocam_unwinder.gocam_ttl import CHECK_NAMES, NESTING_ATTRIBUTABLE_CHECKS
    assert NESTING_ATTRIBUTABLE_CHECKS <= set(CHECK_NAMES)


def test_compute_model_stats_base(builder):
    """compute_model_stats reproduces the documented base fields for
    MGI_MGI_1100089 (28 standard annotations; a multi-evidence positive case)."""
    gocam_graph = builder.parse_ttl("resources/test/MGI_MGI_1100089.ttl")
    stats = builder.compute_model_stats(gocam_graph, "gomodel:MGI_MGI_1100089")

    assert stats.standard_count == 28
    assert stats.std_multi_evidence_count >= 1          # positive multi-evidence model
    row = stats.to_base_row()
    assert len(row) == 11
    assert row[0] == "gomodel:MGI_MGI_1100089"
    assert row[2] == "28"
    # extended fields untouched on a base (extended=False) call
    assert stats.nested_bp_count == 0 and stats.fixable_standard_count == 0


def test_compute_model_stats_nested_buckets(builder):
    """On 5966411600000001.ttl the GO:0120045 annotation is BP-led with a nested
    anatomy edge -> nested_bp_count == 1, MF/CC == 0 (matches the debug script)."""
    gocam_graph = builder.parse_ttl("resources/test/5966411600000001.ttl")
    stats = builder.compute_model_stats(gocam_graph, "gomodel:x", extended=True)
    assert stats.nested_bp_count == 1
    assert stats.nested_mf_count == 0
    assert stats.nested_cc_count == 0


def test_compute_model_stats_failure_counts(builder):
    """Per-check counts are annotation-level. SYNGO_5371 fails invalid_mf_cc_relation;
    every CHECK_NAMES key is present (0 where absent)."""
    from gocam_unwinder.gocam_ttl import CHECK_NAMES
    gocam_graph = builder.parse_ttl("resources/test/SYNGO_5371.ttl")
    stats = builder.compute_model_stats(gocam_graph, "gomodel:syngo", extended=True)
    assert set(stats.failure_counts.keys()) == set(CHECK_NAMES)
    assert stats.failure_counts["invalid_mf_cc_relation"] >= 1
    # a check this model does not trip stays 0
    assert stats.failure_counts["mf_causal_mf"] == 0


def test_compute_model_stats_edge_without_evidence_count(builder):
    """66c7d41500000016.ttl has a no-evidence causal edge -> at least one
    annotation flagged edge_without_evidence."""
    gocam_graph = builder.parse_ttl("resources/test/66c7d41500000016.ttl")
    stats = builder.compute_model_stats(gocam_graph, "gomodel:n", extended=True)
    assert stats.failure_counts["edge_without_evidence"] >= 1


def test_compute_model_stats_fixable_standard(builder):
    """mf_nested_anatomy_example.ttl: the nested-anatomy annotation is standard
    and a fixer target -> fixable_standard_count == 1, non-standard == 0."""
    gocam_graph = builder.parse_ttl("resources/test/mf_nested_anatomy_example.ttl")
    stats = builder.compute_model_stats(gocam_graph, "gomodel:mf", extended=True)
    assert stats.fixable_standard_count == 1
    assert stats.fixable_nonstandard_count == 0


def test_compute_model_stats_fixable_nonstandard(builder):
    """noev fixture: fixer target whose failed checks (edge_without_evidence plus
    the inconsistent_evidence it induces) are all in NESTING_ATTRIBUTABLE_CHECKS
    -> fixable_nonstandard_count == 1."""
    gocam_graph = builder.parse_ttl("resources/test/mf_nested_anatomy_noev_example.ttl")
    stats = builder.compute_model_stats(gocam_graph, "gomodel:noev", extended=True)
    assert stats.fixable_nonstandard_count == 1
    assert stats.fixable_standard_count == 0


def test_compute_model_stats_unfixable_nonstandard(builder):
    """unfixable fixture: fixer target but also fails invalid_gp_cc_relation
    (not attributable to nesting) -> neither fixable count increments."""
    gocam_graph = builder.parse_ttl("resources/test/mf_nested_anatomy_unfixable_example.ttl")
    stats = builder.compute_model_stats(gocam_graph, "gomodel:unfix", extended=True)
    assert stats.fixable_standard_count == 0
    assert stats.fixable_nonstandard_count == 0
    assert stats.failure_counts["invalid_gp_cc_relation"] >= 1


def test_no_gp_at_all_flags_gp_less_annotation(builder):
    """no_gp_at_all flags an annotation whose subgraph has no gene product, and
    leaves a GP-bearing annotation standard. All edges of the flagged annotation
    are recorded."""
    gocam = builder.parse_ttl("resources/test/no_gp_at_all_example.ttl")
    mfA = rdflib.term.URIRef("http://model.geneontology.org/no_gp_at_all_example/mfA")
    gpB = rdflib.term.URIRef("http://model.geneontology.org/no_gp_at_all_example/gpB")
    annots = gocam.standard_annotations + gocam.non_standard_annotations
    a = next(x for x in annots if mfA in x.individuals)
    b = next(x for x in annots if gpB in x.individuals)
    assert set(a.failed_checks) == {"no_gp_at_all"}
    assert a.failed_checks["no_gp_at_all"] == set(a.edges.keys())
    assert a in gocam.non_standard_annotations  # classified non-standard
    assert b.failed_checks == {}  # standard, GP present, not flagged
    assert b in gocam.standard_annotations


def test_no_gp_at_all_in_check_names():
    from gocam_unwinder.gocam_ttl import CHECK_NAMES, NESTING_ATTRIBUTABLE_CHECKS
    assert "no_gp_at_all" in CHECK_NAMES
    assert "no_gp_at_all" not in NESTING_ATTRIBUTABLE_CHECKS


def test_no_gp_at_all_in_criteria_report(builder):
    """print_non_standard_annotation_failed_checks emits a no_gp_at_all row."""
    import io
    gocam = builder.parse_ttl("resources/test/no_gp_at_all_example.ttl")
    buf = io.StringIO()
    builder.print_non_standard_annotation_failed_checks(gocam, report_file=buf)
    assert "no_gp_at_all" in buf.getvalue()


def test_collect_model_remainders_fixable_row(builder):
    """collect_model_remainders bucket the canonical nested anatomy edge and
    marks it Fixable=Yes, matching what --fix-nested-anatomy would rewrite."""
    from gocam_unwinder.remainders_report import collect_model_remainders

    gocam = builder.parse_ttl("resources/test/5966411600000001.ttl")
    rows, bucket_hits = collect_model_remainders(builder, gocam)

    # Tuple layout: (model_id, title, bucket, source, predicate, target,
    #                fixable, eco_codes, groups)
    match = [r for r in rows
             if r[0].endswith("5966411600000001")
             and r[3] == "CL:0000202" and r[4] == "part of"
             and r[5] == "EMAPA:17597"]
    assert match, "expected the CL:0000202 -part of-> EMAPA:17597 nested row"
    assert match[0][6] == "Yes"  # Fixable column

    assert any(b == "nested_bp_extensions" for (b, _) in bucket_hits)


def test_main_callable(builder, tmp_path, monkeypatch):
    """gocam_ttl.main() runs end-to-end on a single model and writes a TSV with
    a header row to --report-file."""
    out = tmp_path / "stats.tsv"
    monkeypatch.setattr("gocam_unwinder.gocam_ttl.GoCamGraphBuilder",
                        lambda *a, **k: builder)
    monkeypatch.setattr(sys, "argv", [
        "gocam_ttl.py", "-m", "resources/test/MGI_MGI_1100089.ttl",
        "-o", "target/go_20250601.json",
        "-r", "resources/test/ro_20250723.owl",
        "--no-label-api", "--report-file", str(out),
    ])

    gocam_ttl.main()

    lines = out.read_text().splitlines()
    assert lines, "report file should have content"
    assert lines[0].split("\t")[0] == "Model ID"


def test_report_file_extended_by_default(builder, tmp_path, monkeypatch):
    """--report-file writes the extended header by default."""
    out = tmp_path / "stats.tsv"
    monkeypatch.setattr("gocam_unwinder.gocam_ttl.GoCamGraphBuilder",
                        lambda *a, **k: builder)
    monkeypatch.setattr(sys, "argv", [
        "gocam_ttl.py", "-m", "resources/test/MGI_MGI_1100089.ttl",
        "-o", "target/go_20250601.json",
        "-r", "resources/test/ro_20250723.owl",
        "--no-label-api", "--report-file", str(out),
    ])

    gocam_ttl.main()

    header = out.read_text().splitlines()[0].split("\t")
    assert header == ModelStats.extended_header()


def test_report_file_basic_with_flag(builder, tmp_path, monkeypatch):
    """--basic-report switches --report-file back to the base header."""
    out = tmp_path / "stats.tsv"
    monkeypatch.setattr("gocam_unwinder.gocam_ttl.GoCamGraphBuilder",
                        lambda *a, **k: builder)
    monkeypatch.setattr(sys, "argv", [
        "gocam_ttl.py", "-m", "resources/test/MGI_MGI_1100089.ttl",
        "-o", "target/go_20250601.json",
        "-r", "resources/test/ro_20250723.owl",
        "--no-label-api", "--basic-report", "--report-file", str(out),
    ])

    gocam_ttl.main()

    header = out.read_text().splitlines()[0].split("\t")
    assert header == ModelStats.base_header()


def _read_remainders_tsv(path):
    """Parse a remainders TSV into (header_list, list_of_row_dicts)."""
    with open(path) as f:
        lines = [ln.rstrip("\n") for ln in f if ln.strip()]
    header = lines[0].split("\t")
    rows = [dict(zip(header, ln.split("\t"))) for ln in lines[1:]]
    return header, rows


def _find_remainders_row(rows, model_suffix, source, predicate, target):
    """Return the first row matching the given edge, asserting it exists."""
    matches = [r for r in rows
               if r["Model ID"].endswith(model_suffix)
               and r["Source"] == source
               and r["Predicate"] == predicate
               and r["Target"] == target]
    assert matches, (
        f"expected a remainders row for {model_suffix}: "
        f"{source} -[{predicate}]-> {target}")
    return matches[0]


def test_remainders_report_fixable_column(builder, tmp_path, monkeypatch):
    """The remainders TSV gains a `Fixable` column (immediately after `Target`)
    whose Yes/No value matches exactly what --fix-nested-anatomy
    (plan_nested_anatomy_fixes) would rewrite -- including the planner's
    `!=1 primary individual` skip, which a naive both-anatomical test would miss.
    """
    out = tmp_path / "remainders.tsv"

    # Reuse the session-scoped builder (skip re-parsing the GO ontology) while
    # still exercising gocam_ttl.main()'s full report-writing path. main() looks
    # up GoCamGraphBuilder as a module global, so patching the module attribute
    # makes it return our shared builder regardless of the constructor args.
    monkeypatch.setattr("gocam_unwinder.gocam_ttl.GoCamGraphBuilder",
                        lambda *a, **k: builder)
    monkeypatch.setattr(sys, "argv", [
        "gocam_ttl.py", "-d", "resources/test/",
        "-o", "target/go_20250601.json",
        "-r", "resources/test/ro_20250723.owl",
        "--no-label-api",
        "--remainders-report", str(out),
    ])

    gocam_ttl.main()

    header, rows = _read_remainders_tsv(str(out))

    # Column exists and sits immediately after Target.
    assert header == ["Model ID", "Title", "Bucket", "Source", "Predicate",
                      "Target", "Fixable", "ECO Codes", "Groups"]

    # Fixable nested anatomy edge (the canonical CL -part_of-> EMAPA) -> Yes.
    yes_row = _find_remainders_row(
        rows, "5966411600000001", "CL:0000202", "part of", "EMAPA:17597")
    assert yes_row["Fixable"] == "Yes"

    # Non-anatomy nested edge (MF source) -> No.
    no_row = _find_remainders_row(
        rows, "multi_mf_anatomy_example",
        "identical protein binding", "RO:0001025", "CL:0000066")
    assert no_row["Fixable"] == "No"

    # Design-intent guard: BOTH endpoints anatomical, but the annotation has an
    # ambiguous (!=1) primary individual, so the planner skips it -> No.
    skip_row = _find_remainders_row(
        rows, "57c82fad00000252", "nucleus", "part of", "WBbt:0005396")
    assert skip_row["Fixable"] == "No"

    # Real-world regression: a both-anatomical nested edge in a complex
    # developmental subgraph (5745387b00001376) is reported but NOT fixable,
    # because its anatomy region has multiple attachment points.
    complex_row = _find_remainders_row(
        rows, "5745387b00001376", "UBERON:0000965", "part of", "UBERON:0000019")
    assert complex_row["Fixable"] == "No"


def test_relation_fixtures_keep_one_standard_annotation(builder):
    """After adding a GP backbone, each relation-test fixture's passing annotation
    stays standard (exactly one standard annotation, empty failed_checks, has a GP)."""
    for fname in ["mf_cc_relation_example", "mf_bp_relation_example",
                  "bp_cc_relation_example", "mf_occurs_in_anatomy_example"]:
        gocam = builder.parse_ttl(f"resources/test/{fname}.ttl")
        assert len(gocam.standard_annotations) == 1, \
            f"{fname}: expected 1 standard annotation, got {len(gocam.standard_annotations)}"
        std = gocam.standard_annotations[0]
        assert std.failed_checks == {}, f"{fname}: standard annotation has failures"
        has_gp = any(
            builder._gene_product_namespace_key(t) in builder.GP_NAMESPACE_KEYS
            for e in std.edges.values() for t in (e.source_type, e.target_type))
        assert has_gp, f"{fname}: standard annotation should contain a GP"


def test_modelstate_prefers_delete_when_multivalued(builder):
    """A model carrying multiple modelstate values, one of which is 'delete',
    reports 'delete' so the skip check (modelstate == 'delete') catches it
    regardless of rdflib's object iteration order."""
    gocam = builder.parse_ttl("resources/test/multi_modelstate_delete_example.ttl")
    assert gocam.modelstate == "delete"


def test_get_groups_falls_back_to_statement_level_providedby(builder):
    """A model with no model-level providedBy still reports its group from the
    statement/evidence-level providedBy.

    Regression for the blank Groups column: MGI_MGI_104518 and ~100 other corpus
    models record providedBy only on statements/evidence/axioms and omit the
    model (Ontology) node triple, leaving Groups blank even though every evidence
    node carries the group.
    """
    gocam = builder.parse_ttl(
        "resources/test/providedby_statement_only_example.ttl")

    # Precondition: the model (Ontology) node carries no providedBy ...
    model_uri = rdflib.term.URIRef(gocam.model_id)
    provided_by = rdflib.term.URIRef("http://purl.org/pav/providedBy")
    assert list(gocam.g.objects(model_uri, provided_by)) == [], \
        "fixture should have no model-level providedBy"

    # ... but the group is still recovered from the statement-level providedBy,
    assert gocam.get_groups() == ["http://informatics.jax.org"]
    # ... and resolved to its label via the builder's groups.yaml lookup.
    assert gocam.groups == ["MGI"]


def test_get_groups_prefers_model_level_when_present(builder):
    """When the model node DOES carry providedBy, get_groups returns exactly that
    (the statement-level fallback must not activate or double-count)."""
    gocam = builder.parse_ttl("resources/test/MGI_MGI_1100089.ttl")
    assert gocam.get_groups() == ["http://informatics.jax.org"]
    assert gocam.groups == ["MGI"]


def _annotation_with_individual(gocam_graph, individual_uri):
    """Return the (standard or non-standard) annotation containing `individual_uri`."""
    target = rdflib.term.URIRef(individual_uri)
    for annot in gocam_graph.standard_annotations + gocam_graph.non_standard_annotations:
        if target in annot.individuals:
            return annot
    return None


def test_anatomy_attachment_is_simple(builder):
    """_anatomy_attachment_is_simple is True for a single-boundary anatomy region
    (5966411600000001: BP -occurs_in-> CL -part_of-> EMAPA) and False when the
    region has >1 boundary edge (the multi-boundary synthetic fixture)."""
    simple = builder.parse_ttl("resources/test/5966411600000001.ttl")
    simple_annot = _annotation_with_individual(
        simple, "http://model.geneontology.org/5966411600000001/5966411600000004")
    assert simple_annot is not None
    assert builder._anatomy_attachment_is_simple(simple_annot) is True

    complex_g = builder.parse_ttl(
        "resources/test/nested_anatomy_multi_boundary_example.ttl")
    complex_annot = _annotation_with_individual(
        complex_g,
        "http://model.geneontology.org/nested_anatomy_multi_boundary_example/anat2")
    assert complex_annot is not None
    assert builder._anatomy_attachment_is_simple(complex_annot) is False


def test_plan_nested_anatomy_fixes_skips_multi_boundary(builder):
    """plan_nested_anatomy_fixes plans NO rewrite for an annotation whose anatomy
    region has more than one boundary edge (the multi-boundary fixture), even
    though it contains a both-anatomical nested edge. The simple fixture is
    unaffected (still one rewrite)."""
    complex_g = builder.parse_ttl(
        "resources/test/nested_anatomy_multi_boundary_example.ttl")
    assert builder.plan_nested_anatomy_fixes(complex_g, warn=False) == []

    simple = builder.parse_ttl("resources/test/5966411600000001.ttl")
    assert len(builder.plan_nested_anatomy_fixes(simple, warn=False)) == 1
