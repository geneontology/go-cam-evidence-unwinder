import pytest
import rdflib
from gocam_unwinder.gocam_ttl import GoCamGraph, GoCamGraphBuilder

ontology_file = "resources/test/go_20250601.json"  # TODO: Make this GitHub-friendly, maybe LFS

def test_gocam_ttl():
    builder = GoCamGraphBuilder(ontology_file)
    gocam_graph = builder.parse_ttl("resources/test/SGD_S000004491.ttl")
    evidence_triples = list(gocam_graph.evidence_triples())
    assert gocam_graph is not None
    assert len(evidence_triples) == 17

    input_gene = rdflib.term.URIRef('http://model.geneontology.org/SGD_S000004491/f75adc43-3c09-4a9a-b730-3e52b967a60f')
    std_annot = gocam_graph.get_standard_annotation_by_individual(input_gene)
    assert len(std_annot.edges) == 3

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
