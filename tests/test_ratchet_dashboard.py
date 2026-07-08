"""Tests for the self-contained HTML dashboard builder."""

import json
import re

from gocam_unwinder.ratchet.dashboard import build_dashboard, build_data


def _make_out_dir(tmp_path):
    out = tmp_path / "out"
    (out / "rejects").mkdir(parents=True)
    (out / "summary.tsv").write_text(
        "Phase\tStage\tRule\tIn\tRemoved\tOut\n"
        "A\t01\tskip_prefix\t100\t10\t90\n"
        "B\t04\tedge_without_evidence\t200\t5\t195\n")
    (out / "rejects" / "01-skip_prefix.rejects.tsv").write_text(
        "id\trule\treason\tdetail\n"
        + "".join(f"m{i}\tskip_prefix\tskip_prefix\t"
                 + json.dumps({"prefix": "SYNGO" if i % 2 else "R-HSA"}) + "\n"
                 for i in range(10)))
    (out / "rejects" / "04-edge_without_evidence.rejects.tsv").write_text(
        "id\trule\treason\tdetail\n"
        + "".join(f"http://m/A{i}#1\tedge_without_evidence\tedge_without_evidence\t"
                 + json.dumps({"source": "x", "predicate": "part of",
                               "target": "nucleus", "flagged_edges": 1}) + "\n"
                 for i in range(5)))
    return out


def test_build_data_shapes(tmp_path):
    out = _make_out_dir(tmp_path)
    data = build_data(out, "rules")
    assert data["headline"] == {
        "s0_models": 100, "candidate_models": 90,
        "annotation_units": 200, "standard_annotations": 195}
    assert [s["rule_id"] for s in data["stages"]] == ["skip_prefix", "edge_without_evidence"]
    sp = next(s for s in data["stages"] if s["rule_id"] == "skip_prefix")
    labels = {p["label"]: p["count"] for p in sp["patterns"]}
    assert labels == {"prefix: SYNGO": 5, "prefix: R-HSA": 5}
    ewe = next(s for s in data["stages"] if s["rule_id"] == "edge_without_evidence")
    assert ewe["patterns"][0]["label"] == "part of  →  nucleus"
    assert ewe["patterns"][0]["count"] == 5
    # real description pulled from rules/
    assert "evidence" in ewe["description"].lower()


def test_model_axes_and_group_resolution(tmp_path):
    out = _make_out_dir(tmp_path)
    (out / "model_stats.jsonl").write_text(
        json.dumps({"model": "M1", "modelstate": "production",
                    "groups": ["http://informatics.jax.org"],
                    "total_units": 4, "passing_units": 4}) + "\n"
        + json.dumps({"model": "M2", "modelstate": "development",
                      "groups": ["http://informatics.jax.org"],
                      "total_units": 2, "passing_units": 1}) + "\n"
        + json.dumps({"model": "M3", "modelstate": "production",
                      "groups": ["http://zfin.org"],
                      "total_units": 3, "passing_units": 0}) + "\n")
    groups = tmp_path / "groups.yaml"
    groups.write_text(
        "- {id: 'http://informatics.jax.org', label: MGI}\n"
        "- {id: 'http://zfin.org', label: ZFIN}\n")

    data = build_data(out, "rules", groups_yaml=str(groups),
                      noctua_base="https://noctua.example/editor/graph/")
    mb = data["models"]
    assert mb["n"] == 3
    assert mb["passing"] == 2                      # M1, M2 have >=1 passing unit
    assert mb["passing_units"] == 5 and mb["total_units"] == 9
    assert set(mb["modelstates"]) == {"production", "development"}
    assert set(mb["groups"]) == {"MGI", "ZFIN"}    # URIs resolved to labels
    assert mb["histogram"]["100%"] == 1 and mb["histogram"]["0%"] == 1
    assert mb["histogram"]["26–50%"] == 1          # M2 = 50%
    assert mb["noctua_base"] == "https://noctua.example/editor/graph/"
    # compact rows: [model, stateIdx, [groupIdx], total, passing]
    assert len(mb["rows"]) == 3
    m1 = next(r for r in mb["rows"] if r[0] == "M1")
    assert mb["groups"][m1[2][0]] == "MGI" and m1[3] == 4 and m1[4] == 4


def test_no_model_stats_is_graceful(tmp_path):
    out = _make_out_dir(tmp_path)  # no model_stats.jsonl
    data = build_data(out, "rules")
    assert data["models"] is None


def test_build_dashboard_is_self_contained_html(tmp_path):
    out = _make_out_dir(tmp_path)
    html_path = tmp_path / "dashboard.html"
    build_dashboard(out, "rules", html_path)
    html = html_path.read_text()
    assert "/*__DATA__*/" not in html          # placeholder substituted
    assert html.lstrip().startswith("<!DOCTYPE html>")
    # self-contained: no external scripts/stylesheets to fetch
    assert "<script src=" not in html
    assert 'rel="stylesheet"' not in html
    # embedded DATA parses
    m = re.search(r"const DATA = (\{.*?\});\nlet SEL", html, re.S)
    data = json.loads(m.group(1))
    assert data["headline"]["s0_models"] == 100
