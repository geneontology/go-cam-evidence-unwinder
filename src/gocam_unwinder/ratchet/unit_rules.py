"""Phase B unit-level rules: wrap each per-check callable from gocam_ttl as a
ratchet rule over a single annotation unit, plus the per-unit reporting axes
(estimated GPAD lines, contributors).

A unit item carries the parsed model and one annotation in ``item.payload``
(``gcg`` + ``annotation``) -- in-memory, not serialized. The shared ``ctx`` is
the ``GoCamGraphBuilder`` (GO/RO loaded once). A unit rule removes the unit when
its check flags one or more edges; RO-dependent checks SKIP (keep + record) when
no RO ontology is loaded, mirroring gocam_ttl's gating.
"""

import itertools
import weakref
from collections import defaultdict, namedtuple
from pathlib import Path

import rdflib
from rdflib import BNode, OWL, RDF, RDFS, URIRef

from .core import Item, Verdict

DC = rdflib.namespace.DC
DC_CONTRIBUTOR = DC.contributor

# Checks that need the RO ontology; without it they would silently pass, so we
# record them as skipped instead of a misleading "kept".
RO_DEPENDENT = {"mf_causal_mf", "invalid_gp_bp_relation", "invalid_mf_bp_relation"}


def make_unit_rule(check_key):
    """Build a unit rule ``(ctx, item) -> Verdict`` for one check key.

    ctx  -- the GoCamGraphBuilder (provides run_check / term_label / ro_ontology).
    item -- item.payload has "gcg" (GoCamGraph) and "annotation" (StandardAnnotation).
    """

    def unit_rule(ctx, item):
        builder = ctx
        if check_key in RO_DEPENDENT and getattr(builder, "ro_ontology", None) is None:
            return Verdict.skip(check_key + "_skipped_no_ro")

        gcg = item.payload["gcg"]
        annotation = item.payload["annotation"]
        flagged = builder.run_check(check_key, gcg, annotation)
        if flagged:
            # Surface one offending edge in the report (sorted for determinism).
            edge = annotation.edges[sorted(flagged)[0]]
            return Verdict.reject(
                check_key,
                source=builder.term_label(edge.source_type),
                predicate=builder.term_label(edge.property_uri),
                target=builder.term_label(edge.target_type),
                flagged_edges=len(flagged),
            )
        return Verdict.survive()

    unit_rule.__name__ = "unit_rule_" + check_key
    return unit_rule


# ----------------------------------------------------------------------
# GPAD line estimate: minerva's --lego-to-gpad-sparql export rule, replayed
# over one annotation unit
# ----------------------------------------------------------------------
#
# Source of the rule (geneontology/minerva, commit 8977c33):
#   minerva-converter/.../legacy/sparql/GPADSPARQLExport.java      (getGPAD)
#   minerva-converter/.../legacy/sparql/gpad-basic.rq               (candidates)
#   minerva-converter/.../legacy/sparql/gpad-relation-evidence-multiple.rq
#   minerva-converter/.../legacy/sparql/gpad-extensions.rq
#   minerva-converter/.../legacy/sparql/DefaultGPADData.java        (equality = dedup)
#   minerva-core/src/main/resources/org/geneontology/minerva/ro-gpad.ofn
#   minerva-core/.../CoreMolecularModelManager.java  (createInferredModel: abox +
#     the tbox RBox, reasoned with the rules compiled from ro-gpad.ofn -- NO class
#     hierarchy, so every "type" below is an asserted rdf:type).
#
# INVARIANT: the inference closure below is ro-gpad.ofn's property axioms and
# nothing else -- NOT the full RO. ro-gpad.ofn is a trimmed RO: `regulates` and
# its descendants are not sub-properties of RO_0002418 there, so an
# MF -positively_regulates-> BP edge yields no GPAD line.

_OBO = "http://purl.obolibrary.org/obo/"


def _obo(local):
    return URIRef(_OBO + local)


_ENABLED_BY = _obo("RO_0002333"); _ENABLES = _obo("RO_0002327"); _CONTRIBUTES_TO = _obo("RO_0002326")
_PART_OF = _obo("BFO_0000050"); _HAS_PART = _obo("BFO_0000051"); _OCCURS_IN = _obo("BFO_0000066")
_LOCATED_IN = _obo("RO_0001025"); _IS_ACTIVE_IN = _obo("RO_0002432"); _INVOLVED_IN = _obo("RO_0002331")
_AUOW = _obo("RO_0002264"); _AUOW_POS = _obo("RO_0004032"); _AUOW_NEG = _obo("RO_0004033")
_AUO = _obo("RO_0002263"); _AUO_POS = _obo("RO_0004034"); _AUO_NEG = _obo("RO_0004035")
_CUOW = _obo("RO_0002418"); _CUOW_POS = _obo("RO_0004047"); _CUOW_NEG = _obo("RO_0004046")

# gpad-basic.rq VALUES ?rel: the twelve GPAD qualifiers.
_QUALIFIERS = frozenset({_ENABLES, _INVOLVED_IN, _PART_OF, _AUO, _AUO_POS, _AUO_NEG, _AUOW,
                         _AUOW_POS, _AUOW_NEG, _CONTRIBUTES_TO, _LOCATED_IN, _IS_ACTIVE_IN})
# gpad-basic.rq "most specific relationship" filter: a qualifier is dropped for a
# (GP node, target node) pair when a strictly more specific one also holds.
# Pairs from the RO sub-property hierarchy (minerva infers the parents from the
# asserted child, so the closure is what matters).
_STRICT_SUB = {
    _AUO: frozenset({_AUOW}), _AUO_POS: frozenset({_AUO, _AUOW_POS, _AUOW}),
    _AUO_NEG: frozenset({_AUO, _AUOW_NEG, _AUOW}),
    _AUOW_POS: frozenset({_AUOW}), _AUOW_NEG: frozenset({_AUOW}),
}
# ro-gpad.ofn SubObjectPropertyOf axioms, flattened onto the relations the
# chains consume.
_SUB_TO_CUOW = frozenset({_CUOW, _obo("RO_0002010"), _obo("RO_0012013"), _obo("RO_0012014"),
                          _obo("RO_0019004"), _CUOW_POS, _CUOW_NEG})
_SUB_TO_PART_OF = frozenset({_PART_OF, _obo("RO_0019009"), _obo("RO_0019007"), _obo("RO_0019008")})
_SUB_TO_HAS_PART = frozenset({_HAS_PART, _obo("RO_0002180"), _obo("RO_0019005"), _obo("RO_0019006")})
# ro-gpad.ofn property chains (p1 o p2 -> r) plus the transitive properties
# (part_of, has_part, located_in) written as p o p -> p.
_CHAINS = (
    (_PART_OF, _OCCURS_IN, _OCCURS_IN), (_PART_OF, _ENABLES, _CONTRIBUTES_TO),
    (_OCCURS_IN, _PART_OF, _OCCURS_IN), (_LOCATED_IN, _PART_OF, _LOCATED_IN),
    (_CONTRIBUTES_TO, _PART_OF, _INVOLVED_IN), (_CONTRIBUTES_TO, _OCCURS_IN, _IS_ACTIVE_IN),
    (_ENABLES, _PART_OF, _INVOLVED_IN), (_ENABLES, _HAS_PART, _ENABLES),
    (_ENABLES, _OCCURS_IN, _IS_ACTIVE_IN), (_ENABLES, _CUOW, _AUOW),
    (_ENABLES, _CUOW_NEG, _AUOW_NEG), (_ENABLES, _CUOW_POS, _AUOW_POS),
    (_INVOLVED_IN, _PART_OF, _INVOLVED_IN),
    (_PART_OF, _PART_OF, _PART_OF), (_HAS_PART, _HAS_PART, _HAS_PART),
    (_LOCATED_IN, _LOCATED_IN, _LOCATED_IN),
)

_MF_ROOT = _obo("GO_0003674")
_ROOT_TERMS = frozenset({_MF_ROOT, _obo("GO_0008150"), _obo("GO_0005575")})
_ND = _obo("ECO_0000307")
_EXCLUDED_TYPES = frozenset({RDFS.Resource, OWL.Thing, OWL.NamedIndividual, _obo("BFO_0000002"),
                             _obo("BFO_0000003"), _obo("BFO_0000004"), _obo("BFO_0000015"),
                             _obo("BFO_0000040")})
_GP_EXCLUDED_TYPES = _EXCLUDED_TYPES | {_obo("PR_000000001")}
_GP_EXCLUDED_PREFIXES = (_OBO + "GO_", _OBO + "PO_", _OBO + "UBERON_", _OBO + "CL_",
                         _OBO + "EMAPA_", _OBO + "CHEBI_", "http://www.biopax.org/")
# gpad-extensions.rq VALUES ?extension_rel.
_EXTENSION_RELS = frozenset(_obo(x) for x in """
BFO_0000050 BFO_0000066 GOCHEREL_0000001 GOCHEREL_0000002 GOCHEREL_0000003 GOCHEREL_0000004
GOREL_0000040 GOREL_0001004 GOREL_0001006 GOREL_0002003 GOREL_0002004 GOREL_0002005
GOREL_0012006 RO_0002490 RO_0002491 RO_0000053 RO_0000087 RO_0001025 RO_0002007 RO_0002008
RO_0002011 RO_0002087 RO_0002090 RO_0002092 RO_0002093 RO_0002162 RO_0002202 RO_0002211
RO_0002212 RO_0002213 RO_0002215 RO_0002216 RO_0002220 RO_0002223 RO_0002224 RO_0002230
RO_0002231 RO_0002232 RO_0002233 RO_0002234 RO_0002263 RO_0002264 RO_0002295 RO_0002296
RO_0002297 RO_0002298 RO_0002299 RO_0002313 RO_0002315 RO_0002327 RO_0002332 RO_0002334
RO_0002338 RO_0002339 RO_0002340 RO_0002341 RO_0002342 RO_0002343 RO_0002344 RO_0002345
RO_0002348 RO_0002349 RO_0002355 RO_0002356 RO_0002412 RO_0002428 RO_0002429 RO_0002430
RO_0002431 RO_0002495 RO_0002565 RO_0002578 RO_0002629 RO_0002630 RO_0004008 RO_0004009
""".split())
_EVIDENCE_WITH = URIRef("http://geneontology.org/lego/evidence-with")
_PROVIDED_BY = URIRef("http://purl.org/pav/providedBy")
_MAX_EXPLANATIONS = 32   # cap per derived fact; explanations beyond it add no line in practice

# One GPAD line as minerva deduplicates it (DefaultGPADData.equals; the
# per-model constants noctua-model-id and model-state are omitted).
GpadLine = namedtuple("GpadLine", "gp operator qualifier go_class extensions eco references "
                                  "with_from date assigned_by contributors")
UnitGpadLines = namedtuple("UnitGpadLines", "lines gp_types_with_specific_mf")


def evidence_pairs(annotation):
    """The pre-2026-10-26 line estimate: the number of (edge, evidence
    individual) pairs over ALL of the unit's edges. Kept as a plain size
    measure; it over-counts GPAD lines about 2x (extension and placement edges
    get no line of their own, and minerva deduplicates)."""
    return sum(len(edge.evidence_uris) for edge in annotation.edges.values())


def _evidence_rows(g, evidence_uri, cache):
    """GPADEvidence rows for one evidence individual, as
    gpad-relation-evidence-multiple.rq binds them: one row per (asserted ECO
    type, dc:date, evidence-with value); NO row when the individual lacks a
    type, dc:contributor, dc:date or dc:source (those patterns are mandatory
    in the query, so such evidence yields no line)."""
    rows = cache.get(evidence_uri)
    if rows is None:
        types = [t for t in g.objects(evidence_uri, RDF.type)
                 if isinstance(t, URIRef) and t not in _EXCLUDED_TYPES]
        contributors = frozenset(str(c) for c in g.objects(evidence_uri, DC.contributor))
        dates = sorted(str(d) for d in g.objects(evidence_uri, DC.date))
        references = frozenset(str(s) for s in g.objects(evidence_uri, DC.source))
        withs = sorted(str(w) for w in g.objects(evidence_uri, _EVIDENCE_WITH)) or [None]
        provided = frozenset(str(p) for p in g.objects(evidence_uri, _PROVIDED_BY))
        rows = []
        if types and contributors and dates and references:
            for eco, date, with_from in itertools.product(types, dates, withs):
                rows.append((eco, references, with_from, date, provided, contributors))
        cache[evidence_uri] = rows
    return rows


def _go_classes(type_nodes, g):
    """(GO class, operator) pairs for a target node's asserted types: a GO IRI
    -> NONE; a blank node ``owl:complementOf <GO class>`` -> NOT."""
    out = []
    for t in type_nodes:
        if isinstance(t, URIRef):
            if str(t).startswith(_OBO + "GO_"):
                out.append((t, "NONE"))
        elif isinstance(t, BNode):
            for wrapped in g.objects(t, OWL.complementOf):
                if isinstance(wrapped, URIRef) and str(wrapped).startswith(_OBO + "GO_"):
                    out.append((wrapped, "NOT"))
    return out


def _closure(edges):
    """Forward-chain ro-gpad.ofn's property axioms over the unit's edges.
    Returns ``{(s, p, o): set of explanations}``, an explanation being the
    frozenset of asserted edge ids the fact derives from (Arachne's
    ``explain``). Inverses and sub-properties are applied to the asserted
    edges first; the chains iterate to a fixpoint."""
    facts = defaultdict(set)

    def add(s, p, o, explanation):
        if s == o:
            return False
        bucket = facts[(s, p, o)]
        if explanation in bucket or len(bucket) >= _MAX_EXPLANATIONS:
            return False
        bucket.add(explanation)
        return True

    for e in edges:
        ex = frozenset([e.bnode_id])
        s, p, o = e.source_uri, e.property_uri, e.target_uri
        add(s, p, o, ex)
        if p == _ENABLED_BY:
            add(o, _ENABLES, s, ex)
        elif p == _ENABLES:
            add(o, _ENABLED_BY, s, ex)
        if p in _SUB_TO_PART_OF:
            add(s, _PART_OF, o, ex)
            add(o, _HAS_PART, s, ex)
        elif p in _SUB_TO_HAS_PART:
            add(s, _HAS_PART, o, ex)
            add(o, _PART_OF, s, ex)
        if p in _SUB_TO_CUOW:
            add(s, _CUOW, o, ex)
        if p == _CUOW_POS:
            add(s, _CUOW_POS, o, ex)
        elif p == _CUOW_NEG:
            add(s, _CUOW_NEG, o, ex)

    changed = True
    while changed:
        changed = False
        by_sp = defaultdict(list)
        for (s, p, o), explanations in facts.items():
            by_sp[(s, p)].append((o, explanations))
        for (s, p, o), explanations in list(facts.items()):
            for p1, p2, r in _CHAINS:
                if p != p1:
                    continue
                for o2, explanations2 in by_sp.get((o, p2), ()):
                    for e1 in list(explanations):
                        for e2 in list(explanations2):
                            if add(s, r, o2, e1 | e2):
                                changed = True
    return facts


class _RegulationIndex:
    """GPADSPARQLExport's ``regulators`` map (regulation_targets.rq over the
    tbox): a GO class C "regulates" X when some is_a ancestor of C (C included)
    carries a ``regulates``-family restriction on X (logical definition or
    plain subClassOf); the regulated set is X and its is_a ancestors."""

    def __init__(self, builder):
        self.ont = builder.ontology
        props = {"RO:0002211", "RO:0002212", "RO:0002213"}
        ro = getattr(builder, "ro_ontology", None)
        if ro is not None:
            from ..gocam_ttl import get_relation_descendants
            props |= {"RO:" + str(u).rsplit("RO_", 1)[1]
                      for u in get_relation_descendants(ro, _OBO + "RO_0002211") if "RO_" in str(u)}
        props = sorted(props)
        self.fillers = defaultdict(set)
        for ld in getattr(self.ont, "all_logical_definitions", None) or []:
            for prop, filler in ld.restrictions:
                if prop in props:
                    self.fillers[ld.class_id].add(filler)
        for node in self.ont.nodes():
            for parent in self.ont.parents(node, relations=props):
                self.fillers[node].add(parent)
        self._ancestors = {}
        self._regulated = {}

    def ancestors(self, curie):
        found = self._ancestors.get(curie)
        if found is None:
            found = frozenset(self.ont.ancestors(curie, relations=["subClassOf"], reflexive=True))
            self._ancestors[curie] = found
        return found

    def regulated_by(self, curie):
        found = self._regulated.get(curie)
        if found is None:
            found = set()
            for c in self.ancestors(curie):
                for filler in self.fillers.get(c, ()):
                    found |= self.ancestors(filler)
            found = frozenset(found)
            self._regulated[curie] = found
        return found


_REGULATION_INDEXES = weakref.WeakKeyDictionary()


def _regulation_index(builder):
    if builder is None or getattr(builder, "ontology", None) is None:
        return None
    index = _REGULATION_INDEXES.get(builder)
    if index is None:
        index = _RegulationIndex(builder)
        _REGULATION_INDEXES[builder] = index
    return index


def _go_curie(uri):
    return "GO:" + str(uri).rsplit("GO_", 1)[1]


def unit_gpad_lines(annotation, gcg, builder=None, _evidence_cache=None):
    """The distinct GPAD lines minerva's ``--lego-to-gpad-sparql`` emits for a
    model consisting of this unit alone, as ``UnitGpadLines(lines,
    gp_types_with_specific_mf)``; the second field feeds the model-wide root-MF
    rule in ``model_gpad_lines``.

    The rule (GPADSPARQLExport.getGPAD, after ``_closure``):
      1. Candidates are inferred facts GP -q-> T where q is one of the twelve
         GPAD qualifiers, GP has an asserted IRI type that is not a GO / PO /
         UBERON / CL / EMAPA / CHEBI / BioPAX class (so a complex individual
         typed by a GO term is never a GP) and T has an asserted GO type (a
         complementOf blank node gives NOT). One line family per GP type and
         per T type when a node has several types. A T typed as the MF root
         keeps only enables / contributes_to.
      2. Per (GP node, T node) only the most specific qualifier survives
         (acts_upstream_of family).
      3. Every edge in an explanation must carry usable evidence; the line's
         evidence comes from the explanation edges touching T: for GP enables
         MF the enabled_by edge, for involved_in / is_active_in /
         acts_upstream_of_or_within the MF->BP / MF->CC edge, for a BP part_of
         BP2 or CC part_of CC2 hop the hop's own edge. Edges on the GP side
         (occurs_in, has_input, ...) fold into the extension column and never
         get a line of their own.
      4. One line per evidence row: (ECO type, dc:date, evidence-with value),
         keyed with the references, providedBy and contributors; the whole
         tuple is deduplicated, so two edges or two GP individuals with
         identical evidence metadata yield ONE line.
      5. A root-term line (MF/BP/CC root) needs ND evidence; a GP node's
         annotation to a class regulated by another of its annotations is
         dropped (needs ``builder.ontology`` for GO's logical definitions;
         skipped without a builder).
    """
    g = gcg.g
    cache = _evidence_cache if _evidence_cache is not None else {}
    edges = list(annotation.edges.values())
    by_id = {e.bnode_id: e for e in edges}
    nodes = set()
    for e in edges:
        nodes.add(e.source_uri)
        nodes.add(e.target_uri)
    types = {n: [t for t in g.objects(n, RDF.type) if t != OWL.NamedIndividual] for n in nodes}
    gp_types = {n: [t for t in ts if isinstance(t, URIRef) and t not in _GP_EXCLUDED_TYPES
                    and not str(t).startswith(_GP_EXCLUDED_PREFIXES)] for n, ts in types.items()}
    go_classes = {n: _go_classes(ts, g) for n, ts in types.items()}
    regulation = _regulation_index(builder)

    rows_of = {}

    def rows(edge_id):
        found = rows_of.get(edge_id)
        if found is None:
            found = []
            for ev in by_id[edge_id].evidence_uris:
                found.extend(_evidence_rows(g, ev, cache))
            rows_of[edge_id] = found
        return found

    facts = _closure(edges)
    candidates = defaultdict(set)            # (GP node, T node) -> qualifiers
    extension_facts = defaultdict(list)      # T node -> [(rel, filler node, explanations)]
    for (s, p, o), explanations in facts.items():
        if p in _EXTENSION_RELS:
            extension_facts[s].append((p, o, explanations))
        if p in _QUALIFIERS and gp_types.get(s) and go_classes.get(o):
            if _MF_ROOT in types[o] and p not in (_ENABLES, _CONTRIBUTES_TO):
                continue
            candidates[(s, o)].add(p)

    specific_mf = set()                      # GP types with a non-root MF annotation
    node_classes = defaultdict(set)          # GP node -> GO classes it is annotated to
    for (gp, t), qualifiers in candidates.items():
        for cls, _ in go_classes[t]:
            node_classes[gp].add(cls)
            if cls != _MF_ROOT and qualifiers & {_ENABLES, _CONTRIBUTES_TO}:
                specific_mf.update(gp_types[gp])

    regulated = {}
    if regulation is not None:
        for gp, classes in node_classes.items():
            found = set()
            for cls in classes:
                found |= regulation.regulated_by(_go_curie(cls))
            regulated[gp] = {cls for cls in classes if _go_curie(cls) in found}

    extension_cache = {}

    def extensions(t, gp, references):
        key = (t, gp, references)
        found = extension_cache.get(key)
        if found is None:
            found = set()
            for rel, filler, explanations in extension_facts.get(t, ()):
                if filler == gp or filler == t:
                    continue
                # every fact of some explanation has evidence with the line's reference
                if not any(all(any(r[1] == references for r in rows(eid)) for eid in ex)
                           for ex in explanations):
                    continue
                for ft in types.get(filler, ()):
                    if (isinstance(ft, URIRef) and ft not in _EXCLUDED_TYPES
                            and ft not in _ROOT_TERMS and not str(ft).startswith(_OBO + "CARO_")):
                        found.add((rel, ft))
            if any(str(ft).startswith(_OBO + "EMAPA_") for _, ft in found):
                found = {(rel, ft) for rel, ft in found if not str(ft).startswith(_OBO + "UBERON_")}
            found = frozenset(found)
            extension_cache[key] = found
        return found

    lines = set()
    for (gp, t), qualifiers in candidates.items():
        for q in qualifiers:
            if any(sub in qualifiers for sub, supers in _STRICT_SUB.items() if q in supers):
                continue
            for cls, operator in go_classes[t]:
                if cls in regulated.get(gp, ()):
                    continue
                for explanation in facts[(gp, q, t)]:
                    if not all(rows(eid) for eid in explanation):
                        continue
                    for eid in explanation:
                        edge = by_id[eid]
                        if edge.source_uri != t and edge.target_uri != t:
                            continue
                        for eco, references, with_from, date, provided, contributors in rows(eid):
                            if cls in _ROOT_TERMS and eco != _ND:
                                continue
                            ext = extensions(t, gp, references)
                            for gp_type in gp_types[gp]:
                                if cls == _MF_ROOT and gp_type in specific_mf:
                                    continue
                                lines.add(GpadLine(gp_type, operator, q, cls, ext, eco, references,
                                                   with_from, date, provided, contributors))
    return UnitGpadLines(frozenset(lines), frozenset(specific_mf))


def gpad_lines_estimate(annotation, gcg, builder=None):
    """Estimated GPAD lines for one annotation unit on its own: the size of
    ``unit_gpad_lines(...).lines`` (the rule is documented there). The per-model
    figures in reports come from ``model_gpad_lines``, which also applies the two
    model-wide parts of minerva's rule."""
    return len(unit_gpad_lines(annotation, gcg, builder).lines)


def model_gpad_lines(gcg, builder=None):
    """Per-unit estimated GPAD lines for a parsed model, in
    ``gcg.standard_annotations`` order, such that their sum is the model's
    estimated line count. Two parts of minerva's rule span units:
      * a GP type with a non-root MF annotation anywhere in the model loses its
        root-MF (GO:0003674) lines everywhere (gpsWithAnyMFNotRootMF);
      * lines are deduplicated model-wide, so a line two units both produce is
        counted once, in the lower-indexed unit.
    CAVEAT: removing only the counted unit of such a duplicate pair loses no
    line in the real export; the ledger charges it to that unit anyway."""
    cache = {}
    per_unit = [unit_gpad_lines(a, gcg, builder, cache) for a in gcg.standard_annotations]
    specific_mf = set()
    for u in per_unit:
        specific_mf |= u.gp_types_with_specific_mf
    seen = set()
    counts = []
    for u in per_unit:
        lines = {l for l in u.lines if not (l.go_class == _MF_ROOT and l.gp in specific_mf)}
        counts.append(len(lines - seen))
        seen |= lines
    return counts


def unit_contributors(gcg, annotation):
    """Sorted dc:contributor values on the unit's evidence individuals and on
    its edges' reified owl:Axiom nodes (both carry them in Noctua output; the
    model-level and individual-level contributors are deliberately excluded)."""
    found = set()
    for edge in annotation.edges.values():
        found.update(str(c) for c in gcg.g.objects(edge.bnode, DC_CONTRIBUTOR))
        for evidence_uri in edge.evidence_uris:
            found.update(str(c) for c in gcg.g.objects(evidence_uri, DC_CONTRIBUTOR))
    return sorted(found)


def model_contributors(gcg):
    """Sorted dc:contributor values on the model-level owl:Ontology subject."""
    if not gcg.model_id:
        return []
    model_uri = rdflib.URIRef(gcg.model_id)
    return sorted({str(c) for c in gcg.g.objects(model_uri, DC_CONTRIBUTOR)})


def parse_model_units(builder, ttl_path):
    """Parse a model WITHOUT classifying; return ``(gcg, units)`` where ``units``
    is one Item per raw annotation unit (connected-component subgraph). The
    returned ``gcg`` carries model-level metadata (modelstate, groups, title)
    for per-model stats.

    Unit meta carries the reporting axes: ``gpad_lines_est`` (see
    ``model_gpad_lines`` / ``unit_gpad_lines``), ``evidence_pairs`` (see
    ``evidence_pairs``) and ``contributors`` (see ``unit_contributors``).
    """
    gcg = builder.parse_ttl(str(ttl_path), classify=False)
    line_counts = model_gpad_lines(gcg, builder)
    items = []
    for index, (annotation, lines_est) in enumerate(zip(gcg.standard_annotations, line_counts)):
        items.append(Item(
            id=f"{gcg.model_id}#{index}",
            path=Path(ttl_path),
            meta={"model_id": gcg.model_id, "unit_index": index,
                  "edges": len(annotation.edges),
                  "gpad_lines_est": lines_est,
                  "evidence_pairs": evidence_pairs(annotation),
                  "contributors": unit_contributors(gcg, annotation)},
            payload={"gcg": gcg, "annotation": annotation},
        ))
    return gcg, items


def count_model(builder, ttl_path):
    """Cheap counting pass over one model (no checks run): the per-model fields
    the ratchet reports for a model that Phase A removed. Same parser as Phase
    B, so the line estimate is comparable across phases."""
    gcg, units = parse_model_units(builder, ttl_path)
    return {
        "modelstate": gcg.modelstate,
        "groups": gcg.groups or [],
        "title": gcg.title,
        "contributors": model_contributors(gcg),
        "total_units": len(units),
        "gpad_lines_est": sum(u.meta["gpad_lines_est"] for u in units),
        "evidence_pairs": sum(u.meta["evidence_pairs"] for u in units),
    }


def extract_units(builder, ttl_path):
    """Return one Item per raw annotation unit (see ``parse_model_units``)."""
    return parse_model_units(builder, ttl_path)[1]


def build_unit_rule_registry(check_keys=None):
    """Map unit-rule entrypoint names (``unit_<key>``) to callables.

    Defaults to the full ordered check set from the builder. Used by the CLI to
    resolve ``kind: python`` unit rules.
    """
    from ..gocam_ttl import GoCamGraphBuilder
    keys = check_keys if check_keys is not None else GoCamGraphBuilder.UNIT_CHECK_KEYS
    return {f"unit_{key}": make_unit_rule(key) for key in keys}
