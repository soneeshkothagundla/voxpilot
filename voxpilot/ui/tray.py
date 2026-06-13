"""Lightweight status indicator for VoxPilot.

:class:`StatusIndicator` reports the agent's current state. It has no hard
dependency on a tray library: if ``pystray`` (and ``Pillow``) are available it
runs a small tray icon in a background thread; otherwise it falls back to
console output. It never raises if optional dependencies are missing.
"""

from __future__ import annotations

import sys
import threading
from typing import Any

#: Valid lifecycle states reported by the indicator.
_STATES: frozenset[str] = frozenset({"IDLE", "LISTENING", "THINKING", "ACTING", "DONE"})


class StatusIndicator:
    """Console (and optional tray) status indicator.

    Args:
        feedback: Optional feedback object exposing ``status(state)``; when
            provided, state changes are delegated to it.
    """

    def __init__(self, feedback: Any | None = None) -> None:
        self.feedback = feedback
        self._state: str = "IDLE"
        self._lock = threading.Lock()

        # Optional tray icon (best-effort).
        self._icon: Any | None = None
        self._tray_thread: threading.Thread | None = None
        self._start_tray()

    # ------------------------------------------------------------------ #
    # Optional system-tray icon
    # ------------------------------------------------------------------ #

    def _start_tray(self) -> None:
        """Attempt to start a pystray icon in a background thread.

        Any failure (missing pystray/Pillow, no system tray, etc.) is swallowed
        and the indicator falls back to console-only output.
        """
        try:
            import pystray  # lazy, optional
            from PIL import Image

            image = Image.new("RGB", (64, 64), color=(40, 40, 40))
            self._icon = pystray.Icon("voxpilot", image, "VoxPilot: IDLE")

            self._tray_thread = threading.Thread(
                target=self._run_icon, name="voxpilot-tray", daemon=True
            )
            self._tray_thread.start()
        except Exception:  # noqa: BLE001 - tray is entirely optional
            self._icon = None
            self._tray_thread = None

    def _run_icon(self) -> None:
        """Run the tray icon event loop (executed in a daemon thread)."""
        try:
            if self._icon is not None:
                self._icon.run()
        except Exception:  # noqa: BLE001 - never let the tray crash the app
            self._icon = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def set_state(self, state: str) -> None:
        """Update the current state.

        Delegates to ``feedback.status`` when feedback is provided; otherwise
        prints to the console. Also updates the tray tooltip when present.

        Args:
            state: One of IDLE, LISTENING, THINKING, ACTING, DONE.
        """
        with self._lock:
            self._state = state

        if self.feedback is not None:
            try:
                self.feedback.status(state)
            except Exception:  # noqa: BLE001
                print(f"[{state}]", file=sys.stderr)
        else:
            print(f"[{state}]", file=sys.stderr)

        if self._icon is not None:
            try:
                self._icon.title = f"VoxPilot: {state}"
            except Exception:  # noqa: BLE001 - tooltip update is best-effort
                pass

    @property
    def state(self) -> str:
        """The most recently set state."""
        with self._lock:
            return self._state

    def stop(self) -> None:
        """Stop the tray icon (if any). Never raises."""
        icon = self._icon
        self._icon = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:  # noqa: BLE001
                pass
