"""Minimal web command UI for running VoxPilot headless (e.g. in a container).

Serves a page with a command box + live status/log and an embedded noVNC view, so
you can drive and watch the agent in a browser while it operates an isolated
desktop. This is the input path for environments with no microphone (Docker/VM):
you type a command instead of speaking it.

Flask is imported lazily and is only needed for this mode (the ``serve`` extra).
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>VoxPilot</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:'Segoe UI',system-ui,sans-serif; background:#0d0f14; color:#eef1f6;
         display:flex; flex-direction:column; height:100vh; }
  header { padding:12px 16px; background:#15171c; border-bottom:1px solid #262a33; }
  h1 { margin:0 0 8px; font-size:16px; font-weight:600; letter-spacing:.3px; }
  .bar { display:flex; gap:8px; align-items:center; }
  input { flex:1; padding:10px 14px; font-size:15px; border-radius:10px; border:1px solid #2c313c;
          background:#0f1115; color:#eef1f6; outline:none; }
  input:focus { border-color:#4c8dff; }
  button { padding:10px 16px; font-size:14px; font-weight:600; border:0; border-radius:10px;
           background:#4c8dff; color:#fff; cursor:pointer; }
  button.secondary { background:#2c313c; }
  button:disabled { opacity:.5; cursor:default; }
  .status { margin-top:8px; font-size:13px; color:#9aa3b2; }
  .dot { display:inline-block; width:8px; height:8px; border-radius:50%; background:#4c8dff; margin-right:6px; }
  main { flex:1; display:flex; min-height:0; }
  iframe { flex:1; border:0; background:#000; }
  aside { width:300px; border-left:1px solid #262a33; background:#0f1115; display:flex; flex-direction:column; }
  aside h2 { margin:0; padding:10px 14px; font-size:12px; text-transform:uppercase; color:#8a93a3;
             border-bottom:1px solid #262a33; }
  #log { flex:1; overflow:auto; padding:10px 14px; font:12px/1.5 ui-monospace,Consolas,monospace;
         color:#c7cedb; white-space:pre-wrap; }
</style>
</head>
<body>
<header>
  <h1>VoxPilot &mdash; type a command, watch it work</h1>
  <div class="bar">
    <input id="cmd" placeholder="e.g. Open Firefox and search for the weather" autofocus />
    <button id="run" onclick="run()">Run</button>
    <button class="secondary" onclick="abort()">Abort</button>
  </div>
  <div class="status"><span class="dot" id="dot"></span><span id="state">IDLE</span></div>
</header>
<main>
  <iframe src="http://__HOST__:__NOVNC_PORT__/vnc.html?autoconnect=1&resize=scale&reconnect=1"></iframe>
  <aside>
    <h2>Activity</h2>
    <div id="log"></div>
  </aside>
</main>
<script>
  const cmd=document.getElementById('cmd'), runBtn=document.getElementById('run');
  async function run(){
    const command=cmd.value.trim(); if(!command) return;
    runBtn.disabled=true;
    await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command})});
    cmd.value='';
  }
  async function abort(){ await fetch('/abort',{method:'POST'}); }
  cmd.addEventListener('keydown',e=>{ if(e.key==='Enter') run(); });
  async function poll(){
    try{
      const r=await fetch('/status'); const s=await r.json();
      document.getElementById('state').textContent=s.status+(s.busy?' (working)':'');
      document.getElementById('dot').style.background=s.busy?'#e8a01a':'#1ae87a';
      runBtn.disabled=s.busy;
      document.getElementById('log').textContent=s.log.join('\\n');
      document.getElementById('log').scrollTop=1e9;
    }catch(e){}
  }
  setInterval(poll,1000); poll();
</script>
</body>
</html>
"""


class _State:
    """Shared, lock-guarded UI state."""

    def __init__(self) -> None:
        self.busy = False
        self.status = "IDLE"
        self.last = ""
        self.log: deque[str] = deque(maxlen=300)
        self.lock = threading.Lock()


def serve(
    cfg: Any,
    loop: Any,
    feedback: Any,
    guard: Any,
    host: str = "0.0.0.0",
    port: int = 5000,
    novnc_port: int = 6080,
) -> None:
    """Run the Flask command server (blocks).

    Args:
        cfg: Loaded configuration (unused directly; kept for symmetry/future use).
        loop: The :class:`~voxpilot.agent.loop.AgentLoop`.
        feedback: Feedback object whose sinks are routed into the activity log.
        guard: Safety guard (used for abort/reset).
        host: Bind address.
        port: Command-UI port.
        novnc_port: Port the noVNC desktop is served on (for the embedded view).
    """
    from flask import Flask, jsonify, request

    state = _State()

    def add(msg: str) -> None:
        with state.lock:
            state.log.append(msg)

    feedback.message_sink = add
    feedback.status_sink = lambda s: setattr(state, "status", s)

    app = Flask(__name__)

    @app.get("/")
    def index():  # noqa: ANN202
        from flask import Response

        return Response(_page_with_host(novnc_port), mimetype="text/html")

    @app.get("/status")
    def status():  # noqa: ANN202
        with state.lock:
            return jsonify(
                busy=state.busy, status=state.status, last=state.last, log=list(state.log)
            )

    @app.post("/run")
    def run():  # noqa: ANN202
        data = request.get_json(silent=True) or {}
        command = str(data.get("command", "")).strip()
        if not command:
            return jsonify(ok=False, error="empty command"), 400
        if state.busy:
            return jsonify(ok=False, error="busy"), 409
        state.busy = True
        guard.reset()

        def work() -> None:
            add(f"> {command}")
            try:
                state.last = loop.run(command)
            except Exception as exc:  # noqa: BLE001 - surface errors to the UI
                state.last = f"Error: {exc}"
                add(state.last)
            finally:
                state.busy = False
                state.status = "IDLE"

        threading.Thread(target=work, daemon=True).start()
        return jsonify(ok=True)

    @app.post("/abort")
    def abort():  # noqa: ANN202
        guard.abort()
        add("[aborted]")
        return jsonify(ok=True)

    print(f"VoxPilot web UI: http://localhost:{port}  (desktop view embedded)", flush=True)
    app.run(host=host, port=port, threaded=True)


def _page_with_host(novnc_port: int) -> str:
    """Render the page with a runtime hostname for the embedded noVNC iframe."""
    # The iframe host is resolved client-side so it works regardless of how the
    # container is reached (localhost, LAN IP, etc.).
    script = (
        "<script>document.querySelector('iframe').src="
        f"location.protocol+'//'+location.hostname+':{novnc_port}"
        "/vnc.html?autoconnect=1&resize=scale&reconnect=1';</script>"
    )
    return _PAGE.replace(
        'src="http://__HOST__:__NOVNC_PORT__/vnc.html?autoconnect=1&resize=scale&reconnect=1"',
        'src="about:blank"',
    ).replace("</body>", script + "</body>")
