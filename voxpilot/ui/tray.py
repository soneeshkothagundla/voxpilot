"""System-tray indicators for VoxPilot.

:class:`TrayIcon` is the primary indicator used in windowed mode: a colored
tray icon that reflects the agent's state and offers a Quit action, so the app
is usable with no terminal window. :class:`StatusIndicator` is a lighter,
console-or-tray helper kept for compatibility. Both degrade gracefully (and
never raise) when ``pystray``/``Pillow`` or a system tray are unavailable.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from typing import Any

#: Valid lifecycle states reported by the indicators.
_STATES: frozenset[str] = frozenset({"IDLE", "LISTENING", "THINKING", "ACTING", "DONE"})

#: Per-state icon colors.
_COLORS: dict[str, str] = {
    "idle": "#1a73e8",
    "listening": "#e8451a",
    "thinking": "#e8a01a",
    "acting": "#1ae87a",
    "done": "#1a73e8",
}


def _make_icon_image(color: str) -> Any:
    """Build a 64x64 RGBA tray icon (a colored disc with a mic glyph)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((6, 6, 58, 58), fill=color)
    draw.rounded_rectangle((26, 16, 38, 38), radius=6, fill="white")
    draw.line((32, 38, 32, 47), fill="white", width=3)
    draw.line((24, 47, 40, 47), fill="white", width=3)
    return img


class TrayIcon:
    """A system-tray icon that shows VoxPilot's state and offers Quit.

    Runs the tray event loop in a daemon thread. If ``pystray``/``Pillow`` are
    missing (or there is no system tray), the icon is simply absent and all
    methods are safe no-ops.
    """

    def __init__(
        self, on_quit: Callable[[], None] | None = None, app_name: str = "VoxPilot"
    ) -> None:
        """Initialize the tray icon.

        Args:
            on_quit: Called (once) when the user picks Quit from the menu.
            app_name: Name shown in the tooltip/menu.
        """
        self._on_quit = on_quit
        self._app_name = app_name
        self._state = "idle"
        self._icon: Any | None = None
        self._thread: threading.Thread | None = None
        self._available = True
        try:
            import pystray  # noqa: F401
            from PIL import Image  # noqa: F401
        except Exception:  # noqa: BLE001 - tray is entirely optional
            self._available = False

    def start(self) -> None:
        """Create the icon and run its event loop in a daemon thread."""
        if not self._available:
            return
        try:
            import pystray

            menu = pystray.Menu(
                pystray.MenuItem(
                    lambda _item: f"{self._app_name} - {self._state}", None, enabled=False
                ),
                pystray.MenuItem("Quit", self._quit),
            )
            self._icon = pystray.Icon(
                "voxpilot", _make_icon_image(_COLORS["idle"]), self._app_name, menu
            )
            self._thread = threading.Thread(target=self._run, name="voxpilot-tray", daemon=True)
            self._thread.start()
        except Exception:  # noqa: BLE001 - never let the tray crash the app
            self._icon = None

    def _run(self) -> None:
        try:
            if self._icon is not None:
                self._icon.run()
        except Exception:  # noqa: BLE001
            self._icon = None

    def set_state(self, state: str) -> None:
        """Update the tray color and tooltip to reflect ``state`` (best-effort)."""
        self._state = state
        icon = self._icon
        if icon is None:
            return
        try:
            icon.title = f"{self._app_name} - {state}"
            icon.icon = _make_icon_image(_COLORS.get(state, _COLORS["idle"]))
        except Exception:  # noqa: BLE001
            pass

    def _quit(self, icon: Any, _item: Any) -> None:
        try:
            icon.stop()
        except Exception:  # noqa: BLE001
            pass
        if self._on_quit is not None:
            try:
                self._on_quit()
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        """Stop the tray icon if running. Never raises."""
        icon = self._icon
        self._icon = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:  # noqa: BLE001
                pass


class StatusIndicator:
    """Console (and optional tray) status indicator.

    Kept for compatibility; windowed mode uses :class:`TrayIcon` directly.

    Args:
        feedback: Optional feedback object exposing ``status(state)``; when
            provided, state changes are delegated to it.
    """

    def __init__(self, feedback: Any | None = None) -> None:
        self.feedback = feedback
        self._state: str = "IDLE"
        self._lock = threading.Lock()
        self._icon: Any | None = None
        self._tray_thread: threading.Thread | None = None
        self._start_tray()

    def _start_tray(self) -> None:
        """Attempt to start a pystray icon in a background thread (best-effort)."""
        try:
            import pystray

            self._icon = pystray.Icon(
                "voxpilot", _make_icon_image(_COLORS["idle"]), "VoxPilot: IDLE"
            )
            self._tray_thread = threading.Thread(
                target=self._run_icon, name="voxpilot-tray", daemon=True
            )
            self._tray_thread.start()
        except Exception:  # noqa: BLE001 - tray is entirely optional
            self._icon = None
            self._tray_thread = None

    def _run_icon(self) -> None:
        try:
            if self._icon is not None:
                self._icon.run()
        except Exception:  # noqa: BLE001
            self._icon = None

    def set_state(self, state: str) -> None:
        """Update the current state (delegates to feedback or prints)."""
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
            except Exception:  # noqa: BLE001
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
