#!/usr/bin/env python3
"""Inject a universal top-right "grade" param panel + save/config system into pieces.

Pattern adopted from piece 05l (small param control set, top right), generalized so it
works on ANY canvas piece without touching its internals: the panel drives CSS filters
(hue / saturation / brightness / contrast) applied to the piece's <canvas>. A "save config"
button snapshots the current values to localStorage (keyed pa_cfg_<id>, each config keyed by
timestamp) and renders a stack of "config N" buttons below it; clicking one re-applies that
snapshot. Each save also POSTs to /api/config so the mutation worker can read saved gradings.

Idempotent: skips any piece that already contains the marker id `gp-panel`.

Usage:
    python3 scripts/inject_grade_panel.py            # all pieces/*/index.html
    python3 scripts/inject_grade_panel.py n8q 05l    # only the named piece ids
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PIECES = REPO / "pieces"
MARKER = "gp-panel"

BLOCK = r"""<!-- gp: universal grade panel + save/config (injected by scripts/inject_grade_panel.py) -->
<style>
  #gp-panel{position:fixed;right:10px;top:52px;width:172px;z-index:998;
    background:rgba(8,12,18,0.82);border:1px solid rgba(205,210,220,0.12);border-radius:6px;
    padding:10px 12px 8px;backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);
    font-family:ui-monospace,'SF Mono',Menlo,monospace;color:#cfe1d4;}
  #gp-panel .gp-h{font-size:9px;letter-spacing:0.14em;text-transform:uppercase;
    color:rgba(205,210,220,0.45);margin-bottom:8px;}
  #gp-panel label{display:flex;justify-content:space-between;font-size:9px;
    letter-spacing:0.04em;color:rgba(205,210,220,0.6);margin-bottom:2px;}
  #gp-panel label span{color:rgba(205,210,220,0.85);font-variant-numeric:tabular-nums;}
  #gp-panel input[type=range]{-webkit-appearance:none;appearance:none;width:100%;height:3px;
    background:rgba(205,210,220,0.12);border-radius:2px;outline:none;cursor:pointer;margin:0 0 9px;}
  #gp-panel input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:10px;height:10px;
    border-radius:50%;background:rgba(205,210,220,0.55);border:none;cursor:pointer;}
  #gp-panel input[type=range]::-moz-range-thumb{width:10px;height:10px;border-radius:50%;
    background:rgba(205,210,220,0.55);border:none;cursor:pointer;}
  #gp-save{width:100%;margin-top:2px;background:rgba(0,0,0,0.5);
    border:1px solid rgba(201,168,106,0.4);border-radius:3px;padding:5px 6px;
    color:rgba(201,168,106,0.9);cursor:pointer;font-family:inherit;font-size:10px;letter-spacing:0.04em;}
  #gp-save:hover{border-color:rgba(201,168,106,0.7);}
  #gp-panel button.gp-cfg{display:block;width:100%;margin-top:5px;background:rgba(0,0,0,0.4);
    border:1px solid rgba(205,210,220,0.15);border-radius:3px;padding:4px 6px;
    color:rgba(205,210,220,0.75);cursor:pointer;font-family:inherit;font-size:10px;text-align:left;}
  #gp-panel button.gp-cfg:hover{border-color:rgba(205,210,220,0.4);}
</style>
<div id="gp-panel">
  <div class="gp-h">grade</div>
  <label>hue<span id="gp-v-hue">0&deg;</span></label>
  <input id="gp-hue" type="range" min="0" max="360" step="1" value="0">
  <label>saturation<span id="gp-v-sat">1.00</span></label>
  <input id="gp-sat" type="range" min="0" max="2" step="0.01" value="1">
  <label>brightness<span id="gp-v-bri">1.00</span></label>
  <input id="gp-bri" type="range" min="0.3" max="2" step="0.01" value="1">
  <label>contrast<span id="gp-v-con">1.00</span></label>
  <input id="gp-con" type="range" min="0.5" max="2" step="0.01" value="1">
  <button id="gp-save">save config</button>
  <div id="gp-saved"></div>
</div>
<script>
(function(){
  function pieceId(){
    var parts=location.pathname.split('/').filter(Boolean);
    var i=parts.indexOf('pieces');
    if(i>=0&&parts[i+1]&&parts[i+1]!=='index.html')return parts[i+1];
    var last=parts.pop();
    if((last==='index.html'||!last)&&parts.length)last=parts.pop();
    if(last)return last;
    var el=document.querySelector('.id');
    return el?el.textContent.trim().split(/[\s·]/)[0]:'unknown';
  }
  var pid=pieceId();
  var KEY='pa_cfg_'+pid;
  var G={hue:0,sat:1,bri:1,con:1};
  function fmt(k,v){return k==='hue'?Math.round(v)+'°':(+v).toFixed(2);}
  function applyFilter(){
    var f='hue-rotate('+G.hue+'deg) saturate('+G.sat+') brightness('+G.bri+') contrast('+G.con+')';
    var cs=document.querySelectorAll('canvas');
    for(var i=0;i<cs.length;i++){cs[i].style.filter=f;}
  }
  var tries=0;
  (function waitCanvas(){
    if(document.querySelector('canvas')||tries++>80){applyFilter();return;}
    setTimeout(waitCanvas,100);
  })();
  function bind(key,id){
    var el=document.getElementById('gp-'+id),vl=document.getElementById('gp-v-'+id);
    el.addEventListener('input',function(){G[key]=parseFloat(el.value);vl.textContent=fmt(key,G[key]);applyFilter();});
  }
  bind('hue','hue');bind('sat','sat');bind('bri','bri');bind('con','con');
  function setSliders(c){
    G={hue:+c.hue,sat:+c.sat,bri:+c.bri,con:+c.con};
    var map={hue:'hue',sat:'sat',bri:'bri',con:'con'};
    for(var k in map){
      document.getElementById('gp-'+map[k]).value=G[k];
      document.getElementById('gp-v-'+map[k]).textContent=fmt(k,G[k]);
    }
    applyFilter();
  }
  function load(){try{return JSON.parse(localStorage.getItem(KEY)||'[]');}catch(e){return [];}}
  function store(arr){try{localStorage.setItem(KEY,JSON.stringify(arr));}catch(e){}}
  function renderSaved(){
    var wrap=document.getElementById('gp-saved');wrap.innerHTML='';
    var arr=load();
    arr.forEach(function(c,i){
      var b=document.createElement('button');
      b.className='gp-cfg';b.textContent='config '+(i+1);
      try{b.title=new Date(c.id).toLocaleString()+' · right-click to delete';}catch(e){}
      b.onclick=function(){setSliders(c);};
      b.oncontextmenu=function(e){e.preventDefault();var a=load();a.splice(i,1);store(a);renderSaved();};
      wrap.appendChild(b);
    });
  }
  document.getElementById('gp-save').onclick=function(){
    var arr=load();
    var cfg={id:Date.now(),hue:G.hue,sat:G.sat,bri:G.bri,con:G.con};
    arr.push(cfg);store(arr);renderSaved();
    fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({piece_id:pid,config:cfg})}).catch(function(){});
  };
  renderSaved();
  function reposition(){
    var gp=document.getElementById('gp-panel'),maxB=0;
    ['piece-ctrl','panel'].forEach(function(id){
      var el=document.getElementById(id);
      if(el){var r=el.getBoundingClientRect();
        if(r.right>window.innerWidth-80&&r.bottom>maxB)maxB=r.bottom;}
    });
    gp.style.top=(maxB>0?maxB+8:52)+'px';
  }
  reposition();addEventListener('resize',reposition);
})();
</script>
"""


def inject(html: str) -> str:
    if MARKER in html:
        return html  # already injected
    idx = html.rfind("</body>")
    if idx == -1:
        return html.rstrip() + "\n" + BLOCK + "\n"
    return html[:idx] + BLOCK + "\n" + html[idx:]


def main(argv):
    if argv:
        targets = [PIECES / pid / "index.html" for pid in argv]
    else:
        targets = sorted(PIECES.glob("*/index.html"))

    changed = skipped = missing = 0
    for path in targets:
        if not path.exists():
            print(f"missing: {path}")
            missing += 1
            continue
        html = path.read_text(encoding="utf-8")
        out = inject(html)
        if out == html:
            skipped += 1
            continue
        path.write_text(out, encoding="utf-8")
        changed += 1

    print(f"injected={changed} skipped(already/no-op)={skipped} missing={missing} "
          f"total_targets={len(targets)}")


if __name__ == "__main__":
    main(sys.argv[1:])
