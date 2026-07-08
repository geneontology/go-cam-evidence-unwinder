"""Build a self-contained, shareable HTML dashboard from a ratchet run.

Reads a run's ``summary.tsv`` + ``rejects/*.rejects.tsv`` + ``model_stats.jsonl``
(and the ``rules/`` manifests for descriptions) and emits a single
``dashboard.html`` with the data embedded inline -- no external libraries, no
server, no network: it works by double-clicking the file, so it's easy to share.

Beyond the per-stage funnel and per-rule drill-down, it provides explorable model
axes: modelstate, editorial group (providedBy, resolved via groups.yaml), models
passing, per-model percent passing, with deep links into the Noctua graph editor.

    python -m gocam_unwinder.ratchet.dashboard \\
        --out-dir target_ratchet_YYYYMMDD/out --rules-dir rules \\
        --groups-yaml target/groups.yaml \\
        --output target_ratchet_YYYYMMDD/dashboard.html
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import yaml

DEFAULT_NOCTUA_BASE = "https://noctua.geneontology.org/editor/graph/"


def _read_summary(out_dir):
    rows = []
    with open(out_dir / "summary.tsv") as fh:
        next(fh)  # header
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 6:
                continue
            phase, index, rule_id, n_in, removed, n_out = parts[:6]
            rows.append({
                "phase": phase, "index": index, "rule_id": rule_id,
                "in": int(n_in), "removed": int(removed), "out": int(n_out),
            })
    return rows


def _load_rule_meta(rules_dir):
    meta = {}
    for manifest in Path(rules_dir).glob("*/rule.yaml"):
        data = yaml.safe_load(manifest.read_text()) or {}
        if "id" in data:
            meta[data["id"]] = {
                "tsv": data.get("tsv"),
                "kind": data.get("kind"),
                "description": " ".join((data.get("description") or "").split()),
            }
    return meta


def _load_groups(groups_yaml):
    """groups.yaml (go-site metadata) -> {group URI: label}."""
    if not groups_yaml or not Path(groups_yaml).exists():
        return {}
    data = yaml.safe_load(Path(groups_yaml).read_text()) or []
    lookup = {}
    for g in data:
        if isinstance(g, dict) and g.get("id") and g.get("label"):
            lookup[g["id"]] = g["label"]
    return lookup


def _short_group(uri):
    """Fallback label for an unmapped group URI: its host/path, scheme stripped."""
    s = str(uri).split("://", 1)[-1].rstrip("/")
    return s or str(uri)


def _model_of(item_id):
    """Reduce a reject id to its model id (Phase B id is '<modelURI>#<n>')."""
    base = item_id.split("#", 1)[0]
    return base.rsplit("/", 1)[-1]


def _pattern_label(detail):
    if "predicate" in detail:
        return f'{detail.get("predicate", "?")}  →  {detail.get("target", "?")}'
    if "prefix" in detail:
        return f'prefix: {detail["prefix"]}'
    if "modelstate" in detail:
        return f'modelstate: {detail["modelstate"]}'
    return "(removed)"


def _aggregate_rejects(reject_file, top=30, examples_per=5):
    counts = Counter()
    examples = defaultdict(list)
    sources = defaultdict(Counter)
    total = 0
    if not reject_file.exists():
        return {"total": 0, "patterns": []}
    with open(reject_file) as fh:
        next(fh, None)  # header
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            item_id, detail_raw = parts[0], parts[3]
            try:
                detail = json.loads(detail_raw) if detail_raw else {}
            except json.JSONDecodeError:
                detail = {}
            label = _pattern_label(detail)
            counts[label] += 1
            total += 1
            if len(examples[label]) < examples_per:
                examples[label].append(_model_of(item_id))
            src = detail.get("source")
            if src:
                sources[label][src.rsplit("/", 1)[-1]] += 1
    patterns = []
    for label, count in counts.most_common(top):
        top_src = [s for s, _ in sources[label].most_common(3)] if sources.get(label) else []
        patterns.append({"label": label, "count": count,
                         "examples": examples[label], "sources": top_src})
    return {"total": total, "patterns": patterns}


def _read_model_stats(out_dir, groups_lookup):
    """Per-model records (one per Phase B model) with resolved group labels."""
    path = Path(out_dir) / "model_stats.jsonl"
    if not path.exists():
        return None
    models = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        groups = [groups_lookup.get(g, _short_group(g)) for g in (r.get("groups") or [])]
        models.append({
            "model": r["model"],
            "modelstate": r.get("modelstate") or "unknown",
            "groups": groups or ["(none)"],
            "total": r.get("total_units", 0),
            "passing": r.get("passing_units", 0),
        })
    return models


def _build_models_block(models, noctua_base):
    """Compact, embeddable model table + axis-ready encoding."""
    states = sorted({m["modelstate"] for m in models})
    state_idx = {s: i for i, s in enumerate(states)}
    group_set = sorted({g for m in models for g in m["groups"]})
    group_idx = {g: i for i, g in enumerate(group_set)}
    rows = [[m["model"], state_idx[m["modelstate"]],
             [group_idx[g] for g in m["groups"]], m["total"], m["passing"]]
            for m in models]

    hist = {"0%": 0, "1–25%": 0, "26–50%": 0, "51–75%": 0, "76–99%": 0, "100%": 0}
    for m in models:
        if m["total"] == 0:
            continue
        p = 100.0 * m["passing"] / m["total"]
        if p == 0:
            hist["0%"] += 1
        elif p <= 25:
            hist["1–25%"] += 1
        elif p <= 50:
            hist["26–50%"] += 1
        elif p <= 75:
            hist["51–75%"] += 1
        elif p < 100:
            hist["76–99%"] += 1
        else:
            hist["100%"] += 1

    return {
        "rows": rows, "modelstates": states, "groups": group_set,
        "n": len(models),
        "passing": sum(1 for m in models if m["passing"] > 0),
        "total_units": sum(m["total"] for m in models),
        "passing_units": sum(m["passing"] for m in models),
        "histogram": hist, "noctua_base": noctua_base,
    }


def build_data(out_dir, rules_dir, groups_yaml=None, noctua_base=DEFAULT_NOCTUA_BASE):
    out_dir = Path(out_dir)
    summary = _read_summary(out_dir)
    meta = _load_rule_meta(rules_dir)
    rejects_dir = out_dir / "rejects"

    stages = []
    for row in summary:
        rule_id = row["rule_id"]
        agg = _aggregate_rejects(rejects_dir / f"{row['index']}-{rule_id}.rejects.tsv")
        rmeta = meta.get(rule_id, {})
        stages.append({
            "phase": row["phase"], "index": row["index"], "rule_id": rule_id,
            "tsv": rmeta.get("tsv"), "kind": rmeta.get("kind"),
            "description": rmeta.get("description", ""),
            "in": row["in"], "removed": row["removed"], "out": row["out"],
            "patterns": agg["patterns"],
        })

    phase_a = [s for s in stages if s["phase"] == "A"]
    phase_b = [s for s in stages if s["phase"] == "B"]
    headline = {
        "s0_models": phase_a[0]["in"] if phase_a else 0,
        "candidate_models": phase_a[-1]["out"] if phase_a else 0,
        "annotation_units": phase_b[0]["in"] if phase_b else 0,
        "standard_annotations": phase_b[-1]["out"] if phase_b else 0,
    }

    models = _read_model_stats(out_dir, _load_groups(groups_yaml))
    models_block = _build_models_block(models, noctua_base) if models else None

    return {"label": out_dir.parent.name or out_dir.name,
            "headline": headline, "stages": stages, "models": models_block,
            "noctua_base": noctua_base}


def build_dashboard(out_dir, rules_dir, output_html, groups_yaml=None,
                    noctua_base=DEFAULT_NOCTUA_BASE):
    data = build_data(out_dir, rules_dir, groups_yaml, noctua_base)
    html = _TEMPLATE.replace("/*__DATA__*/", json.dumps(data))
    Path(output_html).write_text(html)
    return output_html


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GO-CAM Standard-Annotation Ratchet</title>
<style>
  :root{--bg:#0f1419;--panel:#1b232c;--line:#2c3742;--ink:#e6edf3;--muted:#8b98a5;
        --keep:#3fb950;--drop:#e5534b;--accent:#58a6ff;--chip:#21303f;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
  header{padding:20px 24px;border-bottom:1px solid var(--line)}
  h1{margin:0;font-size:20px}
  .sub{color:var(--muted);font-size:13px;margin-top:4px}
  .cards{display:flex;gap:12px;flex-wrap:wrap;margin:16px 24px;align-items:stretch}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
        padding:12px 16px;min-width:140px;flex:1}
  .card .n{font-size:24px;font-weight:700}
  .card .l{color:var(--muted);font-size:12px;margin-top:2px}
  .card .arrow{color:var(--muted);font-size:18px;align-self:center}
  .wrap{display:flex;gap:16px;padding:8px 24px 8px;align-items:flex-start;flex-wrap:wrap}
  .col{flex:1;min-width:420px}
  section{padding:8px 24px 24px}
  h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
     margin:18px 0 8px}
  .stage{background:var(--panel);border:1px solid var(--line);border-radius:8px;
         padding:8px 10px;margin-bottom:6px;cursor:pointer;transition:border-color .1s}
  .stage:hover{border-color:var(--accent)}
  .stage.sel{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}
  .stage .top{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
  .stage .name{font-weight:600}
  .stage .tsv{color:var(--muted);font-weight:400;font-size:12px}
  .stage .nums{font-variant-numeric:tabular-nums;font-size:12px;color:var(--muted);white-space:nowrap}
  .stage .nums b{color:var(--drop)}
  .bar{height:9px;border-radius:5px;background:#0c1117;margin-top:6px;overflow:hidden;display:flex}
  .bar .k{background:var(--keep)} .bar .d{background:var(--drop)}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;position:sticky;top:12px}
  .panel .desc{color:var(--muted);margin:6px 0 14px;font-size:13px}
  table{width:100%;border-collapse:collapse;font-size:13px}
  td,th{padding:5px 6px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}
  th{color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.04em;cursor:pointer}
  .c{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
  .ptbl .c{color:var(--accent);font-weight:600}
  .pbar{height:6px;background:var(--accent);border-radius:3px;margin-top:4px;opacity:.5}
  .ex{color:var(--muted);font-size:11px;margin-top:3px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  .ex span{background:var(--chip);padding:1px 5px;border-radius:4px;margin-right:4px;display:inline-block;margin-top:2px}
  input{background:#0c1117;border:1px solid var(--line);color:var(--ink);border-radius:6px;
        padding:6px 9px;width:100%;margin-bottom:10px;font-size:13px}
  .empty{color:var(--muted);font-style:italic}
  .foot{color:var(--muted);font-size:11px;padding:0 24px 24px}
  a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
  .axisbtns{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap}
  .btn{background:var(--panel);border:1px solid var(--line);color:var(--ink);border-radius:7px;
       padding:6px 12px;cursor:pointer;font-size:13px}
  .btn.on{border-color:var(--accent);color:var(--accent)}
  .mwrap{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap}
  .mcol{flex:1;min-width:420px}
  .axtbl tr{cursor:pointer} .axtbl tr.sel td{background:#101a26}
  .mini{display:flex;gap:3px;align-items:flex-end;height:38px;margin:4px 0 2px}
  .mini .b{flex:1;background:var(--accent);border-radius:2px 2px 0 0;min-height:2px;opacity:.7;position:relative}
  .mini .lbl{color:var(--muted);font-size:10px;text-align:center}
  .ratepill{display:inline-block;min-width:42px;text-align:right;font-variant-numeric:tabular-nums}
  .ubar{height:6px;background:#0c1117;border-radius:3px;margin-top:3px;overflow:hidden}
  .ubar>div{height:100%;background:var(--keep)}
</style>
</head>
<body>
<header>
  <h1>GO-CAM Standard-Annotation Ratchet</h1>
  <div class="sub" id="sub"></div>
</header>
<div class="cards" id="cards"></div>
<div class="wrap">
  <div class="col">
    <h2>Phase A &mdash; model ratchet</h2><div id="phaseA"></div>
    <h2>Phase B &mdash; annotation-unit ratchet</h2><div id="phaseB"></div>
  </div>
  <div class="col">
    <div class="panel">
      <div id="dd-head"><b>Select a stage</b></div>
      <div class="desc" id="dd-desc">Click any rule on the left to see what it filtered out and the most common offending patterns.</div>
      <input id="filter" placeholder="filter patterns…" oninput="renderPatterns()" style="display:none">
      <div id="dd-body"></div>
    </div>
  </div>
</div>
<section id="models-sec" style="display:none">
  <h2>Explore models</h2>
  <div class="sub" id="models-head" style="margin-bottom:10px"></div>
  <div class="mwrap">
    <div class="mcol">
      <div class="axisbtns">
        <button class="btn" id="ax-modelstate" onclick="setAxis('modelstate')">By modelstate</button>
        <button class="btn" id="ax-group" onclick="setAxis('group')">By editorial group</button>
      </div>
      <div id="axis-table"></div>
    </div>
    <div class="mcol">
      <div class="panel">
        <div style="margin-bottom:10px"><b>Percent of model passing</b> (per-model unit pass rate)</div>
        <div class="mini" id="hist"></div>
        <div id="model-list-head" style="margin-top:14px"><b>Models</b> <span class="muted" id="ml-sub"></span></div>
        <input id="mfilter" placeholder="filter models…" oninput="renderModelList()" style="margin-top:8px">
        <div id="model-list"></div>
      </div>
    </div>
  </div>
</section>
<div class="foot" id="foot"></div>
<script>
const DATA = /*__DATA__*/;
let SEL = null, AXIS = 'modelstate', AXVAL = null;
let patSort={key:'count',dir:-1}, axisSort={key:'models',dir:-1}, mlSort={key:'total',dir:-1};
function sArrow(st,k){return st.key===k?(st.dir<0?' ▾':' ▴'):'';}
function sortPat(k){if(patSort.key===k)patSort.dir=-patSort.dir;else{patSort.key=k;patSort.dir=(k==='label')?1:-1;}renderPatterns();}
function sortAxis(k){if(axisSort.key===k)axisSort.dir=-axisSort.dir;else{axisSort.key=k;axisSort.dir=(k==='value')?1:-1;}renderAxisTable();}
function sortMl(k){if(mlSort.key===k)mlSort.dir=-mlSort.dir;else{mlSort.key=k;mlSort.dir=(k==='model')?1:-1;}renderModelList();}
function noctua(id){return DATA.noctua_base+'gomodel:'+id;}

function fmt(n){return n.toLocaleString();}
function pct(a,b){return b? (100*a/b).toFixed(1)+'%' : '—';}
function el(tag, cls, html){const e=document.createElement(tag); if(cls)e.className=cls; if(html!=null)e.innerHTML=html; return e;}
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}

function cards(){
  const h=DATA.headline, c=document.getElementById('cards');
  const items=[['Models (S0)',h.s0_models],['Candidate models',h.candidate_models],
               ['Annotation units',h.annotation_units],['Standard annotations',h.standard_annotations]];
  items.forEach((it,i)=>{
    c.appendChild(el('div','card','<div class="n">'+fmt(it[1])+'</div><div class="l">'+it[0]+'</div>'));
    if(i<items.length-1) c.appendChild(el('div','arrow','→'));
  });
  document.getElementById('sub').textContent=
    'run: '+DATA.label+'  ·  '+DATA.stages.length+' stages  ·  '
    +fmt(h.s0_models)+' models → '+fmt(h.standard_annotations)+' standard annotations';
}

/* ---------- funnel + per-rule drill-down ---------- */
function stageRow(s, phaseS0){
  const row=el('div','stage'); row.dataset.id=s.index+'-'+s.rule_id;
  const keepPct=100*s.out/phaseS0, dropPct=100*s.removed/phaseS0;
  row.innerHTML =
    '<div class="top"><span class="name">'+s.rule_id+
      (s.tsv?' <span class="tsv">#'+s.tsv+'</span>':'')+'</span>'+
      '<span class="nums">'+fmt(s['in'])+' &minus;<b>'+fmt(s.removed)+'</b> = '+fmt(s.out)+'</span></div>'+
    '<div class="bar"><div class="k" style="width:'+keepPct+'%"></div>'+
      '<div class="d" style="width:'+dropPct+'%"></div></div>';
  row.onclick=()=>selectStage(s);
  return row;
}
function funnel(){
  const A=DATA.stages.filter(s=>s.phase==='A'), B=DATA.stages.filter(s=>s.phase==='B');
  const a0=A.length?A[0]['in']:1, b0=B.length?B[0]['in']:1;
  A.forEach(s=>document.getElementById('phaseA').appendChild(stageRow(s,a0)));
  B.forEach(s=>document.getElementById('phaseB').appendChild(stageRow(s,b0)));
}
function selectStage(s){
  SEL=s;
  document.querySelectorAll('.stage').forEach(r=>r.classList.toggle('sel', r.dataset.id===s.index+'-'+s.rule_id));
  document.getElementById('dd-head').innerHTML='<b>'+s.rule_id+'</b>'+(s.tsv?' &middot; rule #'+s.tsv:'')+
    ' &middot; '+(s.kind||'')+' &middot; <span style="color:var(--drop)">'+fmt(s.removed)+' removed</span>';
  document.getElementById('dd-desc').textContent=s.description||'';
  const f=document.getElementById('filter'); f.style.display=s.patterns.length?'block':'none'; f.value='';
  renderPatterns();
}
function renderPatterns(){
  const body=document.getElementById('dd-body'); body.innerHTML='';
  if(!SEL){return;}
  if(!SEL.patterns.length){body.appendChild(el('div','empty','No per-edge detail (membership/structural filter).')); return;}
  const q=(document.getElementById('filter').value||'').toLowerCase();
  let pats=SEL.patterns.filter(p=>!q||p.label.toLowerCase().includes(q));
  const max=Math.max.apply(null,SEL.patterns.map(p=>p.count));
  pats=pats.slice().sort((a,b)=> patSort.dir*(patSort.key==='label'? a.label.localeCompare(b.label) : a.count-b.count));
  const tbl=el('table','ptbl');
  tbl.innerHTML='<tr><th onclick="sortPat(\'label\')">Pattern'+sArrow(patSort,'label')+
    '</th><th class="c" onclick="sortPat(\'count\')">Count'+sArrow(patSort,'count')+'</th></tr>';
  pats.forEach(p=>{
    let ex=''; if(p.sources&&p.sources.length) ex+=p.sources.map(s=>'<span>'+esc(s)+'</span>').join('');
    if(p.examples&&p.examples.length) ex+='<div class="ex">'+p.examples.map(e=>'<a href="'+noctua(e)+'" target="_blank" rel="noopener"><span>'+esc(e)+'</span></a>').join('')+'</div>';
    const tr=el('tr');
    tr.innerHTML='<td>'+esc(p.label)+'<div class="pbar" style="width:'+(100*p.count/max)+'%"></div>'+ex+'</td>'+
      '<td class="c">'+fmt(p.count)+'</td>';
    tbl.appendChild(tr);
  });
  body.appendChild(tbl);
}

/* ---------- explorable model axes ---------- */
const M=DATA.models;
function mState(m){return M.modelstates[m[1]];}
function mGroups(m){return m[2].map(i=>M.groups[i]);}

function computeAxis(axis){
  const agg=new Map();
  M.rows.forEach(m=>{
    const keys = axis==='modelstate' ? [mState(m)] : mGroups(m);
    keys.forEach(k=>{
      let a=agg.get(k); if(!a){a={value:k,models:0,passing:0,tU:0,pU:0}; agg.set(k,a);}
      a.models++; if(m[4]>0)a.passing++; a.tU+=m[3]; a.pU+=m[4];
    });
  });
  return [...agg.values()].sort((x,y)=>y.models-x.models);
}
function setAxis(axis){
  AXIS=axis; AXVAL=null;
  document.getElementById('ax-modelstate').classList.toggle('on',axis==='modelstate');
  document.getElementById('ax-group').classList.toggle('on',axis==='group');
  renderAxisTable();
  renderModelList();
}
function renderAxisTable(){
  let rows=computeAxis(AXIS);
  const max=Math.max.apply(null,rows.map(r=>r.models));
  const rate=r=>r.tU?r.pU/r.tU:0;
  rows=rows.slice().sort((a,b)=>{
    let v; if(axisSort.key==='value')v=a.value.localeCompare(b.value);
    else if(axisSort.key==='rate')v=rate(a)-rate(b);
    else v=a[axisSort.key]-b[axisSort.key];
    return axisSort.dir*v;
  });
  const tbl=el('table','axtbl');
  tbl.innerHTML='<tr><th onclick="sortAxis(\'value\')">'+(AXIS==='modelstate'?'Modelstate':'Editorial group')+sArrow(axisSort,'value')+
    '</th><th class="c" onclick="sortAxis(\'models\')">Models'+sArrow(axisSort,'models')+
    '</th><th class="c" onclick="sortAxis(\'passing\')">Passing'+sArrow(axisSort,'passing')+
    '</th><th class="c" onclick="sortAxis(\'rate\')">Unit pass rate'+sArrow(axisSort,'rate')+'</th></tr>';
  rows.forEach(r=>{
    const tr=el('tr'); if(r.value===AXVAL)tr.classList.add('sel');
    tr.innerHTML='<td>'+esc(r.value)+'<div class="pbar" style="width:'+(100*r.models/max)+'%"></div></td>'+
      '<td class="c">'+fmt(r.models)+'</td>'+
      '<td class="c">'+fmt(r.passing)+' <span class="muted">('+pct(r.passing,r.models)+')</span></td>'+
      '<td class="c"><span class="ratepill">'+pct(r.pU,r.tU)+'</span>'+
        '<div class="ubar"><div style="width:'+(r.tU?100*r.pU/r.tU:0)+'%"></div></div></td>';
    tr.onclick=()=>{AXVAL=r.value; renderAxisTable(); renderModelList();};
    tbl.appendChild(tr);
  });
  const c=document.getElementById('axis-table'); c.innerHTML=''; c.appendChild(tbl);
}
function renderModelList(){
  const head=document.getElementById('ml-sub'), c=document.getElementById('model-list'); c.innerHTML='';
  let rows=M.rows;
  if(AXVAL!==null) rows=rows.filter(m=> (AXIS==='modelstate'? mState(m)===AXVAL : mGroups(m).includes(AXVAL)));
  const q=(document.getElementById('mfilter').value||'').toLowerCase();
  if(q) rows=rows.filter(m=>m[0].toLowerCase().includes(q));
  const rrate=m=>m[3]?m[4]/m[3]:0;
  rows=rows.slice().sort((a,b)=>{
    let v; if(mlSort.key==='model')v=a[0].localeCompare(b[0]);
    else if(mlSort.key==='pass')v=rrate(a)-rrate(b);
    else v=a[3]-b[3];
    return mlSort.dir*v;
  });
  const CAP=300, shown=rows.slice(0,CAP);
  head.textContent=(AXVAL!==null?'in “'+AXVAL+'” ':'')+'· '+fmt(rows.length)+' models'+(rows.length>CAP?' (showing '+CAP+')':'');
  const tbl=el('table');
  tbl.innerHTML='<tr><th onclick="sortMl(\'model\')">Model'+sArrow(mlSort,'model')+
    '</th><th class="c" onclick="sortMl(\'total\')">Std/Total'+sArrow(mlSort,'total')+
    '</th><th class="c" onclick="sortMl(\'pass\')">Pass'+sArrow(mlSort,'pass')+'</th></tr>';
  shown.forEach(m=>{
    const rate=m[3]?100*m[4]/m[3]:0;
    const tr=el('tr');
    tr.innerHTML='<td><a href="'+noctua(m[0])+'" target="_blank" rel="noopener">'+esc(m[0])+'</a>'+
        ' <span class="muted" style="font-size:11px">'+esc(mState(m))+'</span></td>'+
      '<td class="c">'+fmt(m[4])+'/'+fmt(m[3])+'</td>'+
      '<td class="c"><span class="ratepill">'+(m[3]?rate.toFixed(0)+'%':'—')+'</span>'+
        '<div class="ubar"><div style="width:'+rate+'%"></div></div></td>';
    tbl.appendChild(tr);
  });
  c.appendChild(tbl);
}
function histogram(){
  const h=M.histogram, keys=Object.keys(h), max=Math.max.apply(null,keys.map(k=>h[k]))||1;
  const c=document.getElementById('hist'); c.innerHTML='';
  keys.forEach(k=>{
    const col=el('div'); col.style.flex='1'; col.style.textAlign='center';
    col.innerHTML='<div class="b" title="'+h[k]+' models" style="height:'+(100*h[k]/max)+'%"></div>'+
      '<div class="lbl">'+k+'<br>'+fmt(h[k])+'</div>';
    c.appendChild(col);
  });
}
function models(){
  if(!M){return;}
  document.getElementById('models-sec').style.display='block';
  document.getElementById('models-head').innerHTML=
    '<b>'+fmt(M.n)+'</b> candidate models · <b>'+fmt(M.passing)+'</b> passing (≥1 standard annotation, '+
    pct(M.passing,M.n)+') · '+fmt(M.passing_units)+' / '+fmt(M.total_units)+' units standard ('+
    pct(M.passing_units,M.total_units)+'). Model ids link to the Noctua graph editor.';
  histogram(); setAxis('modelstate');
}

cards(); funnel(); models();
document.getElementById('foot').innerHTML=
  'Generated by gocam_unwinder.ratchet.dashboard · self-contained (no network) · '+
  'funnel bars scaled to each phase’s starting set (green = surviving, red = removed).';
(function(){const b=DATA.stages.filter(s=>s.phase==='B'); if(b.length){b.slice().sort((x,y)=>y.removed-x.removed); selectStage(b.slice().sort((x,y)=>y.removed-x.removed)[0]);}})();
</script>
</body>
</html>
"""


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m gocam_unwinder.ratchet.dashboard",
        description="Build a self-contained HTML dashboard from a ratchet run.")
    ap.add_argument("--out-dir", required=True, help="Ratchet output dir (summary.tsv + rejects/ + model_stats.jsonl).")
    ap.add_argument("--rules-dir", default="rules", help="Rule manifests dir (for descriptions).")
    ap.add_argument("--groups-yaml", help="go-site groups.yaml to resolve group URIs to labels.")
    ap.add_argument("--noctua-base", default=DEFAULT_NOCTUA_BASE,
                    help="Noctua editor base URL for model links.")
    ap.add_argument("--output", required=True, help="Path to write dashboard.html.")
    args = ap.parse_args(argv)
    path = build_dashboard(args.out_dir, args.rules_dir, args.output,
                           groups_yaml=args.groups_yaml, noctua_base=args.noctua_base)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
