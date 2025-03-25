import pytest
import rdflib
from gocam_unwinder.gocam_ttl import GoCamGraph

def test_gocam_ttl():
    gocam_graph = GoCamGraph.parse_ttl("resources/test/SGD_S000004491.ttl")
    evidence_triples = list(gocam_graph.evidence_triples())
    assert gocam_graph is not None
    assert len(evidence_triples) == 17

    input_gene = rdflib.term.URIRef('http://model.geneontology.org/SGD_S000004491/f75adc43-3c09-4a9a-b730-3e52b967a60f')
    std_annot = gocam_graph.get_standard_annotation_by_individual(input_gene)
    assert len(std_annot.edges) == 3


    assert True == True

    gocam_graph = GoCamGraph.parse_ttl("resources/test/MGI_MGI_1335098.ttl")  # has occurs_in extension