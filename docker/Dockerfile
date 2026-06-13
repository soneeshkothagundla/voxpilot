# VoxPilot in an isolated Linux desktop you watch/drive from your browser.
# The agent operates a virtual X display (Xvfb); x11vnc + noVNC stream it to the
# browser, and a small Flask UI (voxpilot --serve) takes typed commands. Your
# host machine is never touched.
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    PYTHONUNBUFFERED=1

# Virtual display + VNC/noVNC + a window manager + a browser for the agent to
# drive, plus the X libraries pyautogui/mss need at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
      xvfb x11vnc fluxbox novnc websockify xterm firefox-esr xclip \
      x11-utils x11-xserver-utils scrot procps curl ca-certificates \
      libportaudio2 libx11-6 libxext6 libxrandr2 libxtst6 libxi6 libxfixes3 \
      fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# Install VoxPilot + its deps, plus Flask for the --serve web UI.
RUN pip install --no-cache-dir . flask \
    && chmod +x docker/entrypoint.sh

# 5000 = command UI, 6080 = noVNC desktop view.
EXPOSE 5000 6080

CMD ["bash", "docker/entrypoint.sh"]
