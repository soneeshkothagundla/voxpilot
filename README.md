# VoxPilot

**Hold a hotkey, speak a command, and let Claude drive your real screen.** VoxPilot
is a Python 3.12 desktop voice agent: press and hold a key, say what you want done,
release. It transcribes your speech **locally**, screenshots your display, sends the
screenshot plus your command to Claude's [computer-use](https://docs.anthropic.com/en/docs/build-with-claude/computer-use)
tool on **AWS Bedrock**, executes the mouse/keyboard actions Claude returns on your
**real** screen, and loops on fresh screenshots until the task is done — then idles.

---

## ⚠️ SAFETY & CONSENT — READ THIS FIRST

> **VoxPilot moves your real mouse and types on your real keyboard.** When it is
> running and you give it a command, it can click anything, type anything, open
> anything, and change or delete files, send messages, make purchases, or run shell
> commands — **on your behalf, on your live machine.**
>
> - **Never leave it unattended while a task is running.** Supervise every task.
> - **Run untrusted or first-time commands in `--dry-run`** (it logs intended
>   actions but executes nothing).
> - **Know your kill switch:** triple-press **Esc** (within ~1 second) to abort the
>   current task immediately. The **fail-safe corner** is always available too —
>   slam your mouse into any screen corner to raise `pyautogui`'s `FailSafeException`
>   and stop everything.
> - **Confirmation gate:** by default, actions that look destructive (pressing
>   Enter/Delete/Backspace, or typing things like `rm -rf`, `sudo`, `shutdown`,
>   `DROP TABLE`, `git push --force`) require explicit confirmation. The gate is
>   skipped entirely in dry-run, where nothing executes anyway.
> - **You are responsible for what it does.** Only point it at tasks and screens you
>   are comfortable having an autonomous agent control.
>
> By running VoxPilot you consent to it controlling the input devices of the
> computer it runs on.

---

## What it looks like in use

1. VoxPilot starts and sits **idle**, printing `Hold <ptt_key> to talk. Ctrl+C to quit.`
2. You **hold the push-to-talk key** (default: **F9**) and say,
   e.g. *"Open the Downloads folder and sort by date."*
3. You **release** the key. VoxPilot transcribes locally, screenshots, and starts
   the agent loop:
   - status moves through `LISTENING → THINKING → ACTING → DONE`,
   - it speaks short confirmations aloud (offline TTS) and prints them,
   - it takes screenshots, clicks, types, scrolls — looping until Claude stops
     calling tools, then gives a one-sentence confirmation.
4. VoxPilot returns to **idle**, ready for the next command.

---

## Requirements

- **Python 3.11+** (developed and tested on 3.12).
- An **AWS Bedrock API key** (a long-term bearer token) with access to the Claude
  computer-use models — *or* a first-party **Anthropic API key** if you switch the
  provider.
- A working **microphone** (for interactive voice mode; not needed for `--once`).
- An OS that `pyautogui`, `mss`, and `sounddevice` support (Windows, macOS, Linux/X11).
- **macOS only:** several system permissions (see [macOS permissions](#macos-permissions)).

---

## Features

- **Push-to-talk or toggle** voice capture (configurable key).
- **Local, offline speech-to-text** via `faster-whisper` (no audio leaves your
  machine), with an optional OpenAI Whisper API backend.
- **Claude computer use** on **AWS Bedrock** (default) or the first-party Anthropic
  API, with **Sonnet** by default and **Opus** on demand (`--opus`).
- **DPI-correct, scaled screenshots:** the screen is downscaled to a target box for
  the model; returned coordinates are scaled back up to real pixels.
- **Full action set:** clicks (left/right/middle/double/triple), drags, mouse
  down/up, key presses and combos, hold-key, typing, scrolling, waits, cursor
  position, and screenshots.
- **Safety first:** dry-run mode, a destructive-action confirmation gate, a triple-Esc
  kill switch, the `pyautogui` fail-safe corner, and a **rotating action log** at
  `~/.voxpilot/logs/`.
- **Spoken + on-screen feedback** (offline `pyttsx3` TTS) and a lightweight status
  indicator.
- **Graceful degradation:** missing microphone, TTS, or tray library never crash the
  app — those features simply turn off.

---

## Install

```bash
# 1. Clone, then create and activate a virtual environment
python -m venv .venv

# macOS / Linux
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# 2. Install VoxPilot (editable install; pulls in all dependencies)
pip install -e .

# For development (tests + linters):
pip install -e ".[dev]"
```

> On Linux you may also need system packages for audio (`portaudio`) and an X11
> session for `pyautogui`/`mss`.

---

## Configuration

VoxPilot reads **secrets from `.env`** and **non-secret settings from `config.yaml`**.

### Secrets: `.env`

Copy the provided template and fill it in:

```bash
cp .env.example .env
```

```dotenv
# Amazon Bedrock (default provider) — a Bedrock bearer/API key.
AWS_BEARER_TOKEN_BEDROCK=your-bedrock-bearer-token
AWS_REGION=us-east-1

# First-party Anthropic API (only if you set agent.provider: anthropic)
ANTHROPIC_API_KEY=

# OpenAI Whisper API (only if you set stt.backend: openai)
OPENAI_API_KEY=
```

VoxPilot calls `load_dotenv()` on startup, so a `.env` in your working directory is
picked up automatically. Secrets are **never** read from `config.yaml` and are
**never** committed (`.env` is gitignored).

> **Generating a Bedrock key:** AWS console → **Bedrock → API keys → Generate
> long-term API key**. The bearer token is read by `AnthropicBedrock` directly.

> 🔁 **Rotate the provided key.** If a Bedrock API key was supplied to you to build
> or demo VoxPilot, treat it as compromised and **rotate it** in the AWS console
> before any real use. Never commit it.

### Settings: `config.yaml`

Copy and edit the example (or run with built-in defaults — `config.example.yaml` is
loaded as a fallback if `config.yaml` is absent):

```bash
cp config.example.yaml config.yaml
```

#### Configuration reference

| Section | Key | Default | Description |
|---|---|---|---|
| `agent` | `provider` | `bedrock` | `bedrock` or `anthropic`. |
| `agent` | `model` | `us.anthropic.claude-sonnet-4-6` | Default computer-use model id. |
| `agent` | `opus_model` | `us.anthropic.claude-opus-4-8` | Model used when `--opus` is passed. |
| `agent` | `region` | `us-east-1` | AWS region for Bedrock (env `AWS_REGION` can override). |
| `agent` | `max_tokens` | `4096` | Max tokens per Claude response. |
| `agent` | `max_iterations` | `40` | Hard cap on agent-loop iterations (runaway guard). |
| `agent` | `target_width` | `1280` | Width the screenshot is scaled down to for the model. |
| `agent` | `target_height` | `800` | Height the screenshot is scaled down to for the model. |
| `agent` | `tool_version` | `computer_20251124` | Computer-tool type sent to the API. |
| `agent` | `beta_flag` | `computer-use-2025-11-24` | Beta flag for the computer tool. |
| `agent` | `enable_zoom` | `false` | Enable the tool's zoom action. |
| `stt` | `backend` | `faster_whisper` | `faster_whisper` (local, offline) or `openai`. |
| `stt` | `model` | `base` | faster-whisper size: `tiny`/`base`/`small`/`medium`/`large-v3`. |
| `stt` | `device` | `auto` | `auto`/`cpu`/`cuda`. |
| `stt` | `compute_type` | `auto` | `auto`/`int8`/`int8_float16`/`float16`/`float32`. |
| `stt` | `language` | `null` | Force a language code (e.g. `en`) or autodetect. |
| `stt` | `openai.model` | `whisper-1` | Model used by the OpenAI backend. |
| `hotkey` | `mode` | `push_to_talk` | `push_to_talk` (hold) or `toggle` (tap on/off). |
| `hotkey` | `ptt_key` | `f9` | Push-to-talk key (pynput name, e.g. `f9`, `ctrl_l`, `ctrl_r`, `cmd_r`). |
| `hotkey` | `kill_key` | `esc` | Kill-switch key. |
| `hotkey` | `kill_press_count` | `3` | Presses required to abort. |
| `hotkey` | `kill_press_window_s` | `1.0` | Time window (seconds) for the presses. |
| `safety` | `dry_run` | `false` | Log actions but **do not** execute them. |
| `safety` | `confirm_destructive` | `false` | Prompt before risky actions (off by default so it just runs; kill-switch + corner fail-safe + dry-run remain). |
| `safety` | `confirmation_mode` | `onscreen` | `onscreen` / `spoken` / `both`. |
| `safety` | `failsafe_corner` | `true` | Honor the pyautogui corner fail-safe. |
| `safety` | `action_log` | `true` | Append executed actions to `~/.voxpilot/logs/`. |
| `feedback` | `tts` | `true` | Speak short confirmations aloud (offline). |
| `feedback` | `tts_rate` | `180` | Speech rate (words per minute). |
| `feedback` | `tts_volume` | `1.0` | Speech volume (`0.0`–`1.0`). |
| `feedback` | `verbose` | `true` | Print status/confirmations to the terminal. |

---

## Running

```bash
# Interactive voice mode (default): hold PTT, speak, release.
python -m voxpilot

# Safe, no-execution mode — logs intended actions only.
python -m voxpilot --dry-run

# Run a single instruction and exit (great for testing without a mic).
python -m voxpilot --once "open the calculator and type 2+2="

# Use the more capable Opus model for this run.
python -m voxpilot --opus

# Force a provider / model for this run.
python -m voxpilot --provider anthropic --model claude-sonnet-4-6

# Turn off speech / reduce console output.
python -m voxpilot --no-tts --quiet
```

If you installed the console script, `voxpilot` works the same as `python -m voxpilot`.

### Desktop mode (no terminal, on-screen overlay)

For a Wispr-Flow-style experience — a floating pill that shows it's listening, a
system-tray icon, and **no terminal window** — use `--windowed`:

```bash
# Desktop mode in the current terminal (overlay + tray)
python -m voxpilot --windowed

# Fully windowless (no console at all) — launch via pythonw:
pythonw -m voxpilot --windowed
```

While you hold **F9**, a **Liquid-Glass** pill appears near the bottom of the
screen — a frosted capsule that blurs the desktop behind it (real backdrop blur),
with a glossy specular rim and a live **waveform** that reacts to your voice;
release to send and it
switches to a **"Working"** animation while it transcribes, thinks, and acts. The
overlay is excluded from screen capture (`WDA_EXCLUDEFROMCAPTURE`), so the agent
never sees it in its own screenshots. The tray icon also reflects state
(idle / listening / thinking / acting). **Quit** from the tray menu or with
**Ctrl+Alt+Q**. In this mode output is written to `~/.voxpilot/logs/voxpilot.log`.

To launch it from the Start Menu / Desktop (or at login) with no terminal, run
the installer once:

```powershell
# Start Menu + Desktop shortcuts
powershell -ExecutionPolicy Bypass -File scripts\install_shortcuts.ps1

# ...and also start automatically at login
powershell -ExecutionPolicy Bypass -File scripts\install_shortcuts.ps1 -Startup
```

### Command-line options

| Flag | Description |
|---|---|
| `--config PATH` | Path to a specific `config.yaml`. |
| `--dry-run` | Log intended actions; execute nothing. |
| `--opus` | Use the configured Opus model for this run. |
| `--provider {bedrock,anthropic}` | Override the provider. |
| `--model STR` | Override the model id. |
| `--no-tts` | Disable spoken feedback. |
| `--once TEXT` | Run one instruction, then exit. |
| `--max-iter N` | Override the agent-loop iteration cap. |
| `--quiet` | Reduce console output (verbose off). |
| `-h`, `--help` | Show help. Works without any credentials or network. |

---

## Hotkeys & controls

| Control | Default | What it does |
|---|---|---|
| **Push-to-talk** | **Hold F9** (`f9`) | Hold to record; release to transcribe and act. In `toggle` mode, tap to start, tap to stop. |
| **Kill switch** | **Triple-press Esc** within ~1s | Aborts the running task immediately. |
| **Fail-safe corner** | **Mouse → any screen corner** | Raises pyautogui's `FailSafeException` and stops everything. |
| **Quit** | **Ctrl+C** in the terminal | Stops VoxPilot and cleans up listeners/threads. |

The PTT key, mode, and kill-switch settings are all configurable in `config.yaml`.

---

## Safety features (summary)

- **Idle by default** — nothing happens until you hold the key and speak.
- **Dry-run mode** (`--dry-run` / `safety.dry_run`) — logs intended actions, executes
  none, and **skips the confirmation gate entirely** (nothing to confirm).
- **Confirmation gate** — destructive-looking actions (Enter/Delete/Backspace key
  presses; typing `rm -rf`, `sudo`, `shutdown`, `DROP TABLE`, `git push --force`,
  etc.) require explicit confirmation (`onscreen`, `spoken`, or `both`).
- **Triple-Esc kill switch** — aborts the loop mid-task.
- **pyautogui fail-safe corner** — hardware-style emergency stop.
- **Iteration cap** — `agent.max_iterations` prevents runaway loops.
- **Rotating action log** — every executed (and dry-run) action is appended to
  `~/.voxpilot/logs/actions.log` (rotated, 5 backups) for auditing.

---

## Architecture

VoxPilot is a small, layered package. The flow is:

```
mic ─▶ audio.recorder ─▶ stt (local whisper) ─▶ agent.loop
                                                   │
   screen.screenshot ◀──────────────────────────┐ │  (scaled PNG + command)
        │                                        │ ▼
        ▼                                  agent.anthropic_client
   screen.scaling  (downscale / upscale coords)   │  (Bedrock / Anthropic)
        │                                        │ ▼
        ▼                                   tool_use blocks
   screen.actions (pyautogui) ◀── safety.guard (gate / log / kill switch)
        │
        ▼
   feedback.tts + ui.tray  (spoken + status)
```

| Module | Responsibility |
|---|---|
| `voxpilot.config` | Typed dataclass config; loads `.env` + `config.yaml`; validation. |
| `voxpilot.audio.recorder` | Push-to-talk / toggle capture (`sounddevice`), hotkey listener (`pynput`). |
| `voxpilot.stt` | Speech-to-text backends: local `faster-whisper` and OpenAI Whisper API. |
| `voxpilot.screen.scaling` | DPI awareness + coordinate scaling between model and real pixels. |
| `voxpilot.screen.screenshot` | Captures the primary monitor (`mss`), downscales, base64-encodes. |
| `voxpilot.screen.actions` | Executes computer-tool actions via `pyautogui`. |
| `voxpilot.agent.anthropic_client` | Bedrock/Anthropic client + computer-tool builder. |
| `voxpilot.agent.loop` | The screenshot → think → act loop until Claude stops. |
| `voxpilot.safety.guard` | Dry-run, confirmation gate, kill switch, action logging. |
| `voxpilot.feedback.tts` | Offline spoken + console feedback. |
| `voxpilot.ui.tray` | Lightweight status indicator (optional tray icon). |
| `voxpilot.__main__` | CLI entry point, banner, wiring, interactive/`--once` modes. |

Heavy/optional libraries (`pyautogui`, `mss`, `pyttsx3`, `faster_whisper`,
`pystray`) are lazy-imported so the package imports cleanly in headless/test
environments and degrades gracefully when hardware is missing.

---

## macOS permissions

macOS requires you to grant several permissions before VoxPilot can see the screen,
move the mouse, type, or hear the microphone. Open **System Settings → Privacy &
Security**, then grant your terminal (e.g. **Terminal** or **iTerm**) — *not* Python
directly — under:

- **Accessibility** — `System Settings → Privacy & Security → Accessibility`
  (required to control the mouse/keyboard).
- **Screen Recording** — `System Settings → Privacy & Security → Screen Recording`
  (required for screenshots; without it screenshots come back **black**).
- **Microphone** — `System Settings → Privacy & Security → Microphone`
  (required for voice capture).
- **Input Monitoring** — `System Settings → Privacy & Security → Input Monitoring`
  (required for the global push-to-talk / kill-switch hotkeys).

> After granting or changing any of these, **fully quit your terminal with Cmd-Q and
> relaunch it** — macOS only applies the new permissions to freshly launched
> processes.

---

## Testing & linting

```bash
# Run the test suite
pytest

# Lint
ruff check .

# Format check
black --check .
```

Tests stub out hardware (a fake `pyautogui`, dummy capture/STT, scripted fake
Anthropic client), so they run without a screen, microphone, network, or model
download.

### End-to-end browser test (optional, live)

`scripts/playwright_e2e_test.py` puts **Playwright in the loop as a verifier**: it
opens a real Chromium page with a text box, runs VoxPilot *live* with an
instruction to type a known phrase into it, then reads the DOM back to confirm the
text actually landed. This is a real integration test — it needs a display, your
Bedrock key in `.env`, and the `e2e` extra:

```bash
pip install -e ".[e2e]"
playwright install chromium
python scripts/playwright_e2e_test.py   # exit code 0 = PASS
```

---

## Troubleshooting

- **Black screenshots on macOS** — Screen Recording permission is not granted (or you
  didn't relaunch the terminal after granting it). Grant it under
  `Privacy & Security → Screen Recording`, then **Cmd-Q and reopen** your terminal.
- **Mouse clicks land in the wrong place / wrong scale on Windows** — This is a DPI
  issue. VoxPilot sets process DPI awareness at startup *before* importing
  `pyautogui`/`mss`. Make sure you launch via `python -m voxpilot` (so
  `ensure_dpi_awareness()` runs first) and avoid importing those libraries earlier.
- **`No audio device` / no input on start** — No working microphone was found. Check
  your OS sound input settings and that the terminal has Microphone permission
  (macOS). You can still use `--once "..."` without a mic.
- **`model id ... not found` / `AccessDeniedException`** — The configured model id
  isn't available in your region/account. List what you can use with:
  ```bash
  aws bedrock list-inference-profiles --region us-east-1
  ```
  and set `agent.model` / `agent.opus_model` to a valid id or inference profile.
- **`UnrecognizedClientException` / `invalid bearer token` / 403** — Your
  `AWS_BEARER_TOKEN_BEDROCK` is missing, wrong, or for the wrong region. Re-check
  `.env`, confirm `AWS_REGION`, and regenerate the key if needed (then **rotate** any
  key that may have been shared).
- **No speech output** — `pyttsx3` couldn't initialize a TTS engine on your system.
  VoxPilot keeps working and falls back to console output; install a system TTS voice
  or run with `--no-tts`.
- **Hotkey does nothing (macOS)** — Grant **Input Monitoring** to your terminal and
  relaunch with Cmd-Q.

---

## License

MIT — see [`LICENSE`](LICENSE). © 2026 Soneesh Kothagundla.
