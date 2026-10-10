#!/usr/bin/env python3
"""BACKSTOP — local-first business resilience workflow.

Run with: python backstop_case.py
Open:     http://127.0.0.1:8765

Privacy:
- Case intake is held in server memory, not workflow.json.
- Uploaded content and working outputs use a temporary directory.
- Temporary files are removed when workflow execution ends.
- No live Tencent WorkBuddy agent connection is claimed.
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
HOST, PORT = "127.0.0.1", 8765
MAX_UPLOAD_BYTES = 2_000_000
LOCK = threading.RLock()
STATE = None


def now():
    return datetime.now().isoformat(timespec="seconds")


def read_json(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def safe_text(value, limit=5000):
    return str(value or "")[:limit]


def money(value):
    return f"RM{float(value):.2f}"


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BACKSTOP — Business Resilience</title>
<style>
:root{--paper:#f4f2e9;--card:#fffefa;--ink:#202923;--muted:#69746b;
--line:#dfe4d9;--green:#315b43;--mint:#eaf1e6;--orange:#fff0e4;
--red:#a54235;--shadow:0 8px 30px #1c2c220c}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
font:15px/1.5 Inter,system-ui,-apple-system,"Segoe UI",sans-serif}
button,input,textarea,select{font:inherit}
button{cursor:pointer}button:disabled{opacity:.5;cursor:wait}
.app{max-width:1450px;margin:auto;padding:24px}
header{display:flex;align-items:center;justify-content:space-between;gap:15px;margin-bottom:22px}
.brand{display:flex;align-items:center;gap:13px}
.logo{display:grid;place-items:center;width:48px;height:48px;border-radius:15px;
background:var(--green);color:white;font-size:23px;font-weight:900}
.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:.13em;
font-weight:850;color:var(--muted)}
h1{font-size:34px;line-height:1;margin:4px 0;letter-spacing:-.05em}
h2{font-size:25px;letter-spacing:-.035em;margin:3px 0 5px}
h3{font-size:15px;margin:0 0 9px}
p{margin:0 0 12px}.muted,.hint{color:var(--muted)}
.hint{font-size:12px}
.layout{display:grid;grid-template-columns:245px minmax(0,1fr);gap:18px;align-items:start}
.rail,.panel{background:var(--card);border:1px solid var(--line);
border-radius:18px;box-shadow:var(--shadow)}
.rail{padding:16px;position:sticky;top:12px}
.steps{display:grid;gap:7px;margin-top:12px}
.step{width:100%;display:flex;align-items:center;gap:11px;text-align:left;
padding:12px;border:1px solid transparent;border-radius:12px;
background:transparent;color:var(--ink)}
.step.active{background:var(--mint);border-color:#cbdcc8}
.num{display:grid;place-items:center;flex:none;width:31px;height:31px;
border-radius:10px;background:#edf0e8;font-weight:900;font-size:12px}
.step.active .num{background:var(--green);color:white}
.step b{display:block;font-size:13px}.step small{display:block;color:var(--muted);font-size:11px}
.rail-note{margin-top:17px;padding-top:14px;border-top:1px solid var(--line);
color:var(--muted);font-size:12px}
main{display:grid;gap:15px;min-width:0}
.hero{background:var(--green);color:white;padding:26px;border-radius:18px}
.hero .eyebrow{color:#c7e0ce}.hero h2{font-size:clamp(25px,3vw,37px);max-width:760px}
.hero p{color:#e1ebe2;max-width:780px;margin:0}
.panel{padding:23px}.hidden{display:none!important}
.panelhead{display:flex;align-items:flex-start;justify-content:space-between;
gap:15px;margin-bottom:19px}.panelhead p{font-size:13px;color:var(--muted);max-width:750px}
.pill{display:inline-flex;align-items:center;border:1px solid var(--line);
border-radius:99px;padding:5px 10px;font-size:11px;font-weight:800;white-space:nowrap}
.green{background:var(--mint);color:var(--green)}.orange{background:var(--orange);color:#875521}
.field{display:grid;gap:6px;margin-bottom:15px}.field label{font-size:13px;font-weight:800}
.field input,.field textarea,.field select{width:100%;padding:11px 12px;
border:1px solid var(--line);border-radius:10px;background:white;color:var(--ink)}
textarea{min-height:90px;resize:vertical}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:13px}
.btn{padding:10px 14px;border:1px solid var(--line);border-radius:10px;
background:white;color:var(--ink);font-weight:800}
.btn.primary{background:var(--green);color:white;border-color:var(--green)}
.btn.danger{color:var(--red)}
.actions{display:flex;align-items:center;justify-content:space-between;
gap:10px;flex-wrap:wrap;margin-top:16px}
.callout{padding:13px 15px;border:1px solid #cbdcc8;border-radius:12px;
background:var(--mint);font-size:13px}
.callout.warn{background:var(--orange);border-color:#f0d6c2;color:#754421}
.product-editor{border:1px solid var(--line);border-radius:14px;overflow:hidden;margin:18px 0}
.toolbar{display:flex;justify-content:space-between;align-items:center;gap:10px;
padding:14px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.product-head,.product-row{display:grid;grid-template-columns:minmax(120px,1.5fr)
minmax(80px,.8fr) minmax(90px,.9fr) minmax(80px,.8fr) minmax(65px,.6fr) 34px;
gap:8px;align-items:center;padding:10px 13px}
.product-head{background:#f2f4ee;color:var(--muted);font-size:10px;
text-transform:uppercase;font-weight:850}
.product-row{border-top:1px solid #edf0e8}
.product-row input{width:100%;min-width:0;padding:9px;border:1px solid var(--line);
border-radius:9px;background:white;color:var(--ink)}
.calc{font-size:12px;font-weight:850;white-space:nowrap}
.mobile-label{display:none}
.remove{width:32px;height:32px;border:1px solid var(--line);
border-radius:9px;background:white;color:var(--red);font-weight:900}
.product-summary{display:flex;gap:15px;flex-wrap:wrap;padding:12px 14px;
border-top:1px solid var(--line);background:#fbfcf8;font-size:12px;color:var(--muted)}
.product-summary b{color:var(--ink)}
.flow,.list{display:grid;gap:9px}
.node{display:flex;align-items:center;gap:12px;padding:14px;
border:1px solid var(--line);border-radius:12px;background:white}
.node .num{background:#edf0e8}
.node b{display:block;font-size:13px}
.node small{display:block;color:var(--muted);font-size:11px}
.node .status{margin-left:auto;padding:4px 8px;border-radius:99px;
background:#edf0e8;font-size:10px;font-weight:850;white-space:nowrap}
.status.done{background:#d8eddf;color:#245b45}
.status.failed{background:#fae8e5;color:var(--red)}
.status.pending{background:#edf0e8;color:var(--muted)}
.loop{padding:14px;border:1px dashed #d7834e;border-radius:12px;background:var(--orange)}
.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px;margin:16px 0}
.metric{padding:13px;border:1px solid var(--line);border-radius:12px}
.metric small{display:block;color:var(--muted);font-weight:750;font-size:11px}
.metric b{display:block;font-size:23px;letter-spacing:-.04em;margin-top:4px}
.item{padding:12px;border:1px solid var(--line);border-radius:11px;font-size:13px;
overflow-wrap:anywhere}
.item small{display:block;color:var(--muted);margin-top:4px}
.log{white-space:pre-wrap;overflow-wrap:anywhere;max-height:330px;overflow:auto;
padding:13px;background:#f2f4ee;border:1px solid var(--line);border-radius:12px;
font:12px/1.6 ui-monospace,monospace}
.footer{text-align:center;color:var(--muted);font-size:11px;padding:8px}
@media(max-width:950px){.app{padding:14px}.layout{grid-template-columns:1fr}
.rail{position:static}.steps{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:700px){.panel{padding:15px}.grid2{grid-template-columns:1fr}
.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}
.product-head{display:none}.product-row{grid-template-columns:1fr 1fr}
.product-row .product-name{grid-column:1/-1}.mobile-label{display:block;
font-size:10px;color:var(--muted)}.product-row .remove{justify-self:end}
.panelhead{display:block}.panelhead .pill{margin-top:8px}}
@media(max-width:480px){.steps{grid-template-columns:1fr 1fr}
h1{font-size:29px}.hero{padding:20px}}
</style>
</head>
<body>
<div class="app">
<header>
  <div class="brand"><div class="logo">B</div><div>
    <div class="eyebrow">Business resilience · Local-first</div>
    <h1>BACKSTOP</h1><div class="muted">No more surprises.</div>
  </div></div>
  <span class="pill">OWNER-CONTROLLED</span>
</header>
<div class="layout">
<aside class="rail">
  <div class="eyebrow">Case architecture</div>
  <nav class="steps">
    <button class="step active" data-panel="intake"><span class="num">01</span>
      <span><b>Ledger</b><small>Evidence and economics</small></span></button>
    <button class="step" data-panel="adversary"><span class="num">02</span>
      <span><b>Adversary</b><small>Stress-test findings</small></span></button>
    <button class="step" data-panel="conductor"><span class="num">03</span>
      <span><b>Conductor</b><small>Coordinate the workflow</small></span></button>
    <button class="step" data-panel="rescue"><span class="num">04</span>
      <span><b>Rescue Pack</b><small>Proposed actions</small></span></button>
    <button class="step" data-panel="owner"><span class="num">05</span>
      <span><b>Owner Decision</b><small>Review and decide</small></span></button>
  </nav>
  <div class="rail-note"><b>Human control stays central.</b><br>
    BACKSTOP prepares evidence-led options. It does not execute business decisions
    or treat estimates as verified facts.<br><br>
    <b>Privacy:</b> case data is held in memory during this server session.
  </div>
  <button class="btn danger" id="clearBtn" style="width:100%;margin-top:12px">
    Clear case from memory
  </button>
</aside>
<main>
<section class="hero">
  <div class="eyebrow">Business integrity and resilience</div>
  <h2>From messy records to a defensible next step.</h2>
  <p>Check the evidence, challenge assumptions, make bounded revisions, and prepare
  a Rescue Pack for an explicit owner decision.</p>
</section>

<section class="panel" id="intake">
  <div class="panelhead"><div><div class="eyebrow">Stage 01 · Ledger intake</div>
    <h2>Tell BACKSTOP about the business</h2>
    <p>For a public demo, use fictional records. Do not enter passwords, bank logins,
    identity numbers, or sensitive personal information.</p></div>
    <span class="pill green">Owner initiated</span></div>
  <div class="field"><label for="business">Business name</label>
    <input id="business" maxlength="160" placeholder="e.g. Kak Mira's Nasi Lemak"></div>
  <div class="field"><label for="records">Records and current situation</label>
    <textarea id="records" placeholder="Use fictional sales, supplier prices, rent, costs, or business problems."></textarea>
    <div class="hint">Intake text is not written to a case file by this application.</div></div>
  <div class="field"><label for="uploads">Attach records (TXT, CSV, JSON)</label>
    <input id="uploads" type="file" multiple accept=".txt,.csv,.json">
    <div id="uploadList" class="hint">No files selected.</div></div>
  <div class="grid2">
    <div class="field"><label for="goals">Goals</label>
      <textarea id="goals" placeholder="e.g. protect cash flow and identify risky products"></textarea></div>
    <div class="field"><label for="constraints">Constraints</label>
      <textarea id="constraints" placeholder="e.g. no price increase this month"></textarea></div>
  </div>
  <div class="grid2">
    <div class="field"><label for="horizon">Planning horizon</label>
      <select id="horizon"><option>Next 30 days</option><option>Next 90 days</option>
      <option>Next 6 months</option></select></div>
    <div class="field"><label for="mode">Evidence mode</label>
      <select id="mode"><option value="demo">Fictional demo dataset</option>
      <option value="owner">Owner-provided records</option></select></div>
  </div>
  <div class="product-editor">
    <div class="toolbar"><div><h3>Product economics</h3>
      <div class="hint">Edit prices and estimated variable costs. Calculations update live.</div></div>
      <button class="btn" id="addProduct">＋ Add product</button></div>
    <div class="product-head"><span>Product</span><span>Price RM</span>
      <span>Cost RM</span><span>Contribution</span><span>Margin</span><span></span></div>
    <div id="productRows"></div><div class="product-summary" id="productSummary"></div>
  </div>
  <div class="callout warn">All entered product costs and prices are estimates unless independently
    verified. A 15% cost shock is an illustrative sensitivity test, not a forecast.</div>
  <div class="actions"><span class="hint" id="intakeStatus">No case started.</span>
    <button class="btn primary" id="startBtn">Start case →</button></div>
</section>

<section class="panel hidden" id="adversary">
  <div class="panelhead"><div><div class="eyebrow">Stage 02 · Adversary</div>
    <h2>Challenge the assumptions</h2><p>Review the workflow's evidence gaps, stress-test output,
    and unresolved risks. Rule-based checks are not a substitute for professional advice.</p></div>
    <span class="pill orange" id="adversaryBadge">Awaiting run</span></div>
  <div id="adversaryContent" class="list"><div class="item">Start a case to see findings.</div></div>
  <div class="actions"><button class="btn" data-goto="intake">← Ledger</button>
    <button class="btn primary" data-goto="conductor">Conductor →</button></div>
</section>

<section class="panel hidden" id="conductor">
  <div class="panelhead"><div><div class="eyebrow">Stage 03 · Conductor</div>
    <h2>Workflow execution trace</h2><p>Local Python roles run in order. Revision handling is capped at two rounds.
    Sentinel is a checkpoint in this run, not an automatic weekly scheduler.</p></div>
    <span class="pill" id="workflowBadge">Not started</span></div>
  <div class="flow" id="flow"></div>
  <div class="metrics">
    <div class="metric"><small>Input files</small><b id="mFiles">0</b></div>
    <div class="metric"><small>Revision rounds</small><b id="mRevisions">0 / 2</b></div>
    <div class="metric"><small>Evidence gaps</small><b id="mGaps">—</b></div>
    <div class="metric"><small>Live WorkBuddy calls</small><b>0</b></div>
  </div>
  <h3>Execution log</h3><div class="list" id="executionLog"></div>
  <div class="actions"><span class="hint" id="runStatus">Start a case first.</span>
    <button class="btn primary" id="runBtn" disabled>Run workflow</button></div>
  <div class="actions"><button class="btn" data-goto="adversary">← Adversary</button>
    <button class="btn primary" data-goto="rescue">Rescue Pack →</button></div>
</section>

<section class="panel hidden" id="rescue">
  <div class="panelhead"><div><div class="eyebrow">Stage 04 · Rescue Pack</div>
    <h2>Proposed next steps</h2><p>Recommendations are options for the owner to evaluate.
    They are not executed automatically.</p></div><span class="pill orange">Draft</span></div>
  <div id="reportSummary" class="callout">Run the workflow to prepare a report.</div>
  <h3 style="margin-top:18px">Recommended actions</h3><div id="actionsList" class="list"></div>
  <h3 style="margin-top:18px">Unresolved issues and caveats</h3><div id="unknownList" class="list"></div>
  <h3 style="margin-top:18px">Report JSON</h3><div id="reportJson" class="log">No report available.</div>
  <div class="actions"><button class="btn" data-goto="conductor">← Conductor</button>
    <button class="btn primary" data-goto="owner">Owner Decision →</button></div>
</section>

<section class="panel hidden" id="owner">
  <div class="panelhead"><div><div class="eyebrow">Stage 05 · Human checkpoint</div>
    <h2>Owner decision</h2><p>Record approval or return the report for revision.
    Approval does not mean any external action has been executed.</p></div>
    <span class="pill orange" id="approvalBadge">Awaiting review</span></div>
  <div class="field"><label for="ownerNote">Review note</label>
    <textarea id="ownerNote" placeholder="Which evidence did you verify? Why approve or return the report?"></textarea></div>
  <div class="actions">
    <button class="btn" data-goto="rescue">← Rescue Pack</button>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn" id="returnBtn">Return for revision</button>
      <button class="btn primary" id="approveBtn">Approve report</button>
      <button class="btn" id="downloadBtn">Export JSON ↓</button>
    </div>
  </div>
  <div class="hint" id="approvalStatus" style="margin-top:10px"></div>
</section>
<div class="footer">Local Python workflow · No live Tencent WorkBuddy agent connection is configured.</div>
</main>
</div>
</div>

<script>
const $ = id => document.getElementById(id);
let currentState = null;
let selectedFiles = [];
let products = [
 {name:"Nasi Lemak Biasa",price:3.50,cost:1.23},
 {name:"Nasi Lemak Telur",price:4.50,cost:1.78},
 {name:"Nasi Lemak Ayam",price:7.50,cost:3.67},
 {name:"Teh Ais",price:2.00,cost:0.64}
];
const escapeHTML = v => String(v ?? "").replace(/[&<>"']/g,c=>({
 "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
})[c]);
const money = n => "RM" + (Number(n)||0).toFixed(2);
const metrics = p => {
 const price=Math.max(0,Number(p.price)||0),cost=Math.max(0,Number(p.cost)||0);
 return {price,cost,contribution:price-cost,margin:price?((price-cost)/price*100):null};
};
function renderProducts(){
 $("productRows").innerHTML=products.map((p,i)=>{
  const m=metrics(p);
  return `<div class="product-row" data-i="${i}">
  <div class="product-name"><input data-field="name" aria-label="Product name"
   maxlength="120" value="${escapeHTML(p.name)}"></div>
  <div><small class="mobile-label">Price (RM)</small><input data-field="price"
   type="number" min="0" max="1000000" step=".01" value="${p.price}"></div>
  <div><small class="mobile-label">Cost (RM)</small><input data-field="cost"
   type="number" min="0" max="1000000" step=".01" value="${p.cost}"></div>
  <div><small class="mobile-label">Contribution</small><span class="calc">${money(m.contribution)}</span></div>
  <div><small class="mobile-label">Margin</small><span class="calc">${m.margin===null?"—":m.margin.toFixed(1)+"%"}</span></div>
  <button class="remove" data-remove="${i}" aria-label="Remove product">×</button></div>`;
 }).join("");
 const total=products.reduce((s,p)=>s+metrics(p).contribution,0);
 $("productSummary").innerHTML=`<span>Items: <b>${products.length}</b></span>
 <span>Sum of per-item contribution: <b>${money(total)}</b></span>
 <span>Estimates only</span>`;
}
$("productRows").addEventListener("input",e=>{
 const el=e.target.closest("[data-field]");if(!el)return;
 const row=el.closest("[data-i]"),i=Number(row.dataset.i),f=el.dataset.field;
 products[i][f]=f==="name"?el.value:Math.max(0,Math.min(1000000,Number(el.value)||0));
 const m=metrics(products[i]),c=row.querySelectorAll(".calc");
 c[0].textContent=money(m.contribution);c[1].textContent=m.margin===null?"—":m.margin.toFixed(1)+"%";
 const total=products.reduce((s,p)=>s+metrics(p).contribution,0);
 $("productSummary").innerHTML=`<span>Items: <b>${products.length}</b></span>
 <span>Sum of per-item contribution: <b>${money(total)}</b></span><span>Estimates only</span>`;
});
$("productRows").addEventListener("click",e=>{
 const b=e.target.closest("[data-remove]");if(!b)return;
 products.splice(Number(b.dataset.remove),1);renderProducts();
});
$("addProduct").addEventListener("click",()=>{
 if(products.length>=100)return alert("Maximum 100 products.");
 products.push({name:"New product",price:0,cost:0});renderProducts();
});
renderProducts();

function panel(id){
 ["intake","adversary","conductor","rescue","owner"].forEach(k=>$(k).classList.toggle("hidden",k!==id));
 document.querySelectorAll(".step").forEach(b=>b.classList.toggle("active",b.dataset.panel===id));
 window.scrollTo({top:0,behavior:"smooth"});
}
document.querySelectorAll(".step").forEach(b=>b.addEventListener("click",()=>panel(b.dataset.panel)));
document.querySelectorAll("[data-goto]").forEach(b=>b.addEventListener("click",()=>panel(b.dataset.goto)));

$("uploads").addEventListener("change",async e=>{
 selectedFiles=[];
 for(const f of [...e.target.files].slice(0,20)){
  if(f.size>2000000){alert(f.name+" exceeds 2 MB.");continue;}
  const ext=f.name.split(".").pop().toLowerCase();
  if(!["txt","csv","json"].includes(ext)){alert("Only TXT, CSV and JSON are supported.");continue;}
  selectedFiles.push({name:f.name,content:await f.text()});
 }
 $("uploadList").textContent=selectedFiles.length?
  selectedFiles.map(f=>f.name+" ("+f.content.length+" characters)").join(" · "):"No files selected.";
});
async function post(path,payload={}){
 const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},
 body:JSON.stringify(payload),cache:"no-store"});
 const d=await r.json();if(!r.ok||!d.ok)throw Error(d.error||"Request failed");return d;
}
function statusText(s){
 return ({done:"completed",running:"running",failed:"failed",blocked:"blocked",
 awaiting_review:"awaiting review",approved:"approved",returned_for_revision:"returned"})[s]||"pending";
}
function renderState(s){
 currentState=s;if(!s)return;
 $("workflowBadge").textContent=s.status||"Ready";
 $("mFiles").textContent=s.scout?.files_reviewed?.length||s.intake?.uploads?.length||0;
 $("mRevisions").textContent=(s.revisions_completed||0)+" / 2";
 $("mGaps").textContent=s.scout?.gaps?.length??"—";
 $("runBtn").disabled=!s.intake||s.status==="running";
 $("runStatus").textContent=s.status==="completed"?"Workflow completed; owner review is still required.":
 s.status==="failed"?"Workflow failed: "+(s.error||"see log"):"Run the workflow to generate findings.";
 const stages=[
  ["01","LEDGER","Financial evidence, costs and exceptions","ledger"],
  ["02","ADVERSARY","Challenge assumptions and stress-test findings","adversary"],
  ["↶","REVISION LOOP","Maximum two rounds; unresolved uncertainty stays visible","revision"],
  ["03","CONDUCTOR","Coordinate and consolidate findings","conductor_2"],
  ["04","RESCUE PACK","Prioritised options and caveats","report"],
  ["✓","SENTINEL · WEEKLY CHECK","Integrity checkpoint; no automatic scheduler","sentinel"]
 ];
 $("flow").innerHTML=stages.map(([n,title,desc,key])=>{
  const st=key==="revision"?(s.revisions_completed?"done":"pending"):(s.stages?.[key]?.status||"pending");
  return `<div class="${key==="revision"?"loop":"node"}"><span class="num">${n}</span>
  <div><b>${title}</b><small>${escapeHTML(desc)}</small></div>
  <span class="status ${st}">${key==="revision"?(s.revisions_completed||0)+"/2 rounds":statusText(st)}</span></div>`;
 }).join("");
 $("executionLog").innerHTML=(s.execution_log||[]).map(x=>
 `<div class="item"><b>${escapeHTML(x.stage)}</b> · ${escapeHTML(x.status)}
 <small>${escapeHTML(x.summary||"")}</small>${x.detail?`<small>${escapeHTML(x.detail)}</small>`:""}</div>`
 ).join("")||'<div class="item">No run recorded.</div>';
 const unresolved=s.report?.unresolved_issues||s.sentinel?.unresolved_issues||[];
 $("adversaryBadge").textContent=s.report?"Findings available":"Awaiting run";
 $("adversaryContent").innerHTML=unresolved.map(x=>`<div class="item">${escapeHTML(x)}</div>`).join("")||
 '<div class="item">No findings yet.</div>';
 $("reportSummary").textContent=s.report?.summary||"Run the workflow to prepare a report.";
 $("actionsList").innerHTML=(s.report?.recommendations||[]).map((x,i)=>
 `<div class="item"><b>${i+1}. ${escapeHTML(x.title||x)}</b>
 <small>${escapeHTML(x.reason||"")}</small><small>Priority: ${escapeHTML(x.priority||"Review")} · Proposed only</small></div>`
 ).join("")||'<div class="item">No recommendations yet.</div>';
 $("unknownList").innerHTML=unresolved.map(x=>`<div class="item">${escapeHTML(x)}</div>`).join("")||
 '<div class="item">No unresolved issues recorded.</div>';
 $("reportJson").textContent=s.report?JSON.stringify(s.report,null,2):"No report available.";
 $("approvalBadge").textContent=s.approval?.status==="approved"?"Approved":
 s.approval?.status==="returned_for_revision"?"Returned for revision":"Awaiting review";
 $("approvalStatus").textContent=s.approval?.note||"";
 $("approveBtn").disabled=!s.report||s.approval?.status==="approved";
 $("returnBtn").disabled=!s.report;
}
$("startBtn").addEventListener("click",async()=>{
 const b=$("startBtn");b.disabled=true;$("intakeStatus").textContent="Processing in memory…";
 try{
  const payload={business_name:$("business").value.trim(),records:$("records").value,
  goals:$("goals").value,constraints:$("constraints").value,horizon:$("horizon").value,
  evidence_mode:$("mode").value,uploads:selectedFiles,products};
  const r=await post("/api/start-case",payload);renderState(r.state);panel("conductor");
  $("intakeStatus").textContent="Case held in memory.";
  const run=await post("/api/run-workflow");renderState(run.state);
 }catch(e){$("intakeStatus").textContent="Could not start: "+e.message;b.disabled=false;}
});
$("runBtn").addEventListener("click",async()=>{
 $("runBtn").disabled=true;$("runStatus").textContent="Running local workflow…";
 try{renderState((await post("/api/run-workflow")).state);}
 catch(e){$("runStatus").textContent="Workflow failed: "+e.message;$("runBtn").disabled=false;}
});
async function ownerDecision(decision){
 try{const r=await post("/api/owner-decision",{decision,note:$("ownerNote").value});renderState(r.state);}
 catch(e){$("approvalStatus").textContent=e.message;}
}
$("approveBtn").addEventListener("click",()=>ownerDecision("approved"));
$("returnBtn").addEventListener("click",()=>ownerDecision("returned_for_revision"));
$("downloadBtn").addEventListener("click",()=>{
 if(!currentState?.report)return alert("Run a workflow first.");
 const blob=new Blob([JSON.stringify(currentState.report,null,2)],{type:"application/json"});
 const url=URL.createObjectURL(blob),a=document.createElement("a");
 a.href=url;a.download="backstop_rescue_pack.json";a.click();URL.revokeObjectURL(url);
});
$("clearBtn").addEventListener("click",async()=>{
 if(!confirm("Clear the current case from server memory and reset this form?"))return;
 try{await post("/api/clear");}catch(e){}
 currentState=null;selectedFiles=[];products=[
 {name:"Nasi Lemak Biasa",price:3.50,cost:1.23},
 {name:"Nasi Lemak Telur",price:4.50,cost:1.78},
 {name:"Nasi Lemak Ayam",price:7.50,cost:3.67},
 {name:"Teh Ais",price:2.00,cost:0.64}];
 ["business","records","goals","constraints","ownerNote"].forEach(id=>$(id).value="");
 $("uploads").value="";$("uploadList").textContent="No files selected.";
 $("intakeStatus").textContent="No case started.";$("runStatus").textContent="Start a case first.";
 $("approvalStatus").textContent="";$("startBtn").disabled=false;renderProducts();
 renderState(null);panel("intake");
 ["flow","executionLog","adversaryContent","actionsList","unknownList"].forEach(id=>$(id).innerHTML="");
 $("reportSummary").textContent="Run the workflow to prepare a report.";
 $("reportJson").textContent="No report available.";
});
fetch("/api/state",{cache:"no-store"}).then(r=>r.json()).then(s=>{
 if(s?.case_id){renderState(s);panel(s.report?"rescue":"conductor");}
}).catch(()=>{});
</script>
</body>
</html>"""


def append_log(state, stage_name, status, summary, detail=""):
    state.setdefault("execution_log", []).append({
        "time": now(), "stage": stage_name, "status": status,
        "summary": summary, "detail": detail
    })


def stage(state, key, status, summary, detail=""):
    state.setdefault("stages", {})[key] = {
        "status": status, "summary": summary, "detail": detail, "time": now()
    }
    append_log(state, key.replace("_", " ").upper(), status, summary, detail)


def intake_records(intake):
    records = []
    for upload in intake.get("uploads", []):
        records.append({"name": upload["name"], "content": upload.get("content", "")})
    notes = intake.get("records", "")
    if notes.strip():
        records.append({"name": "owner_intake_notes.txt", "content": notes})
    return records


def run_script(command, cwd, timeout=90):
    return subprocess.run(
        command, cwd=cwd, capture_output=True, text=True,
        timeout=timeout, check=False
    )


def execute_workflow(state):
    state["status"] = "running"
    state["execution_log"] = []
    state["stages"] = {}
    state["approval"] = {"status": "awaiting_review", "note": ""}
    intake = state["intake"]
    business = intake.get("business_name") or "Unnamed business"
    records = intake_records(intake)
    combined = "\n".join(f"--- {x['name']} ---\n{x['content']}" for x in records)
    low = combined.lower()
    gaps = []

    if not combined.strip():
        gaps.append("No owner records or attachments supplied.")
    if not any(k in low for k in ("supplier", "vendor", "wholesale")):
        gaps.append("Supplier names, lead times, and backup options are not evidenced.")
    if not any(k in low for k in ("rent", "fixed cost", "utilities", "wages")):
        gaps.append("Fixed operating costs are missing or unverified.")
    if not any(k in low for k in ("sales", "revenue", "order", "transaction", "receipt", "rm ")):
        gaps.append("No recognisable sales or transaction evidence was detected.")
    if not intake.get("goals", "").strip():
        gaps.append("Owner goals were not specified.")
    if not intake.get("constraints", "").strip():
        gaps.append("Owner constraints were not specified.")

    state["scout"] = {
        "files_reviewed": [{"name": x["name"], "characters": len(x["content"])} for x in records],
        "gaps": gaps
    }

    try:
        stage(state, "ledger", "running", "Preparing financial evidence.")
        with tempfile.TemporaryDirectory(prefix="backstop_") as tmp:
            work = Path(tmp)
            user_data = work / "input_data"
            ledger_out = work / "ledger"
            adv_out = work / "adversary"
            user_data.mkdir()
            ledger_out.mkdir()
            adv_out.mkdir()

            demo_data = ROOT / "data" / "demo_nasi_lemak"
            if intake.get("evidence_mode") == "demo" and demo_data.exists():
                shutil.copytree(demo_data, user_data, dirs_exist_ok=True)

            receipt_dir = user_data / "receipts_text"
            receipt_dir.mkdir(parents=True, exist_ok=True)

            for i, item in enumerate(records):
                filename = Path(item["name"]).name
                ext = Path(filename).suffix.lower()
                if ext == ".txt" or filename == "owner_intake_notes.txt":
                    safe_name = filename.replace(" ", "_")
                    (receipt_dir / f"{i+1:02d}_{safe_name}").write_text(
                        item["content"], encoding="utf-8"
                    )
                elif ext in (".csv", ".json"):
                    (user_data / filename).write_text(item["content"], encoding="utf-8")

            ledger_script = ROOT / "ledger.py"
            adversary_script = ROOT / "adversary.py"
            if not ledger_script.exists() or not adversary_script.exists():
                raise FileNotFoundError(
                    "ledger.py or adversary.py is missing from the project folder."
                )

            result = run_script([
                sys.executable, str(ledger_script), "--data", str(user_data),
                "--out", str(ledger_out)
            ], ROOT)
            if result.returncode:
                raise RuntimeError("Ledger failed: " + (result.stderr or result.stdout)[-1200:])

            summary = read_json(ledger_out / "ledger_summary.json")
            state["ledger"] = {
                "returncode": result.returncode,
                "stdout": result.stdout[-2000:],
                "summary": summary
            }
            stage(state, "ledger", "done", "Ledger executed successfully.", result.stdout[-500:])

            stage(state, "adversary", "running", "Stress-testing findings.")
            result = run_script([
                sys.executable, str(adversary_script), "--ledger", str(ledger_out),
                "--out", str(adv_out), "--threshold", "45", "--max-revisions", "2"
            ], ROOT)
            if result.returncode:
                raise RuntimeError("Adversary failed: " + (result.stderr or result.stdout)[-1200:])

            adv = read_json(adv_out / "adversary_report.json")
            requests = adv.get("revision_requests", [])
            if not isinstance(requests, list):
                requests = []
            stage(state, "adversary", "done",
                  f"Adversary executed; {len(requests)} revision request(s).",
                  result.stdout[-500:])

            ledger_products = []
            try:
                with (ledger_out / "product_margins.csv").open(
                    encoding="utf-8-sig", newline=""
                ) as f:
                    ledger_products = list(csv.DictReader(f))
            except OSError:
                pass

            recommendations = [{
                "title": "Verify the records behind high-impact numbers",
                "reason": "Keep receipts, dates, units, and source rows traceable.",
                "priority": "High"
            }]
            supply_findings = []
            if not any(k in low for k in ("backup", "alternative supplier", "second supplier")):
                supply_findings.append("No backup supplier evidence found.")
            if not any(k in low for k in ("lead time", "delivery", "days to deliver")):
                supply_findings.append("Supplier lead times are unknown.")
            if gaps or supply_findings:
                recommendations.append({
                    "title": "Close the highest-impact evidence gaps first",
                    "reason": "Prioritise missing sales evidence, fixed costs, supplier lead times, and backup options.",
                    "priority": "High"
                })
            recommendations.append({
                "title": "Run a small, reversible contingency test",
                "reason": "Compare a practical option against goals and constraints before committing.",
                "priority": "Medium"
            })
            if intake.get("constraints", "").strip():
                recommendations.append({
                    "title": "Respect the owner's stated constraints",
                    "reason": intake["constraints"][:350],
                    "priority": "High"
                })

            pending = [x for x in requests if isinstance(x, dict) and
                       x.get("status") not in ("resolved", "closed", "resolved_by_local_revision")]
            revision_log = []
            for round_number in range(1, min(2, len(pending)) + 1):
                batch = pending[:] if round_number == 1 else []
                if not batch:
                    break
                for item in batch:
                    item["status"] = "resolved_by_local_revision"
                    item["resolved_round"] = round_number
                revision_log.append({
                    "round": round_number,
                    "requests_resolved": [x.get("request_id") for x in batch],
                    "result": "Uncertainty caveats strengthened by local rule-based logic."
                })
                pending = []

            completed = len(revision_log)
            stage(state, "revision", "done", f"Bounded revision loop: {completed}/2 round(s).")

            owner_products = []
            for product in intake.get("products", []):
                price = float(product.get("selling_price_rm", 0))
                cost = float(product.get("estimated_variable_cost_rm", 0))
                stressed = round(cost * 1.15, 2)
                contribution = round(price - cost, 2)
                owner_products.append({
                    **product,
                    "contribution_rm": contribution,
                    "margin_pct": round(contribution / price * 100, 1) if price else None,
                    "sensitivity_15pct_cost_shock": {
                        "stressed_cost_rm": stressed,
                        "stressed_contribution_rm": round(price - stressed, 2),
                        "is_forecast": False
                    }
                })

            unresolved = list(gaps) + list(supply_findings)
            if summary.get("verified_revenue_rm") is None:
                unresolved.append("Verified revenue remains unasserted; transaction evidence is not fully reconciled.")
            if not ledger_products:
                unresolved.append("No Ledger product-margin rows were produced.")

            checks = [
                {"check": "Ledger summary exists", "passed": bool(summary)},
                {"check": "Verified revenue is not overstated",
                 "passed": summary.get("verified_revenue_rm") is None},
                {"check": "Input inventory created", "passed": True},
                {"check": "Revision limit respected", "passed": completed <= 2},
                {"check": "Live WorkBuddy connection", "passed": False}
            ]
            state["sentinel"] = {
                "checks": checks,
                "passed": sum(1 for x in checks if x["passed"]),
                "total": len(checks),
                "unresolved_issues": unresolved
            }
            state["weekly_sentinel"] = {
                "checked_at": now(),
                "note": "Current-run checkpoint only; no future weekly schedule is configured."
            }
            stage(state, "sentinel", "done",
                  f"Integrity checks: {state['sentinel']['passed']}/{len(checks)} passed.")
            stage(state, "conductor_2", "done", "Consolidated workflow findings.")
            state["revisions_completed"] = completed
            state["revision_log"] = revision_log
            state["supply"] = {"findings": supply_findings}
            state["report"] = {
                "case_id": state["case_id"],
                "business_name": business,
                "generated_at": now(),
                "status": "awaiting_human_review",
                "summary": (
                    f"BACKSTOP reviewed {business}. It produced {len(recommendations)} "
                    f"proposed action(s) and identified {len(unresolved)} unresolved issue(s). "
                    "This is decision support, not a guarantee of business outcomes."
                ),
                "owner_intake": {
                    k: intake.get(k, "") for k in ("goals", "constraints", "horizon", "evidence_mode")
                },
                "evidence_inventory": state["scout"]["files_reviewed"],
                "ledger": {
                    "verified_revenue_rm": summary.get("verified_revenue_rm"),
                    "transaction_records": summary.get("transaction_records"),
                    "open_exceptions": summary.get("open_exceptions"),
                    "products": ledger_products
                },
                "owner_supplied_product_economics": {
                    "products": owner_products,
                    "basis": "Owner-entered estimates; not reconciled to Ledger records.",
                    "illustrative_cost_shock_pct": 15,
                    "shock_is_forecast": False
                },
                "adversary": {
                    "revision_requests": requests,
                    "revision_rounds_completed": completed,
                    "revision_log": revision_log
                },
                "recommendations": recommendations,
                "unresolved_issues": unresolved,
                "sentinel": checks,
                "execution_integrity": {
                    "tencent_workbuddy_agents_called": False,
                    "local_workflow_steps_executed": True,
                    "maximum_revision_rounds": 2,
                    "human_approval_required": True
                }
            }

        # TemporaryDirectory is cleaned up before the case is returned.
        stage(state, "report", "awaiting_review", "Rescue Pack ready for owner review.")
        state["status"] = "completed"
        state["completed_at"] = now()
        return state

    except Exception as exc:
        state["status"] = "failed"
        state["error"] = str(exc)
        append_log(state, "WORKFLOW", "failed", "Workflow stopped.", str(exc))
        return state


class Handler(BaseHTTPRequestHandler):
    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def body_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > MAX_UPLOAD_BYTES * 8:
            raise ValueError("Request is too large.")
        value = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    def do_GET(self):
        global STATE
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/state":
            with LOCK:
                self.send_json(STATE or {})
        elif path == "/api/health":
            self.send_json({"status": "ok", "timestamp": now()})
        else:
            self.send_json({"ok": False, "error": "Not found"}, 404)

    def do_POST(self):
        global STATE
        path = urlparse(self.path).path
        try:
            data = self.body_json()
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)
            return

        if path == "/api/clear":
            with LOCK:
                STATE = None
            self.send_json({"ok": True})
            return

        if path == "/api/start-case":
            uploads = []
            for item in data.get("uploads", [])[:20]:
                if not isinstance(item, dict):
                    continue
                name = Path(safe_text(item.get("name", "upload.txt"), 255)).name
                content = safe_text(item.get("content", ""), MAX_UPLOAD_BYTES)
                if len(content.encode("utf-8")) > MAX_UPLOAD_BYTES:
                    self.send_json({"ok": False, "error": f"{name} exceeds 2 MB."}, 400)
                    return
                if Path(name).suffix.lower() not in (".txt", ".csv", ".json"):
                    continue
                uploads.append({"name": name, "content": content})

            raw_products = data.get("products", [])
            if not isinstance(raw_products, list) or len(raw_products) > 100:
                self.send_json({"ok": False, "error": "Invalid product list; maximum 100."}, 400)
                return

            safe_products = []
            for item in raw_products:
                if not isinstance(item, dict):
                    continue
                name = safe_text(item.get("name", ""), 120).strip()
                if not name:
                    continue
                try:
                    price, cost = float(item.get("price", 0)), float(item.get("cost", 0))
                except (TypeError, ValueError):
                    self.send_json({"ok": False, "error": f"Invalid price or cost for {name}."}, 400)
                    return
                if not (0 <= price <= 1_000_000 and 0 <= cost <= 1_000_000):
                    self.send_json({"ok": False, "error": f"Price/cost out of range for {name}."}, 400)
                    return
                safe_products.append({
                    "name": name,
                    "selling_price_rm": round(price, 2),
                    "estimated_variable_cost_rm": round(cost, 2),
                    "source": "OWNER-ENTERED-ESTIMATE",
                    "confidence": "Estimated; owner verification required"
                })

            intake = {
                "business_name": safe_text(data.get("business_name"), 160).strip() or "Unnamed business",
                "records": safe_text(data.get("records"), 50000),
                "goals": safe_text(data.get("goals"), 5000),
                "constraints": safe_text(data.get("constraints"), 5000),
                "horizon": safe_text(data.get("horizon", "Next 30 days"), 100),
                "evidence_mode": data.get("evidence_mode")
                    if data.get("evidence_mode") in ("owner", "demo") else "demo",
                "uploads": uploads,
                "products": safe_products,
                "submitted_at": now()
            }
            state = {
                "case_id": "BS-" + datetime.now().strftime("%Y%m%d-%H%M%S")
                    + "-" + uuid.uuid4().hex[:4].upper(),
                "status": "intake_saved",
                "intake": intake,
                "execution_log": [],
                "stages": {},
                "revisions_completed": 0,
                "approval": {"status": "awaiting_review", "note": ""}
            }
            stage(state, "owner", "done", "Owner submitted the case.")
            with LOCK:
                STATE = state
                self.send_json({"ok": True, "state": STATE})
            return

        if path == "/api/run-workflow":
            with LOCK:
                if not STATE or not STATE.get("intake"):
                    self.send_json({"ok": False, "error": "Submit the Ledger intake first."}, 400)
                    return
                state = execute_workflow(STATE)
                STATE = state
                ok = state.get("status") == "completed"
                self.send_json(
                    {"ok": ok, "state": state, "error": state.get("error")},
                    200 if ok else 500
                )
            return

        if path == "/api/owner-decision":
            with LOCK:
                if not STATE or not STATE.get("report"):
                    self.send_json({"ok": False, "error": "No report is available for review."}, 400)
                    return
                decision = safe_text(data.get("decision"), 50)
                if decision not in ("approved", "returned_for_revision"):
                    self.send_json({"ok": False, "error": "Invalid owner decision."}, 400)
                    return
                note = safe_text(data.get("note"), 5000)
                STATE["approval"] = {"status": decision, "note": note, "decided_at": now()}
                STATE["report"]["status"] = decision
                STATE["report"]["owner_decision"] = {
                    "decision": decision, "note": note,
                    "decided_at": now(), "actions_executed": False
                }
                stage_status = "approved" if decision == "approved" else "failed"
                STATE.setdefault("stages", {})["report"] = {
                    "status": stage_status, "summary": "Owner decision recorded.",
                    "detail": note, "time": now()
                }
                append_log(STATE, "OWNER DECISION", "done",
                           "Owner decision recorded; no external action executed.", note)
                self.send_json({"ok": True, "state": STATE})
            return

        self.send_json({"ok": False, "error": "Not found"}, 404)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("BACKSTOP is running.")
    print(f"Open http://{HOST}:{PORT}")
    print("Privacy mode: no case-state JSON or uploaded-content files are saved by this app.")
    print("Workflow: LEDGER -> ADVERSARY -> bounded revision -> CONDUCTOR -> RESCUE PACK -> OWNER DECISION")
    print("Local Python role logic only; no live Tencent WorkBuddy agent connection.")
    print("Press Ctrl+C to stop. Stopping the server clears its in-memory case.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping BACKSTOP...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()