"""End-to-end test: Playwright opens a browser, VoxPilot types into it, Playwright verifies.

This is a *live integration test* (not a unit test). It requires:
  - a real display, a working AWS Bedrock key in .env, and network access;
  - the Playwright library + Chromium (`pip install playwright && playwright install chromium`).

Flow:
  1. Playwright launches a headed Chromium maximized and loads a local page with a
     large, autofocused text input (``#target``).
  2. Playwright focuses the input, then shells out to VoxPilot in LIVE mode with a
     single instruction to type a known phrase into the on-screen text box.
  3. VoxPilot screenshots the real screen, finds the box, and types via the OS
     keyboard (the same computer-use path used in normal operation).
  4. Playwright reads ``#target``'s value back from the DOM and asserts the phrase
     landed there.

Run from the project root with the venv interpreter::

    .\\.venv\\Scripts\\python.exe scripts\\playwright_e2e_test.py

Exit code 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# Force UTF-8 console output so result glyphs never crash a cp1252 Windows console.
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if callable(_reconfigure):
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

PHRASE = "hello playwright"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

PAGE_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>VoxPilot Playwright Test</title>
    <style>
      body { margin: 0; font-family: Arial, sans-serif; background: #ffffff; }
      h1 { text-align: center; font-size: 42px; margin-top: 80px; color: #111; }
      .wrap { display: flex; justify-content: center; margin-top: 60px; }
      #target {
        font-size: 34px; width: 760px; height: 70px; padding: 12px;
        border: 4px solid #1a73e8; border-radius: 8px; outline: none;
      }
      p { text-align: center; font-size: 24px; color: #444; margin-top: 40px; }
    </style>
  </head>
  <body>
    <h1>VoxPilot &times; Playwright end-to-end test</h1>
    <div class="wrap">
      <input id="target" autofocus placeholder="VoxPilot should type here" />
    </div>
    <p>Type the phrase into the blue text box above.</p>
  </body>
</html>
"""


def _normalize(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def main() -> int:
    page_path = Path(__file__).with_name("_pw_test_page.html")
    page_path.write_text(PAGE_HTML, encoding="utf-8")

    creationflags = 0
    if sys.platform == "win32":  # avoid a console window stealing focus from the browser
        creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, args=["--start-maximized"])
        context = browser.new_context(no_viewport=True)
        page = context.new_page()
        page.goto(page_path.as_uri())
        page.bring_to_front()
        page.click("#target")  # ensure the input is focused before VoxPilot acts
        time.sleep(1.5)

        instruction = (
            f"There is a web browser open with a large blue text box in the center "
            f"of the screen. Click that text box and type exactly: {PHRASE} . "
            f"Then stop - do not press enter or do anything else."
        )
        cmd = [
            sys.executable,
            "-m",
            "voxpilot",
            "--once",
            instruction,
            "--no-confirm",
            "--no-tts",
            "--max-iter",
            "8",
        ]
        print("Launching VoxPilot (live):", " ".join(cmd[:4]), "...", flush=True)
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            creationflags=creationflags,
        )
        print("----- VoxPilot output (tail) -----")
        print((proc.stdout or "")[-1800:])
        if proc.returncode != 0:
            print(f"(VoxPilot exited with code {proc.returncode})")
            print((proc.stderr or "")[-800:])

        time.sleep(1.0)
        value = page.input_value("#target")
        browser.close()

    print("=" * 60)
    print(f"INPUT VALUE READ BY PLAYWRIGHT: {value!r}")
    passed = _normalize(PHRASE) in _normalize(value)
    print("RESULT:", "PASS" if passed else "FAIL")
    print("=" * 60)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
