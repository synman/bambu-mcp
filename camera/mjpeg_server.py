"""
mjpeg_server.py — Local HTTP MJPEG server for Bambu Lab camera streams.

Serves Motion JPEG (multipart/x-mixed-replace) over HTTP using Python stdlib only.
One server instance per active printer stream. Started on demand, stopped on request
or MCP shutdown.

MJPEG is a standard HTTP streaming format where each frame is a complete JPEG image
delivered as a multipart HTTP part. Any modern browser natively displays it as live
video when the URL is opened directly.

Port allocation: starts at BASE_PORT (8090), increments by 1 per additional stream.
URL format: http://localhost:{port}/
"""

from __future__ import annotations

import collections
import json
import logging
import socket
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Iterator

log = logging.getLogger(__name__)

BASE_PORT = 8090

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
#hud{position:fixed;top:16px;left:16px;background:rgba(0,0,0,.75);color:#ddd;
  font:13px/1.6 'Courier New',monospace;padding:10px 14px;border-radius:8px;
  pointer-events:none;min-width:230px;max-width:310px;
  border:1px solid rgba(255,255,255,.08)}
.img-panel{position:fixed;bottom:16px;pointer-events:none;
  background:rgba(0,0,0,.6);border-radius:8px;padding:6px;
  border:1px solid rgba(255,255,255,.08)}
.img-panel.hidden{display:none}
.img-panel img{display:block;max-width:190px;max-height:190px;border-radius:4px;opacity:.92}
#thumb-wrap{left:16px}
#layout-wrap{right:16px}
.hdr{font-size:10px;color:#666;text-transform:uppercase;letter-spacing:.08em;
  border-bottom:1px solid rgba(255,255,255,.1);margin-bottom:3px;padding-bottom:2px;margin-top:6px}
.hdr:first-child{margin-top:0}
.row{display:flex;justify-content:space-between;gap:12px}
.lbl{color:#888}
.val{color:#ddd}
.hot{color:#ff9040}
.ok{color:#60d080}
.dim{color:#555}
.warn{color:#ff5050}
.sep{border-top:1px solid rgba(255,255,255,.08);margin:6px 0}
#badge{display:inline-block;font-size:11px;font-weight:700;padding:1px 7px;
  border-radius:3px;margin-bottom:5px;letter-spacing:.04em}
.bRUNNING{background:#1a5c2a;color:#60d080}
.bPREPARE{background:#1a2e5c;color:#80a0ff}
.bPAUSE{background:#5c4a1a;color:#ffcc40}
.bFAILED{background:#5c1a1a;color:#ff6060}
.bFINISH{background:#1a4a5c;color:#60d0e0}
.bIDLE,.bINIT{background:#222;color:#777}
.hidden{display:none}
#fps{position:fixed;top:14px;right:16px;color:rgba(255,255,255,.55);
  font:700 15px/1 'Courier New',monospace;pointer-events:none;letter-spacing:.04em;
  text-shadow:0 1px 3px rgba(0,0,0,.9)}
@keyframes pulse{0%{opacity:1}50%{opacity:.42}100%{opacity:1}}
.heating{animation:pulse 1.5s ease-in-out infinite}
#badge-row{display:flex;align-items:center;gap:6px;margin-bottom:5px}
#badge{margin-bottom:0}
#speed-badge{display:none;font-size:10px;font-weight:700;padding:1px 6px;
  border-radius:3px;letter-spacing:.03em}
.sQ{background:#2a2a2a;color:#666}.sS{background:#1a3a1a;color:#50b060}
.sSP{background:#4a2e10;color:#e0902a}.sL{background:#4a1010;color:#e05050}
#progress-bar{height:3px;background:rgba(255,255,255,.08);border-radius:2px;margin:3px 0 4px}
#progress-fill{height:100%;border-radius:2px;transition:width .6s}
#filament-row{margin:2px 0 1px;font-size:12px}
#door-warn{font-size:11px;font-weight:700;margin-top:2px;padding:1px 0}
#humidity-row{font-size:11px;margin-top:3px}
</style>
</head>
<body>
<img id="stream">
<div id="fps"></div>
<div id="hud">
  <div id="badge-row">
    <div id="badge" class="bIDLE">IDLE</div>
    <div id="speed-badge"></div>
  </div>
  <div id="subtask" class="dim" style="font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:275px;margin-bottom:2px"></div>
  <div class="row"><span class="lbl">Progress</span><span id="pct" class="val">\u2014</span></div>
  <div class="row"><span class="lbl">Layer</span><span id="layers" class="val">\u2014</span></div>
  <div id="progress-bar"><div id="progress-fill"></div></div>
  <div id="stage-row" class="row hidden"><span class="lbl">Stage</span><span id="stage" class="val" style="text-align:right;max-width:190px;font-size:11px">\u2014</span></div>
  <div id="time-row" class="row hidden"><span class="lbl">Elapsed</span><span id="elapsed" class="val">\u2014</span></div>
  <div id="remain-row" class="row hidden"><span class="lbl">Remain</span><span id="remain" class="val">\u2014</span></div>
  <div class="sep"></div>
  <div class="hdr">Temps</div>
  <div id="nozzles"></div>
  <div id="filament-row" style="display:none"></div>
  <div class="row"><span class="lbl">Bed</span><span id="bed" class="dim">\u2014</span></div>
  <div id="chamber-row" class="row hidden"><span class="lbl">Chamber</span><span id="chamber" class="dim">\u2014</span></div>
  <div id="door-warn" style="display:none"></div>
  <div id="sec-fans" class="hidden">
    <div class="sep"></div>
    <div class="hdr">Fans</div>
    <div id="fans"></div>
  </div>
  <div id="humidity-row" style="display:none"></div>
  <div class="sep"></div>
  <div class="row" style="font-size:11px">
    <span id="wifi" class="dim"></span>
    <span id="errors" class="warn hidden"></span>
  </div>
</div>
<div id="thumb-wrap" class="img-panel hidden">
  <img id="thumb-img" src="" alt="3D preview">
</div>
<div id="layout-wrap" class="img-panel hidden">
  <img id="layout-img" src="" alt="Plate layout">
</div>
<script>
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
  document.getElementById('pct').textContent=active?d.print_percentage+'%':'\u2014';

  var lEl=document.getElementById('layers');
  lEl.textContent=(active&&d.total_layers>0)?d.current_layer+' / '+d.total_layers:'\u2014';

  // E3 — progress bar
  var pfEl=document.getElementById('progress-fill');
  var stateColors={RUNNING:'#60d080',PREPARE:'#80a0ff',PAUSE:'#ffcc40',FAILED:'#ff6060',FINISH:'#60d0e0'};
  pfEl.style.width=(active?(d.print_percentage||0):0)+'%';
  pfEl.style.background=stateColors[s]||'#555';

  var sub=document.getElementById('subtask');
  sub.textContent=(active&&d.subtask_name)?d.subtask_name:'';

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
  if(d.elapsed_minutes>0){document.getElementById('elapsed').textContent=fmtM(d.elapsed_minutes);tRow.classList.remove('hidden');}
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
  if(hIdx>=3){
    var hc=hIdx>=4?'#ff5050':'#ffcc40';
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
  if(d.active_error_count>0){
    eEl.textContent='\u26a0 '+d.active_error_count+' error'+(d.active_error_count>1?'s':'');
    eEl.classList.remove('hidden');
  } else eEl.classList.add('hidden');
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
}
function poll(){fetch('/status').then(function(r){return r.json();}).then(function(d){
  update(d);
  var f=d.fps||0;
  document.getElementById('fps').textContent=f>0?f+' fps':'';
}).catch(function(){});}
poll();
setInterval(poll,2000);
refreshImages();
setInterval(refreshImages,15000);
// Fetch-based MJPEG parser: bypasses Safari's broken <img src="multipart"> loader.
// Reads the raw multipart stream via fetch(), parses Content-Length from each part
// header, extracts the JPEG bytes, and sets img.src to a blob URL. Works in all browsers.
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
                 layout_fn: Callable[[], bytes | None] | None = None):
        super().__init__(addr, handler_class)
        log.debug("_MJPEGHTTPServer.__init__: starting on port %s", addr[1])
        self.frame_factory = frame_factory
        self.status_fn = status_fn
        self.thumbnail_fn = thumbnail_fn
        self.layout_fn = layout_fn
        self._running = True
        # FPS tracking — rolling 10s window of frame timestamps; deduplicated by frame id
        self._fps_lock = threading.Lock()
        self._fps_times: collections.deque[float] = collections.deque()
        self._fps_last_frame_id: int = -1
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
        elif path in ("/", "/index.html"):
            self._serve_html()
        else:
            self._serve_stream()

    def _serve_html(self):
        log.debug("_serve_html: serving HTML to %s", self.client_address)
        body = _HTML_PAGE.encode()
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
        data["fps"] = round(fps_val, 1) if fps_val < 2 else round(fps_val)
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

    def _serve_stream(self):
        ua = self.headers.get("User-Agent", "unknown")
        log.debug("_serve_stream: starting for client %s", self.client_address)
        log.info("stream_connect: client=%s ua=%s", self.client_address[0], ua)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        try:
            for jpeg in self.server.frame_factory():
                if not self.server._running:
                    break
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
        except (BrokenPipeError, ConnectionResetError) as e:
            log.warning("_serve_stream: client %s disconnected: %s", self.client_address, e, exc_info=True)
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
              closer: Callable[[], None] | None = None) -> str:
        """
        Start a local MJPEG server for the named printer.

        If a server is already running for this name, returns the existing URL.
        Returns the server URL: http://localhost:{port}/
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
            allocated_port = port if port is not None else self._next_port()
            log.info("MJPEGServer.start: starting '%s' on port %d", name, allocated_port)
            server = _MJPEGHTTPServer(
                ("", allocated_port), _StreamHandler, frame_factory,
                status_fn=status_fn, thumbnail_fn=thumbnail_fn, layout_fn=layout_fn,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self._servers[name] = _ServerEntry(server=server, thread=thread, port=allocated_port, closer=closer)
        log.info("MJPEGServer.start: started server for %s at http://localhost:%d/", name, allocated_port)
        return f"http://localhost:{allocated_port}/"

    def stop(self, name: str) -> bool:
        """
        Stop the MJPEG server for the named printer.

        Returns True if a server was running and has been stopped.
        """
        log.info("MJPEGServer.stop: stopping '%s'", name)
        with self._lock:
            entry = self._servers.pop(name, None)
        if entry is None:
            log.debug("MJPEGServer.stop: '%s' not found", name)
            return False
        entry.server._running = False
        entry.server.shutdown()
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

    def _next_port(self) -> int:
        """Find the next available port starting at BASE_PORT."""
        log.debug("MJPEGServer._next_port: searching from BASE_PORT=%d", BASE_PORT)
        used = {e.port for e in self._servers.values()}
        port = BASE_PORT
        while True:
            if port not in used and _port_available(port):
                log.debug("MJPEGServer._next_port: allocated port=%d", port)
                return port
            port += 1


def _port_available(port: int) -> bool:
    log.debug("_port_available: checking port=%d", port)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("", port))
            log.debug("_port_available: port=%d is available", port)
            return True
        except OSError:
            log.debug("_port_available: port=%d is in use", port, exc_info=True)
            return False


# Module-level singleton
mjpeg_server = MJPEGServer()
