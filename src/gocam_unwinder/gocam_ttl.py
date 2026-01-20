import argparse
import os
import sys

import ontobio.util.go_utils
import rdflib
from ontobio.rdfgen import relations
from rdflib import URIRef
from prefixcommons import curie_util
from typing import List

parser = argparse.ArgumentParser()
parser.add_argument('-m', '--model_filename', help="Single GO-CAM model file to process")
parser.add_argument('-d', '--models_folder', help="Directory containing GO-CAM model files")
parser.add_argument('-l', '--pathway_id_list', help="File containing list of model IDs (one per line) to filter processing")
parser.add_argument('-o', '--ontology_filename', help="GO ontology filename (JSON format)")
parser.add_argument('-r', '--ro_filename', help="RO ontology filename (OWL format) for causal relation hierarchy")
parser.add_argument('--split-evidence', action='store_true', help="Split multi-evidence edges into separate edges")
parser.add_argument('--output-dir', help="Output directory for split evidence files")
parser.add_argument('--report-file', help="Output file for statistics report (TSV format). If not specified, output goes to stdout.")
parser.add_argument('--criteria-fail-report', help="Output file for standard annotation criteria failure report (TSV format).")
parser.add_argument('--skip-prefix', action='append', dest='skip_prefixes', metavar='PREFIX',
                    help="Skip files starting with PREFIX (can be specified multiple times, e.g., --skip-prefix SYNGO --skip-prefix R-HSA)")

GOCAM_RELATIONS = [str(r) for r in relations.__relation_label_lookup.values()]


def get_relation_descendants(ro_graph: rdflib.Graph, root_relation_uri: str) -> set:
    """
    Extract all descendants of a given relation from an RO ontology graph.

    Uses rdfs:subPropertyOf to traverse the relation hierarchy and find all
    relations that are descendants of the specified root relation.

    Args:
        ro_graph: Parsed RO ontology as an rdflib.Graph
        root_relation_uri: URI of the root relation to find descendants of

    Returns:
        Set of URIs (as strings) including the root and all its descendants
    """
    from collections import deque

    root_uri = rdflib.URIRef(root_relation_uri)
    subprop_of = rdflib.RDFS.subPropertyOf

    # Include the root itself
    descendants = {root_relation_uri}

    # BFS to find all descendants
    queue = deque([root_uri])
    while queue:
        current = queue.popleft()
        # Find all properties that have current as their superProperty
        for child in ro_graph.subjects(subprop_of, current):
            child_str = str(child)
            if child_str not in descendants:
                descendants.add(child_str)
                queue.append(child)

    return descendants


class StandardAnnotationEdge:
    def __init__(self, bnode: rdflib.term.BNode, source_uri: rdflib.term.URIRef, target_uri: rdflib.term.URIRef,
                 property_uri: rdflib.term.URIRef,
                 # contributors: List[rdflib.term.URIRef], date: str,
                 # provided_by: rdflib.term.URIRef, created: str = None, date_accepted: str = None
                 ):
        self.bnode_id = str(bnode)
        self.bnode = bnode
        self.source_uri = source_uri
        self.property_uri = property_uri
        self.target_uri = target_uri
        self.source_type = None
        self.target_type = None
        self.evidence_uris = []
        # self.contributors = contributors
        # self.date = date
        # self.created = created
        # self.date_accepted = date_accepted
        # self.provided_by = provided_by


class StandardAnnotation:
    def __init__(self):
        self.edges = {}  # keyed by bnodeID
        self.individuals = set()
        self.failed_checks = None

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
                edge_classes.add(e.source_type)
                edge_classes.add(e.target_type)
            return " ".join(edge_classes)
        else:
            super()


class GoCamGraph:
    PREDICATES_TO_COPY = [rdflib.RDF.type,
                          rdflib.namespace.DC.contributor,
                          rdflib.namespace.DC.date,
                          rdflib.URIRef("http://purl.org/dc/terms/created"),
                          rdflib.URIRef("http://purl.org/dc/terms/dateAccepted"),
                          rdflib.URIRef("http://purl.org/pav/providedBy"),
                          rdflib.RDFS.comment]

    def __init__(self):
        self.g = rdflib.graph.Graph()
        self.edges = []
        self.standard_annotations = []
        self.non_standard_annotations = []
        self.model_id = None
        self.title = None
        self.individual_to_annotation = {}

    def write_ttl(self, filename):
        self.g.serialize(destination=filename, format='ttl')

    def get_evidence_metadata(self, evidence_uri):
        """
        Extract metadata for an evidence individual as a hashable tuple.
        This creates a signature that can identify equivalent evidence across edges.
        """
        metadata = []

        # Collect predicates defined in PREDICATES_TO_COPY
        for pred in self.PREDICATES_TO_COPY:
            values = sorted([str(obj) for obj in self.g.objects(evidence_uri, pred)])
            if values:
                metadata.append((str(pred), tuple(values)))

        # Also include evidence-with predicate
        evidence_with_pred = rdflib.URIRef("http://geneontology.org/lego/evidence-with")
        values = sorted([str(obj) for obj in self.g.objects(evidence_uri, evidence_with_pred)])
        if values:
            metadata.append((str(evidence_with_pred), tuple(values)))

        # Include source predicate
        source_pred = rdflib.DC.source
        values = sorted([str(obj) for obj in self.g.objects(evidence_uri, source_pred)])
        if values:
            metadata.append((str(source_pred), tuple(values)))

        return tuple(sorted(metadata))

    def group_evidence_by_metadata(self, std_annot: StandardAnnotation):
        """
        Group evidence URIs across all edges in a standard annotation by their metadata.

        For MOD imports, evidence on different edges often share identical metadata
        (dates, contributors, sources). These represent the same "evidence event" and
        should be grouped together when splitting.

        Returns: dict mapping group_index -> dict of edge_bnode_id -> list of evidence_uris

        Example:
        If Edge1 has evidence [A, B] and Edge2 has evidence [C, D],
        and metadata(A) == metadata(C) and metadata(B) == metadata(D),
        returns: {
            0: {edge1_id: [A], edge2_id: [C]},
            1: {edge1_id: [B], edge2_id: [D]}
        }
        """
        # Collect all evidence URIs and their metadata signatures
        evidence_to_metadata = {}
        for edge in std_annot.edges.values():
            for evidence_uri in edge.evidence_uris:
                if evidence_uri not in evidence_to_metadata:
                    evidence_to_metadata[evidence_uri] = self.get_evidence_metadata(evidence_uri)

        # Group evidence URIs by their metadata signature
        metadata_to_evidence = {}
        for evidence_uri, metadata in evidence_to_metadata.items():
            if metadata not in metadata_to_evidence:
                metadata_to_evidence[metadata] = []
            metadata_to_evidence[metadata].append(evidence_uri)

        # Build groups: for each metadata signature, find which evidence from each edge belongs to it
        groups = {}
        group_index = 0
        for metadata_sig, evidence_list in metadata_to_evidence.items():
            evidence_set = set(evidence_list)
            group = {}

            for edge in std_annot.edges.values():
                # Find evidence from this edge that belongs to this metadata group
                edge_evidence_in_group = [ev for ev in edge.evidence_uris if ev in evidence_set]
                if edge_evidence_in_group:
                    group[edge.bnode_id] = edge_evidence_in_group

            if group:
                groups[group_index] = group
                group_index += 1

        return groups

    def split_evidence_and_write_ttl(self, filename):
        """
        Split multi-evidence edges by grouping evidence with identical metadata across edges.

        For each standard annotation:
        1. Group evidence by metadata signature (date, contributor, source, etc.)
        2. For each evidence group, create a new annotation subgraph
        3. First group keeps original nodes; subsequent groups get new nodes with suffix

        This ensures that evidence representing the same "evidence event" across
        different edges stays together in the split annotations.
        """
        evidence_pred = rdflib.URIRef("http://geneontology.org/lego/evidence")

        for std_annot in self.standard_annotations:
            # Get evidence groups for this annotation
            evidence_groups = self.group_evidence_by_metadata(std_annot)

            # Track which individuals have been created for each group
            # Map: (original_uri, group_suffix) -> new_uri
            individual_mapping = {}

            # Process each evidence group
            for group_index, group_edges in sorted(evidence_groups.items()):
                # Determine suffix for this group (first group uses original nodes)
                if group_index == 0:
                    suffix = ""
                else:
                    suffix = f"-{group_index + 1}"

                # Process each edge in this group
                for edge_bnode_id, evidence_uris in group_edges.items():
                    edge = std_annot.edges[edge_bnode_id]

                    if suffix == "":
                        # First group: keep original bnode, but remove extra evidence
                        original_bnode = rdflib.term.BNode(edge.bnode_id)
                        # Remove all evidence except those in this group
                        for ev_uri in edge.evidence_uris:
                            if ev_uri not in evidence_uris:
                                self.g.remove((original_bnode, evidence_pred, ev_uri))
                    else:
                        # Subsequent groups: create new bnode and individuals
                        new_bnode = rdflib.term.BNode(edge.bnode_id + suffix)
                        original_bnode = rdflib.term.BNode(edge.bnode_id)

                        # Clone bnode metadata
                        self.clone_bnode(original_bnode, new_bnode)

                        # Get or create new individual URIs for source and target
                        source_key = (str(edge.source_uri), suffix)
                        if source_key not in individual_mapping:
                            new_source_uri = rdflib.URIRef(str(edge.source_uri) + suffix)
                            individual_mapping[source_key] = new_source_uri
                            self.clone_individual(edge.source_uri, new_source_uri)
                        else:
                            new_source_uri = individual_mapping[source_key]

                        target_key = (str(edge.target_uri), suffix)
                        if target_key not in individual_mapping:
                            new_target_uri = rdflib.URIRef(str(edge.target_uri) + suffix)
                            individual_mapping[target_key] = new_target_uri
                            self.clone_individual(edge.target_uri, new_target_uri)
                        else:
                            new_target_uri = individual_mapping[target_key]

                        # Add the axiom triples
                        self.g.add((new_bnode, rdflib.namespace.OWL.annotatedSource, new_source_uri))
                        self.g.add((new_bnode, rdflib.namespace.OWL.annotatedTarget, new_target_uri))
                        self.g.add((new_bnode, rdflib.namespace.OWL.annotatedProperty, edge.property_uri))

                        # Add only the evidence for this group
                        for evidence_uri in evidence_uris:
                            self.g.add((new_bnode, evidence_pred, evidence_uri))

        self.write_ttl(filename)

    def clone_bnode(self, old_bnode: rdflib.term.BNode, new_bnode: rdflib.term.BNode):
        # Clone the bnode and its properties to a new bnode
        for pred, obj in self.g.predicate_objects(old_bnode):
            if pred in self.PREDICATES_TO_COPY:
                self.g.add((new_bnode, pred, obj))

    def clone_individual(self, old_individual_uri: rdflib.URIRef, new_individual_uri: rdflib.URIRef):
        # Clone the individual and its properties to a new URI
        for pred, obj in self.g.predicate_objects(old_individual_uri):
            if pred in self.PREDICATES_TO_COPY:
                self.g.add((new_individual_uri, pred, obj))
        # # Also clone the type
        # for obj in self.g.objects(old_individual_uri, rdflib.RDF.type):
        #     self.g.add((new_individual_uri, rdflib.RDF.type, obj))

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

    def get_standard_annotations_by_individual(self, individual_uri):
        standard_annotations = []
        for sa in self.standard_annotations:
            if individual_uri in sa.individuals:
                standard_annotations.append(sa)
        return standard_annotations

    def get_edge_by_bnode_id(self, bnode_id):
        for e in self.edges:
            if e.bnode_id == bnode_id:
                return e

    def get_model_id(self):
        for model_id in self.g.subjects(rdflib.namespace.RDF.type, rdflib.namespace.OWL.Ontology):
            return str(model_id)

    def get_title(self):
        for title in self.g.objects(None, rdflib.DC.title):
            return title.replace("\t", " ").replace("\n", " ")

    def find_axiom_bits(self, bnode_id):
        source_id = list(self.g.objects(bnode_id, rdflib.namespace.OWL.annotatedSource))[0]
        target_id = list(self.g.objects(bnode_id, rdflib.namespace.OWL.annotatedTarget))[0]
        relation = list(self.g.objects(bnode_id, rdflib.namespace.OWL.annotatedProperty))[0]
        contributors = list(self.g.objects(bnode_id, rdflib.namespace.DC.contributor))
        date = next(self.g.objects(bnode_id, rdflib.namespace.DC.date), None)  # optional
        provided_by = next(self.g.objects(bnode_id, rdflib.URIRef("http://purl.org/pav/providedBy")), None)  # optional
        created = next(self.g.objects(bnode_id, rdflib.URIRef("http://purl.org/dc/terms/created")), None)  # optional
        date_accepted = next(self.g.objects(bnode_id, rdflib.URIRef("http://purl.org/dc/terms/dateAccepted")), None)  # optional
        return source_id, target_id, relation, contributors, date, provided_by, created, date_accepted

    def find_axiom_bnode_by_triple(self, source_id, relation, target_id):
        for bnode in self.g.subjects(rdflib.namespace.OWL.annotatedSource, source_id):
            if list(self.g.objects(bnode, rdflib.namespace.OWL.annotatedTarget))[0] == target_id and list(self.g.objects(bnode, rdflib.namespace.OWL.annotatedProperty))[0] == relation:
                return bnode

    def extract_edges(self):
        ets = list(self.evidence_triples())
        for triple in ets:
            bnode = triple[0]
            bnode_id = str(bnode)

            edge = self.get_edge_by_bnode_id(bnode_id)
            if edge is None:
                source_id, target_id, relation, contributors, date, provided_by, created, date_accepted = self.find_axiom_bits(bnode)
                edge = StandardAnnotationEdge(bnode, source_id, target_id, relation,
                                              # contributors, date, provided_by, created, date_accepted
                                              )
                self.edges.append(edge)
            evidence_id = triple[2]
            edge.evidence_uris.append(evidence_id)
        return self.edges

    def extract_standard_annotations(self):
        edges = self.extract_edges()
        # Process all edges first to identify connected components
        edge_to_annotation = {}  # Map to track which annotation each edge belongs to

        for edge in edges:
            edge.source_type = self.get_individual_type(edge.source_uri)
            edge.target_type = self.get_individual_type(edge.target_uri)

            source_annot = self.individual_to_annotation.get(edge.source_uri)
            target_annot = self.individual_to_annotation.get(edge.target_uri)

            if source_annot is None and target_annot is None:
                # Create new annotation if neither individual belongs to one
                new_annot = StandardAnnotation()
                self.standard_annotations.append(new_annot)
                new_annot.add_edge(edge)
                self.individual_to_annotation[edge.source_uri] = new_annot
                self.individual_to_annotation[edge.target_uri] = new_annot
                edge_to_annotation[edge.bnode_id] = new_annot
            elif source_annot is not None and target_annot is None:
                # Add to source's annotation
                source_annot.add_edge(edge)
                self.individual_to_annotation[edge.target_uri] = source_annot
                edge_to_annotation[edge.bnode_id] = source_annot
            elif source_annot is None and target_annot is not None:
                # Add to target's annotation
                target_annot.add_edge(edge)
                self.individual_to_annotation[edge.source_uri] = target_annot
                edge_to_annotation[edge.bnode_id] = target_annot
            elif source_annot is target_annot:
                # Both already in same annotation
                source_annot.add_edge(edge)
                edge_to_annotation[edge.bnode_id] = source_annot
            else:
                # Both individuals belong to different annotations - merge them
                # Keep source_annot, remove target_annot
                for ind in list(target_annot.individuals):
                    self.individual_to_annotation[ind] = source_annot

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
                self.individual_to_annotation[related_edge.source_uri] = annot
                self.individual_to_annotation[related_edge.target_uri] = annot

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
            bnode_id = str(bnode)

            # Look up the already-extracted edge instead of creating a new one
            # This preserves the evidence_uris that were populated during extract_edges()
            next_edge = self.get_edge_by_bnode_id(bnode_id)

            if next_edge is None:
                # Edge wasn't extracted (no evidence), create a new one
                next_edge = StandardAnnotationEdge(bnode, edge.target_uri, obj, pred)
                next_edge.source_type = source_type
                target_type = self.get_individual_type(obj)
                next_edge.target_type = target_type

            related_edges.append(next_edge)
            related_edges.extend(self.find_related_edges(next_edge, visited_bnodes))
        return related_edges

    def has_consistent_evidence_across_edges(self, sa: StandardAnnotation):
        """
        Check if all edges in a standard annotation have evidence with matching metadata.

        For a subgraph to be a true standard annotation, all edges must participate in
        each evidence group. This means:
        - If there are N edges and M evidence groups, each group should have evidence from all N edges
        - Evidence with identical metadata across different edges represents the same annotation event

        Returns: True if all edges have consistent evidence metadata, False otherwise
        """
        if len(sa.edges) <= 1:
            # Single edge annotations are always consistent
            return True

        # Get evidence groups
        evidence_groups = self.group_evidence_by_metadata(sa)

        # Check if each evidence group has evidence from all edges
        num_edges = len(sa.edges)
        for group_index, group_edges in evidence_groups.items():
            if len(group_edges) != num_edges:
                # This group doesn't have evidence from all edges
                return False

        return True


class GoCamGraphBuilder:
    # URI for the root causal relation
    CAUSALLY_UPSTREAM_OF_OR_WITHIN = "http://purl.obolibrary.org/obo/RO_0002418"

    def __init__(self, ontology_path, ro_ontology_path=None):
        # Store and parse the GO ontology
        self.ontology = ontobio.ontol_factory.OntologyFactory().create(ontology_path)
        self.go_aspector = ontobio.util.go_utils.GoAspector(self.ontology)

        # Store and parse the RO ontology if provided
        self.ro_ontology = None
        if ro_ontology_path:
            self.ro_ontology = rdflib.Graph()
            self.ro_ontology.parse(ro_ontology_path, format="xml")
            self.causal_relations = get_relation_descendants(self.ro_ontology, self.CAUSALLY_UPSTREAM_OF_OR_WITHIN)
        else:
            self.causal_relations = set()

    def uri_is_causal_relation(self, uri: URIRef) -> bool:
        """
        Check if the URI refers to a causal relation (descendant of causally_upstream_of_or_within).
        """
        return str(uri) in self.causal_relations

    def uri_is_molecular_function(self, uri: URIRef):
        """
        Check if the URI refers to a molecular function in the GO ontology.
        """
        if uri == URIRef("http://purl.obolibrary.org/obo/go/extensions/reacto.owl#molecular_event"):
            return True
        parsed_curies = curie_util.contract_uri(str(uri))
        # parsed_curie = str(curie_util.contract_uri(str(uri)))
        if parsed_curies and parsed_curies[0].startswith("GO:"):
            return self.go_aspector.is_molecular_function(parsed_curies[0])
        return False

    def parse_ttl(self, ttl_filename):
        gocam = GoCamGraph()
        gocam.g.parse(ttl_filename, format="ttl")
        gocam.model_id = gocam.get_model_id()
        gocam.title = gocam.get_title()
        gocam.standard_annotations = []
        gocam.extract_standard_annotations()
        gocam = self.filter_out_non_std_annotations(gocam)
        return gocam

    def filter_out_non_std_annotations(self, go_cam_graph: GoCamGraph):
        new_standard_annotations = []
        non_standard_annotations = []

        for std_annot in go_cam_graph.standard_annotations:
            failed_checks = {}

            # Check 1: Evidence consistency - all edges must have matching evidence metadata
            if not go_cam_graph.has_consistent_evidence_across_edges(std_annot):
                failed_checks["inconsistent_evidence"] = set()
                # Every edge gets this failure
                for edge_bnode_id in std_annot.edges.keys():
                    failed_checks["inconsistent_evidence"].add(edge_bnode_id)

            # Check 2: Multiple part_of edges from molecular functions
            part_of_edges = []
            for edge in std_annot.edges.values():
                if edge.property_uri == URIRef(relations.lookup_label("part of")) and self.uri_is_molecular_function(edge.source_type):
                    part_of_edges.append(edge)
            if len(part_of_edges) > 1:
                failed_checks["multiple_mf_part_of"] = set([part_of_edge.bnode_id for part_of_edge in part_of_edges])

            # Check 3: Causal relation edge between two molecular function nodes
            # If RO ontology was provided and causal relations were loaded
            if self.causal_relations:
                for edge in std_annot.edges.values():
                    if (self.uri_is_causal_relation(edge.property_uri) and
                            self.uri_is_molecular_function(edge.source_type) and
                            self.uri_is_molecular_function(edge.target_type)):
                        # Uh oh, start reporting the failure
                        if "mf_causal_mf" not in failed_checks:
                            failed_checks["mf_causal_mf"] = set()
                        failed_checks["mf_causal_mf"].add(edge.bnode_id)

            std_annot.failed_checks = failed_checks

            # Categorize annotation
            if failed_checks:
                non_standard_annotations.append(std_annot)
            else:
                new_standard_annotations.append(std_annot)

        go_cam_graph.standard_annotations = new_standard_annotations
        go_cam_graph.non_standard_annotations = non_standard_annotations
        return go_cam_graph

    def term_label(self, uri: URIRef) -> str:
        """
        Look up the label for a term URI using the stored ontologies.

        For GO terms, uses the GO ontology. For RO terms, uses the RO ontology.
        Returns the CURIE if no label is found.
        """
        parsed_curies = curie_util.contract_uri(str(uri))
        if not parsed_curies:
            return str(uri)

        curie = parsed_curies[0]

        # Try GO ontology for GO terms
        if curie.startswith("GO:"):
            label = self.ontology.label(curie)
            if label:
                return label

        # Try RO ontology for RO terms
        if self.ro_ontology and (curie.startswith("RO:") or curie.startswith("BFO:")):
            # Look up rdfs:label in the RO graph
            for label in self.ro_ontology.objects(uri, rdflib.RDFS.label):
                return str(label)

        # Fall back to CURIE
        return str(curie)

    def print_non_standard_annotation_failed_checks(self, go_cam_graph: GoCamGraph, report_file):
        print_rows = []
        for non_std_annot in go_cam_graph.non_standard_annotations:
            for failure_reason in non_std_annot.failed_checks:
                for bnode_id in non_std_annot.failed_checks[failure_reason]:
                    edge = non_std_annot.edges[bnode_id]
                    source_label = self.term_label(edge.source_type)
                    prop_label = self.term_label(edge.property_uri)
                    target_label = self.term_label(edge.target_type)
                    # Model ID, title, reason, source, predicate, object
                    cols = [
                        go_cam_graph.model_id,
                        go_cam_graph.title,
                        failure_reason,
                        source_label,
                        prop_label,
                        target_label,
                    ]
                    if cols not in print_rows:
                        print_rows.append(cols)
        for r in sorted(print_rows):
            print("\t".join(r), file=report_file)


if __name__ == "__main__":
    args = parser.parse_args()

    # Load model ID list if provided
    model_id_filter = None
    if args.pathway_id_list:
        with open(args.pathway_id_list, 'r') as f:
            model_id_filter = set(line.strip() for line in f if line.strip())

    model_files = []
    if args.model_filename:
        model_files.append(args.model_filename)
    elif args.models_folder:
        for f in os.listdir(args.models_folder):
            if f.endswith(".ttl"):
                # Skip files that start with any of the specified prefixes
                if args.skip_prefixes and any(f.startswith(prefix) for prefix in args.skip_prefixes):
                    continue
                # If filter is provided, only include models in the filter
                if model_id_filter is None or f.replace(".ttl", "") in model_id_filter:
                    model_files.append(os.path.join(args.models_folder, f))

    go_cam_graph_builder = GoCamGraphBuilder(args.ontology_filename, args.ro_filename)

    # Open report file if specified, otherwise use stdout
    report_file = None
    if args.report_file:
        report_file = open(args.report_file, 'w')
        output = report_file
    else:
        output = sys.stdout

    # Always print statistics header
    headers = ["Model ID", "Title", "Standard Annotations", "Non-Standard Annotations", "Multi-Evidence Annotations", "Mixed Annotation Type", "MF-causal->MF Edges"]
    print("\t".join(headers), file=output)

    fail_report_file = None
    if args.criteria_fail_report:
        fail_report_file = open(args.criteria_fail_report, 'w')
        criteria_fail_output = fail_report_file
        crit_fail_report_headers = ["Model ID", "Title", "Reason", "Source", "Predicate", "Object"]
        print("\t".join(crit_fail_report_headers), file=criteria_fail_output)

    if args.split_evidence and args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)

    for f in model_files:
        gocam_graph = go_cam_graph_builder.parse_ttl(f)
        filename = os.path.basename(f)
        model_id = filename.split(".")[0]

        # Print statistics
        mixed_annotation_type = "No"
        if gocam_graph.standard_annotations and gocam_graph.non_standard_annotations:
            mixed_annotation_type = "Yes"

        # Count annotations with multiple evidence on at least one edge
        multi_evidence_count = 0
        for std_annot in gocam_graph.standard_annotations:
            for edge in std_annot.edges.values():
                if len(edge.evidence_uris) > 1:
                    multi_evidence_count += 1
                    break  # Count this annotation once, move to next

        # Count MF causal edges in non-standard annotations
        mf_causal_count = 0
        for non_std_annot in gocam_graph.non_standard_annotations:
            for causal_bnode_id in non_std_annot.failed_checks.get("mf_causal_mf", set()):
                mf_causal_count += 1

        if mixed_annotation_type == "Yes":
            # print standard annotation fail_checks by edge
            go_cam_graph_builder.print_non_standard_annotation_failed_checks(gocam_graph, report_file=criteria_fail_output)

        print("\t".join(["gomodel:"+model_id, gocam_graph.title, str(len(gocam_graph.standard_annotations)), str(len(gocam_graph.non_standard_annotations)), str(multi_evidence_count), mixed_annotation_type, str(mf_causal_count)]), file=output)

        # Split evidence if requested
        if args.split_evidence and multi_evidence_count >= 1:
            if args.output_dir:
                output_filename = os.path.join(args.output_dir, filename)
            else:
                # Default to same directory with _split suffix
                base_name = os.path.splitext(f)[0]
                output_filename = base_name + "_split.ttl"

            gocam_graph.split_evidence_and_write_ttl(output_filename)
            print(f"Split evidence for {filename} -> {output_filename}")

    # Close report file if it was opened
    if report_file:
        report_file.close()

    if fail_report_file:
        fail_report_file.close()