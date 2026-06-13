# Run VoxPilot in a container (use your PC in parallel)

A computer-use agent drives one shared cursor/keyboard/screen, so on your normal
desktop you can't work *while* it acts. This runs VoxPilot in an **isolated Linux
desktop inside Docker** — it controls *that* screen, which you watch and drive from
your browser, while your real machine stays completely free.

Because there's no microphone in a container, you **type** commands instead of
speaking them.

```
┌─ your browser ──────────────────────────────┐
│  http://localhost:5000                       │
│  [ type a command ............ ] [Run]       │
│  ┌───────────── embedded desktop ─────────┐  │
│  │  (Firefox etc. — the agent drives this) │  │
│  └─────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘
        the container's screen, not yours
```

## 1. Install Docker Desktop (one time)

1. Download Docker Desktop for Windows: https://www.docker.com/products/docker-desktop/
2. Run the installer and keep **"Use WSL 2 instead of Hyper-V"** checked (this is
   the default and works on Windows 11 Home). It will install WSL 2 if needed.
3. **Reboot** when prompted, then launch Docker Desktop once and wait until it
   says *"Engine running"*.

Verify in a terminal:
```powershell
docker --version
docker compose version
```

## 2. Check your key

The container reads your Bedrock key from the existing `.env` (it is **not** baked
into the image). Make sure `C:\Users\sonee\voxpilot\.env` contains:
```
AWS_BEARER_TOKEN_BEDROCK=...your bedrock key...
AWS_REGION=us-east-1
```

## 3. Build and run

From the project folder:
```powershell
cd C:\Users\sonee\voxpilot
docker compose up --build
```
The first build takes a few minutes and the image is ~1.5–2 GB (Python, the X
virtual display, noVNC, and Firefox). Subsequent runs are instant.

## 4. Use it

- Open **http://localhost:5000** — a command box with the agent's desktop embedded
  below it. Type something like *"Open Firefox and search for the weather in
  Austin"* and press **Run**. Watch it work in the embedded view; the Activity
  panel shows what it's doing.
- Prefer a full-screen view of the agent's desktop? Open
  **http://localhost:6080/vnc.html**.
- Meanwhile, **keep using your own computer normally** — the agent is sandboxed.

## 5. Stop it

Press **Ctrl+C** in the terminal, then:
```powershell
docker compose down
```

## Notes & troubleshooting

- **Text, not voice** — containers have no mic, so you type commands here.
- **What it can touch** — only the container's desktop (Firefox is preinstalled).
  Your host files/screen are not visible to it unless you mount them.
- **Black desktop for a second** on first open — Xvfb/Firefox are still starting;
  give it a few seconds and refresh.
- **Port already in use** — change the left side of the `ports:` mappings in
  `docker-compose.yml` (e.g. `"5001:5000"`).
- **Auth / model errors in the Activity log** — confirm `.env` has a valid
  `AWS_BEARER_TOKEN_BEDROCK` and that `us.anthropic.claude-sonnet-4-6` is enabled
  for your account in `us-east-1` (`aws bedrock list-inference-profiles`).
- **Logs** — `docker compose logs -f`, or inside the container `/tmp/*.log`.
