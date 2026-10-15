"""
mjpeg_server.py — per-printer local stream server: the live camera page with its HUD.

The video is the bambu-mse-stream camera relay's fragmented MP4 (camera/mse_relay.py runs it as a
child process), played in the page with Media Source Extensions. Every Bambu model streams the same
way: RTSPS printers (X1, H2D, P2S) pass their H.264 through untouched, A1/P1 JPEG stills are encoded
once as intra H.264 frames. The module keeps its MJPEG-era name; it no longer serves MJPEG.

Routes: / (page), /open (single-tab portal), /stream.mp4 and /stats (proxied to the relay),
/snapshot (the relay's latest frame as JPEG), /status (printer telemetry), /thumbnail and /layout
(plate images), /job_state, /annotated and /factors_radar (job monitor results).

One server instance per active printer stream. Started on demand, stopped on request
or MCP shutdown.

Port allocation: drawn from the shared ephemeral port pool (port_pool.py).
Pool default range: 49152–49251 (IANA RFC 6335 Dynamic/Private range).
URL format: http://localhost:{port}/
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from html import escape as _escape_html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

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
#stream{max-width:100%;max-height:100vh;display:block;background:#000}
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
#preview-wrap{left:16px}
#hp-sec-anomaly{overflow:hidden;transition:max-height .35s ease}
#hp-sec-anomaly img{display:block;width:100%;aspect-ratio:1/1;border-radius:4px;opacity:.92;margin-top:4px}
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
  display:none;flex-direction:column;align-items:stretch;gap:3px;text-align:center;overflow:hidden}
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
<video id="stream" autoplay muted playsinline></video>
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
    <div class="hdr" onclick="hudToggle(this,'hp-sec-metrics')">Metrics<span class="hdr-chev open">▲</span></div>
    <div class="hdr-section" id="hp-sec-metrics">
      <div class="hp-metric-row"><span class="hp-lbl">Hot px</span><span id="hp-hot" class="hp-val">—</span></div>
      <div class="hp-metric-row"><span class="hp-lbl">Strand</span><span id="hp-strand" class="hp-val">—</span></div>
      <div class="hp-metric-row"><span class="hp-lbl">Diff</span><span id="hp-diff" class="hp-val">—</span></div>
    </div>
    <div class="hdr" onclick="hudToggle(this,'hp-sec-trends')">Trends<span class="hdr-chev open">▲</span></div>
    <div class="hdr-section" id="hp-sec-trends">
      <div class="hp-spark-row"><span class="hp-slbl">Success</span><canvas id="hp-sp-canvas"></canvas></div>
      <div class="hp-spark-row"><span class="hp-slbl">Confidence</span><canvas id="hp-dc-canvas"></canvas></div>
      <div class="hp-spark-row"><span class="hp-slbl">Nozzle °C</span><canvas id="hp-nz-canvas" class="hp-spark-mini"></canvas></div>
      <div class="hp-spark-row"><span class="hp-slbl">Bed °C</span><canvas id="hp-bd-canvas" class="hp-spark-mini"></canvas></div>
      <div id="hp-trend-status" style="font-size:11px;color:#888;padding:4px 2px 2px;line-height:1.6"></div>
    </div>
    <div id="hp-radar-section" style="display:none">
      <div class="hdr" onclick="hpWideToggle(this)">Failure Drivers<span class="hdr-chev open">▲</span></div>
      <div id="hp-sec-anomaly">
        <img id="hp-radar-img" src="" alt="Failure factor radar">
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
<div id="preview-wrap" class="img-panel hidden">
  <div class="img-panel-hdr" onclick="imgPanelHdrToggle(this,'preview-body')">PLATE PREVIEW<span class="hdr-chev open">▲</span></div>
  <div class="img-panel-body" id="preview-body" style="display:flex;gap:6px;align-items:flex-start">
    <img id="thumb-img" src="" alt="Isometric view" onclick="imgPanelToggle(this.closest('.img-panel'))">
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
function escAttr(v){
  return String(v==null?'':v).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
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
  if(sn&&sn!=='Printing'&&sn!=='Completed'){
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
  (d.nozzles||[]).slice().sort(function(a,b){return b.id-a.id;}).forEach(function(n){
    var lbl=(d.nozzles.length>1)?((n.id===0?'Right':'Left')+' Nozzle'):'Nozzle';
    var nt=Number(n.temp)||0,ng=Number(n.target)||0;
    var cls=tCls(nt,ng);
    nEl.innerHTML+='<div class="row"><span class="lbl">'+lbl+'</span><span class="'+cls+htg(nt,ng)+'">'+fmtT(nt,ng)+'</span></div>';
  });

  // E4 — active filament swatch
  var fRow=document.getElementById('filament-row');
  if(d.active_filament&&active){
    var f=d.active_filament;
    var fc=f.color||'#888';
    if(fc&&!fc.startsWith('#')&&/^([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/.test(fc)) fc='#'+fc;
    // The colour goes into a style attribute: only # plus 6 or 8 hex digits gets through, anything else is the grey default.
    if(!/^#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/.test(fc)) fc='#888';
    var fh='<span style="display:inline-block;width:10px;height:10px;background:'+fc+
           ';border-radius:2px;vertical-align:middle;margin-right:4px;border:1px solid rgba(255,255,255,.2)"></span>';
    fh+='<span class="val">'+escAttr(f.type||'\u2014')+'</span>';
    var fpct=Number(f.remaining_pct)||0;
    if(fpct>0) fh+=' <span class="dim">'+fpct+'%</span>';
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
  var fanData=[['Part',Number(d.part_cooling_pct)||0],['Aux',Number(d.aux_pct)||0],['Exhaust',Number(d.exhaust_pct)||0],['Circulation',Number(d.circulation_pct)||0],['Heatbreak',Number(d.heatbreak_pct)||0]].filter(function(f){return f[1]>0;});
  if(fanData.length>0){
    var fEl=document.getElementById('fans');
    fEl.innerHTML='';
    fanData.forEach(function(f){fEl.innerHTML+='<div class="row"><span class="lbl">'+f[0]+'</span><span class="val">'+f[1]+'%</span></div>';});
    fanSec.classList.remove('hidden');
  } else fanSec.classList.add('hidden');

  // E7 — AMS humidity (shown only when elevated)
  var hmEl=document.getElementById('humidity-row');
  var hIdx=Number(d.ams_humidity_index)||0;
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
      var lbl='\u26a0 '+escAttr(e.code||'error');
      if(e.url) html+='<a class="error-link warn" href="'+escAttr(e.url)+'" title="'+escAttr(e.msg)+'" onclick="window.open(this.href,\\'hms_popup\\',\\'width=600,height=400,menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes\\');return false;">'+lbl+'</a><br>';
      else html+='<span class="warn" title="'+escAttr(e.msg)+'">'+lbl+'</span><br>';
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
  var pw=document.getElementById('preview-wrap');
  // An image the server no longer has is cleared, so a panel never keeps an older plate.
  function _plateImg(url,id){
    var el=document.getElementById(id);
    function show(ok){
      if(ok){el.src=url;el.style.display='';}
      else{el.removeAttribute('src');el.style.display='none';}
      return ok;
    }
    return fetch(url).then(function(r){
      return show(r.ok&&(r.headers.get('Content-Type')||'').indexOf('image')>=0);
    }).catch(function(){return show(false);});
  }
  Promise.all([_plateImg('/thumbnail?t='+t,'thumb-img'),_plateImg('/layout?t='+t,'layout-img')])
    .then(function(ok){if(ok[0]||ok[1])pw.classList.remove('hidden');else pw.classList.add('hidden');});
  fetch('/factors_radar?t='+t).then(function(r){
    if(r.ok&&r.status!==204&&r.headers.get('Content-Type')&&r.headers.get('Content-Type').indexOf('image')>=0){
      document.getElementById('hp-radar-img').src='/factors_radar?t='+t;
      document.getElementById('hp-radar-section').style.display='';
    } else { document.getElementById('hp-radar-section').style.display='none'; }
  }).catch(function(){document.getElementById('hp-radar-section').style.display='none';});

}
function poll(){_hpPoll();}
refreshImages();
setInterval(refreshImages,15000);
// Health panel state
var _hpScores=[];var _hpDcScores=[];var _hpNozzles=[];var _hpBeds=[];var _hpCurrentJob=null;
var _hpPollInterval=8000;var _hpLastPoll=0;
var _hpUserOverride=false;var _hpPrevState=null;
function hpToggle(hdr){
  var body=document.getElementById('hp-body');
  var chev=hdr.querySelector('.hp-chev');
  if(body.classList.contains('collapsed')){body.classList.remove('collapsed');chev.classList.add('open');}
  else{body.classList.add('collapsed');chev.classList.remove('open');}
  _hpUserOverride=true;
}
function hpWideToggle(hdr){
  var panel=document.getElementById('health-panel');
  var anomaly=document.getElementById('hp-sec-anomaly');
  var chev=hdr.querySelector('.hdr-chev');
  if(panel.classList.contains('hp-wide')){
    panel.classList.remove('hp-wide');chev.classList.remove('open');
    anomaly.style.maxHeight='';
  } else {
    panel.classList.add('hp-wide');chev.classList.add('open');
    requestAnimationFrame(function(){
      var hdrBottom=hdr.getBoundingClientRect().bottom;
      var available=window.innerHeight-hdrBottom-14;
      anomaly.style.maxHeight=Math.max(80,available)+'px';
    });
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
  var sp=d.success_probability;
  _hpScores.push(sp!==null&&sp!==undefined?sp:0);
  hpUpdateSparkline('hp-sp-canvas',_hpScores,'#60d080',0,1,(sp!==null&&sp!==undefined?Math.round(sp*100)+'%':'—'));
  _hpDcScores.push(dc!==null&&dc!==undefined?dc:0);
  hpUpdateSparkline('hp-dc-canvas',_hpDcScores,'#80a0ff',0,1,(dc!==null&&dc!==undefined?Math.round(dc*100)+'%':'—'),true);
  // Status text row in Trends section
  var statusEl=document.getElementById('hp-trend-status');
  if(statusEl){
    var gs=d.gcode_state||'';
    var stateColor=gs==='RUNNING'?'#40d0c0':gs==='PAUSE'?'#f0c040':gs==='FAILED'?'#ff5050':'#888';
    var stateStr='<span style="color:'+stateColor+'">'+escAttr(gs)+'</span>';
    var lyr=Number(d.layer),lyrTotal=Number(d.total_layers);
    var layerStr=(lyr&&lyrTotal)?(' &nbsp;Layer: '+lyr+'/'+lyrTotal):'';
    var humIdx=Number(d.ams_humidity);var humStr='';
    if(humIdx>0){
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
    // Reset history when a new print job starts
    var sub=d.subtask_name||'';
    if(sub&&sub!==_hpCurrentJob){_hpCurrentJob=sub;_hpScores=[];_hpDcScores=[];_hpNozzles=[];_hpBeds=[];}
    // Update temp sparklines (fixed scales: nozzle 0-310°C, bed 0-120°C)
    var nozzle=d.nozzles&&d.nozzles.length?d.nozzles[0].temp:0;
    var bed=d.bed_temp||0;
    _hpNozzles.push(nozzle);
    _hpBeds.push(bed);
    hpUpdateSparkline('hp-nz-canvas',_hpNozzles,'#ff9040',0,310,Math.round(nozzle)+'°');
    hpUpdateSparkline('hp-bd-canvas',_hpBeds,'#80a0ff',0,120,Math.round(bed)+'°');
    hpPollJobState();
  }).catch(function(){});}
poll();setInterval(poll,2000);
// Live video: the camera relay's fragmented MP4 (/stream.mp4) played through Media Source
// Extensions, ported from bambu-mse-stream's index.html. RTSPS printers (H2D, X1, P2S) send their
// own H.264, passed through untouched; A1/P1 stills arrive as one intra H.264 frame each, every
// 2 to 3 s. The relay ends the response when its camera session resets; the page reconnects.
(function(){
  var video=document.getElementById('stream');
  var MS=window.ManagedMediaSource||window.MediaSource;
  var hasRVFC=typeof HTMLVideoElement!=='undefined'&&'requestVideoFrameCallback' in HTMLVideoElement.prototype;
  // live edge per camera family: 30 fps can sit 0.5 s behind it, a still stream holds each frame 2-3 s
  var EDGE={rtsps:{target:0.5,max:1.5},jpeg:{target:2.5,max:8}};
  // a TCP path that goes quiet looks like a live stream; reconnect when no bytes come for this long
  var STALL_MS={rtsps:10000,jpeg:20000};
  var camera='rtsps',serverRate=0,ctrl=null,ms=null,sb=null,pending=[],frames=[],lastQ=null;
  var stopped=false,gen=0,lastByteAt=0,fpsHist=[];
  if(hasRVFC){var tick=function(now){frames.push(now);video.requestVideoFrameCallback(tick);};video.requestVideoFrameCallback(tick);}
  function ahead(){var b=video.buffered;return b&&b.length?b.end(b.length-1)-video.currentTime:null;}
  function drawFps(f){
    var box=document.getElementById('fps');
    if(!(f>0)){box.style.display='none';return;}
    box.style.display='flex';
    // judged against the rate the relay receives from the printer, never a fixed number: the H2D
    // sends about 20 fps idle and 30 while printing, an A1 about 0.5
    var ref=serverRate||(camera==='jpeg'?0.5:30);
    var cls=f>=ref*.8?'fps-hi':f>=ref*.4?'fps-mid':'fps-lo';
    var num=document.getElementById('fps-num');
    num.textContent=f<2?f.toFixed(2):Math.round(f);
    num.className=cls;
    var cv=document.getElementById('fps-chart');
    if(!cv||fpsHist.length<2)return;
    var c=cv.getContext('2d');
    c.clearRect(0,0,cv.width,cv.height);
    var maxV=Math.max.apply(null,fpsHist.concat([ref]));
    c.strokeStyle=cls==='fps-hi'?'#39ff6e':cls==='fps-mid'?'#f5a623':'#ff4444';
    c.lineWidth=1.5;
    c.beginPath();
    var inLine=false;
    for(var i=0;i<fpsHist.length;i++){
      var x=i/(fpsHist.length-1)*cv.width,v=fpsHist[i];
      if(!(v>0)){inLine=false;continue;}
      var y=cv.height-(v/maxV)*(cv.height-2)+1;
      if(!inLine){c.moveTo(x,y);inLine=true;}else c.lineTo(x,y);
    }
    c.stroke();
  }
  setInterval(function(){
    var now=performance.now(),win=camera==='jpeg'?10000:2000,f=0;
    frames=frames.filter(function(t){return now-t<=win;});
    // frames per second across the frames seen, not frames per window: an A1's 3 or 4 stills in
    // 10 s would read 0.3 against its 0.45 and show amber while healthy. Zero once the last frame
    // is over three gaps (and at least 1.5 s) old.
    if(hasRVFC&&frames.length>1){
      var n=frames.length,gap=(frames[n-1]-frames[0])/(n-1);
      f=gap>0&&now-frames[n-1]<=Math.max(3*gap,1500)?1000/gap:0;
    }
    else if(video.getVideoPlaybackQuality){
      var q=video.getVideoPlaybackQuality();
      f=lastQ?q.totalVideoFrames-lastQ.totalVideoFrames:0;
      lastQ=q;
    }
    if(!lastByteAt)f=0;
    fpsHist.push(f);if(fpsHist.length>60)fpsHist.shift();
    drawFps(f);
    var lat=ahead(),edge=EDGE[camera]||EDGE.rtsps;
    if(lat!=null&&lat>edge.max)video.currentTime=video.buffered.end(video.buffered.length-1)-edge.target;
    if(video.paused&&!video.ended)video.play().catch(function(){});
    if(lastByteAt&&now-lastByteAt>(STALL_MS[camera]||STALL_MS.rtsps)){lastByteAt=0;restart();}
  },1000);
  function pollStats(){
    return fetch('/stats',{cache:'no-store'}).then(function(r){return r.json();}).then(function(s){
      camera=s.camera||camera;serverRate=s.packets_per_s||0;return s;});
  }
  setInterval(function(){pollStats().catch(function(){});},2000);
  function appendNext(){
    if(!sb||sb.updating||!pending.length)return;
    var chunk=pending.shift();
    try{sb.appendBuffer(chunk);}catch(e){restart();}
  }
  function trimBuffer(){
    if(!sb||sb.updating||!video.buffered.length)return;
    var s=video.buffered.start(0);
    if(video.currentTime-s>20){try{sb.remove(s,video.currentTime-10);}catch(e){}}
  }
  function retry(g,ms_){setTimeout(function(){if(g===gen)restart();},ms_);}
  function start(){
    var g=++gen;stopped=false;lastByteAt=0;
    pollStats().then(function(s){
      if(g!==gen)return;
      if(!s.codec){retry(g,2000);return;}   // the relay has no camera session yet
      var mime='video/mp4; codecs="'+s.codec+'"';
      if(!MS||!MS.isTypeSupported(mime))return;   // this browser cannot play the stream
      ms=new MS();
      if(window.ManagedMediaSource)video.disableRemotePlayback=true;
      var src=URL.createObjectURL(ms);
      video.src=src;
      ms.addEventListener('sourceopen',function(){
        URL.revokeObjectURL(src);   // the element holds the MediaSource; the URL entry is not needed
        if(g!==gen)return;
        sb=ms.addSourceBuffer(mime);
        sb.mode='segments';
        var seeded=false;
        sb.addEventListener('updateend',function(){
          // start on the first buffered frame at once, not on the next 1 s tick
          if(!seeded&&video.buffered.length){
            seeded=true;
            var edge=EDGE[camera]||EDGE.rtsps,end=video.buffered.end(video.buffered.length-1);
            video.currentTime=Math.max(video.buffered.start(0),end-edge.target);
          }
          trimBuffer();appendNext();
        });
        ctrl=new AbortController();
        fetch('/stream.mp4?cb='+Date.now(),{cache:'no-store',signal:ctrl.signal}).then(function(res){
          if(g!==gen)return;
          if(!res.ok||!res.body)throw new Error('http '+res.status);
          lastByteAt=performance.now();
          var reader=res.body.getReader();
          function pump(){
            return reader.read().then(function(r){
              if(g!==gen)return;
              if(r.done)throw new Error('stream ended');
              lastByteAt=performance.now();
              pending.push(r.value);appendNext();
              return pump();
            });
          }
          return pump();
        }).catch(function(){if(g===gen&&!stopped)retry(g,1000);});
      },{once:true});
    }).catch(function(){if(g===gen)retry(g,2000);});
  }
  function stop(){
    stopped=true;
    try{if(ctrl)ctrl.abort();}catch(e){}
    pending=[];frames=[];
    try{if(ms&&ms.readyState==='open')ms.endOfStream();}catch(e){}
    sb=null;ms=null;
    video.removeAttribute('src');video.load();
  }
  function restart(){stop();start();}
  video.addEventListener('error',function(){
    if(!video.error)return;   // emptied by our own restart, not a failure
    retry(gen,1000);
  });
  start();
})();
</script>
</body>
</html>
"""


# Seconds with no /stream.mp4 client before an idle-stop server shuts down (RTSPS printers only)
_IDLE_STOP_S = 10.0
# Hop-by-hop headers a proxy never forwards (RFC 9110 7.6.1)
_HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade",
                "proxy-authenticate", "proxy-authorization", "content-length"}


def _js_str(value: str) -> str:
    """``value`` as a single-quoted JavaScript string literal that is also safe inside <script>."""
    out = []
    for ch in value:
        if ch in "\\'":
            out.append("\\" + ch)
        elif ch == "<" or ch < " " or ch in "  ":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "'" + "".join(out) + "'"


class _MJPEGHTTPServer(ThreadingHTTPServer):
    """One printer's stream server: the HUD page, its data routes, and the camera relay's routes.

    ``relay`` is the printer's camera.mse_relay.RelayProcess, or any object with its ``get(path)``,
    ``open_stream()`` and ``snapshot()``. /stream.mp4 and /stats are proxied to it, so the page has
    one origin.
    """

    daemon_threads = True

    def __init__(self, addr, handler_class, relay=None,
                 status_fn: Callable[[], dict] | None = None,
                 thumbnail_fn: Callable[[], bytes | None] | None = None,
                 layout_fn: Callable[[], bytes | None] | None = None,
                 printer_name: str = "",
                 on_idle: Callable[[], None] | None = None):
        super().__init__(addr, handler_class)
        log.debug("_MJPEGHTTPServer.__init__: starting on port %s", addr[1])
        self.relay = relay
        self.status_fn = status_fn
        self.thumbnail_fn = thumbnail_fn
        self.layout_fn = layout_fn
        self.printer_name = printer_name
        self._running = True
        # Idle stop: armed only after a /stream.mp4 client has come and gone, as before.
        self.on_idle = on_idle
        self._clients_lock = threading.Lock()
        self._stream_clients = 0
        self._idle_timer: threading.Timer | None = None

    def stream_client_added(self) -> None:
        with self._clients_lock:
            self._stream_clients += 1
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None

    def stream_client_removed(self) -> None:
        with self._clients_lock:
            self._stream_clients -= 1
            if self._stream_clients > 0 or self.on_idle is None or not self._running:
                return
            log.debug("stream server: no clients left, stopping in %.0f s unless one returns", _IDLE_STOP_S)
            self._idle_timer = threading.Timer(_IDLE_STOP_S, self._idle_check)
            self._idle_timer.daemon = True
            self._idle_timer.start()

    def _idle_check(self) -> None:
        with self._clients_lock:
            self._idle_timer = None
            if self._stream_clients > 0 or not self._running:
                return
        log.info("stream server: idle for %.0f s, stopping %s", _IDLE_STOP_S, self.printer_name)
        self.on_idle()


class _StreamHandler(BaseHTTPRequestHandler):
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
        elif path in ("/", "/index.html"):
            self._serve_html()
        elif path == "/snapshot":
            self._serve_snapshot()
        elif path == "/job_state":
            self._serve_job_state()
        elif path == "/open":
            self._serve_open()
        elif path == "/stream.mp4":
            self._serve_stream()
        elif path == "/stats":
            self._serve_stats()
        else:
            self._send_bytes(404, "text/plain", b"not found")

    def _send_bytes(self, code: int, ctype: str, body: bytes, no_store: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if no_store:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self):
        log.debug("_serve_html: serving HTML to %s", self.client_address)
        title = f"Bambu Cam — {_escape_html(self.server.printer_name)}" if self.server.printer_name else "Bambu Cam"
        body = _HTML_PAGE.replace("<title>Bambu Cam</title>", f"<title>{title}</title>", 1).encode()
        self._send_bytes(200, "text/html; charset=utf-8", body)
        log.debug("_serve_html: done, %d bytes sent to %s", len(body), self.client_address)

    def _serve_status(self):
        log.debug("_serve_status: called from %s", self.client_address)
        data = {}
        if self.server.status_fn:
            try:
                data = self.server.status_fn()
            except Exception as e:
                log.error("_serve_status: status_fn raised: %s", e, exc_info=True)
        self._send_bytes(200, "application/json", json.dumps(data).encode())

    def _serve_stats(self):
        """The relay's /stats: camera family, codec, input rates, clients. 503 when the relay is down."""
        relay = self.server.relay
        try:
            status, ctype, body = relay.get("/stats", timeout=5.0) if relay else (503, "text/plain", b"")
        except OSError as e:
            log.debug("_serve_stats: relay unreachable: %s", e)
            status, ctype, body = 503, "text/plain", b"camera relay unreachable"
        self._send_bytes(status, ctype or "application/json", body, no_store=True)

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
        self._send_bytes(200, "image/png", data, no_store=True)

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
        """Return the relay's latest frame as image/jpeg; 503 while it has none."""
        log.debug("_serve_snapshot: requested by %s", self.client_address)
        relay = self.server.relay
        jpeg = relay.snapshot() if relay else None
        if not jpeg:
            log.warning("_serve_snapshot: no frame available for %s", self.client_address)
            self._send_bytes(503, "text/plain", b"no frame yet")
            return
        log.debug("_serve_snapshot: serving %d bytes to %s", len(jpeg), self.client_address)
        self._send_bytes(200, "image/jpeg", jpeg, no_store=True)

    def _serve_open(self):
        """Serve a portal page that opens the stream in a named browser window/tab.

        Called by view_stream() via webbrowser.open('/open?name=bambu-{printer}').
        The page calls window.open('/', target) — browsers reuse an existing window
        with that name on subsequent calls, achieving single-tab-per-printer behavior.
        """
        log.debug("_serve_open: serving portal to %s", self.client_address)
        stream_js = _js_str("/")
        html = (
            "<!doctype html><html><head><title>Opening Bambu Cam…</title></head>"
            "<body><script>"
            "var n=new URLSearchParams(location.search).get('name')||'bambu-cam';"
            f"var w=window.open(location.origin+{stream_js},n);"
            "if(w){w.focus();setTimeout(function(){window.close();},150);}"
            f"else{{location.replace({stream_js});}}"
            "</script><p>Opening stream…</p></body></html>"
        ).encode()
        self._send_bytes(200, "text/html; charset=utf-8", html, no_store=True)

    def _serve_stream(self):
        """Proxy the relay's live fragmented MP4 to this client until either side ends it."""
        ua = self.headers.get("User-Agent", "unknown")
        relay = self.server.relay
        if relay is None:
            self._send_bytes(503, "text/plain", b"no camera relay")
            return
        try:
            conn, resp = relay.open_stream()
        except OSError as e:
            log.warning("_serve_stream: relay unreachable: %s", e)
            self._send_bytes(503, "text/plain", b"camera relay unreachable")
            return
        log.info("stream_connect: client=%s ua=%s", self.client_address[0], ua)
        self.server.stream_client_added()
        try:
            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() not in _HOP_HEADERS:
                    self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()
            while self.server._running:
                chunk = resp.read1(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            log.info("stream_disconnect: client=%s reason=%s", self.client_address[0], type(e).__name__)
        else:
            log.info("stream_disconnect: client=%s reason=normal", self.client_address[0])
        finally:
            conn.close()
            self.close_connection = True
            self.server.stream_client_removed()

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
    Manages the per-printer local stream servers (HUD page + camera relay proxy).

    Each printer gets its own server instance on a unique port.
    """

    def __init__(self):
        self._servers: dict[str, _ServerEntry] = {}
        self._lock = threading.Lock()

    def start(self, name: str, relay, port: int | None = None,
              status_fn: Callable[[], dict] | None = None,
              thumbnail_fn: Callable[[], bytes | None] | None = None,
              layout_fn: Callable[[], bytes | None] | None = None,
              closer: Callable[[], None] | None = None,
              idle_stop: bool = False) -> str:
        """
        Start the local stream server for the named printer.

        If a server is already running for this name, returns the existing URL.
        Returns the server URL: http://localhost:{port}/

        Port is drawn from the shared ephemeral port pool (IANA RFC 6335 range
        49152–49251 by default).  Caller may pass a preferred port; the pool will
        try it first and rotate to the next available port if it is taken.

        relay       — the printer's running camera.mse_relay.RelayProcess; /stream.mp4,
                      /stats and /snapshot are served from it
        status_fn   — returns live printer state dict for /status (polled every 2s)
        thumbnail_fn — returns PNG bytes of the isometric 3D thumbnail for /thumbnail
        layout_fn   — returns PNG bytes of the annotated top-down plate layout for /layout
        Both image endpoints are polled every 15s and hidden when unavailable.
        closer      — called once when the server stops (stops the relay)
        idle_stop   — stop the server 10 s after its last /stream.mp4 client leaves
        """
        with self._lock:
            log.debug("MJPEGServer.start: name=%s port=%s", name, port if port is not None else "auto")
            if name in self._servers:
                existing_port = self._servers[name].port
                log.info("MJPEGServer.start: '%s' already running on port %d", name, existing_port)
                if closer:
                    closer()   # a racing start's relay is not used; stop it rather than leak it
                return f"http://localhost:{existing_port}/"
            from port_pool import port_pool as _pp
            allocated_port = _pp.allocate(preferred=port)
            log.info("MJPEGServer.start: starting '%s' on port %d", name, allocated_port)
            server = _MJPEGHTTPServer(
                ("", allocated_port), _StreamHandler, relay,
                status_fn=status_fn, thumbnail_fn=thumbnail_fn, layout_fn=layout_fn,
                printer_name=name,
                on_idle=(lambda n=name: self.stop(n)) if idle_stop else None,
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
        entry.server.server_close()   # free the listening socket before the port goes back to the pool
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
