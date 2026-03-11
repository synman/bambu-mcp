"""
mjpeg_server.py — Local HTTP MJPEG server for Bambu Lab camera streams.

Serves Motion JPEG (multipart/x-mixed-replace) over HTTP using Python stdlib only.
One server instance per active printer stream. Started on demand, stopped on request
or MCP shutdown.

MJPEG is a standard HTTP streaming format where each frame is a complete JPEG image
delivered as a multipart HTTP part. Any modern browser natively displays it as live
video when the URL is opened directly.

Port allocation: drawn from the shared ephemeral port pool (port_pool.py).
Pool default range: 49152–49251 (IANA RFC 6335 Dynamic/Private range).
URL format: http://localhost:{port}/
"""

from __future__ import annotations

import collections
import json
import logging
import threading
import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Iterator

log = logging.getLogger(__name__)

_HTML_PAGE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Bambu Cam</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#000;display:flex;align-items:center;justify-content:center;height:100vh;overflow:hidden}
#stream{max-width:100%;max-height:100vh;display:block}
#hud{position:fixed;top:16px;left:16px;background:rgba(0,0,0,.85);color:#ddd;
  font:14px/1.6 'Courier New',monospace;padding:10px 14px;border-radius:8px;
  pointer-events:none;min-width:230px;max-width:310px;
  border:1px solid rgba(255,255,255,.08)}
.hud-hdr{font-size:12px;color:#888;letter-spacing:.08em;text-transform:uppercase;
  display:flex;justify-content:space-between;align-items:center;
  cursor:pointer;user-select:none;pointer-events:auto;margin-bottom:4px}
#hud-body{overflow:hidden;transition:max-height .25s ease}
#hud-body.collapsed{max-height:0}
.img-panel{position:fixed;bottom:16px;pointer-events:auto;
  background:rgba(0,0,0,.6);border-radius:8px;padding:6px;
  border:1px solid rgba(255,255,255,.08);
  transition:max-width .35s cubic-bezier(.17,.67,.36,1.12),
             max-height .35s cubic-bezier(.17,.67,.36,1.12),
             padding .35s ease}
.img-panel.hidden{display:none}
.img-panel-hdr{font:700 12px/1.4 'Courier New',monospace;color:#888;text-transform:uppercase;
  letter-spacing:.08em;display:flex;justify-content:space-between;align-items:center;
  cursor:pointer;user-select:none;padding-bottom:4px;pointer-events:auto}
.img-panel-body{overflow:hidden;transition:max-height .35s cubic-bezier(.17,.67,.36,1.12);max-height:600px}
.img-panel-body.collapsed{max-height:0}
.img-panel img{display:block;max-width:190px;max-height:190px;border-radius:4px;opacity:.92;
  transition:max-width .35s cubic-bezier(.17,.67,.36,1.12),
             max-height .35s cubic-bezier(.17,.67,.36,1.12)}
.img-panel.expanded img{max-width:570px;max-height:570px}
#thumb-wrap{left:16px}
#layout-wrap{right:16px}
#hp-sec-anomaly{overflow:hidden;transition:max-height .35s ease}
#hp-sec-anomaly img{display:block;width:100%;aspect-ratio:16/9;border-radius:4px;opacity:.92;margin-top:4px}
#health-panel.hp-wide #hp-sec-anomaly img{width:100%;object-fit:fill}
.hdr{font-size:12px;color:#888;text-transform:uppercase;letter-spacing:.08em;
  border-bottom:1px solid rgba(255,255,255,.1);margin-bottom:3px;padding-bottom:2px;margin-top:6px;
  cursor:pointer;pointer-events:auto;display:flex;justify-content:space-between;align-items:center;
  user-select:none}
.hdr:first-child{margin-top:0}
.hdr-chev{font-size:12px;color:#444;transition:transform .2s;margin-left:4px}
.hdr-chev.open{transform:rotate(180deg)}
.hdr-section{overflow:hidden;transition:max-height .25s ease}
.hdr-section.collapsed{max-height:0}
.row{display:flex;justify-content:space-between;gap:12px}
.lbl{color:#888}
.val{color:#ddd}
.hot{color:#ff9040}
.ok{color:#60d080}
.dim{color:#555}
.warn{color:#ff5050}
.sep{border-top:1px solid rgba(255,255,255,.08);margin:6px 0}
#badge{display:inline-block;font-size:12px;font-weight:700;padding:1px 7px;
  border-radius:3px;margin-bottom:5px;letter-spacing:.04em}
.bRUNNING{background:#1a5c2a;color:#60d080}
.bPREPARE{background:#1a2e5c;color:#80a0ff}
.bPAUSE{background:#5c4a1a;color:#ffcc40}
.bFAILED{background:#5c1a1a;color:#ff6060}
.bFINISH{background:#1a4a5c;color:#60d0e0}
.bIDLE,.bINIT{background:#222;color:#777}
.hidden{display:none}
#fps{position:fixed;top:12px;right:14px;background:rgba(0,0,0,.72);padding:6px 8px 8px;
  border-radius:6px;pointer-events:none;border:1px solid rgba(255,255,255,.10);
  display:none;flex-direction:column;align-items:stretch;gap:3px;width:52px;text-align:center}
#fps-num{font:800 14px/1 'Courier New',monospace;letter-spacing:-.02em;transition:color .4s;width:100%}
#fps-lbl{font:400 12px/1 'Courier New',monospace;letter-spacing:.08em;color:rgba(255,255,255,.4);width:100%}
#fps-chart{display:block;width:52px;height:20px}
.fps-hi{color:#39ff6e}.fps-mid{color:#f5a623}.fps-lo{color:#ff4444}
.error-link{pointer-events:auto;color:inherit;text-decoration:none;cursor:pointer}
.error-link:hover{text-decoration:underline}
@keyframes pulse{0%{opacity:1}50%{opacity:.42}100%{opacity:1}}
.heating{animation:pulse 1.5s ease-in-out infinite}
#speed-badge{display:none;font-size:11px;font-weight:700;padding:1px 6px;
  border-radius:3px;letter-spacing:.03em}
.sQ{background:#2a2a2a;color:#666}.sS{background:#1a3a1a;color:#50b060}
.sSP{background:#4a2e10;color:#e0902a}.sL{background:#4a1010;color:#e05050}
#progress-bar{display:flex;align-items:center;gap:6px;margin:4px 0 5px}
#progress-track{flex:1;height:3px;background:rgba(255,255,255,.18);border-radius:2px;overflow:hidden}
#progress-fill{height:100%;border-radius:2px;transition:width .6s;width:0}
#progress-pct{color:#ddd;font-family:'Courier New',monospace;white-space:nowrap;min-width:30px;text-align:right}
#filament-row{margin:2px 0 1px;font-size:13px}
#door-warn{font-size:12px;font-weight:700;margin-top:2px;padding:1px 0}
#humidity-row{font-size:12px;margin-top:3px}
#health-panel{position:fixed;top:118px;right:14px;width:180px;max-height:calc(100vh - 132px);overflow:hidden;
  background:rgba(0,0,0,.85);border:1px solid rgba(255,255,255,.08);border-radius:6px;
  padding:7px 10px 8px;display:none;flex-direction:column;gap:4px;pointer-events:auto;
  font-family:'Courier New',monospace;font-size:14px;
  transition:width .35s cubic-bezier(.17,.67,.36,1.12)}
#health-panel.hp-wide{width:calc(100vw - 340px)}
#health-panel .hp-hdr{font-size:12px;color:#888;letter-spacing:.08em;text-transform:uppercase;
  display:flex;justify-content:space-between;align-items:center;cursor:pointer;user-select:none}
#health-panel .hp-hdr .hp-chev{font-size:12px;color:#888;transition:transform .2s}
#health-panel .hp-hdr .hp-chev.open{transform:rotate(180deg)}
#health-panel .hp-body{overflow:hidden;transition:max-height .25s ease}
#health-panel .hp-body.collapsed{max-height:0}
#hp-verdict{display:inline-block;font-size:12px;font-weight:700;padding:1px 8px;
  border-radius:3px;letter-spacing:.04em}
.hpC{background:#1a5c2a;color:#60d080}.hpW{background:#5c4a1a;color:#ffcc40}.hpX{background:#5c1a1a;color:#ff5050}.hpD{background:#323248;color:#8888af}
#hp-score-row{display:flex;align-items:center;gap:6px;margin:3px 0 2px}
#hp-score-bar-track{flex:1;height:3px;background:#555;border-radius:2px}
#hp-score-bar-fill{height:100%;border-radius:2px;transition:width .6s}
#hp-phdc-row{display:flex;justify-content:space-between;align-items:center;margin:3px 0 2px}
#hp-health-val{font-size:14px;font-weight:700;min-width:46px}
#hp-conf-val{font-size:11px;color:#888;text-align:right}
.hp-metric-row{display:flex;justify-content:space-between;font-size:14px;margin:1px 0}
.hp-metric-row .hp-lbl{color:#888}.hp-metric-row .hp-val{color:#ddd}
.hp-sep{border:none;border-top:1px solid rgba(255,255,255,.08);margin:4px 0}
#hp-trend-section{}
.hp-spark-row{display:flex;align-items:center;gap:6px;margin:3px 0}
.hp-spark-row .hp-slbl{font-size:11px;color:#666;width:64px;flex-shrink:0;letter-spacing:.04em;text-transform:uppercase}
.hp-spark-row canvas{flex:1;height:48px;border-radius:3px;background:rgba(255,255,255,.04)}
.hp-spark-mini{flex:1;height:36px !important;border-radius:3px;background:rgba(255,255,255,.04)}
#hp-det-legend{margin-top:5px;font-size:11px;font-family:'Courier New',monospace}
.hp-det-row{display:flex;align-items:center;gap:5px;margin:2px 0}
.hp-det-swatch{display:inline-block;width:12px;height:12px;border-radius:2px;border:1.5px solid transparent;flex-shrink:0}
.hp-det-key{color:#888;min-width:52px;flex-shrink:0}
.hp-det-val{color:#555;font-size:10px;flex:1}
.hp-det-live{color:#ddd;min-width:44px;flex-shrink:0;font-weight:700}
.hp-det-thresh{color:#444;font-size:10px;flex:1}
</style>
</head>
<body>
<img id="stream">
<div id="fps"><span id="fps-num"></span><span id="fps-lbl">FPS</span><canvas id="fps-chart" width="52" height="20"></canvas></div>
<div id="health-panel">
  <div class="hp-hdr" onclick="hpToggle(this)">
    <span>JOB HEALTH</span><span class="hp-chev">▲</span>
  </div>
  <div id="hp-score-row">
    <span id="hp-verdict" class="hpC">CLEAN</span>
    <div id="hp-score-bar-track"><div id="hp-score-bar-fill" style="width:0%;background:#60d080"></div></div>
    <span id="hp-score-val" style="font-size:14px;font-weight:700;color:#60d080;min-width:34px;text-align:right">—</span>
  </div>
  <div id="hp-phdc-row" style="display:flex;justify-content:flex-end;align-items:baseline;gap:5px">
    <span style="font-size:11px;color:#666;letter-spacing:.04em">Confidence</span>
    <span id="hp-conf-val" style="font-size:13px;text-align:right">—</span>
  </div>
  <div id="hp-body" class="hp-body collapsed">
    <div class="hdr" onclick="hudToggle(this,'hp-sec-score')">Score<span class="hdr-chev open">▲</span></div>
    <div class="hdr-section" id="hp-sec-score" style="display:none">
      <img id="hp-gauge-img" src="" alt="Composite health gauge" style="display:block;width:100%;border-radius:4px;opacity:.92;margin-top:4px">
    </div>
    <div class="hdr" onclick="hudToggle(this,'hp-sec-metrics')">Metrics<span class="hdr-chev open">▲</span></div>
    <div class="hdr-section" id="hp-sec-metrics">
      <div class="hp-metric-row"><span class="hp-lbl">Hot px</span><span id="hp-hot" class="hp-val">—</span></div>
      <div class="hp-metric-row"><span class="hp-lbl">Strand</span><span id="hp-strand" class="hp-val">—</span></div>
      <div class="hp-metric-row"><span class="hp-lbl">Diff</span><span id="hp-diff" class="hp-val">—</span></div>
      <div class="hp-metric-row"><span class="hp-lbl">Layer</span><span id="hp-layer" class="hp-val">—</span></div>
      <div class="hp-metric-row"><span class="hp-lbl">Progress</span><span id="hp-progress" class="hp-val">—</span></div>
    </div>
    <div class="hdr" onclick="hudToggle(this,'hp-sec-trends')">Trends<span class="hdr-chev open">▲</span></div>
    <div class="hdr-section" id="hp-sec-trends">
      <div class="hp-spark-row"><span class="hp-slbl">Success</span><canvas id="hp-sp-canvas"></canvas></div>
      <div class="hp-spark-row"><span class="hp-slbl">Confidence</span><canvas id="hp-dc-canvas"></canvas></div>
      <div class="hp-spark-row"><span class="hp-slbl">Nozzle °C</span><canvas id="hp-nz-canvas" class="hp-spark-mini"></canvas></div>
      <div class="hp-spark-row"><span class="hp-slbl">Bed °C</span><canvas id="hp-bd-canvas" class="hp-spark-mini"></canvas></div>
      <div id="hp-trend-status" style="font-size:11px;color:#888;padding:4px 2px 2px;line-height:1.6"></div>
    </div>
    <div id="hp-anomaly-section" style="display:none">
      <div class="hdr" onclick="hpAnomalyToggle(this)">AI Detection<span class="hdr-chev open">▲</span></div>
      <div id="hp-sec-anomaly">
        <img id="hp-anomaly-img" src="" alt="Anomaly detection">
        <div id="hp-det-legend">
          <div class="hp-det-row"><span class="hp-det-swatch" style="border-color:#ffcc40"></span><span class="hp-det-key">Air Zone</span></div>
          <div class="hp-det-row"><span class="hp-det-swatch" style="border-color:#60d080"></span><span class="hp-det-key">Plate Zone</span></div>
          <div class="hp-det-row"><span class="hp-det-swatch" style="background:linear-gradient(90deg,#ff9040,#ff5050)"></span><span class="hp-det-key">Heat Map</span></div>
        </div>
      </div>
    </div>
    <div id="hp-radar-section" style="display:none">
      <div class="hdr" onclick="hudToggle(this,'hp-sec-radar')">Failure Drivers<span class="hdr-chev open">▲</span></div>
      <div class="hdr-section" id="hp-sec-radar">
        <img id="hp-radar-img" src="" alt="Failure factor radar" style="display:block;width:100%;aspect-ratio:1/1;border-radius:4px;opacity:.92;margin-top:4px">
      </div>
    </div>
  </div>
</div>
<div id="hud">
  <div class="hud-hdr" onclick="hudBodyToggle(this)">
    <span>PRINTER STATUS</span><span class="hp-chev open">▲</span>
  </div>
  <div id="progress-bar"><div id="progress-track"><div id="progress-fill"></div></div><span id="progress-pct"></span></div>
  <div id="hud-body">
    <div class="hdr" onclick="hudToggle(this,'sec-print')" style="align-items:baseline"><div id="badge" class="bIDLE">IDLE</div><div id="speed-badge" style="margin-left:4px"></div><span class="hdr-chev open" style="margin-left:auto">▲</span></div>
    <div class="hdr-section" id="sec-print">
      <div id="subtask" style="font-size:13px;font-weight:600;color:#e0b84e;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:275px;margin-bottom:2px"></div>
      <div class="row"><span class="lbl">Layer</span><span id="layers" class="val">\u2014</span></div>
      <div id="stage-row" class="row hidden"><span class="lbl">Stage</span><span id="stage" class="val" style="text-align:right;max-width:190px;font-size:12px">—</span></div>
      <div id="time-row" class="row hidden"><span class="lbl">Elapsed</span><span id="elapsed" class="val">\u2014</span></div>
      <div id="remain-row" class="row hidden"><span class="lbl">Remain</span><span id="remain" class="val">\u2014</span></div>
    </div>
    <div class="hdr" onclick="hudToggle(this,'sec-temps')">Temps<span class="hdr-chev open">▲</span></div>
    <div class="hdr-section" id="sec-temps">
    <div id="nozzles"></div>
    <div id="filament-row" style="display:none"></div>
    <div class="row"><span class="lbl">Bed</span><span id="bed" class="dim">\u2014</span></div>
    <div id="chamber-row" class="row hidden"><span class="lbl">Chamber</span><span id="chamber" class="dim">\u2014</span></div>
    <div id="door-warn" style="display:none"></div>
    </div>
    <div id="sec-fans" class="hidden">
      <div class="hdr" onclick="hudToggle(this,'sec-fans-body')">Fans<span class="hdr-chev open">▲</span></div>
      <div class="hdr-section" id="sec-fans-body"><div id="fans"></div></div>
    </div>
    <div class="hdr" id="hdr-status" style="align-items:center;cursor:default;pointer-events:none"><span id="wifi" class="dim"></span></div>
    <div class="hdr-section" id="sec-status">
      <div id="humidity-row" style="display:none"></div>
      <div id="errors" class="hidden" style="font-size:12px;margin-top:2px"></div>
    </div>
  </div>
</div>
<div id="thumb-wrap" class="img-panel hidden">
  <div class="img-panel-hdr" onclick="imgPanelHdrToggle(this,'thumb-body')">3D PREVIEW<span class="hdr-chev open">▲</span></div>
  <div class="img-panel-body" id="thumb-body">
    <img id="thumb-img" src="" alt="3D preview" onclick="imgPanelToggle(this.closest('.img-panel'))">
  </div>
</div>
<div id="layout-wrap" class="img-panel hidden">
  <div class="img-panel-hdr" onclick="imgPanelHdrToggle(this,'layout-body')">PLATE LAYOUT<span class="hdr-chev open">▲</span></div>
  <div class="img-panel-body" id="layout-body">
    <img id="layout-img" src="" alt="Plate layout" onclick="imgPanelToggle(this.closest('.img-panel'))">
  </div>
</div>
<script>
function hudBodyToggle(hdr){
  var body=document.getElementById('hud-body');
  var chev=hdr.querySelector('.hp-chev');
  if(body.classList.contains('collapsed')){body.classList.remove('collapsed');chev.classList.add('open');}
  else{body.classList.add('collapsed');chev.classList.remove('open');}
}
function hudToggle(hdr,secId){
  var sec=document.getElementById(secId);
  var chev=hdr.querySelector('.hdr-chev');
  if(!sec)return;
  if(sec.classList.contains('collapsed')){
    sec.classList.remove('collapsed');
    if(chev){chev.classList.add('open');}
  }else{
    sec.classList.add('collapsed');
    if(chev){chev.classList.remove('open');}
  }
}
function imgPanelToggle(el){
  el.classList.toggle('expanded');
}
function imgPanelHdrToggle(hdr,bodyId){
  var body=document.getElementById(bodyId);
  var chev=hdr.querySelector('.hdr-chev');
  if(body.classList.contains('collapsed')){body.classList.remove('collapsed');if(chev)chev.classList.add('open');}
  else{body.classList.add('collapsed');if(chev)chev.classList.remove('open');}
}
function fmtT(t,tgt){
  var s=t+'\u00b0C';
  if(tgt>0) s+=' / '+tgt+'\u00b0C';
  return s;
}
function tCls(t,tgt){
  if(tgt<=0) return 'dim';
  var d=Math.abs(t-tgt);
  return d<=5?'ok':d<=40?'hot':'val';
}
function fmtM(m){
  if(!m||m<=0) return '\u2014';
  return m<60?m+'m':Math.floor(m/60)+'h '+(m%60)+'m';
}
function update(d){
  var s=d.gcode_state||'IDLE';
  var badge=document.getElementById('badge');
  badge.textContent=s;
  badge.className='b'+s;

  var active=s==='RUNNING'||s==='PREPARE'||s==='PAUSE';
  var activeOrFinish=active||s==='FINISH';

  // E3 — progress bar + inline pct label
  var pfEl=document.getElementById('progress-fill');
  var stateColors={RUNNING:'#60d080',PREPARE:'#80a0ff',PAUSE:'#ffcc40',FAILED:'#ff6060',FINISH:'#60d0e0'};
  var pct=active?(d.print_percentage||0):0;
  pfEl.style.width=pct+'%';
  pfEl.style.background=stateColors[s]||'#555';
  document.getElementById('progress-pct').textContent=active?pct+'%':'';

  var lEl=document.getElementById('layers');
  lEl.textContent=(activeOrFinish&&d.total_layers>0)?d.current_layer+' / '+d.total_layers:'\u2014';

  var sub=document.getElementById('subtask');
  sub.textContent=(activeOrFinish&&d.subtask_name)?d.subtask_name:'';

  // E6 — speed level badge
  var spdEl=document.getElementById('speed-badge');
  var spdMap={1:['QUIET','sQ'],2:['STANDARD','sS'],3:['SPORT','sSP'],4:['LUDICROUS','sL']};
  var se=spdMap[d.speed_level];
  if(se&&active){spdEl.textContent=se[0];spdEl.className=se[1];spdEl.style.display='inline-block';}
  else spdEl.style.display='none';

  var sn=d.stage_name||'';
  var sRow=document.getElementById('stage-row');
  if(sn&&sn!=='Printing normally'){
    document.getElementById('stage').textContent=sn;
    sRow.classList.remove('hidden');
  } else { sRow.classList.add('hidden'); }

  var tRow=document.getElementById('time-row');
  var rRow=document.getElementById('remain-row');
  var elapsedVal=d.elapsed_minutes||0;
  var jobKey='elapsed_'+((d.subtask_name||d.gcode_file||'').replace(/[^a-zA-Z0-9]/g,'_'));
  if(elapsedVal>0){localStorage.setItem(jobKey,elapsedVal);}
  if(s==='IDLE'||s==='PREPARE'){localStorage.removeItem(jobKey);}
  var displayElapsed=elapsedVal>0?elapsedVal:(s!=='IDLE'&&s!=='PREPARE'?parseInt(localStorage.getItem(jobKey)||'0'):0);
  if(displayElapsed>0){document.getElementById('elapsed').textContent=fmtM(displayElapsed);tRow.classList.remove('hidden');}
  else tRow.classList.add('hidden');
  if(d.remaining_minutes>0){document.getElementById('remain').textContent=fmtM(d.remaining_minutes);rRow.classList.remove('hidden');}
  else rRow.classList.add('hidden');

  // E8 — heating animation helper
  function htg(t,tgt){return (tgt>0&&tgt-t>10)?' heating':'';}

  var nEl=document.getElementById('nozzles');
  nEl.innerHTML='';
  (d.nozzles||[]).forEach(function(n){
    var lbl=(d.nozzles.length>1)?('Nozzle '+(n.id===0?'R':'L')):'Nozzle';
    var cls=tCls(n.temp,n.target);
    nEl.innerHTML+='<div class="row"><span class="lbl">'+lbl+'</span><span class="'+cls+htg(n.temp,n.target)+'">'+fmtT(n.temp,n.target)+'</span></div>';
  });

  // E4 — active filament swatch
  var fRow=document.getElementById('filament-row');
  if(d.active_filament&&active){
    var f=d.active_filament;
    var fc=f.color||'#888';
    if(fc&&!fc.startsWith('#')&&/^[0-9a-fA-F]{6}$/.test(fc)) fc='#'+fc;
    var fh='<span style="display:inline-block;width:10px;height:10px;background:'+fc+
           ';border-radius:2px;vertical-align:middle;margin-right:4px;border:1px solid rgba(255,255,255,.2)"></span>';
    fh+='<span class="val">'+(f.type||'\u2014')+'</span>';
    if(f.remaining_pct>0) fh+=' <span class="dim">'+f.remaining_pct+'%</span>';
    fRow.innerHTML=fh;fRow.style.display='block';
  } else fRow.style.display='none';

  var bedEl=document.getElementById('bed');
  bedEl.textContent=fmtT(d.bed_temp,d.bed_temp_target);
  bedEl.className=tCls(d.bed_temp,d.bed_temp_target)+htg(d.bed_temp,d.bed_temp_target);

  var cRow=document.getElementById('chamber-row');
  if(d.chamber_temp>0||d.chamber_temp_target>0){
    var cEl=document.getElementById('chamber');
    cEl.textContent=fmtT(d.chamber_temp,d.chamber_temp_target);
    cEl.className=tCls(d.chamber_temp,d.chamber_temp_target)+htg(d.chamber_temp,d.chamber_temp_target);
    cRow.classList.remove('hidden');
  } else cRow.classList.add('hidden');

  // E5 — chamber door / lid warning
  var dwEl=document.getElementById('door-warn');
  var openParts=[];
  if(d.is_chamber_door_open) openParts.push('DOOR');
  if(d.is_chamber_lid_open) openParts.push('LID');
  if(openParts.length>0){
    dwEl.innerHTML='<span style="color:#ff6040">\u26a0 '+openParts.join(' + ')+' OPEN</span>';
    dwEl.style.display='block';
  } else dwEl.style.display='none';

  // E2 — heatbreak fan added to fan list
  var fanSec=document.getElementById('sec-fans');
  var fanData=[['Part',d.part_cooling_pct],['Aux',d.aux_pct],['Exhaust',d.exhaust_pct],['Heatbreak',d.heatbreak_pct]].filter(function(f){return f[1]>0;});
  if(fanData.length>0){
    var fEl=document.getElementById('fans');
    fEl.innerHTML='';
    fanData.forEach(function(f){fEl.innerHTML+='<div class="row"><span class="lbl">'+f[0]+'</span><span class="val">'+f[1]+'%</span></div>';});
    fanSec.classList.remove('hidden');
  } else fanSec.classList.add('hidden');

  // E7 — AMS humidity (shown only when elevated)
  var hmEl=document.getElementById('humidity-row');
  var hIdx=d.ams_humidity_index||0;
  if(hIdx>0&&hIdx<=2){
    var hc=hIdx===1?'#ff5050':'#ffcc40';
    hmEl.innerHTML='<span style="color:'+hc+'">&#x1F4A7; Humid '+hIdx+'/5</span>';
    hmEl.style.display='block';
  } else hmEl.style.display='none';

  // E1 — Wi-Fi signal bars
  var wEl=document.getElementById('wifi');
  if(d.wifi_signal){
    var wm=d.wifi_signal.match(/-?\\d+/);
    if(wm){
      var dbm=parseInt(wm[0]);
      var tier=dbm>=-50?4:dbm>=-60?3:dbm>=-70?2:dbm>=-80?1:0;
      var wc=tier===4?'#60d080':tier===3?'#a0e040':tier===2?'#ffcc40':tier===1?'#ff9040':'#ff5050';
      var bars=['\u2581','\u2583','\u2585','\u2587'];
      var ws='<span style="letter-spacing:1px">';
      bars.forEach(function(b,i){ws+='<span style="color:'+(i<tier?wc:'#333')+'">'+b+'</span>';});
      ws+='</span> <span style="color:#555">'+dbm+'</span>';
      wEl.innerHTML=ws;
    } else wEl.textContent=d.wifi_signal;
  } else wEl.textContent='';

  var eEl=document.getElementById('errors');
  if(d.hms_errors&&d.hms_errors.length>0){
    var html='';
    d.hms_errors.forEach(function(e){
      var lbl='\u26a0 '+(e.code||'error');
      if(e.url) html+='<a class="error-link warn" href="'+e.url+'" title="'+(e.description||'')+'" onclick="window.open(this.href,\\'hms_popup\\',\\'width=600,height=400,menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes\\');return false;">'+lbl+'</a><br>';
      else html+='<span class="warn" title="'+(e.description||'')+'">'+lbl+'</span><br>';
    });
    eEl.innerHTML=html;
    eEl.classList.remove('hidden');
  } else eEl.classList.add('hidden');

  // Auto-expand health panel when printing or failed; collapse otherwise.
  // Cleared on state transition so a new print always auto-expands fresh.
  var hpBody=document.getElementById('hp-body');
  var hpChev=document.querySelector('.hp-hdr .hp-chev');
  var hpShouldExpand=(s==='RUNNING'||s==='PAUSE'||s==='FAILED'||s==='FINISH');
  if(s!==_hpPrevState){_hpUserOverride=false;_hpPrevState=s;}
  if(!_hpUserOverride){
    if(hpShouldExpand){
      hpBody.classList.remove('collapsed');
      if(hpChev)hpChev.classList.add('open');
    } else {
      hpBody.classList.add('collapsed');
      if(hpChev)hpChev.classList.remove('open');
    }
  }
}
function refreshImages(){
  var t=Date.now();
  var tw=document.getElementById('thumb-wrap');
  var lw=document.getElementById('layout-wrap');
  fetch('/thumbnail?t='+t).then(function(r){
    if(r.ok&&r.headers.get('Content-Type').indexOf('image')>=0){
      document.getElementById('thumb-img').src='/thumbnail?t='+t;
      tw.classList.remove('hidden');
    } else { tw.classList.add('hidden'); }
  }).catch(function(){tw.classList.add('hidden');});
  fetch('/layout?t='+t).then(function(r){
    if(r.ok&&r.headers.get('Content-Type').indexOf('image')>=0){
      document.getElementById('layout-img').src='/layout?t='+t;
      lw.classList.remove('hidden');
    } else { lw.classList.add('hidden'); }
  }).catch(function(){lw.classList.add('hidden');});
  fetch('/annotated?t='+t).then(function(r){
    if(r.ok&&r.status!==204&&r.headers.get('Content-Type')&&r.headers.get('Content-Type').indexOf('image')>=0){
      document.getElementById('hp-anomaly-img').src='/annotated?t='+t;
      document.getElementById('hp-anomaly-section').style.display='';
    } else { document.getElementById('hp-anomaly-section').style.display='none'; }
  }).catch(function(){document.getElementById('hp-anomaly-section').style.display='none';});
  fetch('/factors_radar?t='+t).then(function(r){
    if(r.ok&&r.status!==204&&r.headers.get('Content-Type')&&r.headers.get('Content-Type').indexOf('image')>=0){
      document.getElementById('hp-radar-img').src='/factors_radar?t='+t;
      document.getElementById('hp-radar-section').style.display='';
    } else { document.getElementById('hp-radar-section').style.display='none'; }
  }).catch(function(){document.getElementById('hp-radar-section').style.display='none';});
  fetch('/health_panel_img?t='+t).then(function(r){
    if(r.ok&&r.status!==204&&r.headers.get('Content-Type')&&r.headers.get('Content-Type').indexOf('image')>=0){
      document.getElementById('hp-gauge-img').src='/health_panel_img?t='+t;
      document.getElementById('hp-sec-score').style.display='';
    } else { document.getElementById('hp-sec-score').style.display='none'; }
  }).catch(function(){document.getElementById('hp-sec-score').style.display='none';});
}
function poll(){_hpPoll();}
refreshImages();
setInterval(refreshImages,15000);
// Health panel state
var _hpScores=[];var _hpDcScores=[];var _hpNozzles=[];var _hpBeds=[];var _hpMaxSamples=30;
var _hpPollInterval=8000;var _hpLastPoll=0;
var _hpUserOverride=false;var _hpPrevState=null;
function hpToggle(hdr){
  var body=document.getElementById('hp-body');
  var chev=hdr.querySelector('.hp-chev');
  if(body.classList.contains('collapsed')){body.classList.remove('collapsed');chev.classList.add('open');}
  else{body.classList.add('collapsed');chev.classList.remove('open');}
  _hpUserOverride=true;
}
function hpAnomalyToggle(hdr){
  var panel=document.getElementById('health-panel');
  var chev=hdr.querySelector('.hdr-chev');
  if(panel.classList.contains('hp-wide')){
    panel.classList.remove('hp-wide');
    chev.classList.remove('open');
  } else {
    panel.classList.add('hp-wide');
    chev.classList.add('open');
  }
}
function hpUpdateSparkline(canvasId,data,color,minV,maxV,valLabel,dashed){
  var c=document.getElementById(canvasId);if(!c)return;
  c.width=c.offsetWidth||90;c.height=c.offsetHeight||48;
  var ctx=c.getContext('2d');var w=c.width;var h=c.height;
  ctx.clearRect(0,0,w,h);
  if(data.length<2)return;
  var mn=minV!==undefined?minV:Math.min.apply(null,data);
  var mx=maxV!==undefined?maxV:Math.max.apply(null,data);
  if(mx===mn)mx=mn+1;
  function yOf(v){return h-(v-mn)/(mx-mn)*(h-4)-2;}
  // Gradient fill (skip for dashed/confidence line)
  if(!dashed){
    ctx.beginPath();
    data.forEach(function(v,i){
      var x=i/(data.length-1)*w;
      if(i===0)ctx.moveTo(x,yOf(v));else ctx.lineTo(x,yOf(v));
    });
    ctx.save();
    ctx.lineTo(w,h);ctx.lineTo(0,h);ctx.closePath();
    var grd=ctx.createLinearGradient(0,0,0,h);
    grd.addColorStop(0,hexToRgba(color,.35));
    grd.addColorStop(1,hexToRgba(color,.03));
    ctx.fillStyle=grd;ctx.fill();ctx.restore();
  }
  // Stroke on top
  ctx.beginPath();
  data.forEach(function(v,i){
    var x=i/(data.length-1)*w;
    if(i===0)ctx.moveTo(x,yOf(v));else ctx.lineTo(x,yOf(v));
  });
  if(dashed)ctx.setLineDash([4,3]);
  ctx.strokeStyle=color;ctx.lineWidth=dashed?1.2:1.5;ctx.stroke();
  ctx.setLineDash([]);
  // Threshold hairlines for anomaly score
  if(minV===0&&maxV===0.3){
    ctx.setLineDash([2,3]);ctx.lineWidth=1;
    ctx.strokeStyle='rgba(255,204,64,.6)';ctx.beginPath();ctx.moveTo(0,yOf(0.08));ctx.lineTo(w,yOf(0.08));ctx.stroke();
    ctx.strokeStyle='rgba(255,80,80,.6)';ctx.beginPath();ctx.moveTo(0,yOf(0.20));ctx.lineTo(w,yOf(0.20));ctx.stroke();
    ctx.setLineDash([]);
  }
  // Current value label (top-right corner)
  if(valLabel!==undefined){
    ctx.font='bold 10px "Courier New",monospace';
    ctx.textAlign='right';ctx.textBaseline='top';
    ctx.fillStyle='rgba(0,0,0,.55)';
    ctx.fillText(valLabel,w-1,2);
    ctx.fillStyle=color;
    ctx.fillText(valLabel,w-2,1);
  }
}
function hexToRgba(hex,a){
  var r=parseInt(hex.slice(1,3),16),g=parseInt(hex.slice(3,5),16),b=parseInt(hex.slice(5,7),16);
  return 'rgba('+r+','+g+','+b+','+a+')';
}
function hpUpdateFromResult(d){
  var panel=document.getElementById('health-panel');
  panel.style.display='flex';
  var score=d.anomaly_score||0;
  var ph=d.success_probability;
  var dc=d.decision_confidence;
  var stageGated=(d.stage!==undefined&&d.stage!==255);
  // Composite = health × confidence (penalises low confidence proportionally)
  var comp=(ph!==null&&ph!==undefined&&dc!==null&&dc!==undefined)?(ph*dc):(ph!==null&&ph!==undefined?ph:null);
  var compVerdict,hColor;
  if(stageGated||comp===null){
    compVerdict='standby';hColor='#8888af';
  }else if(comp>=0.70){
    compVerdict='clean';hColor='#60d080';
  }else if(comp>=0.50){
    compVerdict='warning';hColor='#ffcc40';
  }else{
    compVerdict='critical';hColor='#ff5050';
  }
  var vEl=document.getElementById('hp-verdict');
  vEl.textContent=(compVerdict==='standby'?'STANDBY':compVerdict).toUpperCase();
  vEl.className=compVerdict==='critical'?'hpX':compVerdict==='warning'?'hpW':compVerdict==='standby'?'hpD':'hpC';
  var scoreValEl=document.getElementById('hp-score-val');
  if(comp!==null){
    scoreValEl.textContent=Math.round(comp*100)+'%';
    scoreValEl.style.color=hColor;
  }else{scoreValEl.textContent='—';scoreValEl.style.color='#888';}
  var fillEl=document.getElementById('hp-score-bar-fill');
  fillEl.style.width=(comp!==null?Math.min(100,comp*100):0)+'%';
  fillEl.style.background=hColor;
  var cEl=document.getElementById('hp-conf-val');
  if(d.decision_confidence!==null&&d.decision_confidence!==undefined){
    cEl.textContent=Math.round(d.decision_confidence*100)+'%';
  }else{cEl.textContent='—';}
  document.getElementById('hp-hot').textContent=d.hot_pct!==undefined?(d.hot_pct*100).toFixed(1)+'%':'—';
  document.getElementById('hp-strand').textContent=d.strand_score!==undefined?d.strand_score.toFixed(4):'—';
  document.getElementById('hp-diff').textContent=d.diff_score!==null&&d.diff_score!==undefined?d.diff_score.toFixed(4):'—';
  document.getElementById('hp-layer').textContent=(d.layer&&d.total_layers)?d.layer+'/'+d.total_layers:'—';
  document.getElementById('hp-progress').textContent=d.progress_pct!==undefined?d.progress_pct+'%':'—';
  var sp=d.success_probability;
  _hpScores.push(sp!==null&&sp!==undefined?sp:0);if(_hpScores.length>_hpMaxSamples)_hpScores.shift();
  hpUpdateSparkline('hp-sp-canvas',_hpScores,'#60d080',0,1,(sp!==null&&sp!==undefined?Math.round(sp*100)+'%':'—'));
  _hpDcScores.push(dc!==null&&dc!==undefined?dc:0);if(_hpDcScores.length>_hpMaxSamples)_hpDcScores.shift();
  hpUpdateSparkline('hp-dc-canvas',_hpDcScores,'#80a0ff',0,1,(dc!==null&&dc!==undefined?Math.round(dc*100)+'%':'—'),true);
  // Status text row in Trends section
  var statusEl=document.getElementById('hp-trend-status');
  if(statusEl){
    var gs=d.gcode_state||'';
    var stateColor=gs==='RUNNING'?'#40d0c0':gs==='PAUSE'?'#f0c040':gs==='FAILED'?'#ff5050':'#888';
    var stateStr='<span style="color:'+stateColor+'">'+gs+'</span>';
    var layerStr=(d.layer&&d.total_layers)?(' &nbsp;Layer: '+d.layer+'/'+d.total_layers):'';
    var humIdx=d.ams_humidity;var humStr='';
    if(humIdx!==null&&humIdx!==undefined&&humIdx>0){
      var humPct=Math.round((6-humIdx)/5*100);
      var humLabel=humIdx<=2?'WET':humIdx<=3?'Damp':'Dry';
      humStr=' &nbsp;AMS: '+humPct+'% ('+humLabel+')';
    }
    statusEl.innerHTML=stateStr+layerStr+humStr;
  }
}
function hpPollJobState(){
  var now=Date.now();
  if(now-_hpLastPoll<_hpPollInterval)return;
  _hpLastPoll=now;
  fetch('/job_state').then(function(r){return r.json();}).then(function(d){
    if(!d.error&&!d.status)hpUpdateFromResult(d);
  }).catch(function(){});
}
// Wire sparkline updates into existing status poll
var _origUpdate=typeof update==='function'?update:null;
function _hpPoll(){
  fetch('/status').then(function(r){return r.json();}).then(function(d){
    if(_origUpdate)_origUpdate(d);
    var f=d.fps||0;
    if(_lastFrameMs>0&&Date.now()-_lastFrameMs>3000)f=0;
    var fpsCont=document.getElementById('fps');
    if(f>0){
      fpsCont.style.display='flex';
      var numEl=document.getElementById('fps-num');
      numEl.textContent=f<2?f.toFixed(1):f;
      var cap=d.fps_cap||30;
      numEl.className=f>=cap*.8?'fps-hi':f>=cap*.4?'fps-mid':'fps-lo';
      var hist=d.fps_history||[];
      var cv=document.getElementById('fps-chart');
      if(cv&&hist.length>1){
        var ctx=cv.getContext('2d');
        ctx.clearRect(0,0,cv.width,cv.height);
        var maxV=Math.max(cap,Math.max.apply(null,hist));
        var lineColor=f>=cap*.8?'#39ff6e':f>=cap*.4?'#f5a623':'#ff4444';
        ctx.strokeStyle=lineColor;
        ctx.lineWidth=1.5;
        ctx.beginPath();
        for(var i=0;i<hist.length;i++){
          var x=i/(hist.length-1)*cv.width;
          var y=cv.height-(hist[i]/maxV)*(cv.height-2)+1;
          if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
        }
        ctx.stroke();
      }
    }else{fpsCont.style.display='none';}
    // Update temp sparklines (fixed scales: nozzle 0-310°C, bed 0-120°C)
    var nozzle=d.nozzles&&d.nozzles.length?d.nozzles[0].temp:0;
    var bed=d.bed_temp||0;
    _hpNozzles.push(nozzle);if(_hpNozzles.length>_hpMaxSamples)_hpNozzles.shift();
    _hpBeds.push(bed);if(_hpBeds.length>_hpMaxSamples)_hpBeds.shift();
    hpUpdateSparkline('hp-nz-canvas',_hpNozzles,'#ff9040',0,310,Math.round(nozzle)+'°');
    hpUpdateSparkline('hp-bd-canvas',_hpBeds,'#80a0ff',0,120,Math.round(bed)+'°');
    hpPollJobState();
  }).catch(function(){});}
poll();setInterval(poll,2000);
// Fetch-based MJPEG parser: bypasses Safari's broken <img src="multipart"> loader.
// Reads the raw multipart stream via fetch(), parses Content-Length from each part
// header, extracts the JPEG bytes, and sets img.src to a blob URL. Works in all browsers.
var _lastFrameMs=0;
(function(){
  var img=document.getElementById('stream');
  var prevUrl=null;
  function connect(){
    fetch('/stream').then(function(r){
      var reader=r.body.getReader();
      var buf=new Uint8Array(0);
      function indexOf(h,n,s){
        s=s||0;
        outer:for(var i=s;i<=h.length-n.length;i++){
          for(var j=0;j<n.length;j++){if(h[i+j]!==n[j])continue outer;}
          return i;
        }
        return -1;
      }
      var HDR_END=new Uint8Array([13,10,13,10]); // \r\n\r\n
      function process(){
        while(true){
          var he=indexOf(buf,HDR_END);
          if(he===-1)return;
          var hdr=new TextDecoder().decode(buf.slice(0,he));
          var m=hdr.match(/Content-Length:\\s*(\\d+)/i);
          if(!m){buf=buf.slice(he+4);continue;}
          var cl=parseInt(m[1]);
          var ds=he+4;
          if(buf.length<ds+cl)return;
          var jpeg=buf.slice(ds,ds+cl);
          buf=buf.slice(ds+cl);
          var blob=new Blob([jpeg],{type:'image/jpeg'});
          var url=URL.createObjectURL(blob);
          if(prevUrl)URL.revokeObjectURL(prevUrl);
          prevUrl=url;
          _lastFrameMs=Date.now();
          img.src=url;
        }
      }
      function pump(){
        reader.read().then(function(res){
          if(res.done){setTimeout(connect,1000);return;}
          var n=new Uint8Array(buf.length+res.value.length);
          n.set(buf);n.set(res.value,buf.length);buf=n;
          process();
          pump();
        }).catch(function(){setTimeout(connect,1000);});
      }
      pump();
    }).catch(function(){setTimeout(connect,1000);});
  }
  connect();
})();
</script>
</body>
</html>
"""


class _MJPEGHTTPServer(ThreadingHTTPServer):
    """HTTPServer subclass that holds a frame factory and optional status/image callbacks.

    Each client connection to /stream gets its own independent frame iterator via
    frame_factory(), so multiple browser tabs never compete for the same generator.
    """

    def __init__(self, addr, handler_class, frame_factory: Callable[[], Iterator[bytes]],
                 status_fn: Callable[[], dict] | None = None,
                 thumbnail_fn: Callable[[], bytes | None] | None = None,
                 layout_fn: Callable[[], bytes | None] | None = None,
                 fps_cap: float = 30,
                 printer_name: str = "",
                 frame_transform_fn: Callable[[bytes, str, int], bytes] | None = None):
        super().__init__(addr, handler_class)
        log.debug("_MJPEGHTTPServer.__init__: starting on port %s", addr[1])
        self.frame_factory = frame_factory
        self.status_fn = status_fn
        self.thumbnail_fn = thumbnail_fn
        self.layout_fn = layout_fn
        self.fps_cap = fps_cap
        self.printer_name = printer_name
        self.frame_transform_fn = frame_transform_fn
        self._running = True
        # FPS tracking — rolling 10s window of frame timestamps; deduplicated by frame id
        self._fps_lock = threading.Lock()
        self._fps_times: collections.deque[float] = collections.deque()
        self._fps_last_frame_id: int = -1
        self._fps_history: collections.deque[float] = collections.deque(maxlen=60)
        log.debug("_MJPEGHTTPServer.__init__: ready on port %s", addr[1])


class _StreamHandler(BaseHTTPRequestHandler):
    # HTTP/1.0: no chunked encoding; Safari's MJPEG parser expects raw multipart bytes
    def do_GET(self):
        path = self.path.split("?", 1)[0]  # strip query params (cache-busting)
        log.debug("do_GET: path=%s client=%s", path, self.client_address[0])
        if path == "/status":
            self._serve_status()
        elif path == "/thumbnail":
            self._serve_image(self.server.thumbnail_fn)
        elif path == "/layout":
            self._serve_image(self.server.layout_fn)
        elif path == "/annotated":
            self._serve_annotated()
        elif path == "/factors_radar":
            self._serve_monitor_png("factors_radar_png")
        elif path == "/health_panel_img":
            self._serve_monitor_png("health_panel_png")
        elif path in ("/", "/index.html"):
            self._serve_html()
        elif path == "/snapshot":
            self._serve_snapshot()
        elif path == "/job_state":
            self._serve_job_state()
        elif path == "/open":
            self._serve_open()
        else:
            self._serve_stream()

    def _serve_html(self):
        log.debug("_serve_html: serving HTML to %s", self.client_address)
        title = f"Bambu Cam — {self.server.printer_name}" if self.server.printer_name else "Bambu Cam"
        # Inject per-client quality params into the stream fetch URL so each browser
        # tab gets the resolution/quality it requested via ?resolution=X&quality=Y.
        stream_url = "/stream"
        if "?" in self.path:
            qs = self.path.split("?", 1)[1]
            params = urllib.parse.parse_qs(qs)
            resolution = params.get("resolution", ["native"])[0]
            quality = int(params.get("quality", ["85"])[0])
            if not (resolution == "native" and quality == 85):
                stream_url = f"/stream?resolution={resolution}&quality={quality}"
        body = _HTML_PAGE.replace("<title>Bambu Cam</title>", f"<title>{title}</title>", 1)
        body = body.replace("fetch('/stream')", f"fetch('{stream_url}')").encode()
        log.debug("_serve_html: %d bytes", len(body))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        log.debug("_serve_html: done, %d bytes sent to %s", len(body), self.client_address)

    def _serve_status(self):
        log.debug("_serve_status: called from %s", self.client_address)
        data = {}
        if self.server.status_fn:
            try:
                data = self.server.status_fn()
            except Exception as e:
                log.error("_serve_status: status_fn raised: %s", e, exc_info=True)
        # Compute FPS from rolling 10s window
        now = time.monotonic()
        with self.server._fps_lock:
            dq = self.server._fps_times
            while dq and now - dq[0] > 10.0:
                dq.popleft()
            total = len(dq)
        fps_val = total / 10.0
        fps_rounded = round(fps_val, 2) if fps_val < 2 else round(fps_val)
        self.server._fps_history.append(fps_rounded)
        fps_history = list(self.server._fps_history)
        data["fps"] = fps_rounded
        data["fps_cap"] = self.server.fps_cap
        data["fps_history"] = fps_history
        log.debug("_serve_status: fps=%.1f for request from %s", fps_val, self.client_address)
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_image(self, fn: Callable[[], bytes | None] | None):
        log.debug("_serve_image: fn=%s", 'present' if fn else 'None')
        if fn is None:
            log.debug("_serve_image: not found for %s", self.client_address)
            self.send_response(404)
            self.end_headers()
            return
        try:
            data = fn()
        except Exception as e:
            log.error("_serve_image: fn raised: %s", e, exc_info=True)
            data = None
        if not data:
            log.debug("_serve_image: not found for %s", self.client_address)
            self.send_response(404)
            self.end_headers()
            return
        log.debug("_serve_image: served %d bytes to %s", len(data), self.client_address)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_job_state(self):
        """Return the cached active job state report from the background monitor."""
        log.debug("_serve_job_state: requested by %s", self.client_address)
        printer_name = getattr(self.server, "printer_name", None)
        if not printer_name:
            body = json.dumps({"error": "no_printer"}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        try:
            from camera import job_monitor
            result = job_monitor.get_latest_result(printer_name)
            if result is None:
                body = json.dumps({"status": "no_data", "printer": printer_name}).encode()
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = json.dumps(result).encode()
            log.debug("_serve_job_state: cached result for %s (%d bytes)", printer_name, len(body))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            log.error("_serve_job_state: error: %s", e, exc_info=True)
            body = json.dumps({"error": "internal_error", "detail": str(e)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)




    def _serve_annotated(self):
        """Return the annotated anomaly-detection frame from the background monitor cache."""
        log.debug("_serve_annotated: requested by %s", self.client_address)
        printer_name = getattr(self.server, "printer_name", None)
        try:
            import base64
            from camera import job_monitor
            result = job_monitor.get_latest_result(printer_name) if printer_name else None
            uri = (result or {}).get("annotated_png", "")
            if uri and uri.startswith("data:"):
                _, b64 = uri.split(",", 1)
                body = base64.b64decode(b64)
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
        except Exception as e:
            log.debug("_serve_annotated: error: %s", e)
        self.send_response(204)
        self.end_headers()

    def _serve_monitor_png(self, result_key: str):
        """Serve a PNG stored as a data URI in the monitor result dict."""
        printer_name = getattr(self.server, "printer_name", None)
        try:
            import base64
            from camera import job_monitor
            result = job_monitor.get_latest_result(printer_name) if printer_name else None
            uri = (result or {}).get(result_key, "")
            if uri and uri.startswith("data:"):
                _, b64 = uri.split(",", 1)
                body = base64.b64decode(b64)
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
        except Exception as e:
            log.debug("_serve_monitor_png(%s): error: %s", result_key, e)
        self.send_response(204)
        self.end_headers()

    def _serve_snapshot(self):
        """Return a single JPEG frame as image/jpeg and close the connection."""
        log.debug("_serve_snapshot: requested by %s", self.client_address)
        try:
            jpeg = next(iter(self.server.frame_factory()))
        except StopIteration:
            log.warning("_serve_snapshot: no frame available for %s", self.client_address)
            self.send_response(503)
            self.end_headers()
            return
        except Exception as e:
            log.error("_serve_snapshot: frame_factory raised: %s", e, exc_info=True)
            self.send_response(500)
            self.end_headers()
            return
        log.debug("_serve_snapshot: serving %d bytes to %s", len(jpeg), self.client_address)
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(jpeg)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(jpeg)

    def _serve_open(self):
        """Serve a portal page that opens the stream in a named browser window/tab.

        Called by view_stream() via webbrowser.open('/open?name=bambu-{printer}').
        The page calls window.open('/', target) — browsers reuse an existing window
        with that name on subsequent calls, achieving single-tab-per-printer behavior.
        Resolution/quality params are forwarded to the stream page.
        """
        log.debug("_serve_open: serving portal to %s", self.client_address)
        # Forward resolution/quality so the opened tab requests the right quality.
        stream_path = "/"
        if "?" in self.path:
            qs = self.path.split("?", 1)[1]
            params = urllib.parse.parse_qs(qs)
            resolution = params.get("resolution", ["native"])[0]
            quality = int(params.get("quality", ["85"])[0])
            if not (resolution == "native" and quality == 85):
                stream_path = f"/?resolution={resolution}&quality={quality}"
        html = (
            "<!doctype html><html><head><title>Opening Bambu Cam\u2026</title></head>"
            "<body><script>"
            "var n=new URLSearchParams(location.search).get('name')||'bambu-cam';"
            f"var w=window.open(location.origin+'{stream_path}',n);"
            "if(w){w.focus();setTimeout(function(){window.close();},150);}"
            f"else{{location.replace('{stream_path}');}}"
            "</script><p>Opening stream\u2026</p></body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(html)

    def _serve_stream(self):
        ua = self.headers.get("User-Agent", "unknown")
        log.debug("_serve_stream: starting for client %s", self.client_address)
        log.info("stream_connect: client=%s ua=%s", self.client_address[0], ua)
        # Parse per-client quality params from the request URL.
        resolution = "native"
        quality = 85
        if "?" in self.path:
            qs = self.path.split("?", 1)[1]
            params = urllib.parse.parse_qs(qs)
            resolution = params.get("resolution", ["native"])[0]
            quality = int(params.get("quality", ["85"])[0])
        transform = self.server.frame_transform_fn
        apply_transform = transform is not None and not (resolution == "native" and quality == 85)
        log.debug("_serve_stream: resolution=%s quality=%d apply_transform=%s", resolution, quality, apply_transform)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        try:
            for jpeg in self.server.frame_factory():
                if not self.server._running:
                    break
                if apply_transform:
                    try:
                        jpeg = transform(jpeg, resolution, quality)
                    except Exception as _te:
                        log.debug("_serve_stream: transform error: %s", _te)
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(b"X-Timestamp: 0.000000\r\n")
                self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                log.debug("_serve_stream: frame written size=%d", len(jpeg))
                # Count FPS once per unique frame regardless of how many clients are connected
                with self.server._fps_lock:
                    fid = id(jpeg)
                    if fid != self.server._fps_last_frame_id:
                        self.server._fps_last_frame_id = fid
                        self.server._fps_times.append(time.monotonic())
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as e:
            log.warning("_serve_stream: client %s disconnected: %s", self.client_address, e)
        else:
            log.info("stream_disconnect: client=%s reason=normal", self.client_address[0])
        log.debug("_serve_stream: stream ended for client %s", self.client_address)

    def log_message(self, format, *args):
        log.debug("HTTP %s %s from %s", args[1] if len(args) > 1 else '?', self.path, self.client_address[0])


@dataclass
class _ServerEntry:
    server: _MJPEGHTTPServer
    thread: threading.Thread
    port: int
    closer: Callable[[], None] | None = None


class MJPEGServer:
    """
    Manages per-printer local MJPEG HTTP servers.

    Each printer gets its own server instance on a unique port.
    """

    def __init__(self):
        self._servers: dict[str, _ServerEntry] = {}
        self._lock = threading.Lock()

    def start(self, name: str, frame_factory: Callable[[], Iterator[bytes]], port: int | None = None,
              status_fn: Callable[[], dict] | None = None,
              thumbnail_fn: Callable[[], bytes | None] | None = None,
              layout_fn: Callable[[], bytes | None] | None = None,
              closer: Callable[[], None] | None = None,
              fps_cap: float = 30,
              frame_transform_fn: Callable[[bytes, str, int], bytes] | None = None) -> str:
        """
        Start a local MJPEG server for the named printer.

        If a server is already running for this name, returns the existing URL.
        Returns the server URL: http://localhost:{port}/

        Port is drawn from the shared ephemeral port pool (IANA RFC 6335 range
        49152–49251 by default).  Caller may pass a preferred port; the pool will
        try it first and rotate to the next available port if it is taken.

        frame_factory — callable returning a fresh Iterator[bytes] per client connection;
                        each browser tab gets its own independent RTSPS session.
        status_fn   — returns live printer state dict for /status (polled every 2s)
        thumbnail_fn — returns PNG bytes of the isometric 3D thumbnail for /thumbnail
        layout_fn   — returns PNG bytes of the annotated top-down plate layout for /layout
        Both image endpoints are polled every 15s and hidden when unavailable.
        """
        with self._lock:
            log.debug("MJPEGServer.start: name=%s port=%s", name, port if port is not None else "auto")
            if name in self._servers:
                existing_port = self._servers[name].port
                log.info("MJPEGServer.start: '%s' already running on port %d", name, existing_port)
                return f"http://localhost:{existing_port}/"
            from port_pool import port_pool as _pp
            allocated_port = _pp.allocate(preferred=port)
            log.info("MJPEGServer.start: starting '%s' on port %d", name, allocated_port)
            server = _MJPEGHTTPServer(
                ("", allocated_port), _StreamHandler, frame_factory,
                status_fn=status_fn, thumbnail_fn=thumbnail_fn, layout_fn=layout_fn,
                fps_cap=fps_cap, printer_name=name,
                frame_transform_fn=frame_transform_fn,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self._servers[name] = _ServerEntry(server=server, thread=thread, port=allocated_port, closer=closer)
            # Probe the socket to ensure serve_forever() has entered its accept loop
            # before we return the URL (prevents "connection refused" on immediate browser open).
            import socket as _socket
            import time as _time
            deadline = _time.monotonic() + 3.0
            while _time.monotonic() < deadline:
                try:
                    with _socket.create_connection(("127.0.0.1", allocated_port), timeout=0.25):
                        break
                except OSError:
                    _time.sleep(0.05)
        log.info("MJPEGServer.start: started server for %s at http://localhost:%d/", name, allocated_port)
        return f"http://localhost:{allocated_port}/"

    def stop(self, name: str) -> bool:
        """
        Stop the MJPEG server for the named printer.

        Returns True if a server was running and has been stopped.
        Releases the port back to the shared ephemeral pool.
        """
        log.info("MJPEGServer.stop: stopping '%s'", name)
        with self._lock:
            entry = self._servers.pop(name, None)
        if entry is None:
            log.debug("MJPEGServer.stop: '%s' not found", name)
            return False
        entry.server._running = False
        entry.server.shutdown()
        try:
            from port_pool import port_pool as _pp
            _pp.release(entry.port)
            log.debug("MJPEGServer.stop: released port %d to pool", entry.port)
        except Exception as e:
            log.warning("MJPEGServer.stop: port release error for '%s': %s", name, e, exc_info=True)
        if entry.closer:
            log.debug("MJPEGServer.stop: calling closer for '%s'", name)
            entry.closer()
        log.info("MJPEGServer.stop: '%s' stopped", name)
        return True

    def stop_all(self) -> None:
        """Stop all running MJPEG servers."""
        with self._lock:
            names = list(self._servers.keys())
        log.info("MJPEGServer.stop_all: stopping %d servers: %s", len(names), names)
        for name in names:
            log.debug("MJPEGServer.stop_all: stopping '%s'", name)
            self.stop(name)

    def get_url(self, name: str) -> str | None:
        """Return the server URL if running, otherwise None."""
        log.debug("MJPEGServer.get_url: called for name=%s", name)
        with self._lock:
            entry = self._servers.get(name)
        url = f"http://localhost:{entry.port}/" if entry else None
        log.debug("MJPEGServer.get_url: name=%s -> %s", name, url)
        return url

    def is_running(self, name: str) -> bool:
        """Return True if a server is active for this printer."""
        log.debug("MJPEGServer.is_running: called for name=%s", name)
        with self._lock:
            result = name in self._servers
        log.debug("MJPEGServer.is_running: name=%s -> %s", name, result)
        return result

    def get_active_streams(self) -> dict[str, dict]:
        """Return {printer_name: {port, url}} for all active MJPEG streams."""
        with self._lock:
            return {
                name: {
                    "port": entry.port,
                    "url": f"http://localhost:{entry.port}/",
                }
                for name, entry in self._servers.items()
            }


# Module-level singleton
mjpeg_server = MJPEGServer()
