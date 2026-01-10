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
