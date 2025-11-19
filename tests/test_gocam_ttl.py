import pytest
import rdflib
from gocam_unwinder.gocam_ttl import GoCamGraph, GoCamGraphBuilder

ontology_file = "target/go_20250601.json"  # TODO: Make this GitHub-friendly, maybe LFS

def test_gocam_ttl():
    builder = GoCamGraphBuilder(ontology_file)
    gocam_graph = builder.parse_ttl("resources/test/SGD_S000004491.ttl")
    evidence_triples = list(gocam_graph.evidence_triples())
    assert gocam_graph is not None
    assert len(evidence_triples) == 15

    input_gene = rdflib.term.URIRef('http://model.geneontology.org/SGD_S000004491/f75adc43-3c09-4a9a-b730-3e52b967a60f')
    std_annot = gocam_graph.get_standard_annotation_by_individual(input_gene)
    assert len(std_annot.edges) == 3
    gocam_graph.split_evidence_and_write_ttl("target/SGD_S000004491_test.ttl")

    gocam_graph = builder.parse_ttl("resources/test/MGI_MGI_1335098.ttl")  # has occurs_in extension
    enabler_gene = rdflib.term.URIRef('http://model.geneontology.org/MGI_MGI_1335098/ddd9e2b1-6c95-48ec-be4f-47d7daa1d19a')
    std_annot = gocam_graph.get_standard_annotation_by_individual(enabler_gene)
    assert len(gocam_graph.standard_annotations) == 34

    gocam_graph = builder.parse_ttl("resources/test/R-HSA-9937080.ttl")  # Reactome
    assert len(gocam_graph.standard_annotations) == 0
    assert len(gocam_graph.non_standard_annotations) == 1

    gocam_graph = builder.parse_ttl("resources/test/SYNGO_5371.ttl")  # SynGO
    assert len(gocam_graph.standard_annotations) == 1
    assert len(gocam_graph.non_standard_annotations) == 0

    gocam_graph = builder.parse_ttl("resources/test/MGI_MGI_1100089.ttl")  # Tnfsf11
    test_individual = rdflib.term.URIRef(
        'http://model.geneontology.org/MGI_MGI_1100089/95094c4f-335a-4e08-9840-b42760a96357')
    std_annot = gocam_graph.get_standard_annotation_by_individual(test_individual)
    assert len(std_annot.edges) == 3
    assert len(gocam_graph.standard_annotations) == 29
    assert len(gocam_graph.non_standard_annotations) == 0

    gocam_graph = builder.parse_ttl("resources/test/61452e3d00000323.ttl")  # MAL loci in Saccharomyces
    test_individual = rdflib.term.URIRef('http://model.geneontology.org/61452e3d00000323/61452e3d00000330')
    std_annot = gocam_graph.get_standard_annotation_by_individual(test_individual)
    assert len(std_annot.edges) == 3
    assert len(gocam_graph.standard_annotations) == 10
    assert len(gocam_graph.non_standard_annotations) == 0
    gocam_graph.split_evidence_and_write_ttl("target/61452e3d00000323.ttl")


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

    # After splitting, we should have more annotations (2 annotations per original multi-evidence annotation)
    # Original has 29 standard annotations, at least one with multi-evidence across 2 edges
    # So we expect at least 30 standard annotations after splitting
    assert len(gocam_graph_split.standard_annotations) >= 30

    # Find the split annotations - look for the original individual and the -2 suffix version
    original_annot = gocam_graph_split.get_standard_annotation_by_individual(test_individual)
    split_individual = rdflib.term.URIRef(str(test_individual) + "-2")
    split_annot = gocam_graph_split.get_standard_annotation_by_individual(split_individual)

    # Both should exist
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
