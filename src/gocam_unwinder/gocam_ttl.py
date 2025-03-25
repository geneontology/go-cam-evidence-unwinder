import argparse
import os
import rdflib
from ontobio.rdfgen import relations


parser = argparse.ArgumentParser()
parser.add_argument('-m', '--model_filename', help="Directory containing GO-CAM models")
parser.add_argument('-d', '--models_folder', help="Directory containing GO-CAM models")
parser.add_argument('-l', '--pathway_id_list', help="Only load and search TTL files matching this list")

GOCAM_RELATIONS = [str(r) for r in relations.__relation_label_lookup.values()]


class StandardAnnotationEdge:
    def __init__(self, bnode: rdflib.term.BNode, source_uri: rdflib.term.URIRef, target_uri: rdflib.term.URIRef, property_uri: rdflib.term.URIRef):
        self.bnode_id = str(bnode)
        self.bnode = bnode
        self.source_uri = source_uri
        self.property_uri = property_uri
        self.target_uri = target_uri
        self.source_type = None
        self.target_type = None
        self.evidence_uris = []


class StandardAnnotation:
    def __init__(self):
        self.edges = {}  # keyed by bnodeID
        self.individuals = set()

    def add_edge(self, edge: StandardAnnotationEdge):
        self.edges[edge.bnode_id] = edge
        self.individuals.add(edge.source_uri)
        self.individuals.add(edge.target_uri)

    def get_evidence_uris(self):
        evidence_uris = set()
        for edge in self.edges.values():
            [evidence_uris.add(ev) for ev in edge.evidence_uris]
        return evidence_uris

    def __str__(self):
        if self.edges:
            edge_classes = set()
            for bnode_id, e in self.edges.items():
                edge_classes.add(e.target_type)
            return " ".join(edge_classes)
        else:
            super()


class GoCamGraph:
    def __init__(self):
        self.g = rdflib.graph.Graph()
        self.edges = []
        self.standard_annotations = []
        self.title = None

    @classmethod
    def parse_ttl(GoCamGraph, ttl_filename):
        gocam = GoCamGraph()
        gocam.g.parse(ttl_filename, format="ttl")
        gocam.standard_annotations = []
        gocam.extract_standard_annotations()
        return gocam

    def evidence_triples(self):
        evidence_rel = rdflib.URIRef("http://geneontology.org/lego/evidence")
        for triple in self.g.triples((None, evidence_rel, None)):
            if isinstance(triple[0], rdflib.term.BNode):
                yield triple

    def get_individual_type(self, individual_uri):
        for obj in self.g.objects(individual_uri, rdflib.RDF.type):
            if obj != rdflib.namespace.OWL.NamedIndividual:
                return obj

    def get_standard_annotation_by_bnode_id(self, bnode_id):
        # Iterate all standard_annotations and their edges until an edge has bnode_id
        for sa in self.standard_annotations:
            if bnode_id in sa.edges:
                return sa

    def get_standard_annotation_by_individual(self, individual_uri):
        for sa in self.standard_annotations:
            if individual_uri in sa.individuals:
                return sa

    def get_edge_by_bnode_id(self, bnode_id):
        for e in self.edges:
            if e.bnode_id == bnode_id:
                return e

    def find_axiom_bits(self, bnode_id):
        source_id = list(self.g.objects(bnode_id, rdflib.namespace.OWL.annotatedSource))[0]
        target_id = list(self.g.objects(bnode_id, rdflib.namespace.OWL.annotatedTarget))[0]
        relation = list(self.g.objects(bnode_id, rdflib.namespace.OWL.annotatedProperty))[0]
        return source_id, target_id, relation

    def find_axiom_bnode_by_triple(self, source_id, relation, target_id):
        for bnode in self.g.subjects(rdflib.namespace.OWL.annotatedSource, source_id):
            if list(self.g.objects(bnode, rdflib.namespace.OWL.annotatedTarget))[0] == target_id and list(self.g.objects(bnode, rdflib.namespace.OWL.annotatedProperty))[0] == relation:
                return bnode

    def extract_edges(self):
        ets = list(self.evidence_triples())
        for triple in ets:
            bnode = triple[0]
            bnode_id = str(bnode)

            source_id, target_id, relation = self.find_axiom_bits(bnode)
            edge = self.get_edge_by_bnode_id(bnode_id)
            if edge is None:
                edge = StandardAnnotationEdge(bnode, source_id, target_id, relation)
                self.edges.append(edge)
            evidence_id = triple[2]
            edge.evidence_uris.append(evidence_id)
        return self.edges

    def extract_standard_annotations(self):
        edges = self.extract_edges()
        # Process all edges first to identify connected components
        edge_to_annotation = {}  # Map to track which annotation each edge belongs to
        individual_to_annotation = {}  # Map to track which annotation each individual belongs to

        for edge in edges:
            edge.source_type = self.get_individual_type(edge.source_uri)
            edge.target_type = self.get_individual_type(edge.target_uri)

            source_annot = individual_to_annotation.get(edge.source_uri)
            target_annot = individual_to_annotation.get(edge.target_uri)

            if source_annot is None and target_annot is None:
                # Create new annotation if neither individual belongs to one
                new_annot = StandardAnnotation()
                self.standard_annotations.append(new_annot)
                new_annot.add_edge(edge)
                individual_to_annotation[edge.source_uri] = new_annot
                individual_to_annotation[edge.target_uri] = new_annot
                edge_to_annotation[edge.bnode_id] = new_annot
            elif source_annot is not None and target_annot is None:
                # Add to source's annotation
                source_annot.add_edge(edge)
                individual_to_annotation[edge.target_uri] = source_annot
                edge_to_annotation[edge.bnode_id] = source_annot
            elif source_annot is None and target_annot is not None:
                # Add to target's annotation
                target_annot.add_edge(edge)
                individual_to_annotation[edge.source_uri] = target_annot
                edge_to_annotation[edge.bnode_id] = target_annot
            elif source_annot is target_annot:
                # Both already in same annotation
                source_annot.add_edge(edge)
                edge_to_annotation[edge.bnode_id] = source_annot
            else:
                # Both individuals belong to different annotations - merge them
                # Keep source_annot, remove target_annot
                for ind in list(target_annot.individuals):
                    individual_to_annotation[ind] = source_annot

                # Move all edges from target_annot to source_annot
                for edge_id, edge_obj in target_annot.edges.items():
                    source_annot.add_edge(edge_obj)
                    edge_to_annotation[edge_id] = source_annot

                # Remove target_annot from the list
                self.standard_annotations.remove(target_annot)

                # Add the current edge
                source_annot.add_edge(edge)
                edge_to_annotation[edge.bnode_id] = source_annot

        # Now process related edges while maintaining annotation integrity
        for edge in edges:
            annot = edge_to_annotation[edge.bnode_id]
            for related_edge in self.find_related_edges(edge):
                annot.add_edge(related_edge)
                edge_to_annotation[related_edge.bnode_id] = annot
                individual_to_annotation[related_edge.source_uri] = annot
                individual_to_annotation[related_edge.target_uri] = annot

    # Recursive function to find all edges that are part of the same StandardAnnotation
    def find_related_edges(self, edge: StandardAnnotationEdge, visited_bnodes=None):
        if visited_bnodes is None:
            visited_bnodes = set()
        if edge.bnode_id in visited_bnodes:
            # Skip if we've already visited this edge
            return []
        visited_bnodes.add(edge.bnode_id)

        related_edges = []
        source_type = self.get_individual_type(edge.target_uri)
        for pred, obj in self.g.predicate_objects(edge.target_uri):
            if str(pred) not in GOCAM_RELATIONS:
                continue
            bnode = self.find_axiom_bnode_by_triple(edge.target_uri, pred, obj)
            next_edge = StandardAnnotationEdge(bnode, edge.target_uri, obj, pred)
            next_edge.source_type = source_type
            target_type = self.get_individual_type(obj)
            next_edge.target_type = target_type
            related_edges.append(next_edge)
            related_edges.extend(self.find_related_edges(next_edge, visited_bnodes))
        return related_edges

    def is_actually_std_annot(self, sa: StandardAnnotation):
        return len(sa.edges) > 0


if __name__ == "__main__":
    args = parser.parse_args()

    model_files = []
    if args.model_filename:
        model_files.append(args.model_filename)
    elif args.models_folder:
        for f in os.listdir(args.models_folder):
            if f.endswith(".ttl"):
                model_files.append(os.path.join(args.models_folder, f))

    for f in model_files:
        gocam_graph = GoCamGraph.parse_ttl(f)
        print("\t".join([f, str(len(gocam_graph.standard_annotations))]))
        # for sa in gocam_graph.standard_annotations:
        #     print("\t".join([str(sa), str(len(sa.get_evidence_uris()))]))
    # gocam_graph = GoCamGraph.parse_ttl(args.model_filename)
    # print("\t".join([args.model_filename, str(len(gocam_graph.standard_annotations))]))
    # for sa in gocam_graph.standard_annotations:
    #     print("\t".join([str(sa), str(len(sa.get_evidence_uris()))]))
    x = 4