#!/usr/bin/env bash
# Start the virtual desktop stack, then run VoxPilot's web command UI.
set -euo pipefail

SCREEN_RES="${SCREEN_RES:-1280x800x24}"

echo "[entrypoint] starting Xvfb ($SCREEN_RES)"
Xvfb :99 -screen 0 "$SCREEN_RES" -ac +extension RANDR +extension GLX >/tmp/xvfb.log 2>&1 &
# Wait for the display to be ready.
for _ in $(seq 1 40); do
  if xdpyinfo -display :99 >/dev/null 2>&1; then break; fi
  sleep 0.25
done

echo "[entrypoint] starting window manager (fluxbox)"
fluxbox >/tmp/fluxbox.log 2>&1 &

echo "[entrypoint] starting x11vnc + noVNC (desktop on :6080)"
x11vnc -display :99 -forever -shared -nopw -rfbport 5900 -bg -quiet -o /tmp/x11vnc.log
websockify --web=/usr/share/novnc 6080 localhost:5900 >/tmp/novnc.log 2>&1 &

echo "[entrypoint] launching Firefox for the agent to drive"
firefox-esr --no-remote about:blank >/tmp/firefox.log 2>&1 &

sleep 1
echo "[entrypoint] starting VoxPilot web UI on :5000"
cd /app
exec python -m voxpilot --serve --port 5000 --no-tts
