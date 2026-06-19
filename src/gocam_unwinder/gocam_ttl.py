import argparse
import os
import sys

import ontobio.util.go_utils
import requests
import rdflib
import yaml
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
parser.add_argument('--skip-file', dest='skip_file', metavar='FILE',
                    help="Skip TTL files whose filename appears in FILE (one .ttl filename per line, e.g. true GO-CAM models to exclude)")
parser.add_argument('--groups-yaml', help="Path to groups.yaml for resolving group URIs to labels")
parser.add_argument('--date-change-report', help="Output TSV file for date change report (model ID, title, original/new dates, edge labels)")
parser.add_argument('--fix-nested-anatomy', action='store_true',
                    help="Rewrite nested anatomy extension edges to attach directly to the annotation's primary individual")
parser.add_argument('--nested-fix-report', help="Output TSV file for the nested-anatomy fix report (one row per rewritten edge)")
parser.add_argument('--no-label-api', action='store_true',
                    help="Disable OLS API fallback for resolving non-GO/RO term labels (enabled by default)")

GOCAM_RELATIONS = [str(r) for r in relations.__relation_label_lookup.values()]


def load_skip_filenames(skip_file_path: str) -> set:
    """Load a set of .ttl filenames to skip from a file (one filename per line)."""
    with open(skip_file_path) as f:
        return {line.strip() for line in f if line.strip()}


def collect_model_files(models_folder, skip_prefixes=None, skip_filenames=None, model_id_filter=None):
    """Return paths of .ttl files in models_folder, applying skip/filter rules.

    - skip_prefixes: iterable of filename prefixes to skip (e.g. ["SYNGO", "R-HSA"])
    - skip_filenames: set of exact .ttl filenames to skip (e.g. true GO-CAM models)
    - model_id_filter: if not None, only include files whose stem (filename without
      ".ttl") is in this set
    """
    skip_prefixes = skip_prefixes or []
    skip_filenames = skip_filenames or set()
    model_files = []
    for f in os.listdir(models_folder):
        if not f.endswith(".ttl"):
            continue
        if any(f.startswith(prefix) for prefix in skip_prefixes):
            continue
        if f in skip_filenames:
            continue
        if model_id_filter is not None and f.replace(".ttl", "") not in model_id_filter:
            continue
        model_files.append(os.path.join(models_folder, f))
    return model_files


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


def load_groups_lookup(groups_yaml_path: str) -> dict:
    """
    Load groups.yaml and create a URI -> label lookup dictionary.

    The groups.yaml file from go-site contains entries like:
        - label: 'UniProt'
          id: https://www.uniprot.org
          shorthand: UniProt

    Args:
        groups_yaml_path: Path to the groups.yaml file

    Returns:
        Dictionary mapping group URIs to their labels
    """
    lookup = {}
    with open(groups_yaml_path, 'r') as f:
        groups_data = yaml.safe_load(f)

    for group in groups_data:
        if 'id' in group and 'label' in group:
            lookup[group['id']] = group['label']

    return lookup


# Lead-aspect priority order for picking a single aspect per annotation.
# BP wins when present (the BP backbone subsumes MF in the design); CC is
# next, MF is the fallback for annotations with only an MF backbone.
ASPECT_PRIORITY = ("BP", "CC", "MF")


def pick_lead_aspect(primary_terms):
    """Return the lead aspect for an annotation, by priority BP > CC > MF, or None."""
    for aspect in ASPECT_PRIORITY:
        if aspect in primary_terms:
            return aspect
    return None


def find_nested_extensions(annot, builder):
    """Return (lead_aspect, nested_edges) for an annotation, or (None, []) if there
    is no backbone or no nested extension.

    A nested extension edge is an extension edge (per builder.get_extension_edges)
    whose source_type is not in the lead aspect's primary GO term URI set.
    """
    primary_terms = builder.get_primary_go_terms(annot)
    lead = pick_lead_aspect(primary_terms)
    if lead is None:
        return None, []
    primary_uri_set = set(primary_terms[lead])
    nested = [
        edge for edge in builder.get_extension_edges(annot)
        if edge.source_type not in primary_uri_set
    ]
    if not nested:
        return lead, []
    return lead, nested


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

    def has_muliple_evidence(self):
        for edge in self.edges.values():
            if len(edge.evidence_uris) > 1:
                return True  # Found multi-evidence, no need to check more edges
        return False

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
        self.modelstate = None
        self.groups = None
        self.individual_to_annotation = {}

    def write_ttl(self, filename):
        self.g.serialize(destination=filename, format='ttl')

    def get_evidence_metadata(self, evidence_uri):
        """
        Extract metadata for an evidence individual as a hashable tuple.
        This creates a signature that can identify equivalent evidence across edges.
        Excludes date predicates (dc:date, dcterms:created, dcterms:dateAccepted) so that
        evidence differing only in dates can be grouped together.
        """
        date_predicates = {
            rdflib.namespace.DC.date,
            rdflib.namespace.DCTERMS.created,
            rdflib.namespace.DCTERMS.dateAccepted,
        }
        metadata = []

        # Collect predicates defined in PREDICATES_TO_COPY, excluding date predicates
        for pred in self.PREDICATES_TO_COPY:
            if pred in date_predicates:
                continue  # Skip date predicates - handled separately during splitting
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

    def get_most_recent_date(self, evidence_uris):
        """
        Find the most recent dc:date across a list of evidence URIs.
        Returns the most recent date string, or None if no dates found.
        """
        date_pred = rdflib.namespace.DC.date
        dates = []
        for ev_uri in evidence_uris:
            for date_val in self.g.objects(ev_uri, date_pred):
                dates.append(str(date_val))
        if dates:
            return max(dates)  # ISO date strings sort lexicographically
        return None

    def update_evidence_date(self, evidence_uris, new_date):
        """
        Update dc:date on all given evidence URIs to the new_date value.
        Removes existing dc:date triples and adds the new one.
        """
        date_pred = rdflib.namespace.DC.date
        for ev_uri in evidence_uris:
            # Remove existing dc:date
            self.g.remove((ev_uri, date_pred, None))
            # Add new dc:date
            self.g.add((ev_uri, date_pred, rdflib.Literal(new_date)))

    def split_evidence_and_write_ttl(self, filename):
        """
        Split multi-evidence edges by grouping evidence with identical metadata across edges.

        For each standard annotation:
        1. Group evidence by metadata signature (date, contributor, source, etc.)
        2. For each evidence group, create a new annotation subgraph
        3. First group keeps original nodes; subsequent groups get new nodes with suffix

        This ensures that evidence representing the same "evidence event" across
        different edges stays together in the split annotations.

        Returns a list of date change records (dicts with keys: model_id, title,
        original_date, new_date, source_type, property_uri, target_type) for edges
        where dc:date was updated.
        """
        evidence_pred = rdflib.URIRef("http://geneontology.org/lego/evidence")
        date_change_records = []

        for std_annot in self.standard_annotations:
            # Get evidence groups for this annotation
            evidence_groups = self.group_evidence_by_metadata(std_annot)

            # Update dc:date to most recent for each evidence group
            date_pred = rdflib.namespace.DC.date
            for group_index, group_edges in evidence_groups.items():
                all_evidence_in_group = []
                for edge_evidence_list in group_edges.values():
                    all_evidence_in_group.extend(edge_evidence_list)
                # Collect all distinct dates in this group
                all_dates = set()
                for ev_uri in all_evidence_in_group:
                    for date_val in self.g.objects(ev_uri, date_pred):
                        all_dates.add(str(date_val))
                # Only update if dates actually differ
                if len(all_dates) > 1:
                    most_recent_date = max(all_dates)
                    # Collect per-edge date changes before updating
                    for edge_bnode_id, edge_evidence_list in group_edges.items():
                        edge = std_annot.edges[edge_bnode_id]
                        edge_dates = set()
                        for ev_uri in edge_evidence_list:
                            for date_val in self.g.objects(ev_uri, date_pred):
                                edge_dates.add(str(date_val))
                        for orig_date in edge_dates:
                            if orig_date != most_recent_date:
                                date_change_records.append({
                                    "model_id": self.model_id,
                                    "title": self.title,
                                    "original_date": orig_date,
                                    "new_date": most_recent_date,
                                    "source_type": edge.source_type,
                                    "property_uri": edge.property_uri,
                                    "target_type": edge.target_type,
                                })
                    print(f"Date updated to {most_recent_date} for {self.model_id} ({self.title})")
                    self.update_evidence_date(all_evidence_in_group, most_recent_date)
                    # Also update dc:date on the BNode axioms for each edge
                    for edge_bnode_id in group_edges.keys():
                        bnode = rdflib.term.BNode(edge_bnode_id)
                        self.g.remove((bnode, date_pred, None))
                        self.g.add((bnode, date_pred, rdflib.Literal(most_recent_date)))

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
        return date_change_records

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

    def rewrite_edge_source_and_relation(self, bnode_id, old_source, old_property,
                                         target, new_source, new_property):
        """
        Re-point an edge's source individual and relation, updating BOTH the
        assertion triple and its reified owl:Axiom bnode.

        Used to de-nest an anatomy extension: the nested edge
        (old_source -old_property-> target) becomes
        (new_source -new_property-> target), where new_source is the annotation's
        primary individual. annotatedTarget, evidence, dates, and contributors on
        the axiom bnode are left untouched. The axiom bnode is updated only if it
        exists (defensive — a bare assertion may never have been reified).
        """
        # Assertion triple
        self.g.remove((old_source, old_property, target))
        self.g.add((new_source, new_property, target))

        # Reified owl:Axiom bnode
        bnode = rdflib.term.BNode(bnode_id)
        if (bnode, rdflib.namespace.OWL.annotatedSource, old_source) in self.g:
            self.g.remove((bnode, rdflib.namespace.OWL.annotatedSource, old_source))
            self.g.add((bnode, rdflib.namespace.OWL.annotatedSource, new_source))
        if (bnode, rdflib.namespace.OWL.annotatedProperty, old_property) in self.g:
            self.g.remove((bnode, rdflib.namespace.OWL.annotatedProperty, old_property))
            self.g.add((bnode, rdflib.namespace.OWL.annotatedProperty, new_property))

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

    def get_modelstate(self):
        """Get the model state, looking only at the model-level subject."""
        model_uri = rdflib.URIRef(self.get_model_id())
        modelstate_pred = rdflib.URIRef("http://geneontology.org/lego/modelstate")
        for modelstate in self.g.objects(model_uri, modelstate_pred):
            return str(modelstate)

    def get_groups(self):
        """Get all groups (providedBy values) at the model level."""
        model_uri = rdflib.URIRef(self.get_model_id())
        provided_by_pred = rdflib.URIRef("http://purl.org/pav/providedBy")
        groups = []
        for group in self.g.objects(model_uri, provided_by_pred):
            groups.append(str(group))
        return groups

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

        # Second pass: find axiom blank nodes without evidence
        # These are owl:Axiom nodes with annotatedSource/Property/Target but no lego:evidence
        for bnode in self.g.subjects(rdflib.RDF.type, rdflib.namespace.OWL.Axiom):
            if not isinstance(bnode, rdflib.term.BNode):
                continue
            bnode_id = str(bnode)
            # Skip if already extracted (has evidence)
            if self.get_edge_by_bnode_id(bnode_id) is not None:
                continue
            # Check this axiom has the required OWL annotation bits
            sources = list(self.g.objects(bnode, rdflib.namespace.OWL.annotatedSource))
            targets = list(self.g.objects(bnode, rdflib.namespace.OWL.annotatedTarget))
            properties = list(self.g.objects(bnode, rdflib.namespace.OWL.annotatedProperty))
            if not sources or not targets or not properties:
                continue
            # Only include OBO relation edges (skip rdf:type, rdfs:label, oboInOwl#id, etc.)
            # All GO-CAM relations (BFO, RO) use the OBO namespace prefix.
            # GOCAM_RELATIONS is too restrictive (misses valid relations like RO:0002407).
            if not str(properties[0]).startswith("http://purl.obolibrary.org/obo/"):
                continue
            edge = StandardAnnotationEdge(bnode, sources[0], targets[0], properties[0])
            self.edges.append(edge)
            # edge.evidence_uris remains empty []

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

    # Gene-product identifier namespaces, keyed by the identifiers.org path
    # segment (http://identifiers.org/{key}/{id}). Sourced from the mod_id_space
    # values in go-site metadata/goex.yaml, plus protein complexes (ComplexPortal)
    # and the Protein Ontology (PR). Used to distinguish real gene products from
    # anatomy/ontology entities (EMAPA, WBbt, CL, UBERON, ...) in the GP-MF
    # backbone check. HGNC is intentionally excluded (human -> UniProtKB in goex).
    GP_NAMESPACE_KEYS = {
        "uniprot", "mgi", "sgd", "wormbase", "rgd", "zfin", "flybase",
        "tair", "pombase", "japonicusdb", "cgd", "dictybase", "ecocyc",
        "xenbase", "complexportal", "pr",
    }

    # Root of the "acts upstream of or within" relation family (#4, #5)
    ACTS_UPSTREAM_OF_OR_WITHIN = "http://purl.obolibrary.org/obo/RO_0002264"

    # Aspect root terms (full OBO PURLs). Used to distinguish root vs non-root
    # GO terms in the relation/cardinality checks.
    ROOT_GO_TERMS = {
        "http://purl.obolibrary.org/obo/GO_0003674",  # molecular_function
        "http://purl.obolibrary.org/obo/GO_0008150",  # biological_process
        "http://purl.obolibrary.org/obo/GO_0005575",  # cellular_component
    }

    # Anatomical-structure namespaces, keyed like GP_NAMESPACE_KEYS (the OBO PURL
    # prefix / identifiers.org path segment from _gene_product_namespace_key).
    # Distinguishes anatomy targets (CL, UBERON, EMAPA, ...) from gene products
    # for #10 (BP->CC/anatomy) and #12 (MF->anatomy cardinality).
    ANATOMY_NAMESPACE_KEYS = {
        "cl", "uberon", "emapa", "wbbt", "fbbt", "zfa", "ma", "po",
    }

    # EBI OLS4 REST API for resolving labels of terms not in the local GO/RO
    # ontologies (anatomy CL/UBERON/EMAPA/WBbt/..., plus CHEBI/ECO/PR/...).
    OLS4_TERMS_URL = "https://www.ebi.ac.uk/ols4/api/terms"
    OLS4_TIMEOUT = 10                  # connect + read timeout (seconds)
    OLS4_MAX_CONSECUTIVE_FAILURES = 5  # disable API for the run after this many

    def __init__(self, ontology_path, ro_ontology_path=None, groups_yaml_path=None,
                 resolve_labels_api=True):
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

        # "acts upstream of or within" family (#4, #5). Empty without RO ->
        # those two rules are skipped (see _build_relation_rules).
        if self.ro_ontology is not None:
            self.acts_upstream_relations = get_relation_descendants(
                self.ro_ontology, self.ACTS_UPSTREAM_OF_OR_WITHIN)
        else:
            self.acts_upstream_relations = set()

        # Load groups lookup if provided
        self.groups_lookup = {}
        if groups_yaml_path:
            self.groups_lookup = load_groups_lookup(groups_yaml_path)

        # Relation URIs resolved once and reused across all criteria checks.
        self.rel_enabled_by     = URIRef(relations.lookup_label("enabled by"))      # RO:0002333
        self.rel_contributes_to = URIRef(relations.lookup_label("contributes to"))  # RO:0002326
        self.rel_has_input      = URIRef(relations.lookup_label("has input"))       # RO:0002233
        self.rel_has_output     = URIRef(relations.lookup_label("has output"))      # RO:0002234
        self.rel_part_of        = URIRef(relations.lookup_label("part of"))         # BFO:0000050
        self.rel_located_in     = URIRef(relations.lookup_label("located in"))      # RO:0001025
        self.rel_is_active_in   = URIRef(relations.lookup_label("is active in"))    # RO:0002432
        self.rel_occurs_in      = URIRef(relations.lookup_label("occurs in"))       # BFO:0000066
        self.relation_rules = self._build_relation_rules()

        # Valid MF->BP relations for the #11 cardinality count. Sourced from the
        # #5 rule's `valid` set when present (RO loaded) so the two can never
        # diverge; else the part_of-only fallback (the upstream/causal families
        # require RO). Built once -- it does not change per annotation.
        _mf_bp_rule = next((r for r in self.relation_rules
                            if r["key"] == "invalid_mf_bp_relation"), None)
        self.mf_bp_valid_relations = (
            _mf_bp_rule["valid"] if _mf_bp_rule
            else {str(self.rel_part_of)} | set(self.acts_upstream_relations) | set(self.causal_relations))

        # The recognized backbone/placement relations. The relation-validity
        # rules (#3/#4/#5/#6/#10) only validate edges whose relation is in this
        # set; edges using any other relation are annotation extensions (e.g.
        # BP -results_in_development_of-> anatomy) and are left informational
        # (consistent with #7/#8/#9). A *misused* placement relation (e.g.
        # located_in on a BP->CC edge, where occurs_in is required) is still in
        # this set and therefore still flagged by its rule.
        self.backbone_relations = (
            {str(self.rel_located_in), str(self.rel_is_active_in),
             str(self.rel_occurs_in), str(self.rel_part_of)}
            | set(self.acts_upstream_relations) | set(self.causal_relations))

        # OLS API label-resolution state. The cache maps an IRI string to a
        # label (str) or None (negative-cached miss), so each ID is queried at
        # most once per run. The session is created lazily on first fetch.
        self.resolve_labels_api = resolve_labels_api
        self._api_label_cache = {}
        self._api_session = None
        self._api_consecutive_failures = 0
        self._api_disabled = False

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

    def _resolve_mf_type(self, type_node, graph):
        """
        Resolve `type_node` to an underlying MF URI, treating an
        `owl:complementOf <GO_xxxxxxx>` class expression as the wrapped GO term.

        Returns the MF URIRef if `type_node` is (or wraps) a molecular function;
        None otherwise.
        """
        if isinstance(type_node, URIRef):
            return type_node if self.uri_is_molecular_function(type_node) else None
        if isinstance(type_node, rdflib.BNode):
            for wrapped in graph.objects(type_node, rdflib.OWL.complementOf):
                if isinstance(wrapped, URIRef) and self.uri_is_molecular_function(wrapped):
                    return wrapped
        return None

    def _go_aspect(self, type_node, graph):
        """Return "MF" | "BP" | "CC" | None for an individual's type node.

        MF resolution goes through _resolve_mf_type (NOT/owl:complementOf aware);
        BP/CC use the GoAspector classifiers. Single entry point so every check
        classifies aspect identically.
        """
        if self._resolve_mf_type(type_node, graph) is not None:
            return "MF"
        if isinstance(type_node, URIRef):
            if self.uri_is_biological_process(type_node):
                return "BP"
            if self.uri_is_cellular_component(type_node):
                return "CC"
        return None

    def _is_root_go_term(self, type_node):
        """True if type_node is one of the three GO aspect root terms."""
        return isinstance(type_node, URIRef) and str(type_node) in self.ROOT_GO_TERMS

    def _is_anatomical_structure(self, type_node):
        """True if the target is a GO cellular component OR its namespace key
        is in ANATOMY_NAMESPACE_KEYS (CL, UBERON, EMAPA, ...). Used by #12
        (MF->anatomy cardinality). Note: #10 does NOT call this -- it uses
        _category with target categories {"CC", "ANATOMY"} instead.

        Note: _category() returns "CC" (not "ANATOMY") for GO cellular components,
        so _is_anatomical_structure and _category are NOT equivalent for GO CC
        terms. Use _is_anatomical_structure when the caller needs to treat CC and
        anatomy-ontology terms uniformly (e.g. the MF->anatomy cardinality check)."""
        if isinstance(type_node, URIRef) and self.uri_is_cellular_component(type_node):
            return True
        return self._gene_product_namespace_key(type_node) in self.ANATOMY_NAMESPACE_KEYS

    def _category(self, type_node, graph):
        """Disjoint endpoint category for an edge: "MF" | "BP" | "CC" | "GP" |
        "ANATOMY" | None. GO terms resolve to their aspect; otherwise a
        gene-product namespace -> "GP", an anatomy namespace -> "ANATOMY"."""
        aspect = self._go_aspect(type_node, graph)
        if aspect:
            return aspect
        key = self._gene_product_namespace_key(type_node)
        if key in self.GP_NAMESPACE_KEYS:
            return "GP"
        if key in self.ANATOMY_NAMESPACE_KEYS:
            return "ANATOMY"
        return None

    def _build_relation_rules(self):
        """Declarative relation-validity table. Static rules (#10, #3, #6) are
        listed first; RO-dependent rules (#4, #5) are appended last and only
        when an RO ontology is loaded (their valid sets need the relation
        descendant families).

        Each rule: src/tgt are sets of endpoint categories (_category);
        src_root_mf requires the source to be the root MF (always False on the
        GP/BP-source rules); tgt_nonroot requires a non-root target; valid is the
        set of acceptable property-URI strings.
        """
        rules = [
            # #10  BP -> CC/anatomy : occurs_in
            {"key": "invalid_bp_cc_relation", "src": {"BP"}, "tgt": {"CC", "ANATOMY"},
             "src_root_mf": False, "tgt_nonroot": False,
             "valid": {str(self.rel_occurs_in)}},
            # #3  GP -> non-root CC : located_in
            {"key": "invalid_gp_cc_relation", "src": {"GP"}, "tgt": {"CC"},
             "src_root_mf": False, "tgt_nonroot": True,
             "valid": {str(self.rel_located_in)}},
            # #6  root-MF -> non-root CC : is_active_in
            {"key": "invalid_mf_cc_relation", "src": {"MF"}, "tgt": {"CC"},
             "src_root_mf": True, "tgt_nonroot": True,
             "valid": {str(self.rel_is_active_in)}},
        ]
        if self.acts_upstream_relations:
            upstream = set(self.acts_upstream_relations)
            rules.append(
                # #4  GP -> BP (incl. root) : acts_upstream_of_or_within + children
                {"key": "invalid_gp_bp_relation", "src": {"GP"}, "tgt": {"BP"},
                 "src_root_mf": False, "tgt_nonroot": False,
                 "valid": upstream})
            rules.append(
                # #5  root-MF -> non-root BP : part_of OR the acts_upstream
                # (RO:0002264) family OR the causally_upstream (RO:0002418)
                # family. The RO:0002418 family is included because the canonical
                # MOD "BP-only annotation" (unknown/root MF causally_upstream of a
                # BP) uses it and must stay a standard, splittable annotation.
                {"key": "invalid_mf_bp_relation", "src": {"MF"}, "tgt": {"BP"},
                 "src_root_mf": True, "tgt_nonroot": True,
                 "valid": {str(self.rel_part_of)} | upstream | set(self.causal_relations)})
        return rules

    def _matches_relation_rule(self, edge, rule, graph):
        if self._category(edge.source_type, graph) not in rule["src"]:
            return False
        if self._category(edge.target_type, graph) not in rule["tgt"]:
            return False
        if rule["src_root_mf"] and not self._is_root_go_term(edge.source_type):
            return False
        if rule["tgt_nonroot"] and self._is_root_go_term(edge.target_type):
            return False
        return True

    def _gene_product_namespace_key(self, type_uri):
        """
        Return the gene-product namespace key for a type URI, or None.

        - {http,https}://identifiers.org/{seg}/... (or the compact colon form
          .../{seg}:{id}) -> seg, truncated at the first '.' or ':', lowercased
          (dictybase.gene -> dictybase, tair.locus -> tair, complexportal:CPX-1
          -> complexportal; MGI's double-prefixed .../mgi/MGI:1100089 -> mgi)
        - ComplexPortal host URLs (.../complexportal/...) -> "complexportal"
        - Standard OBO PURLs (.../obo/PREFIX_local) -> PREFIX lowercased
          (so PR_... -> "pr", GO_... -> "go", EMAPA_... -> "emapa")

        Non-URIRef nodes and unrecognized URI shapes return None. The caller
        decides which keys count as gene products via GP_NAMESPACE_KEYS.
        """
        if not isinstance(type_uri, URIRef):
            return None
        s = str(type_uri)
        ident = "://identifiers.org/"
        if ident in s:
            seg = s.split(ident, 1)[1].split("/", 1)[0]
            # Truncate sub-namespace ('.') and compact-form ID (':') suffixes:
            # dictybase.gene -> dictybase, complexportal:CPX-566 -> complexportal.
            return seg.split(".", 1)[0].split(":", 1)[0].lower() or None
        if "/complexportal/" in s:
            return "complexportal"
        obo = "://purl.obolibrary.org/obo/"
        if obo in s:
            local = s.split(obo, 1)[1]
            # Standard OBO term is PREFIX_localid with no further path/fragment;
            # non-standard forms (e.g. reacto.owl#REACTO_...) are not GPs.
            if "/" not in local and "#" not in local and "_" in local:
                return local.split("_", 1)[0].lower()
        return None

    def uri_is_biological_process(self, uri: URIRef) -> bool:
        """
        Check if the URI refers to a biological process in the GO ontology.
        """
        parsed_curies = curie_util.contract_uri(str(uri))
        if parsed_curies and parsed_curies[0].startswith("GO:"):
            return self.go_aspector.is_biological_process(parsed_curies[0])
        return False

    def uri_is_cellular_component(self, uri: URIRef) -> bool:
        """
        Check if the URI refers to a cellular component in the GO ontology.
        """
        parsed_curies = curie_util.contract_uri(str(uri))
        if parsed_curies and parsed_curies[0].startswith("GO:"):
            return self.go_aspector.is_cellular_component(parsed_curies[0])
        return False

    def _backbone_role(self, edge: StandardAnnotationEdge):
        """
        Return the gene-product -> MF/BP/CC backbone role of `edge`, or None if
        the edge is an annotation extension (not part of any backbone).

        Backbone patterns (first match wins):
          - "MF": predicate == enabled_by AND source is MF
          - "BP": predicate in mf_bp_valid_relations (part_of OR the
                  acts_upstream_of_or_within RO:0002264 family OR the
                  causally_upstream_of_or_within RO:0002418 family) AND source is
                  the ROOT MF (GO:0003674) AND target is BP
          - "CC": predicate in {located_in, is_active_in} AND target is CC

        The BP rule requires the source MF to be the root MF: a specific
        (non-root) MF -part_of-> BP is an extension, not a BP backbone, so the
        annotation it belongs to remains MF-led (the BP is contextual). Only the
        canonical "BP-only" pattern (unknown/root MF part_of OR causally upstream
        of a BP) counts as a BP backbone. The relation set is shared with the #5
        invalid_mf_bp_relation check (self.mf_bp_valid_relations) so the two
        cannot diverge; without an RO ontology it falls back to part_of only (the
        upstream/causal families require RO).
        """
        if edge.property_uri == self.rel_enabled_by and self.uri_is_molecular_function(edge.source_type):
            return "MF"
        if (str(edge.property_uri) in self.mf_bp_valid_relations
                and self._is_root_go_term(edge.source_type)
                and self.uri_is_molecular_function(edge.source_type)
                and self.uri_is_biological_process(edge.target_type)):
            return "BP"
        if (edge.property_uri in {self.rel_located_in, self.rel_is_active_in}
                and self.uri_is_cellular_component(edge.target_type)):
            return "CC"
        return None

    def get_extension_edges(self, annot: StandardAnnotation) -> List[StandardAnnotationEdge]:
        """
        Return edges in `annot` that are annotation extensions —
        edges that fall outside the gene-product -> MF/BP/CC backbone (see
        _backbone_role for the backbone patterns).

        Multi-hop extensions (e.g., CL -part_of-> EMAPA reached via the BP node)
        are returned because they fail to match any backbone pattern, as is a
        specific (non-root) MF -part_of-> BP edge.

        The returned list preserves the order of `annot.edges`.
        """
        return [edge for edge in annot.edges.values()
                if self._backbone_role(edge) is None]

    def get_primary_go_terms(self, annot: StandardAnnotation) -> dict:
        """
        Return the primary GO term URIs of an annotation, grouped by aspect.

        The primary GO term per aspect is identified by which edge matches a
        backbone pattern (see _backbone_role):
          - MF: source_type of an MF -enabled_by-> GP edge
          - BP: target_type of a root-MF -part_of-> BP edge
          - CC: target_type of a   ? -located_in/is_active_in-> CC edge

        Returns a dict mapping aspect ("MF", "BP", "CC") to a list of primary
        term URIs. Aspect keys are absent when no backbone match is found for
        that aspect. Lists are typically length 1 but can be longer if the
        annotation contains multiple matching backbone edges (rare in
        well-formed data, useful to surface).
        """
        primary = {}
        for edge in annot.edges.values():
            role = self._backbone_role(edge)
            if role == "MF":
                # MF backbone -> primary MF is the source type
                primary.setdefault("MF", []).append(edge.source_type)
            elif role == "BP":
                # BP backbone -> primary BP is the target type
                primary.setdefault("BP", []).append(edge.target_type)
            elif role == "CC":
                # CC backbone -> primary CC is the target type
                primary.setdefault("CC", []).append(edge.target_type)

        return primary

    def get_primary_individuals(self, annot: StandardAnnotation) -> dict:
        """
        Return the primary *individual* URIs of an annotation, grouped by aspect.

        Mirror of get_primary_go_terms, but collects the backbone individual URI
        (not its type) per aspect, using the same _backbone_role dispatch:
          - MF: source_uri of an MF -enabled_by-> GP edge
          - BP: target_uri of a root-MF -part_of-> BP edge
          - CC: target_uri of a   ? -located_in/is_active_in-> CC edge

        Returns a dict mapping aspect ("MF", "BP", "CC") to a list of individual
        URIs. Aspect keys are absent when no backbone match is found. Lists are
        typically length 1 (longer surfaces anomalies, like get_primary_go_terms).
        """
        primary = {}
        for edge in annot.edges.values():
            role = self._backbone_role(edge)
            if role == "MF":
                # MF backbone -> primary MF individual is the source (the MF node)
                primary.setdefault("MF", []).append(edge.source_uri)
            elif role == "BP":
                # BP backbone -> primary BP individual is the target
                primary.setdefault("BP", []).append(edge.target_uri)
            elif role == "CC":
                # CC backbone -> primary CC individual is the target
                primary.setdefault("CC", []).append(edge.target_uri)
        return primary

    def plan_nested_anatomy_fixes(self, gocam: GoCamGraph) -> list:
        """
        Build a list of rewrite instructions for nested anatomy extension edges.

        For every annotation (standard and non-standard), find extension edges
        whose source is not the lead-aspect primary term (nested) and whose source
        and target types are both anatomical structures (_is_anatomical_structure),
        and plan to re-point each onto the annotation's primary individual. The
        relation becomes occurs_in for MF/BP-led annotations, or is kept as-is
        (typically part_of) for CC-led annotations.

        Annotations whose lead aspect has 0 or >1 primary individual are skipped
        with a warning (ambiguous attach point).

        Returns a list of instruction dicts with keys: model_id, title,
        lead_aspect, primary_term, bnode_id, old_source_uri, old_property_uri,
        target_uri, new_source_uri, new_property_uri, old_source_type, target_type.
        """
        plan = []
        all_annots = gocam.standard_annotations + gocam.non_standard_annotations
        for annot in all_annots:
            lead, nested = find_nested_extensions(annot, self)
            if not nested:
                continue
            primary_individuals = self.get_primary_individuals(annot).get(lead, [])
            if len(primary_individuals) != 1:
                print(f"WARNING: skipping annotation in {gocam.model_id} "
                      f"({gocam.title}) — expected 1 primary {lead} individual, "
                      f"found {len(primary_individuals)}")
                continue
            primary_individual = primary_individuals[0]
            # `lead` came from find_nested_extensions -> pick_lead_aspect over
            # get_primary_go_terms, so it is guaranteed to be a present key here.
            primary_term = self.get_primary_go_terms(annot)[lead][0]
            for edge in nested:
                if not (self._is_anatomical_structure(edge.source_type)
                        and self._is_anatomical_structure(edge.target_type)):
                    continue
                if lead in ("MF", "BP"):
                    new_property = self.rel_occurs_in
                else:
                    new_property = edge.property_uri  # CC-led keeps the relation
                plan.append({
                    "model_id": gocam.model_id,
                    "title": gocam.title,
                    "lead_aspect": lead,
                    "primary_term": primary_term,
                    "bnode_id": edge.bnode_id,
                    "old_source_uri": edge.source_uri,
                    "old_property_uri": edge.property_uri,
                    "target_uri": edge.target_uri,
                    "new_source_uri": primary_individual,
                    "new_property_uri": new_property,
                    "old_source_type": edge.source_type,
                    "target_type": edge.target_type,
                })
        return plan

    def parse_ttl(self, ttl_filename):
        gocam = GoCamGraph()
        gocam.g.parse(ttl_filename, format="ttl")
        gocam.model_id = gocam.get_model_id()
        gocam.title = gocam.get_title()
        gocam.modelstate = gocam.get_modelstate()
        # Get groups and resolve URIs to labels if lookup is available
        group_uris = gocam.get_groups()
        if self.groups_lookup:
            gocam.groups = [self.groups_lookup.get(uri, uri) for uri in group_uris]
        else:
            gocam.groups = group_uris
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

            # Check #11: Cardinality MF->BP should be 1 (refines the former
            # multiple_mf_part_of: narrows to MF->BP and admits the same relation
            # set as #5 -- self.mf_bp_valid_relations, built once in __init__).
            mf_bp_edges = [
                edge for edge in std_annot.edges.values()
                if self._go_aspect(edge.source_type, go_cam_graph.g) == "MF"
                and self._go_aspect(edge.target_type, go_cam_graph.g) == "BP"
                and str(edge.property_uri) in self.mf_bp_valid_relations
            ]
            if len(mf_bp_edges) > 1:
                failed_checks["multiple_mf_bp"] = {e.bnode_id for e in mf_bp_edges}

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

            # Check 4: Edges without evidence
            for edge in std_annot.edges.values():
                if len(edge.evidence_uris) == 0:
                    if "edge_without_evidence" not in failed_checks:
                        failed_checks["edge_without_evidence"] = set()
                    failed_checks["edge_without_evidence"].add(edge.bnode_id)

            # Check 5: GP<->MF backbone must use `enabled_by` (MF as source) or
            # `contributes to` / RO:0002326 (MF as target). NOT-qualified MFs are
            # recognized via owl:complementOf class expressions. The non-MF
            # endpoint must be a gene product (GP_NAMESPACE_KEYS) -- anatomy/
            # ontology targets (EMAPA, WBbt, CL, ...) are extensions, not GPs.
            # `has_input`/`has_output` are allowed MF->GP extension relations.
            # Annotations with at least one valid backbone edge pass; those with
            # no valid backbone get every invalid candidate edge flagged.
            enabled_by = self.rel_enabled_by
            contributes_to = self.rel_contributes_to
            mf_source_extensions = {self.rel_has_input, self.rel_has_output}
            invalid_candidates = []
            has_valid_backbone = False
            for edge in std_annot.edges.values():
                src_mf = self._resolve_mf_type(edge.source_type, go_cam_graph.g)
                tgt_mf = self._resolve_mf_type(edge.target_type, go_cam_graph.g)
                # Skip non-MF and MF<->MF edges (latter is mf_causal_mf's domain)
                if (src_mf is None) == (tgt_mf is None):
                    continue
                if src_mf is not None:
                    other_type = edge.target_type
                    mf_on = "source"
                else:
                    other_type = edge.source_type
                    mf_on = "target"
                # Only MF<->gene-product edges are GP-MF backbone candidates.
                # Anatomy/ontology targets resolve to keys absent from the set.
                if self._gene_product_namespace_key(other_type) not in self.GP_NAMESPACE_KEYS:
                    continue
                if mf_on == "source":
                    if edge.property_uri == enabled_by:
                        has_valid_backbone = True
                    elif edge.property_uri in mf_source_extensions:
                        continue  # allowed MF->GP extension (has_input/has_output)
                    else:
                        invalid_candidates.append(edge.bnode_id)
                else:  # mf_on == "target"
                    if edge.property_uri == contributes_to:
                        has_valid_backbone = True
                    else:
                        invalid_candidates.append(edge.bnode_id)
            if not has_valid_backbone and invalid_candidates:
                failed_checks.setdefault("invalid_gp_mf_relation", set()).update(invalid_candidates)

            # Check #12: Cardinality MF->anatomical-structure should be 1.
            mf_anatomy_edges = [
                edge for edge in std_annot.edges.values()
                if self._go_aspect(edge.source_type, go_cam_graph.g) == "MF"
                and self._is_anatomical_structure(edge.target_type)
            ]
            if len(mf_anatomy_edges) > 1:
                failed_checks["multiple_mf_anatomy"] = {e.bnode_id for e in mf_anatomy_edges}

            # Check #13: Enabler must be a gene product (not GO, ChEBI, etc.).
            # Complements #2, which skips non-GP endpoints. The enabler is the
            # target of an enabled_by (MF->GP) edge.
            for edge in std_annot.edges.values():
                if edge.property_uri == self.rel_enabled_by:
                    if self._gene_product_namespace_key(edge.target_type) not in self.GP_NAMESPACE_KEYS:
                        failed_checks.setdefault("enabler_not_gp", set()).add(edge.bnode_id)

            # Checks 3, 4, 5, 6, 10: relation-validity rule table. These validate
            # the BACKBONE of an annotation only. An edge is subject to validation
            # only if its relation is a recognized backbone/placement relation
            # (self.backbone_relations); edges using any other relation are
            # annotation extensions (e.g. BP -results_in_development_of-> anatomy)
            # and are informational, not failures (consistent with #7/#8/#9).
            # Endpoint categories are disjoint, so at most one rule matches an edge.
            for edge in std_annot.edges.values():
                prop = str(edge.property_uri)
                if prop not in self.backbone_relations:
                    continue  # extension-relation edge -> not validated
                for rule in self.relation_rules:
                    if self._matches_relation_rule(edge, rule, go_cam_graph.g):
                        if prop not in rule["valid"]:
                            failed_checks.setdefault(rule["key"], set()).add(edge.bnode_id)
                        break

            std_annot.failed_checks = failed_checks

            # Categorize annotation
            if failed_checks:
                non_standard_annotations.append(std_annot)
            else:
                new_standard_annotations.append(std_annot)

        go_cam_graph.standard_annotations = new_standard_annotations
        go_cam_graph.non_standard_annotations = non_standard_annotations
        return go_cam_graph

    def _fetch_label_from_ols(self, uri, curie):
        """Query OLS4 for the label of ``uri``; return the label or None.

        Never raises: network errors, non-2xx responses, and unparseable JSON
        are treated as a miss (None). After OLS4_MAX_CONSECUTIVE_FAILURES
        consecutive failures the API is disabled for the rest of the run so a
        fully offline run does not make one doomed call per unique term.
        """
        if self._api_disabled:
            return None
        if self._api_session is None:
            self._api_session = requests.Session()
            self._api_session.headers.update(
                {"User-Agent": "go-cam-evidence-unwinder/label-resolver"})
        try:
            resp = self._api_session.get(
                self.OLS4_TERMS_URL,
                params={"iri": str(uri), "size": 20},
                timeout=self.OLS4_TIMEOUT,
            )
            resp.raise_for_status()
            terms = resp.json().get("_embedded", {}).get("terms", [])
            label = self._select_label(terms, curie)
            self._api_consecutive_failures = 0   # reset on any successful response
            return label
        except (requests.RequestException, ValueError, AttributeError, TypeError):
            self._api_consecutive_failures += 1
            if self._api_consecutive_failures >= self.OLS4_MAX_CONSECUTIVE_FAILURES:
                self._api_disabled = True
            return None

    def _select_label(self, terms, curie):
        """Pick the best human-readable label from an OLS4 _embedded.terms list.

        The same IRI may appear in several ontologies; importing ontologies
        sometimes carry a junk label equal to the bare ID fragment. Priority:
          1. a term flagged is_defining_ontology with a "real" label;
          2. a term whose ontology_name matches the CURIE prefix (lowercased);
          3. the first term with any "real" label;
          4. None (caller falls back to the CURIE).
        A label is "real" iff it is non-empty and not equal to the bare ID
        fragment ("CL_0000540") or the CURIE itself ("CL:0000540").
        """
        if not terms:
            return None
        local_id = curie.replace(":", "_")          # "CL:0000540" -> "CL_0000540"
        prefix = curie.split(":", 1)[0].lower()     # "CL:0000540" -> "cl"

        def is_real(term):
            label = term.get("label")
            return bool(label) and label != local_id and label != curie

        for term in terms:                          # rule 1
            if term.get("is_defining_ontology") and is_real(term):
                return term.get("label")
        for term in terms:                          # rule 2
            if term.get("ontology_name", "").lower() == prefix and is_real(term):
                return term.get("label")
        for term in terms:                          # rule 3
            if is_real(term):
                return term.get("label")
        return None                                 # rule 4

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

        # API fallback for terms not in the local GO/RO ontologies (anatomy:
        # CL/UBERON/EMAPA/WBbt/..., plus CHEBI/ECO/PR/...). Cache hits and
        # misses so any IRI is queried at most once per run.
        if self.resolve_labels_api:
            iri = str(uri)
            if iri not in self._api_label_cache:
                self._api_label_cache[iri] = self._fetch_label_from_ols(uri, curie)
            if self._api_label_cache[iri]:
                return self._api_label_cache[iri]

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

    # These are independent transformations of the source models and must not share
    # one invocation: the fixer would run on the already-split in-memory graph (whose
    # annotation objects were built pre-split) and overwrite the split output. Run them
    # as separate passes instead (e.g. the `models_split` and `fix_nested_anatomy`
    # Makefile targets, each with its own --output-dir). Checked before the (slow)
    # ontology load so it fails fast.
    if args.split_evidence and args.fix_nested_anatomy:
        parser.error("--split-evidence and --fix-nested-anatomy cannot be combined in "
                     "one invocation; run them as separate passes with separate "
                     "--output-dir directories.")

    # Load model ID list if provided
    model_id_filter = None
    if args.pathway_id_list:
        with open(args.pathway_id_list, 'r') as f:
            model_id_filter = set(line.strip() for line in f if line.strip())

    # Load skip-file filenames if provided (e.g. true GO-CAM models to exclude)
    skip_filenames = load_skip_filenames(args.skip_file) if args.skip_file else set()

    model_files = []
    if args.model_filename:
        model_files.append(args.model_filename)
    elif args.models_folder:
        model_files = collect_model_files(
            args.models_folder,
            skip_prefixes=args.skip_prefixes,
            skip_filenames=skip_filenames,
            model_id_filter=model_id_filter,
        )

    go_cam_graph_builder = GoCamGraphBuilder(args.ontology_filename, args.ro_filename, args.groups_yaml,
                                             resolve_labels_api=not args.no_label_api)

    # Open report file if specified, otherwise use stdout
    report_file = None
    if args.report_file:
        report_file = open(args.report_file, 'w')
        output = report_file
    else:
        output = sys.stdout

    # Always print statistics header
    headers = ["Model ID", "Title", "Standard Annotations", "Non-Standard Annotations", "Multi-Evidence Annotations", "Mixed Annotation Type", "MF-causal->MF Edges", "Edges w/o Evidence", "Model State", "Groups", "Multi-Evidence GO Terms"]
    print("\t".join(headers), file=output)

    fail_report_file = None
    criteria_fail_output = None
    if args.criteria_fail_report:
        fail_report_file = open(args.criteria_fail_report, 'w')
        criteria_fail_output = fail_report_file
        crit_fail_report_headers = ["Model ID", "Title", "Reason", "Source", "Predicate", "Object"]
        print("\t".join(crit_fail_report_headers), file=criteria_fail_output)

    if (args.split_evidence or args.fix_nested_anatomy) and args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)

    all_date_change_records = []
    all_nested_fix_records = []

    for f in model_files:
        gocam_graph = go_cam_graph_builder.parse_ttl(f)

        # Skip models marked for deletion
        if gocam_graph.modelstate == "delete":
            continue

        filename = os.path.basename(f)
        model_id = filename.split(".")[0]

        # Print statistics
        mixed_annotation_type = "No"
        if gocam_graph.standard_annotations and gocam_graph.non_standard_annotations:
            mixed_annotation_type = "Yes"

        # Count annotations with multiple evidence on at least one edge
        # Also collect GO term labels for terms in multi-evidence annotations
        std_multi_evidence_count = 0
        multi_evidence_go_terms = set()
        for std_annot in gocam_graph.standard_annotations:
            if std_annot.has_muliple_evidence():
                std_multi_evidence_count += 1
                # Collect GO term labels from all edges in this annotation
                # Skip URIs and CURIEs (only include resolved human-readable labels)
                for edge in std_annot.edges.values():
                    if edge.source_type:
                        label = go_cam_graph_builder.term_label(edge.source_type)
                        # Skip if URI, CURIE (contains ':'), or empty
                        if label and not label.startswith("http") and ":" not in label:
                            multi_evidence_go_terms.add(label)
                    if edge.target_type:
                        label = go_cam_graph_builder.term_label(edge.target_type)
                        if label and not label.startswith("http") and ":" not in label:
                            multi_evidence_go_terms.add(label)

        # Also compute multi_evidence_count for non-standard annotations
        non_std_multi_evidence_count = 0
        for non_std_annot in gocam_graph.non_standard_annotations:
            if non_std_annot.has_muliple_evidence():
                non_std_multi_evidence_count += 1

        # This is what goes in the Multi-Evidence Annotations column
        report_multi_evidence_count = std_multi_evidence_count + non_std_multi_evidence_count

        # Count MF causal edges in non-standard annotations
        mf_causal_count = 0
        for non_std_annot in gocam_graph.non_standard_annotations:
            for causal_bnode_id in non_std_annot.failed_checks.get("mf_causal_mf", set()):
                mf_causal_count += 1

        # Count edges without evidence across all annotations
        no_evidence_edge_count = 0
        all_annotations = gocam_graph.standard_annotations + gocam_graph.non_standard_annotations
        for annot in all_annotations:
            for edge in annot.edges.values():
                if len(edge.evidence_uris) == 0:
                    no_evidence_edge_count += 1

        if criteria_fail_output:
            # print standard annotation fail_checks by edge
            go_cam_graph_builder.print_non_standard_annotation_failed_checks(gocam_graph, report_file=criteria_fail_output)

        # Format multi-evidence GO terms as pipe-separated list
        multi_ev_terms_str = "|".join(sorted(multi_evidence_go_terms)) if multi_evidence_go_terms else ""
        # Format groups as pipe-separated list
        groups_str = "|".join(gocam_graph.groups) if gocam_graph.groups else ""
        modelstate_str = gocam_graph.modelstate or ""
        print("\t".join(["gomodel:"+model_id, gocam_graph.title, str(len(gocam_graph.standard_annotations)), str(len(gocam_graph.non_standard_annotations)), str(report_multi_evidence_count), mixed_annotation_type, str(mf_causal_count), str(no_evidence_edge_count), modelstate_str, groups_str, multi_ev_terms_str]), file=output)

        # Split evidence if requested and model contains standard annotations having multiple evidence edges
        if args.split_evidence and std_multi_evidence_count >= 1:
            if args.output_dir:
                output_filename = os.path.join(args.output_dir, filename)
            else:
                # Default to same directory with _split suffix
                base_name = os.path.splitext(f)[0]
                output_filename = base_name + "_split.ttl"

            date_records = gocam_graph.split_evidence_and_write_ttl(output_filename)
            all_date_change_records.extend(date_records)
            print(f"Split evidence for {filename} -> {output_filename}")

        # Fix nested anatomy extensions if requested (independent of --split-evidence)
        if args.fix_nested_anatomy:
            nested_plan = go_cam_graph_builder.plan_nested_anatomy_fixes(gocam_graph)
            if nested_plan:
                for rec in nested_plan:
                    gocam_graph.rewrite_edge_source_and_relation(
                        rec["bnode_id"], rec["old_source_uri"], rec["old_property_uri"],
                        rec["target_uri"], rec["new_source_uri"], rec["new_property_uri"])
                all_nested_fix_records.extend(nested_plan)
                if args.output_dir:
                    fix_output_filename = os.path.join(args.output_dir, filename)
                else:
                    fix_output_filename = os.path.splitext(f)[0] + "_nested_fixed.ttl"
                gocam_graph.write_ttl(fix_output_filename)
                print(f"Fixed nested anatomy for {filename} -> {fix_output_filename} ({len(nested_plan)} edges)")

    # Write date change report if requested
    if args.date_change_report and all_date_change_records:
        with open(args.date_change_report, 'w') as dcr_file:
            dcr_headers = ["Model ID", "Title", "Original Date", "New Date", "Source", "Predicate", "Target"]
            print("\t".join(dcr_headers), file=dcr_file)
            for rec in all_date_change_records:
                source_label = go_cam_graph_builder.term_label(rec["source_type"]) if rec["source_type"] else ""
                pred_label = go_cam_graph_builder.term_label(rec["property_uri"]) if rec["property_uri"] else ""
                target_label = go_cam_graph_builder.term_label(rec["target_type"]) if rec["target_type"] else ""
                cols = [rec["model_id"], rec["title"], rec["original_date"], rec["new_date"],
                        source_label, pred_label, target_label]
                print("\t".join(cols), file=dcr_file)

    # Write nested-anatomy fix report if requested
    if args.nested_fix_report and all_nested_fix_records:
        with open(args.nested_fix_report, 'w') as nfr_file:
            nfr_headers = ["Model ID", "Title", "Lead Aspect", "Primary Term",
                           "Old Source", "Old Relation", "Target", "New Relation"]
            print("\t".join(nfr_headers), file=nfr_file)
            for rec in all_nested_fix_records:
                primary_label = go_cam_graph_builder.term_label(rec["primary_term"]) if rec["primary_term"] else ""
                old_source_label = go_cam_graph_builder.term_label(rec["old_source_type"]) if rec["old_source_type"] else ""
                old_rel_label = go_cam_graph_builder.term_label(rec["old_property_uri"]) if rec["old_property_uri"] else ""
                target_label = go_cam_graph_builder.term_label(rec["target_type"]) if rec["target_type"] else ""
                new_rel_label = go_cam_graph_builder.term_label(rec["new_property_uri"]) if rec["new_property_uri"] else ""
                cols = [rec["model_id"], rec["title"], rec["lead_aspect"], primary_label,
                        old_source_label, old_rel_label, target_label, new_rel_label]
                print("\t".join(cols), file=nfr_file)

    # Close report file if it was opened
    if report_file:
        report_file.close()

    if fail_report_file:
        fail_report_file.close()