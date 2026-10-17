"""LOCAL-ONLY annotation assistant (step D02). Run by a team member on the permitted machine.

For each authorized clip it runs the LOCAL vision model (neutral observation prompt plus the draft
rule predicates as cues) and writes, under the git-ignored evaluation/annotations/private/:
  proposals/<clip_id>.json   model proposals (NOT labels; a proposal until a human reviews it)
  review/<clip_id>.html      offline review page: video, clickable proposals, annotation form,
                             "Export" button that downloads <clip_id>.annotation.json
  index.html                 list of clips with links
Clip ids are opaque (c001.. ordered by SHA-256, same as evaluation/manifest/build_manifest.py).

stdout prints progress counts only. Never paste outputs into a cloud assistant (spec §12).
Do not run while another model job is active (shared accelerator reservation).

Usage: .venv/bin/python scripts/local_annotation_assist.py --dataset data/dataset [--only c003]
       [--rules CW01,CW02,CW04]
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evaluation" / "annotations" / "private"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True, type=Path)
    ap.add_argument("--only", default="")
    ap.add_argument("--rules", default="CW01,CW02,CW03,CW04,CW05")
    args = ap.parse_args()
    os.environ.setdefault("CASHWATCH_MODE", "test")
    os.environ["HF_HUB_OFFLINE"] = "1"

    from cashwatch.deadline import Deadline
    from cashwatch.dto import ObservationRequest
    from cashwatch.gpulock import gpu_reservation
    from cashwatch.inference import create_perception
    from cashwatch.media import create_media
    from cashwatch.pipeline.planner import build_plan
    from cashwatch.rules import load_registry
    from cashwatch.settings import get_settings

    settings = get_settings()
    if settings.backend == "fake":
        print("refusing: fake backend produces no proposals", file=sys.stderr)
        return 2
    reg = load_registry(ROOT / "rules" / "registry.yaml", require_approved=False)
    wanted = [r.strip() for r in args.rules.split(",") if r.strip()]
    cues = [d for rid in wanted if rid in reg.all_rule_ids() for d in reg.definitions(rid)]
    rule_names = {rid: reg.spec(rid).type_ru for rid in reg.all_rule_ids()}
    pred_rule = {d.predicate_id: rid for rid in wanted if rid in reg.all_rule_ids() for d in reg.definitions(rid)}

    clips = sorted((sha256(p), p) for p in args.dataset.rglob("*") if p.suffix.lower() == ".mov")
    ids = {s: f"c{i + 1:03d}" for i, (s, _) in enumerate(clips)}
    (OUT / "proposals").mkdir(parents=True, exist_ok=True)
    (OUT / "review").mkdir(parents=True, exist_ok=True)
    os.chmod(OUT, 0o700)
    media = create_media()
    index_rows = []
    with gpu_reservation("annotation-assist"):
        perception = create_perception(settings)
        perception.warmup(timeout_sec=180)
        for n, (sha, path) in enumerate(clips, 1):
            cid = ids[sha]
            index_rows.append(cid)
            if args.only and cid != args.only:
                continue
            work = Path(tempfile.mkdtemp(dir=ROOT / "data"))
            try:
                meta = media.probe_video(path, timeout_sec=30)
                plan = build_plan(meta, settings.profile)
                man = media.sample_video(path, meta, plan, work, deadline=Deadline.in_seconds(600))
                frames = {f.frame_id: f for f in man.frames}
                windows = []
                for w in plan.windows:
                    req = ObservationRequest(
                        job_id="assist", window_id=w.window_id, start_sec=w.start_sec, end_sec=w.end_sec,
                        frames=[frames[i] for i in man.window_frames[w.window_id]], task_version="assist-v1",
                        cue_predicates=cues, max_output_tokens=int(settings.profile["sampling"]["observe_max_new_tokens"]))
                    try:
                        b = perception.observe(req, work, Deadline.in_seconds(300))
                    except Exception as e:  # keep going; record the failure for the reviewer
                        windows.append({"window_id": w.window_id, "start": w.start_sec, "end": w.end_sec,
                                        "error": getattr(e, "code", type(e).__name__)})
                        if not perception.ready():
                            perception.warmup(timeout_sec=180)
                        continue
                    t = lambda ids_: sorted(round(frames[i].relative_sec, 2) for i in ids_ if i in frames)  # noqa: E731
                    windows.append({
                        "window_id": w.window_id, "start": w.start_sec, "end": w.end_sec,
                        "observations": [{"t": t(o.frame_ids), "action": o.action, "actors": o.actor_refs,
                                          "objects": o.object_hypotheses, "visibility": o.visibility,
                                          "uncertainty": o.uncertainty} for o in b.observations],
                        "cues": [{"predicate_id": a.predicate_id, "rule_id": pred_rule.get(a.predicate_id),
                                  "state": a.state, "supporting_t": t(a.supporting_frame_ids),
                                  "contradicting_t": t(a.contradicting_frame_ids), "note": a.reasoning_note}
                                 for a in b.cue_assessments],
                        "limitations": b.limitations})
                doc = {"clip_id": cid, "source_sha256": sha, "duration_sec": meta.duration_sec,
                       "model": perception.info().model_dump(), "profile": plan.profile_id,
                       "status": "PROPOSAL — not a label until reviewed by a person", "windows": windows}
                (OUT / "proposals" / f"{cid}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
                video_rel = os.path.relpath(path.resolve(), (OUT / "review").resolve())
                (OUT / "review" / f"{cid}.html").write_text(
                    render(doc, video_rel, rule_names, {d.predicate_id: d.question_en for d in cues}), encoding="utf-8")
            finally:
                shutil.rmtree(work, ignore_errors=True)
            print(f"progress {n}/{len(clips)}", flush=True)
        perception.close()
    (OUT / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Annotation review</title><h1>Clips</h1><ul>"
        + "".join(f'<li><a href="review/{c}.html">{c}</a></li>' for c in index_rows) + "</ul>", encoding="utf-8")
    print(f"done: {len(clips)} clips; open {OUT.relative_to(ROOT)}/index.html locally")
    return 0


def render(doc: dict, video_rel: str, rule_names: dict, questions: dict) -> str:
    data = json.dumps({"doc": doc, "rules": rule_names, "questions": questions}, ensure_ascii=False)
    data = data.replace("</", "<\\/")
    return TEMPLATE.replace("__VIDEO__", html.escape(video_rel)).replace("__DATA__", data).replace(
        "__CID__", html.escape(doc["clip_id"]))


TEMPLATE = r"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>__CID__ review</title>
<style>
body{font:14px system-ui,sans-serif;margin:0;display:grid;grid-template-columns:minmax(320px,40%) 1fr;height:100vh}
#left{padding:12px;overflow:auto;border-right:1px solid #ccc}#right{padding:12px;overflow:auto}
video{width:100%;max-height:70vh;background:#000}.w{border:1px solid #ddd;margin:6px 0;padding:6px;border-radius:6px}
.t{color:#06c;cursor:pointer;text-decoration:underline}.sup{color:#070}.con{color:#a00}.unk{color:#777}
fieldset{margin:8px 0}label{display:block;margin:3px 0}input[type=text],input[type=number],select,textarea{width:100%}
.warn{background:#fff3cd;padding:6px;border-radius:4px}button{margin:2px}
</style></head><body>
<div id="left"><h2>__CID__</h2><video id="v" src="__VIDEO__" controls preload="metadata"></video>
<div>t = <b id="now">0.00</b> s</div>
<p class="warn">Model output below is a PROPOSAL, not a label. Verify every fact in the video.
Never copy this page, frames or notes into a cloud tool.</p>
<div id="ann"></div>
<button onclick="addAction()">+ observed action</button><button onclick="addEvent()">+ event (rule link)</button>
<button onclick="exportJson()">Export annotation JSON</button></div>
<div id="right"><h3>Model proposals (by window)</h3><div id="props"></div></div>
<script>
const D=__DATA__, v=document.getElementById('v');
v.ontimeupdate=()=>document.getElementById('now').textContent=v.currentTime.toFixed(2);
const seek=t=>{v.currentTime=Math.max(0,t-0.5);v.pause()};
const T=a=>(a||[]).map(t=>`<span class=t onclick="seek(${t})">${t}</span>`).join(' ');
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
document.getElementById('props').innerHTML=D.doc.windows.map(w=>`<div class=w><b>${w.window_id}</b>
 <span class=t onclick="seek(${w.start})">${w.start}–${w.end} s</span>${w.error?` <b class=con>error ${esc(w.error)}</b>`:''}
 ${(w.observations||[]).map(o=>`<div>• [${T(o.t)}] ${esc(o.action)} <i>(${esc(o.visibility)}${o.uncertainty?'; '+esc(o.uncertainty):''})</i>
 ${o.objects?.length?'<br>&nbsp;&nbsp;objects: '+esc(o.objects.join(', ')):''}</div>`).join('')}
 ${(w.cues||[]).filter(c=>c.state!=='unknown').map(c=>`<div class=${c.state==='supported'?'sup':'con'}>${esc(c.rule_id)} ${esc(c.predicate_id)}: ${c.state}
   [+${T(c.supporting_t)}] [−${T(c.contradicting_t)}] <span class=unk title="${esc(D.questions[c.predicate_id])}">?</span></div>`).join('')}
 ${(w.limitations||[]).length?'<div class=unk>limits: '+esc(w.limitations.join('; '))+'</div>':''}</div>`).join('');
const A={clip_id:D.doc.clip_id,source_sha256:D.doc.source_sha256,duration_sec:D.doc.duration_sec,group_id:'',split:'',
 annotator:'',annotated_at:'',model_proposals_profile:D.doc.profile,observed_actions:[],events:[]};
function now(){return +v.currentTime.toFixed(2)}
function render(){const el=document.getElementById('ann');el.innerHTML=`
 <fieldset><legend>Clip</legend><label>annotator <input type=text value="${esc(A.annotator)}" oninput="A.annotator=this.value"></label>
 <label>group_id (related takes share a group) <input type=text value="${esc(A.group_id)}" oninput="A.group_id=this.value"></label>
 <label>split <select onchange="A.split=this.value">${['','dev','val','holdout'].map(s=>`<option ${A.split===s?'selected':''}>${s}</option>`).join('')}</select></label></fieldset>
 ${A.observed_actions.map((a,i)=>`<fieldset><legend>action a${i+1}</legend>
  <label>start <input type=number step=0.01 value="${a.start_sec??''}" oninput="A.observed_actions[${i}].start_sec=+this.value"><button onclick="A.observed_actions[${i}].start_sec=now();render()">= now</button></label>
  <label>end <input type=number step=0.01 value="${a.end_sec??''}" oninput="A.observed_actions[${i}].end_sec=+this.value"><button onclick="A.observed_actions[${i}].end_sec=now();render()">= now</button></label>
  <label>visible facts only (no identity/intent/authorization) <textarea oninput="A.observed_actions[${i}].description_en=this.value">${esc(a.description_en)}</textarea></label>
  <label>visibility <select onchange="A.observed_actions[${i}].visibility=this.value">${['clear','partial','unusable'].map(s=>`<option ${a.visibility===s?'selected':''}>${s}</option>`).join('')}</select></label>
  <button onclick="A.observed_actions.splice(${i},1);render()">remove</button></fieldset>`).join('')}
 ${A.events.map((e,i)=>`<fieldset><legend>event ${i+1}</legend>
  <label>rule <select onchange="A.events[${i}].rule_id=this.value"><option value="unmapped">unmapped</option>${Object.entries(D.rules).map(([k,n])=>`<option value="${k}" ${e.rule_id===k?'selected':''}>${k} ${esc(n)}</option>`).join('')}</select></label>
  <label>action ids (comma) <input type=text value="${esc(e.action_ids.join(','))}" oninput="A.events[${i}].action_ids=this.value.split(',').map(s=>s.trim()).filter(Boolean)"></label>
  ${['start_sec','end_sec','anchor_sec'].map(k=>`<label>${k} <input type=number step=0.01 value="${e[k]??''}" oninput="A.events[${i}].${k}=+this.value"><button onclick="A.events[${i}].${k}=now();render()">= now</button></label>`).join('')}
  <label>evidence_sec (anchor first) <input type=text value="${esc(e.evidence_sec.join(','))}" oninput="A.events[${i}].evidence_sec=this.value.split(',').map(Number).filter(x=>!isNaN(x))"><button onclick="A.events[${i}].evidence_sec.push(now());render()">+ now</button></label>
  <label><input type=checkbox ${e.eligible?'checked':''} onchange="A.events[${i}].eligible=this.checked"> eligible (applicability + visibility satisfied)</label>
  <label><input type=checkbox ${e.counterexample?'checked':''} onchange="A.events[${i}].counterexample=this.checked"> compliant counterexample</label>
  <label>label_uncertainty <input type=text value="${esc(e.label_uncertainty)}" oninput="A.events[${i}].label_uncertainty=this.value"></label>
  <button onclick="A.events.splice(${i},1);render()">remove</button></fieldset>`).join('')}`}
function addAction(){A.observed_actions.push({action_id:'a'+(A.observed_actions.length+1),start_sec:now(),end_sec:null,description_en:'',roles:[],objects:[],visibility:'clear',occlusion_note:''});render()}
function addEvent(){A.events.push({action_ids:[],rule_id:'unmapped',eligible:false,start_sec:null,end_sec:null,anchor_sec:now(),evidence_sec:[],transaction_boundaries:'',counterexample:false,label_uncertainty:''});render()}
function exportJson(){A.annotated_at=new Date().toISOString();A.observed_actions.forEach((a,i)=>a.action_id='a'+(i+1));
 const b=new Blob([JSON.stringify(A,null,1)],{type:'application/json'}),u=URL.createObjectURL(b),l=document.createElement('a');
 l.href=u;l.download=A.clip_id+'.annotation.json';l.click();setTimeout(()=>URL.revokeObjectURL(u),1000)}
render();
</script></body></html>"""


if __name__ == "__main__":
    sys.exit(main())
