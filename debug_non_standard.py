#!/usr/bin/env python3
"""Debug script: iterate over non-standard annotations in a directory of GO-CAM TTLs."""

import argparse

import rdflib

from gocam_unwinder.gocam_ttl import (
    GoCamGraphBuilder,
    collect_model_files,
    load_skip_filenames,
)

# Lead-aspect priority order for picking the single bucket per annotation.
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


def classify_multiple_mf_bp(annot):
    """Sub-classify a multiple_mf_bp failure.

    Returns "same_bp", "same_mf", or None (ambiguous).
    """
    flagged_bnodes = annot.failed_checks.get("multiple_mf_bp", set())
    if not flagged_bnodes:
        return None

    source_types = set()
    target_uris = set()
    for bnode_id in flagged_bnodes:
        edge = annot.edges[bnode_id]
        source_types.add(edge.source_type)
        target_uris.add(edge.target_uri)

    if len(target_uris) == 1 and len(source_types) > 1:
        return "same_bp"
    if len(source_types) == 1 and len(target_uris) > 1:
        return "same_mf"
    # Also check by type rather than individual URI
    target_types = {annot.edges[b].target_type for b in flagged_bnodes}
    if len(target_types) == 1 and len(source_types) > 1:
        return "same_bp"
    source_uris = {annot.edges[b].source_uri for b in flagged_bnodes}
    if len(source_uris) == 1 and len(target_types) > 1:
        return "same_mf"

    return None


def has_differing_eco_types(annot, gocam):
    """True if edges in the annotation have evidence with different ECO types."""
    eco_sets_per_edge = []
    for edge in annot.edges.values():
        eco_types = set()
        for ev_uri in edge.evidence_uris:
            for rdf_type in gocam.g.objects(ev_uri, rdflib.RDF.type):
                if rdf_type != rdflib.OWL.NamedIndividual:
                    eco_types.add(rdf_type)
        eco_sets_per_edge.append(eco_types)

    if len(eco_sets_per_edge) < 2:
        return False
    first = eco_sets_per_edge[0]
    return any(s != first for s in eco_sets_per_edge[1:])


def get_eco_types_for_annot(annot, gocam):
    """Return the set of ECO type URIs across all evidence in an annotation."""
    eco_types = set()
    for edge in annot.edges.values():
        for ev_uri in edge.evidence_uris:
            for rdf_type in gocam.g.objects(ev_uri, rdflib.RDF.type):
                if rdf_type != rdflib.OWL.NamedIndividual:
                    eco_types.add(rdf_type)
    return eco_types


def print_summary(name, bucket):
    models = len({r[0] for r in bucket})
    print(f"  {name + ':':<30s} {len(bucket):>4d} annotations across {models:>4d} models")


def main():
    ap = argparse.ArgumentParser(description="Debug non-standard GO-CAM annotations")
    ap.add_argument("models_dir", help="Directory of TTL files")
    ap.add_argument("-o", "--ontology", required=True, help="GO ontology (JSON)")
    ap.add_argument("-r", "--ro", help="RO ontology (OWL)")
    ap.add_argument("--groups-yaml", help="groups.yaml for label resolution")
    ap.add_argument("--skip-prefix", action="append", dest="skip_prefixes", default=[])
    ap.add_argument("--skip-file", dest="skip_file",
                    help="Skip TTL files whose filename appears in FILE (one .ttl filename per line, e.g. true GO-CAM models to exclude)")
    ap.add_argument("--tsv-output", help="TSV output file for bucketed annotation report")
    ap.add_argument("--no-label-api", action="store_true",
                    help="Disable OLS API fallback for resolving non-GO/RO term labels (enabled by default)")
    args = ap.parse_args()

    builder = GoCamGraphBuilder(args.ontology, args.ro, args.groups_yaml,
                                resolve_labels_api=not args.no_label_api)

    skip_filenames = load_skip_filenames(args.skip_file) if args.skip_file else set()
    ttl_files = sorted(collect_model_files(
        args.models_dir,
        skip_prefixes=args.skip_prefixes,
        skip_filenames=skip_filenames,
    ))

    # Buckets — each entry is (model_id, title, annot_index, annot)
    nested_mf_extensions = []
    nested_bp_extensions = []
    nested_cc_extensions = []
    multi_mf_same_bp = []
    multi_bp_same_mf = []
    extension_eco_differs = []
    translated_chain_relations = []  # (e.g., regulates_o_occurs_in)
    unclassified = []

    bucket_by_aspect = {
        "MF": nested_mf_extensions,
        "BP": nested_bp_extensions,
        "CC": nested_cc_extensions,
    }

    # TSV rows: (model_id, title, bucket, source, predicate, target, eco_codes, groups)
    tsv_rows = []

    total_non_std = 0

    for ttl_path in ttl_files:
        gocam = builder.parse_ttl(ttl_path)

        if gocam.modelstate == "delete":
            continue

        groups = "|".join(gocam.groups) if gocam.groups else ""

        # Buckets 1a/1b/1c: nested_<aspect>_extensions — check ALL annotations (std + non-std).
        # An annotation lands in the single aspect bucket of its lead aspect (BP > CC > MF).
        all_annots = gocam.standard_annotations + gocam.non_standard_annotations
        for i, annot in enumerate(all_annots):
            lead_aspect, nested_edges = find_nested_extensions(annot, builder)
            if not nested_edges:
                continue
            bucket_name = f"nested_{lead_aspect.lower()}_extensions"
            bucket_by_aspect[lead_aspect].append((gocam.model_id, gocam.title, i, annot))
            eco_codes = "|".join(sorted(builder.term_label(e) for e in get_eco_types_for_annot(annot, gocam)))
            print(f"[{bucket_name}] {gocam.model_id} — {gocam.title}  annot[{i}]")
            for edge in nested_edges:
                src = builder.term_label(edge.source_type) if edge.source_type else "?"
                rel = builder.term_label(edge.property_uri) if edge.property_uri else "?"
                tgt = builder.term_label(edge.target_type) if edge.target_type else "?"
                print(f"             {src} —[{rel}]→ {tgt}")
                tsv_rows.append((gocam.model_id, gocam.title, bucket_name,
                                 src, rel, tgt, eco_codes, groups))

        if not gocam.non_standard_annotations:
            continue

        print(f"=== {gocam.model_id} — {gocam.title} ===")
        print(f"    Standard: {len(gocam.standard_annotations)}  Non-standard: {len(gocam.non_standard_annotations)}")

        for i, annot in enumerate(gocam.non_standard_annotations):
            total_non_std += 1
            checks = ", ".join(annot.failed_checks.keys())
            print(f"  [{i}] failed: {checks}  edges: {len(annot.edges)}")

            for bnode_id, edge in annot.edges.items():
                src = builder.term_label(edge.source_type) if edge.source_type else "?"
                rel = builder.term_label(edge.property_uri) if edge.property_uri else "?"
                tgt = builder.term_label(edge.target_type) if edge.target_type else "?"
                ev_count = len(edge.evidence_uris)
                print(f"      {src} —[{rel}]→ {tgt}  (evidence: {ev_count})")

            record = (gocam.model_id, gocam.title, i, annot)
            classified = False
            eco_codes = "|".join(sorted(builder.term_label(e) for e in get_eco_types_for_annot(annot, gocam)))

            # Bucket 2 & 3: multiple_mf_bp sub-classification
            if "multiple_mf_bp" in annot.failed_checks:
                shape = classify_multiple_mf_bp(annot)
                bucket_name = None
                if shape == "same_bp":
                    multi_mf_same_bp.append(record)
                    bucket_name = "multi_mf_same_bp"
                    classified = True
                elif shape == "same_mf":
                    multi_bp_same_mf.append(record)
                    bucket_name = "multi_bp_same_mf"
                    classified = True
                if bucket_name:
                    for bnode_id in annot.failed_checks["multiple_mf_bp"]:
                        edge = annot.edges[bnode_id]
                        tsv_rows.append((gocam.model_id, gocam.title, bucket_name,
                                         builder.term_label(edge.source_type) if edge.source_type else "?",
                                         builder.term_label(edge.property_uri) if edge.property_uri else "?",
                                         builder.term_label(edge.target_type) if edge.target_type else "?",
                                         eco_codes, groups))

            # Bucket 4: inconsistent_evidence with different ECOs (only if no other failures)
            if annot.failed_checks.keys() == {"inconsistent_evidence"}:
                if has_differing_eco_types(annot, gocam):
                    extension_eco_differs.append(record)
                    classified = True
                    for edge in annot.edges.values():
                        tsv_rows.append((gocam.model_id, gocam.title, "extension_eco_differs",
                                         builder.term_label(edge.source_type) if edge.source_type else "?",
                                         builder.term_label(edge.property_uri) if edge.property_uri else "?",
                                         builder.term_label(edge.target_type) if edge.target_type else "?",
                                         eco_codes, groups))

            if not classified:
                unclassified.append(record)

        print()

    # Summary
    print("=== Bucket Summary ===")
    print_summary("nested_mf_extensions", nested_mf_extensions)
    print_summary("nested_bp_extensions", nested_bp_extensions)
    print_summary("nested_cc_extensions", nested_cc_extensions)
    print_summary("multi_mf_same_bp", multi_mf_same_bp)
    print_summary("multi_bp_same_mf", multi_bp_same_mf)
    print_summary("extension_eco_differs", extension_eco_differs)
    print_summary("unclassified", unclassified)
    print(f"  {'total non-standard:':<30s} {total_non_std:>4d}")

    # Write TSV report
    if args.tsv_output:
        tsv_headers = ["Model ID", "Title", "Bucket", "Source", "Predicate", "Target", "ECO Codes", "Groups"]
        with open(args.tsv_output, "w") as f:
            f.write("\t".join(tsv_headers) + "\n")
            for row in tsv_rows:
                f.write("\t".join(str(v) for v in row) + "\n")
        print(f"\nTSV report written to {args.tsv_output} ({len(tsv_rows)} rows)")


if __name__ == "__main__":
    main()