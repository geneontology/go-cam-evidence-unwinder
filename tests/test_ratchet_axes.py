"""Tests for the reporting axes added 2026-10-26: estimated GPAD lines,
contributors, the complete failing-check set on reject records, the ledger and
its roll-ups, the model lists, the shard merge of those files, their surfacing
in the dashboard, and the GPAD-line validator script.

Fixture facts (verified with an independent rdflib pass over the TTLs):
  66c7d41500000016.ttl  1 unit, 12 edges, 13 lego:evidence triples (one edge
                        has no evidence), one contributor everywhere.
  MGI_MGI_1100089.ttl   29 units, 99 lego:evidence triples, 6 contributors.
  SYNGO_5371 / R-HSA-9937080  12 / 30 estimated lines (Phase A counting pass).
"""

import json
import re
import shutil
from pathlib import Path

import pytest

from gocam_unwinder.ratchet.cli import (
    SET_ASIDE_PHASE,
    StageStat,
    _aggregate_stage_stats,
    _concat_shard_outputs,
    _write_merged_rollups,
    run_ratchet,
)
from gocam_unwinder.ratchet.dashboard import build_dashboard, build_data
from gocam_unwinder.ratchet.runner import REJECTS_TSV_HEADER
from gocam_unwinder.ratchet.summary import (
    LEDGER_COLUMNS,
    MODEL_LIST_COLUMNS,
    SUMMARY_COLUMNS,
    read_ledger,
    read_model_stats,
    read_rollup,
)
from gocam_unwinder.ratchet.unit_rules import (
    count_model,
    gpad_lines_estimate,
    model_contributors,
    parse_model_units,
    unit_contributors,
)

RES = Path("resources/test")
ORCID_8769 = "https://orcid.org/0000-0001-8769-177X"
VALIDATOR = Path("scripts/validate_gpad_lines.py")


def _corpus(root, names):
    d = root / "models"
    d.mkdir()
    for name in names:
        shutil.copy(RES / name, d / name)
    return d


def _tsv(path):
    lines = Path(path).read_text().splitlines()
    header = lines[0].split("\t")
    return header, [dict(zip(header, l.split("\t"))) for l in lines[1:] if l]


def _jsonl(path):
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


# ----------------------------------------------------------------------
# unit meta: lines estimate + contributors
# ----------------------------------------------------------------------

def test_unit_meta_carries_lines_and_contributors(builder):
    gcg, units = parse_model_units(builder, RES / "66c7d41500000016.ttl")
    assert len(units) == 1
    meta = units[0].meta
    assert meta["edges"] == 12
    assert meta["gpad_lines_est"] == 13
    assert meta["contributors"] == [ORCID_8769]
    assert model_contributors(gcg) == [ORCID_8769]
    annotation = units[0].payload["annotation"]
    assert gpad_lines_estimate(annotation) == sum(
        len(e.evidence_uris) for e in annotation.edges.values())
    assert unit_contributors(gcg, annotation) == [ORCID_8769]


def test_unit_meta_totals_match_model_count(builder):
    gcg, units = parse_model_units(builder, RES / "MGI_MGI_1100089.ttl")
    assert len(units) == 29
    assert sum(u.meta["gpad_lines_est"] for u in units) == 99
    unit_level = set().union(*(set(u.meta["contributors"]) for u in units))
    assert unit_level and unit_level <= set(model_contributors(gcg))
    assert len(model_contributors(gcg)) == 6
    counted = count_model(builder, RES / "MGI_MGI_1100089.ttl")
    assert counted["total_units"] == 29 and counted["gpad_lines_est"] == 99
    assert counted["groups"] and counted["modelstate"] == "production"


# ----------------------------------------------------------------------
# a full run: summary line columns, reject records, ledger, roll-ups, lists
# ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def run(tmp_path_factory, builder):
    root = tmp_path_factory.mktemp("axes")
    models = _corpus(root, [
        "MGI_MGI_1100089.ttl",    # partial: 28 / 29 units pass
        "MGI_MGI_1927246.ttl",    # all-standard: 5 / 5
        "66c7d41500000016.ttl",   # 0 / 1 (edge_without_evidence)
        "61452e3d00000323.ttl",   # partial: 1 / 10 (inconsistent_evidence)
        "57c82fad00000252.ttl",   # 0 / 1, no group, 4 failing checks
        "5b318d0900000481.ttl",   # set aside as a true GO-CAM
        "SYNGO_5371.ttl",         # Phase A skip_prefix (12 lines)
        "R-HSA-9937080.ttl",      # Phase A skip_prefix (30 lines)
    ])
    out = root / "out"
    result = run_ratchet(models, rules_dir="rules", out_dir=out,
                         skip_prefixes=["SYNGO", "R-HSA"],
                         true_gocam_ids={"5b318d0900000481"}, builder=builder)
    return out, result


def test_summary_has_line_columns_and_set_aside_row(run):
    out, result = run
    header, rows = _tsv(out / "summary.tsv")
    assert header == list(SUMMARY_COLUMNS)
    by_rule = {(r["Phase"], r["Rule"]): r for r in rows}

    skip = by_rule[("A", "skip_prefix")]
    assert skip["Removed"] == "2" and skip["LinesRemoved"] == "42"   # 12 + 30
    ntg = by_rule[("A", "not_true_gocam")]
    assert ntg["Removed"] == "1" and ntg["LinesRemoved"] == "0"     # set aside, not lost
    aside = by_rule[(SET_ASIDE_PHASE, "not_true_gocam")]
    assert aside["In"] == "1" and aside["Out"] == "1"
    assert aside["LinesIn"] == "" and aside["LinesOut"] == ""        # never parsed

    stats = read_model_stats(out / "model_stats.jsonl")
    total_lines = sum(r["total_lines_est"] for r in stats)
    passing_lines = sum(r["passing_lines_est"] for r in stats)
    chain = [r for r in rows if r["Phase"] in ("A", "B")]
    assert chain[0]["LinesIn"] == str(total_lines + 42)
    b_rows = [r for r in chain if r["Phase"] == "B"]
    assert b_rows[0]["LinesIn"] == str(total_lines)
    assert b_rows[-1]["LinesOut"] == str(passing_lines)
    for prev, nxt in zip(chain, chain[1:]):
        assert prev["LinesOut"] == nxt["LinesIn"]
        assert int(prev["LinesIn"]) - int(prev["LinesRemoved"]) == int(prev["LinesOut"])
    # the set-aside row is not a stage from the result's point of view
    assert [s.phase for s in result.stages if s.phase == SET_ASIDE_PHASE] == [SET_ASIDE_PHASE]


def test_phase_a_reject_records_carry_counts_except_set_aside(run):
    out, _ = run
    header, rows = _tsv(out / "phase-a" / "01-skip_prefix.rejects.tsv")
    assert header == list(REJECTS_TSV_HEADER)
    by_id = {r["id"]: r for r in rows}
    assert by_id["R-HSA-9937080"]["gpad_lines_est"] == "30"
    assert by_id["SYNGO_5371"]["gpad_lines_est"] == "12"
    assert by_id["SYNGO_5371"]["contributors"].count("|") == 2        # 3 ORCIDs
    _, aside = _tsv(out / "phase-a" / "02-not_true_gocam.rejects.tsv")
    assert aside[0]["id"] == "5b318d0900000481"
    assert aside[0]["gpad_lines_est"] == "" and aside[0]["contributors"] == ""
    # the JSONL report rows carry the same axis fields
    report = _jsonl(out / "phase-a" / "reports" / "01-skip_prefix.jsonl")
    removed = {r["id"]: r for r in report if r["decision"] == "remove"}
    assert removed["R-HSA-9937080"]["gpad_lines_est"] == 30
    assert isinstance(removed["R-HSA-9937080"]["contributors"], list)


def test_unit_reject_record_carries_complete_failure_set(run):
    out, _ = run
    reject_files = sorted((out / "phase-b").glob("*.rejects.tsv"))
    seen = 0
    for f in reject_files:
        header, rows = _tsv(f)
        assert header == list(REJECTS_TSV_HEADER)
        for r in rows:
            failed = r["all_failed_checks"].split("|")
            assert r["rule"] in failed                 # attribution stays with the stage
            assert r["gpad_lines_est"] != ""
            seen += 1
    assert seen > 0
    report = _jsonl(out / "phase-b" / "reports" / "04-edge_without_evidence.jsonl")
    rec = next(r for r in report if r["id"].endswith("57c82fad00000252#0"))
    assert rec["decision"] == "remove"
    assert rec["all_failed_checks"] == ["edge_without_evidence", "inconsistent_evidence",
                                        "multiple_mf_anatomy", "multiple_mf_bp"]
    assert rec["gpad_lines_est"] == 12 and len(rec["contributors"]) == 2
    kept = next(r for r in report if r["decision"] == "keep")
    assert "all_failed_checks" not in kept and "gpad_lines_est" in kept


def test_ledger_matches_summary(run):
    out, _ = run
    header, _ = _tsv(out / "ledger.tsv")
    assert header == list(LEDGER_COLUMNS)
    ledger = read_ledger(out / "ledger.tsv")
    _, rows = _tsv(out / "summary.tsv")
    b_rows = [r for r in rows if r["Phase"] == "B"]
    assert len(ledger) == sum(int(r["Removed"]) for r in b_rows)
    for r in b_rows:
        mine = [l for l in ledger if l["removed_by_rule"] == r["Rule"]]
        assert len(mine) == int(r["Removed"]), r["Rule"]
        assert sum(l["gpad_lines_est"] for l in mine) == int(r["LinesRemoved"]), r["Rule"]
    row = next(l for l in ledger if l["model_id"] == "57c82fad00000252")
    assert row["unit_index"] == 0 and row["groups"] == [] and len(row["contributors"]) == 2
    assert row["removed_by_rule"] == "edge_without_evidence"
    assert set(row["all_failed_checks"]) == {"edge_without_evidence", "inconsistent_evidence",
                                             "multiple_mf_anatomy", "multiple_mf_bp"}
    assert row["edges"] == 14 and row["gpad_lines_est"] == 12
    assert row["title"].startswith("C. elegans")
    # the ledger and the surviving manifest partition the Phase B units
    survivors = _jsonl(out / "SB-final.units.manifest.jsonl")
    assert len(ledger) + len(survivors) == int(b_rows[0]["In"])
    assert all("gpad_lines_est" in s["meta"] and "contributors" in s["meta"] for s in survivors)
    assert sum(s["meta"]["gpad_lines_est"] for s in survivors) == int(b_rows[-1]["LinesOut"])


def test_rollups_are_consistent_with_ledger(run):
    out, _ = run
    ledger = read_ledger(out / "ledger.tsv")
    stats = read_model_stats(out / "model_stats.jsonl")
    rules, groups = read_rollup(out / "ledger.by-group.tsv")
    assert rules and rules[0] == "edge_without_evidence" and "inconsistent_evidence" in rules
    # every model here has at most one group, so group columns sum to the ledger
    for rule in rules:
        assert sum(g[rule] for g in groups) == sum(1 for l in ledger if l["removed_by_rule"] == rule)
    assert sum(g["lost_units"] for g in groups) == len(ledger)
    assert sum(g["lost_lines_est"] for g in groups) == sum(l["gpad_lines_est"] for l in ledger)
    assert sum(g["total_units"] for g in groups) == sum(s["total_units"] for s in stats)
    none_row = next(g for g in groups if g["group"] == "(none)")
    assert none_row["lost_units"] == 1 and none_row["total_units"] == 1
    assert [g["lost_units"] for g in groups] == sorted((g["lost_units"] for g in groups), reverse=True)

    crules, contribs = read_rollup(out / "ledger.by-contributor.tsv")
    assert crules == rules
    me = next(c for c in contribs if c["contributor"] == ORCID_8769)
    assert me["total_units"] == 1 and me["lost_units"] == 1 and me["lost_lines_est"] == 13
    assert me["edge_without_evidence"] == 1
    # a unit with k contributors counts once per contributor
    assert sum(c["lost_units"] for c in contribs) == sum(
        max(1, len(l["contributors"])) for l in ledger)


def test_model_lists_partition(run):
    out, _ = run
    header, partial = _tsv(out / "models.partial.tsv")
    assert header == list(MODEL_LIST_COLUMNS)
    header2, all_std = _tsv(out / "models.all-standard.tsv")
    assert header2 == list(MODEL_LIST_COLUMNS)
    assert {r["model_id"] for r in partial} == {"MGI_MGI_1100089", "61452e3d00000323"}
    assert {r["model_id"] for r in all_std} == {"MGI_MGI_1927246"}
    mgi = next(r for r in partial if r["model_id"] == "MGI_MGI_1100089")
    assert (mgi["total_units"], mgi["passing_units"]) == ("29", "28")
    assert (mgi["total_lines_est"], mgi["passing_lines_est"]) == ("99", "95")
    stats = {s["model"]: s for s in read_model_stats(out / "model_stats.jsonl")}
    assert set(stats) == {"MGI_MGI_1100089", "MGI_MGI_1927246", "66c7d41500000016",
                          "61452e3d00000323", "57c82fad00000252"}
    listed = {r["model_id"] for r in partial} | {r["model_id"] for r in all_std}
    for model, s in stats.items():
        assert (model in listed) == (s["passing_units"] > 0)
        assert "contributors" in s and "total_lines_est" in s and "passing_lines_est" in s


# ----------------------------------------------------------------------
# shard merge: ledger concatenates, roll-ups recompute from the merged files
# ----------------------------------------------------------------------

def _fake_shard(d, models, ledger_rows, survivors):
    d.mkdir(parents=True)
    (d / "phase-b").mkdir()
    (d / "SA-final.models.manifest.jsonl").write_text(
        "".join(json.dumps({"id": m["model"]}) + "\n" for m in models))
    (d / "model_stats.jsonl").write_text("".join(json.dumps(m) + "\n" for m in models))
    (d / "SB-final.units.manifest.jsonl").write_text("".join(
        json.dumps({"id": f"http://m/{mid}#{i}", "meta": {"model_id": f"http://m/{mid}",
                    "unit_index": i, "edges": 1, "gpad_lines_est": 1, "contributors": c}}) + "\n"
        for mid, i, c in survivors))
    (d / "ledger.tsv").write_text("\t".join(LEDGER_COLUMNS) + "\n" + "".join(
        "\t".join(r) + "\n" for r in ledger_rows))
    (d / "phase-b" / "04-edge_without_evidence.rejects.tsv").write_text(
        "\t".join(REJECTS_TSV_HEADER) + "\n" + "".join(
            f"http://m/{r[0]}#{r[1]}\tedge_without_evidence\tedge_without_evidence\t{{}}\t{r[8]}\t{r[4]}\t{r[6]}\n"
            for r in ledger_rows if r[5] == "edge_without_evidence"))


def test_shard_merge_concatenates_ledger_and_recomputes_rollups(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    A, B = "https://orcid.org/0000-0000-0000-000A", "https://orcid.org/0000-0000-0000-000B"
    s0 = out / "shards" / "shard-000"
    s1 = out / "shards" / "shard-001"
    _fake_shard(
        s0,
        models=[{"model": "m1", "modelstate": "production", "groups": ["g1"], "contributors": [A],
                 "title": "t1", "total_units": 3, "passing_units": 1,
                 "total_lines_est": 7, "passing_lines_est": 1}],
        ledger_rows=[
            ["m1", "0", "production", "g1", A, "edge_without_evidence", "edge_without_evidence", "2", "3", "t1"],
            ["m1", "1", "production", "g1", f"{A}|{B}", "enabler_not_gp", "enabler_not_gp|multiple_mf_bp", "1", "3", "t1"],
        ],
        survivors=[("m1", 2, [A])])
    _fake_shard(
        s1,
        models=[{"model": "m2", "modelstate": "production", "groups": ["g2"], "contributors": [B],
                 "title": "t2", "total_units": 2, "passing_units": 2,
                 "total_lines_est": 2, "passing_lines_est": 2},
                {"model": "m3", "modelstate": "development", "groups": [], "contributors": [],
                 "title": "t3", "total_units": 1, "passing_units": 0,
                 "total_lines_est": 4, "passing_lines_est": 0}],
        ledger_rows=[
            ["m3", "0", "development", "", A, "edge_without_evidence", "edge_without_evidence", "4", "4", "t3"],
        ],
        survivors=[("m2", 0, [B]), ("m2", 1, [B])])

    stages = [StageStat("B", 4, "edge_without_evidence", 6, 4, 13, 6),
              StageStat("B", 5, "enabler_not_gp", 4, 3, 6, 3)]
    _concat_shard_outputs(out, [s0, s1])
    _write_merged_rollups(out, stages)

    text = (out / "ledger.tsv").read_text().splitlines()
    assert text[0] == "\t".join(LEDGER_COLUMNS) and len(text) == 4   # header once + 3 rows
    assert (out / "rejects" / "04-edge_without_evidence.rejects.tsv").read_text().count("\n") == 3

    rules, groups = read_rollup(out / "ledger.by-group.tsv")
    assert rules == ["edge_without_evidence", "enabler_not_gp"]
    g = {r["group"]: r for r in groups}
    assert g["g1"]["total_units"] == 3 and g["g1"]["lost_units"] == 2 and g["g1"]["lost_lines_est"] == 6
    assert g["g2"]["lost_units"] == 0 and g["g2"]["total_units"] == 2
    assert g["(none)"]["lost_units"] == 1 and g["(none)"]["edge_without_evidence"] == 1

    _, contribs = read_rollup(out / "ledger.by-contributor.tsv")
    c = {r["contributor"]: r for r in contribs}
    # A spans both shards: ONE row, summed (2 lost in shard 0 + 1 in shard 1; 1 surviving unit)
    assert list(c) .count(A) == 1
    assert c[A]["lost_units"] == 3 and c[A]["total_units"] == 4 and c[A]["lost_lines_est"] == 10
    assert c[A]["edge_without_evidence"] == 2 and c[A]["enabler_not_gp"] == 1
    assert c[B]["lost_units"] == 1 and c[B]["total_units"] == 3

    _, partial = _tsv(out / "models.partial.tsv")
    _, all_std = _tsv(out / "models.all-standard.tsv")
    assert [r["model_id"] for r in partial] == ["m1"]
    assert [r["model_id"] for r in all_std] == ["m2"]


def test_aggregate_stage_stats_sums_lines_and_keeps_none():
    a = [StageStat("A", 0, "r", 10, 8, 100, 80), StageStat("B", 4, "u", 8, 5, 80, 50)]
    b = [StageStat("A", 0, "r", 5, 5, 50, 50), StageStat("B", 4, "u", 5, 4, 50, 40)]
    merged = _aggregate_stage_stats([a, b])
    assert [(m.rule_id, m.lines_in, m.lines_out, m.lines_removed) for m in merged] == [
        ("r", 150, 130, 20), ("u", 130, 90, 40)]
    c = [StageStat("A", 0, "r", 5, 5)]
    merged = _aggregate_stage_stats([a[:1], c])
    assert merged[0].lines_in is None and merged[0].lines_removed is None
    # a set-aside row sorts between the A and B rows
    sa = [StageStat(SET_ASIDE_PHASE, 2, "not_true_gocam", 3, 3)]
    merged = _aggregate_stage_stats([a, sa])
    assert [m.phase for m in merged] == ["A", SET_ASIDE_PHASE, "B"]


# ----------------------------------------------------------------------
# dashboard surfaces the new axes
# ----------------------------------------------------------------------

def _dashboard_out_dir(tmp_path):
    out = tmp_path / "out"
    (out / "rejects").mkdir(parents=True)
    (out / "summary.tsv").write_text(
        "\t".join(SUMMARY_COLUMNS) + "\n"
        "A\t01\tskip_prefix\t100\t10\t90\t1000\t100\t900\n"
        "A\t02\tnot_true_gocam\t90\t5\t85\t900\t0\t900\n"
        f"{SET_ASIDE_PHASE}\t02\tnot_true_gocam\t5\t0\t5\t\t\t\n"
        "B\t04\tedge_without_evidence\t200\t5\t195\t900\t50\t850\n")
    (out / "rejects" / "04-edge_without_evidence.rejects.tsv").write_text(
        "\t".join(REJECTS_TSV_HEADER) + "\n"
        + "".join(f"http://m/A{i}#1\tedge_without_evidence\tedge_without_evidence\t"
                  + json.dumps({"source": "x", "predicate": "part of", "target": "nucleus",
                                "flagged_edges": 1})
                  + f"\t10\thttps://orcid.org/0000-0000-0000-000{i}\tedge_without_evidence|multiple_mf_bp\n"
                  for i in range(5)))
    (out / "model_stats.jsonl").write_text(
        json.dumps({"model": "M1", "modelstate": "production", "groups": ["http://informatics.jax.org"],
                    "contributors": ["https://orcid.org/0000-0000-0000-0001"], "title": "t",
                    "total_units": 4, "passing_units": 3, "total_lines_est": 40, "passing_lines_est": 30}) + "\n"
        + json.dumps({"model": "M2", "modelstate": "production", "groups": ["http://zfin.org"],
                      "contributors": [], "title": "t2",
                      "total_units": 2, "passing_units": 2, "total_lines_est": 2, "passing_lines_est": 2}) + "\n")
    (out / "ledger.by-group.tsv").write_text(
        "group\ttotal_units\tlost_units\tlost_lines_est\tedge_without_evidence\n"
        "http://informatics.jax.org\t4\t1\t10\t1\n"
        "http://zfin.org\t2\t0\t0\t0\n")
    (out / "ledger.by-contributor.tsv").write_text(
        "contributor\ttotal_units\tlost_units\tlost_lines_est\tedge_without_evidence\n"
        "https://orcid.org/0000-0000-0000-0001\t4\t1\t10\t1\n")
    groups = tmp_path / "groups.yaml"
    groups.write_text("- {id: 'http://informatics.jax.org', label: MGI}\n"
                      "- {id: 'http://zfin.org', label: ZFIN}\n")
    return out, groups


def test_dashboard_data_carries_lines_set_aside_and_rollups(tmp_path):
    out, groups = _dashboard_out_dir(tmp_path)
    data = build_data(out, "rules", groups_yaml=str(groups))
    h = data["headline"]
    assert h["lines_in"] == 1000 and h["unit_lines"] == 900 and h["standard_lines"] == 850
    assert h["set_aside_models"] == 5
    assert data["set_aside"] == [{"index": "02", "rule_id": "not_true_gocam", "models": 5}]
    assert [s["phase"] for s in data["stages"]] == ["A", "A", "B"]       # set-aside is not a stage
    ewe = next(s for s in data["stages"] if s["rule_id"] == "edge_without_evidence")
    assert (ewe["lines_in"], ewe["lines_removed"], ewe["lines_out"]) == (900, 50, 850)
    assert ewe["patterns"][0]["count"] == 5 and ewe["patterns"][0]["lines"] == 50
    m = data["models"]
    assert m["total_lines"] == 42 and m["passing_lines"] == 32
    assert m["partial"] == 1 and m["all_standard"] == 1
    assert m["rows"][0][5:] == [40, 30]
    r = data["rollups"]
    assert r["by_group"]["rules"] == ["edge_without_evidence"]
    assert r["by_group"]["rows"][0] == ["MGI", 4, 1, 10, [1]]           # URI resolved to label
    assert r["by_contributor"]["rows"][0][0] == "https://orcid.org/0000-0000-0000-0001"


def test_dashboard_reads_old_six_column_summary(tmp_path):
    out = tmp_path / "out"
    (out / "rejects").mkdir(parents=True)
    (out / "summary.tsv").write_text("Phase\tStage\tRule\tIn\tRemoved\tOut\n"
                                     "A\t01\tskip_prefix\t100\t10\t90\n")
    data = build_data(out, "rules")
    assert data["stages"][0]["lines_in"] is None and data["headline"]["lines_in"] is None
    assert data["rollups"] is None and data["set_aside"] == []


def test_dashboard_html_contains_new_tables(tmp_path):
    out, groups = _dashboard_out_dir(tmp_path)
    html_path = tmp_path / "dashboard.html"
    build_dashboard(out, "rules", html_path, groups_yaml=str(groups))
    html = html_path.read_text()
    assert 'id="contrib-table"' in html and 'id="group-table"' in html
    assert 'id="setaside"' in html and "GPAD lines" in html and "est." in html
    assert "<script src=" not in html and 'rel="stylesheet"' not in html   # still self-contained
    data = json.loads(re.search(r"const DATA = (\{.*?\});\nlet SEL", html, re.S).group(1))
    assert data["rollups"]["by_contributor"]["rows"][0][2] == 1
