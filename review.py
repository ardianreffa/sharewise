"""A local review page for one photo: what would be hidden, before and after, with full manual control.

The page is self-contained and offline (no fonts, scripts or images from the internet). It lives in
Sharewise's own data folder and is removed with old checks. It shows a downsized copy of the photo
(no metadata) with the proposed areas, and a pre-rendered "after" preview. The user clicks an area to
switch it off or on, drags to add an area, and copies a short selection code back to Hermes.
"""

from __future__ import annotations

import base64
import html
import io
import json
from pathlib import Path

from PIL import Image

from . import redact

PREVIEW_EDGE = 1600
KIND_LABEL = {"face": "Face", "head": "Person, no face found", "text": "Street / house / plate", "plate": "Plate", "manual": "Your area"}


def _data_uri(im: Image.Image) -> str:
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=85)          # no exif/xmp/icc passed: metadata-free
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def build(upright: Image.Image, areas: list[dict], check_id: str, photo_index: int, name: str, out: Path) -> Path:
    """areas: [{"id": int, "box": (x,y,w,h), "kind": str, "on": bool, "why": str}] in 0..1 upright coordinates."""
    small = upright.copy(); small.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE)); W, H = small.size
    data = {"check": check_id, "photo": photo_index, "areas": []}
    for a in areas:
        # each area is rendered on its own, so the "after" view can follow every click
        x0, y0, x1, y1 = redact.to_pixels(a["box"], (W, H), redact.PAD.get(a["kind"], 0.0))
        m = max(4, int(max(x1 - x0, y1 - y0) * 0.12))
        px0, py0, px1, py1 = max(0, x0 - m), max(0, y0 - m), min(W, x1 + m), min(H, y1 + m)
        patch = redact.redact(small, [a]).crop((px0, py0, px1, py1))
        data["areas"].append({"id": a["id"], "box": [round(v, 4) for v in a["box"]], "kind": a["kind"], "on": a["on"],
                              "label": KIND_LABEL.get(a["kind"], a["kind"]), "patch": _data_uri(patch),
                              "pbox": [px0 / W, py0 / H, (px1 - px0) / W, (py1 - py0) / H]})
    page = TEMPLATE.replace("__BEFORE__", _data_uri(small)).replace("__AFTER__", "") \
                   .replace("__DATA__", html.escape(json.dumps(data), quote=False)).replace("__NAME__", html.escape(name)) \
                   .replace("__W__", str(small.width)).replace("__H__", str(small.height))
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"review-{photo_index}.html"; path.write_text(page, encoding="utf-8")
    return path


TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sharewise review</title><style>
:root{--bg:#e9ecea;--panel:#fbfcfb;--ink:#1c2321;--soft:#5b6662;--on:#a8640a;--off:#7d8783;--add:#2f6b4f;--line:#cfd6d2;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#161a19;--panel:#1f2523;--ink:#e6ebe8;--soft:#9aa6a1;--on:#e0a24a;--off:#7a8580;--add:#6fbf96;--line:#2e3633;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 -apple-system,system-ui,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 20px 60px}h1{font-size:26px;margin:0 0 4px}p.lede{color:var(--soft);margin:0 0 18px;max-width:80ch}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
button{font:inherit;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:999px;padding:7px 16px;cursor:pointer}
button.sel{border-color:var(--ink);font-weight:600}
.stage{position:relative;display:inline-block;max-width:100%;user-select:none;touch-action:none}
.stage img{display:block;max-width:100%;height:auto;border-radius:10px}
.box{position:absolute;border:3px solid var(--on);border-radius:50%;background:rgba(224,162,74,.12);cursor:pointer}
.box.text,.box.plate{border-radius:8px}.box.manual{border-radius:50%;border-color:var(--add);background:rgba(111,191,150,.14)}
.box.off{border-style:dashed;border-color:var(--off);background:transparent}
.tag{position:absolute;left:0;top:-24px;font-size:12px;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:0 6px;white-space:nowrap;color:var(--ink)}
.handle{position:absolute;right:-7px;bottom:-7px;width:14px;height:14px;border-radius:3px;background:var(--panel);border:2px solid currentColor;cursor:nwse-resize}
.box{color:var(--on)}.box.manual{color:var(--add)}.box.off .handle{display:none}
.drawing{position:absolute;border:2px dashed var(--add);border-radius:50%;pointer-events:none}
.after .box,.after .tag{display:none}
.stage img.patch{position:absolute;pointer-events:none;display:none;max-width:none;border-radius:0}.stage.after img.patch.on{display:block}
.ph{position:absolute;pointer-events:none;display:none;border-radius:50%;backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);background:rgba(160,150,140,.35)}
.after .ph{display:block}
.legend{display:flex;gap:18px;flex-wrap:wrap;color:var(--soft);font-size:14px;margin:12px 0}
.legend i{display:inline-block;width:14px;height:14px;border-radius:50%;border:3px solid var(--on);vertical-align:-2px;margin-right:6px}
.legend i.off{border-style:dashed;border-color:var(--off)}.legend i.add{border-color:var(--add);border-radius:4px}
.code{margin-top:16px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
.code textarea{width:100%;font:14px ui-monospace,Menlo,monospace;border:1px solid var(--line);border-radius:8px;padding:8px;background:var(--bg);color:var(--ink);resize:none}
.note{font-size:14px;color:var(--soft)}
</style></head><body><main>
<h1>Sharewise review: __NAME__</h1>
<p class="lede">Nothing has been changed. <b>Click</b> an area to switch it off or on (for example your own face in a selfie). <b>Drag an area</b> to move it, or <b>drag its corner</b> to resize it. <b>Drag on the photo</b> to add an area the detector missed. Then copy the code below and paste it to Hermes. Hidden areas are rebuilt from their surroundings: nothing of what was there is kept.</p>
<div class="bar"><button id="bBefore" class="sel">Before: areas to hide</button><button id="bAfter">After: preview</button><button id="bUndo">Remove my last area</button></div>
<div class="stage" id="stage"><img id="img" src="__BEFORE__" width="__W__" height="__H__" alt="photo" draggable="false"></div>
<div class="legend"><span><i></i>will be hidden</span><span><i class="off"></i>switched off (stays visible)</span><span><i class="add"></i>your own area</span></div>
<div class="code"><b>Selection code</b> <span class="note">(paste this to Hermes, or run <code>/sharewise apply &lt;code&gt;</code>)</span>
<textarea id="code" rows="2" readonly></textarea><div class="note" id="sum"></div></div>
<p class="note">The "after" view follows your clicks. Areas you draw yourself show as a soft placeholder here; Hermes shows you their exact result before anything is saved.</p>
</main><script id="data" type="application/json">__DATA__</script><script>
(function(){
const D=JSON.parse(document.getElementById('data').textContent.replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&'));
const stage=document.getElementById('stage'),img=document.getElementById('img');
let manual=[];
function pct(v){return (v*100).toFixed(3)+'%'}
function place(el,b){el.style.left=pct(b[0]);el.style.top=pct(b[1]);el.style.width=pct(b[2]);el.style.height=pct(b[3])}
D.areas.forEach(a=>{const p=document.createElement('img');p.className='patch';p.src=a.patch;place(p,a.pbox);stage.appendChild(p);a.el=p});
function render(){
  stage.querySelectorAll('.box,.ph').forEach(b=>b.remove());
  D.areas.forEach(a=>a.el.classList.toggle('on',a.on));
  manual.forEach(m=>{const p=document.createElement('div');p.className='ph';place(p,m.box);stage.appendChild(p)});
  D.areas.concat(manual).forEach(a=>{
    const b=document.createElement('div');b.className='box '+a.kind+(a.on?'':' off');
    b.style.left=pct(a.box[0]);b.style.top=pct(a.box[1]);b.style.width=pct(a.box[2]);b.style.height=pct(a.box[3]);
    const t=document.createElement('span');t.className='tag';t.textContent=a.label+(a.on?'':' (off)');b.appendChild(t);
    const hd=document.createElement('span');hd.className='handle';b.appendChild(hd);
    b.addEventListener('pointerdown',e=>{e.stopPropagation();e.preventDefault();
      drag={a:a,mode:(e.target===hd?'resize':'move'),x:e.clientX,y:e.clientY,box:a.box.slice(),moved:false};stage.setPointerCapture(e.pointerId)});
    stage.appendChild(b)});
  const ids=D.areas.filter(a=>a.on).map(a=>a.id).join(',');
  const mb=manual.map(m=>m.box.map(v=>Math.round(v*1000).toString().padStart(3,'0')).join('')).join('-');
  let body=D.check+'.'+D.photo+'.'+(ids||'none')+'.'+(mb||'none');
  let h=0;for(const c of body){h=(h*31+c.charCodeAt(0))>>>0}
  document.getElementById('code').value='SW1.'+body+'.'+(h%46656).toString(36);
  const off=D.areas.filter(a=>!a.on).length;
  document.getElementById('sum').textContent=(D.areas.filter(a=>a.on).length+manual.length)+' areas will be hidden, '+off+' switched off, '+manual.length+' added by you.';
}
let start=null,ghost=null,drag=null;
function own(a){ // moving or resizing a detected area turns it into the user's own area at the new place
  if(a.kind==='manual')return a;
  a.on=false;const m={box:a.box.slice(),kind:'manual',on:true,label:'Your area'};manual.push(m);return m}
stage.addEventListener('pointermove',e=>{if(!drag)return;const r=img.getBoundingClientRect();
  const dx=(e.clientX-drag.x)/r.width,dy=(e.clientY-drag.y)/r.height;
  if(!drag.moved){if(Math.abs(e.clientX-drag.x)+Math.abs(e.clientY-drag.y)<5)return;drag.moved=true;drag.a=own(drag.a)}
  const b=drag.box;
  if(drag.mode==='move'){drag.a.box=[Math.min(Math.max(b[0]+dx,0),1-b[2]),Math.min(Math.max(b[1]+dy,0),1-b[3]),b[2],b[3]]}
  else{drag.a.box=[b[0],b[1],Math.min(Math.max(b[2]+dx,0.01),1-b[0]),Math.min(Math.max(b[3]+dy,0.01),1-b[1])]}
  render()});
stage.addEventListener('pointerup',e=>{if(!drag)return;const d=drag;drag=null;
  if(!d.moved){if(d.a.kind==='manual'){manual=manual.filter(m=>m!==d.a)}else{d.a.on=!d.a.on}}render()});
stage.addEventListener('pointerdown',e=>{if(stage.classList.contains('after')||drag)return;const r=img.getBoundingClientRect();start={x:(e.clientX-r.left)/r.width,y:(e.clientY-r.top)/r.height};
  ghost=document.createElement('div');ghost.className='drawing';stage.appendChild(ghost);stage.setPointerCapture(e.pointerId)});
stage.addEventListener('pointermove',e=>{if(!start||drag)return;const r=img.getBoundingClientRect();const x=Math.min(Math.max((e.clientX-r.left)/r.width,0),1),y=Math.min(Math.max((e.clientY-r.top)/r.height,0),1);
  ghost.style.left=pct(Math.min(x,start.x));ghost.style.top=pct(Math.min(y,start.y));ghost.style.width=pct(Math.abs(x-start.x));ghost.style.height=pct(Math.abs(y-start.y))});
stage.addEventListener('pointerup',e=>{if(!start||drag)return;const r=img.getBoundingClientRect();const x=Math.min(Math.max((e.clientX-r.left)/r.width,0),1),y=Math.min(Math.max((e.clientY-r.top)/r.height,0),1);
  const box=[Math.min(x,start.x),Math.min(y,start.y),Math.abs(x-start.x),Math.abs(y-start.y)];ghost.remove();start=null;
  if(box[2]>0.008&&box[3]>0.008){manual.push({box:box,kind:'manual',on:true,label:'Your area'})}render()});
document.getElementById('bBefore').onclick=()=>{stage.classList.remove('after');bBefore.classList.add('sel');bAfter.classList.remove('sel')};
document.getElementById('bAfter').onclick=()=>{stage.classList.add('after');bAfter.classList.add('sel');bBefore.classList.remove('sel')};
document.getElementById('bUndo').onclick=()=>{manual.pop();render()};
render();
})();
</script></body></html>"""
