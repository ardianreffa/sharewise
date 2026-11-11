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
        x0, y0, x1, y1 = redact.to_pixels(a["box"], (W, H), redact.PAD.get(a["kind"], 0.0), a.get("angle") or 0.0)
        m = max(4, int(max(x1 - x0, y1 - y0) * 0.12))
        px0, py0, px1, py1 = max(0, x0 - m), max(0, y0 - m), min(W, x1 + m), min(H, y1 + m)
        patch = redact.redact(small, [a]).crop((px0, py0, px1, py1))
        data["areas"].append({"id": a["id"], "box": [round(v, 4) for v in a["box"]], "kind": a["kind"], "on": a["on"],
                              "label": KIND_LABEL.get(a["kind"], a["kind"]), "patch": _data_uri(patch),
                              "angle": round(float(a.get("angle") or 0.0), 1), "shape": redact.shape_of(a),
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
main{max-width:1180px;margin:0 auto;padding:28px 20px 60px}h1{font-size:26px;margin:0 0 4px}p.lede{color:var(--soft);margin:0 0 18px;max-width:86ch}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
button{font:inherit;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:999px;padding:7px 16px;cursor:pointer}
button.sel{border-color:var(--ink);font-weight:600}
.stage{position:relative;display:inline-block;max-width:100%;user-select:none;touch-action:none;overflow:hidden;border-radius:10px}
.stage>img#img{display:block;max-width:100%;height:auto}
.box{position:absolute;border:3px solid currentColor;color:var(--on);background:rgba(224,162,74,.10);cursor:move;transform-origin:50% 50%}
.box.oval{border-radius:50%}.box.rect{border-radius:6px}
.box.manual{color:var(--add);background:rgba(111,191,150,.14)}
.box.off{border-style:dashed;color:var(--off);background:transparent}
.tag{position:absolute;left:0;top:-26px;font-size:12px;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:0 6px;white-space:nowrap;color:var(--ink)}
.ctl{position:absolute;width:16px;height:16px;border-radius:4px;background:var(--panel);border:2px solid currentColor}
.resize{right:-9px;bottom:-9px;cursor:nwse-resize}
.rotate{left:50%;top:-30px;margin-left:-8px;border-radius:50%;cursor:grab}
.rotate::after{content:"";position:absolute;left:5px;top:14px;width:2px;height:14px;background:currentColor}
.shape{right:-9px;top:-9px;width:20px;height:20px;font-size:11px;line-height:15px;text-align:center;cursor:pointer;color:var(--ink)}
.box.off .ctl{display:none}
.drawing{position:absolute;border:2px dashed var(--add);border-radius:50%;pointer-events:none}
.stage img.patch{position:absolute;pointer-events:none;display:none;max-width:none;border-radius:0}
.stage.after img.patch.on{display:block}
#approx{position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none;display:none}
.stage.after #approx{display:block}.stage.after .box{display:none}
.legend{display:flex;gap:18px;flex-wrap:wrap;color:var(--soft);font-size:14px;margin:12px 0}
.legend i{display:inline-block;width:14px;height:14px;border-radius:50%;border:3px solid var(--on);vertical-align:-2px;margin-right:6px}
.legend i.off{border-style:dashed;border-color:var(--off)}.legend i.add{border-color:var(--add);border-radius:4px}
.code{margin-top:16px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
.code textarea{width:100%;font:14px ui-monospace,Menlo,monospace;border:1px solid var(--line);border-radius:8px;padding:8px;background:var(--bg);color:var(--ink);resize:none}
.note{font-size:14px;color:var(--soft)}
</style></head><body><main>
<h1>Sharewise review: __NAME__</h1>
<p class="lede">Nothing has been changed. <b>Click</b> an area to switch it off or on (for example your own face in a selfie). <b>Drag</b> an area to move it,
its <b>corner</b> to resize, its <b>top dot</b> to rotate, and its <b>◯/▭ button</b> to change the shape (ovals for faces, rectangles for signs and plates).
<b>Drag on the photo</b> to add an area the detector missed. Then copy the code below and paste it to Hermes.
Ovals are rebuilt from their surroundings; rectangles become a blank plate in the sign's own colour. Nothing of what was there is kept.</p>
<div class="bar"><button id="bBefore" class="sel">Before: areas to hide</button><button id="bAfter">After: preview</button><button id="bUndo">Remove my last area</button></div>
<div class="stage" id="stage"><img id="img" src="__BEFORE__" width="__W__" height="__H__" alt="photo" draggable="false"><canvas id="approx"></canvas></div>
<div class="legend"><span><i></i>will be hidden</span><span><i class="off"></i>switched off (stays visible)</span><span><i class="add"></i>your own area</span></div>
<div class="code"><b>Selection code</b> <span class="note">(paste this to Hermes, or run <code>/sharewise apply &lt;code&gt;</code>)</span>
<textarea id="code" rows="2" readonly></textarea><div class="note" id="sum"></div></div>
<p class="note">The "after" view is exact for detected areas. Areas you changed or drew are shown as a close approximation; Hermes shows you the exact result before anything is shared.</p>
</main><script id="data" type="application/json">__DATA__</script><script>
(function(){
const D=JSON.parse(document.getElementById('data').textContent.replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&'));
const stage=document.getElementById('stage'),img=document.getElementById('img'),cv=document.getElementById('approx');
let manual=[],drag=null,start=null,ghost=null,px=null;
const pct=v=>(v*100).toFixed(3)+'%';
function own(a){ if(a.kind==='manual')return a; a.on=false;
  const m={box:a.box.slice(),kind:'manual',on:true,label:'Your area',angle:a.angle||0,shape:a.shape||'oval'}; manual.push(m); return m}
D.areas.forEach(a=>{a.angle=a.angle||0;a.shape=a.shape||'oval';const p=document.createElement('img');p.className='patch';p.src=a.patch;
  p.style.left=pct(a.pbox[0]);p.style.top=pct(a.pbox[1]);p.style.width=pct(a.pbox[2]);p.style.height=pct(a.pbox[3]);stage.appendChild(p);a.el=p});
function sizePx(){const r=img.getBoundingClientRect();return {W:r.width,H:r.height,L:r.left,T:r.top}}
function render(){
  stage.querySelectorAll('.box').forEach(b=>b.remove());
  D.areas.forEach(a=>a.el.classList.toggle('on',a.on));
  D.areas.concat(manual).forEach(a=>{
    const b=document.createElement('div');b.className='box '+a.kind+' '+a.shape+(a.on?'':' off');
    b.style.left=pct(a.box[0]+a.box[2]/2);b.style.top=pct(a.box[1]+a.box[3]/2);b.style.width=pct(a.box[2]);b.style.height=pct(a.box[3]);
    b.style.transform='translate(-50%,-50%) rotate('+a.angle+'deg)';
    const t=document.createElement('span');t.className='tag';t.textContent=a.label+(a.on?'':' (off)');b.appendChild(t);
    const rs=document.createElement('span');rs.className='ctl resize';b.appendChild(rs);
    const ro=document.createElement('span');ro.className='ctl rotate';b.appendChild(ro);
    const sh=document.createElement('span');sh.className='ctl shape';sh.textContent=a.shape==='oval'?'▭':'◯';sh.title='change shape';b.appendChild(sh);
    b.addEventListener('pointerdown',e=>{e.stopPropagation();e.preventDefault();
      const mode=e.target===rs?'resize':e.target===ro?'rotate':e.target===sh?'shape':'move';
      drag={a:a,mode:mode,x:e.clientX,y:e.clientY,box:a.box.slice(),angle:a.angle,moved:false};stage.setPointerCapture(e.pointerId)});
    stage.appendChild(b)});
  // selection code (v2): detected areas switched on + the user's own areas with angle and shape
  const ids=D.areas.filter(a=>a.on).map(a=>a.id).join(',');
  const n3=v=>Math.round(Math.min(Math.max(v,0),0.999)*1000).toString().padStart(3,'0');
  const mb=manual.map(m=>n3(m.box[0])+n3(m.box[1])+n3(Math.max(m.box[2],0.001))+n3(Math.max(m.box[3],0.001))+
     'a'+(((Math.round(m.angle)%360)+360)%360).toString().padStart(3,'0')+(m.shape==='rect'?'r':'o')).join('-');
  const body=D.check+'.'+D.photo+'.'+(ids||'none')+'.'+(mb||'none');
  let h=0;for(const c of body){h=(h*31+c.charCodeAt(0))>>>0}
  document.getElementById('code').value='SW2.'+body+'.'+(h%46656).toString(36);
  document.getElementById('sum').textContent=(D.areas.filter(a=>a.on).length+manual.length)+' areas will be hidden, '+
     D.areas.filter(a=>!a.on).length+' switched off, '+manual.length+' yours.';
  if(stage.classList.contains('after'))drawApprox();
}
stage.addEventListener('pointermove',e=>{
  if(drag){const s=sizePx(),a0=drag;
    if(!a0.moved){if(Math.abs(e.clientX-a0.x)+Math.abs(e.clientY-a0.y)<4||a0.mode==='shape')return;a0.moved=true;a0.a=own(a0.a)}
    const a=a0.a,b=a0.box,cx=s.L+(b[0]+b[2]/2)*s.W,cy=s.T+(b[1]+b[3]/2)*s.H;
    if(a0.mode==='move'){const dx=(e.clientX-a0.x)/s.W,dy=(e.clientY-a0.y)/s.H;a.box=[b[0]+dx,b[1]+dy,b[2],b[3]]}
    else if(a0.mode==='resize'){const r=-a.angle*Math.PI/180,vx=e.clientX-cx,vy=e.clientY-cy;
      const lx=Math.abs(vx*Math.cos(r)-vy*Math.sin(r)),ly=Math.abs(vx*Math.sin(r)+vy*Math.cos(r));
      const w=Math.max(2*lx/s.W,0.01),h=Math.max(2*ly/s.H,0.01);a.box=[b[0]+b[2]/2-w/2,b[1]+b[3]/2-h/2,w,h]}
    else if(a0.mode==='rotate'){a.angle=Math.round(Math.atan2(e.clientY-cy,e.clientX-cx)*180/Math.PI+90)}
    render();return}
  if(!start)return;const s=sizePx();const x=Math.min(Math.max((e.clientX-s.L)/s.W,0),1),y=Math.min(Math.max((e.clientY-s.T)/s.H,0),1);
  ghost.style.left=pct(Math.min(x,start.x));ghost.style.top=pct(Math.min(y,start.y));ghost.style.width=pct(Math.abs(x-start.x));ghost.style.height=pct(Math.abs(y-start.y))});
stage.addEventListener('pointerup',e=>{
  if(drag){const d=drag;drag=null;
    if(d.mode==='shape'&&!d.moved){const a=own(d.a);a.shape=a.shape==='oval'?'rect':'oval'}
    else if(!d.moved){if(d.a.kind==='manual'){manual=manual.filter(m=>m!==d.a)}else{d.a.on=!d.a.on}}
    render();return}
  if(!start)return;const s=sizePx();const x=Math.min(Math.max((e.clientX-s.L)/s.W,0),1),y=Math.min(Math.max((e.clientY-s.T)/s.H,0),1);
  const box=[Math.min(x,start.x),Math.min(y,start.y),Math.abs(x-start.x),Math.abs(y-start.y)];ghost.remove();start=null;
  if(box[2]>0.008&&box[3]>0.008){manual.push({box:box,kind:'manual',on:true,label:'Your area',angle:0,shape:'oval'})}render()});
stage.addEventListener('pointerdown',e=>{if(stage.classList.contains('after')||drag)return;const s=sizePx();
  start={x:(e.clientX-s.L)/s.W,y:(e.clientY-s.T)/s.H};ghost=document.createElement('div');ghost.className='drawing';stage.appendChild(ghost);stage.setPointerCapture(e.pointerId)});
// approximate "after" for the user's own areas: rectangles take the area's own plate colour, ovals the colour around them
function pixels(){if(px)return px;const c=document.createElement('canvas');c.width=img.naturalWidth;c.height=img.naturalHeight;
  const x=c.getContext('2d');x.drawImage(img,0,0);px={w:c.width,h:c.height,d:x.getImageData(0,0,c.width,c.height).data};return px}
function shapePath(ctx,a,W,H,grow){const cx=(a.box[0]+a.box[2]/2)*W,cy=(a.box[1]+a.box[3]/2)*H,w=a.box[2]*W*grow,h=a.box[3]*H*grow;
  ctx.save();ctx.translate(cx,cy);ctx.rotate(a.angle*Math.PI/180);ctx.beginPath();
  if(a.shape==='rect'){ctx.rect(-w/2,-h/2,w,h)}else{ctx.ellipse(0,0,w/2,h/2,0,0,2*Math.PI)}ctx.restore()}
function sample(a,mode){ // average colour: 'in' = inside the shape (rectangles), 'top'/'bottom' = the ring just outside an oval
  const P=pixels(),m=document.createElement('canvas');m.width=P.w;m.height=P.h;const x=m.getContext('2d');x.fillStyle='#fff';
  if(mode==='in'){shapePath(x,a,P.w,P.h,1);x.fill()}
  else{shapePath(x,a,P.w,P.h,1.5);x.fill();x.globalCompositeOperation='destination-out';shapePath(x,a,P.w,P.h,1.04);x.fill();
    // keep only the upper or lower half of the ring, in the area's own (rotated) frame
    const cx=(a.box[0]+a.box[2]/2)*P.w,cy=(a.box[1]+a.box[3]/2)*P.h;x.save();x.translate(cx,cy);x.rotate(a.angle*Math.PI/180);
    x.fillRect(-P.w*2,mode==='top'?0:-P.h*2,P.w*4,P.h*2);x.restore()}
  const M=x.getImageData(0,0,P.w,P.h).data;let r=0,g=0,b=0,n=0;
  for(let i=0;i<M.length;i+=16){if(M[i+3]>200){r+=P.d[i];g+=P.d[i+1];b+=P.d[i+2];n++}}
  return n?[r/n,g/n,b/n]:null}
function drawApprox(){const P=pixels();cv.width=P.w;cv.height=P.h;const x=cv.getContext('2d');x.clearRect(0,0,P.w,P.h);
  manual.forEach(a=>{const W=P.w,H=P.h,cx=(a.box[0]+a.box[2]/2)*W,cy=(a.box[1]+a.box[3]/2)*H,w=a.box[2]*W,h=a.box[3]*H;
    const rgb=c=>'rgb('+c.map(Math.round).join(',')+')';
    if(a.shape==='rect'){const c=sample(a,'in')||[128,128,128];x.fillStyle=rgb(c);shapePath(x,a,W,H,1);x.fill();return}
    // ovals: a soft gradient from the colours around the top to those around the bottom, feathered outward
    const top=sample(a,'top')||[128,128,128],bot=sample(a,'bottom')||top,soft=Math.max(3,Math.min(w,h)*0.16);
    const L=document.createElement('canvas');L.width=W;L.height=H;const y=L.getContext('2d');
    y.save();y.translate(cx,cy);y.rotate(a.angle*Math.PI/180);
    const gr=y.createLinearGradient(0,-h/2,0,h/2);gr.addColorStop(0,rgb(top));gr.addColorStop(1,rgb(bot));y.fillStyle=gr;
    y.beginPath();y.ellipse(0,0,w/2+soft*0.6,h/2+soft*0.6,0,0,2*Math.PI);y.fill();y.restore();
    x.save();x.filter='blur('+Math.round(soft)+'px)';x.drawImage(L,0,0);x.restore();      // soft outer edge
    x.save();shapePath(x,a,W,H,1);x.clip();x.filter='blur('+Math.round(soft*0.5)+'px)';x.drawImage(L,0,0);x.restore()})}  // solid inside
document.getElementById('bBefore').onclick=()=>{stage.classList.remove('after');bBefore.classList.add('sel');bAfter.classList.remove('sel')};
document.getElementById('bAfter').onclick=()=>{stage.classList.add('after');bAfter.classList.add('sel');bBefore.classList.remove('sel');drawApprox()};
document.getElementById('bUndo').onclick=()=>{manual.pop();render()};
render();
})();
</script></body></html>"""
