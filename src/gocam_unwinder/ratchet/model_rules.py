"""Phase A model-level ratchet rules.

These operate on whole-model items: ``item.id`` is the model's filename stem
(== model id) and ``item.path`` is the model ``.ttl``. Two of the three gates
are filename-/id-only (no graph parse); ``modelstate_gate`` parses the model
with rdflib but needs no ontology, so all of Phase A runs without the GO/RO
ontologies loaded.

A model rule is a callable ``(ctx, item) -> Verdict`` (the registry signature).
The two parameterized gates are built by factories so the CLI can close over
runtime config (skip prefixes, the consumed true-GO-CAM id set).
"""

import json
from pathlib import Path

import rdflib

from .core import Verdict

# Canonical published true-GO-CAM classification report (one JSONL line per
# model: {"model_id", "status": "success"|"filtered", ...}; "success" == a true
# GO-CAM). Relative to a pipeline-from-goa skyhook base, e.g.
# https://skyhook.geneontology.io/pipeline-from-goa/main
TRUE_GOCAM_REPORT_PATH = "reports/go-cam/02-filter.jsonl"

LEGO_MODELSTATE = rdflib.URIRef("http://geneontology.org/lego/modelstate")


def _model_state(graph: rdflib.Graph):
    """Return the lego:modelstate of the model-level owl:Ontology subject, or None."""
    for model in graph.subjects(rdflib.RDF.type, rdflib.OWL.Ontology):
        for state in graph.objects(model, LEGO_MODELSTATE):
            return str(state)
    return None


def modelstate_gate(ctx, item, drop_states=("delete",)) -> Verdict:
    """Drop models whose lego:modelstate is in ``drop_states`` (default: delete).

    Mirrors the ``modelstate == "delete"`` skip in gocam_ttl's main(). A model
    with no modelstate triple is kept (only explicit drop states are removed).
    """
    graph = rdflib.Graph()
    graph.parse(str(item.path), format="ttl")
    state = _model_state(graph)
    if state in drop_states:
        return Verdict.reject("modelstate_" + state, modelstate=state)
    return Verdict.survive()


def make_skip_prefix_gate(prefixes):
    """Build a gate that drops models whose filename starts with any prefix."""
    prefixes = tuple(prefixes or ())

    def skip_prefix_gate(ctx, item) -> Verdict:
        name = item.path.name if item.path else (item.id + ".ttl")
        for prefix in prefixes:
            if name.startswith(prefix):
                return Verdict.reject("skip_prefix", prefix=prefix)
        return Verdict.survive()

    return skip_prefix_gate


def make_not_true_gocam_gate(true_gocam_ids):
    """Build R1: drop models whose id is in the consumed true-GO-CAM set.

    Survivors are exactly the models that are NOT true GO-CAMs (the candidate
    pool for standard annotations).
    """
    true_gocam_ids = set(true_gocam_ids or ())

    def not_true_gocam_gate(ctx, item) -> Verdict:
        if item.id in true_gocam_ids:
            return Verdict.reject("is_true_gocam")
        return Verdict.survive()

    return not_true_gocam_gate


def parse_true_gocam_report(text) -> set:
    """Return the set of true-GO-CAM model ids from a filter-report JSONL body.

    Each line is ``{"model_id": ..., "status": "success"|"filtered", ...}``;
    a true GO-CAM is one with ``status == "success"`` (production + pathway-like).
    """
    ids = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("status") == "success":
            ids.add(rec["model_id"])
    return ids


def _load_true_gocam_ids_from_url(url) -> set:
    """Fetch true-GO-CAM ids over HTTP. A URL ending in ``.jsonl`` is taken as
    the filter report itself; otherwise it is treated as a pipeline-from-goa
    skyhook base and the canonical report path is appended.

    A non-default User-Agent is set because skyhook is Cloudflare-fronted and
    403s the default ``Python-urllib`` agent.
    """
    from urllib.request import Request, urlopen
    if not url.endswith(".jsonl"):
        url = url.rstrip("/") + "/" + TRUE_GOCAM_REPORT_PATH
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (gocam-evidence-unwinder ratchet)"})
    with urlopen(req, timeout=120) as resp:
        return parse_true_gocam_report(resp.read().decode("utf-8"))


def load_true_gocam_ids(source) -> set:
    """Load the set of true-GO-CAM model ids (filename stems) to exclude (R1).

    ``source`` may be:
      * an ``http(s)://`` URL -- either a filter-report ``.jsonl`` directly, or a
        pipeline-from-goa skyhook base (e.g.
        ``https://skyhook.geneontology.io/pipeline-from-goa/main``), to which
        ``reports/go-cam/02-filter.jsonl`` is appended;
      * a directory whose file stems are model ids (e.g. a local ``go-cams/json/``);
      * a local filter-report ``.jsonl`` file (parsed by status);
      * a plain text file with one model id per line (``#`` comments ignored).
    """
    s = str(source)
    if s.startswith("http://") or s.startswith("https://"):
        return _load_true_gocam_ids_from_url(s)
    p = Path(s)
    if p.is_dir():
        return {f.stem for f in p.iterdir() if f.is_file()}
    if p.suffix == ".jsonl":
        return parse_true_gocam_report(p.read_text())
    ids = set()
    with open(p) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                ids.add(line)
    return ids


def build_model_rule_registry(skip_prefixes=None, true_gocam_ids=None) -> dict:
    """Map Phase A rule entrypoint names to runnable callables.

    Used by the CLI to resolve ``kind: python`` model rules. The parameterized
    gates close over runtime config; both are safe no-ops when their config is
    empty (no prefixes / empty id set).
    """
    return {
        "modelstate_gate": modelstate_gate,
        "skip_prefix_gate": make_skip_prefix_gate(skip_prefixes),
        "not_true_gocam_gate": make_not_true_gocam_gate(true_gocam_ids),
    }
