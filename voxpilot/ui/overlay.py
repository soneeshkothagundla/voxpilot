"""On-screen status overlay (a Wispr-Flow-style pill) built with tkinter.

A small, rounded, always-on-top window sits near the bottom-center of the primary
display. It has two looks:

* **Listening** - a red record dot and a live waveform that reacts to the mic
  level while the user holds the push-to-talk key.
* **Working** - a spinner and a traveling-wave animation shown while VoxPilot
  transcribes, thinks, and acts.

On Windows the overlay window is marked ``WDA_EXCLUDEFROMCAPTURE`` so it is
invisible to screen-capture APIs (including ``mss``); the agent therefore never
sees the overlay in its screenshots even while it is on screen.

tkinter requires its event loop on the main thread, so :meth:`Overlay.run`
blocks. Drive everything else from other threads; all public methods are
thread-safe (they enqueue commands applied on the tkinter thread).
"""

from __future__ import annotations

import math
import queue
import sys
import tkinter as tk

#: Chroma key painted behind the pill; these pixels are made transparent so the
#: pill has rounded corners. Must not appear in the visible pill artwork.
_CHROMA = "#ff00ff"
_PILL = "#15171c"
_FG = "#eef1f6"
_ACCENT = "#4c8dff"
_ACCENT2 = "#8a6bff"
_REC = "#ff5a52"
_TRACK = "#2a2e37"


def _exclude_from_capture(root: tk.Tk) -> None:
    """Mark the window so screen-capture APIs (mss) can't see it (Windows only)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        root.update_idletasks()
        user32 = ctypes.windll.user32
        wda_exclude = 0x00000011  # WDA_EXCLUDEFROMCAPTURE (Win10 2004+)
        base = root.winfo_id()
        for hwnd in (base, user32.GetParent(base)):
            if hwnd:
                user32.SetWindowDisplayAffinity(hwnd, wda_exclude)
    except Exception:
        pass


class Overlay:
    """Thread-safe, always-on-top animated status pill rendered with tkinter."""

    def __init__(self, width: int = 296, height: int = 60) -> None:
        """Create the overlay (no window is shown until :meth:`run` is called)."""
        self._q: queue.Queue[tuple] = queue.Queue()
        self._root: tk.Tk | None = None
        self._canvas: tk.Canvas | None = None
        self._w = width
        self._h = height
        self._mode = "hidden"  # "hidden" | "listening" | "working"
        self._visible = False
        self._level = 0.0  # smoothed display level
        self._target = 0.0  # latest requested level
        self._frame = 0
        self._running = False

    # ------------------------------------------------------------------ #
    # Public, thread-safe API
    # ------------------------------------------------------------------ #

    def show_listening(self) -> None:
        """Switch to the listening look and show the pill."""
        self._q.put(("listen",))

    def show_working(self) -> None:
        """Switch to the working look and show the pill."""
        self._q.put(("work",))

    def update_level(self, level: float) -> None:
        """Update the live mic level (0.0-1.0) for the waveform."""
        self._q.put(("level", float(level)))

    def hide(self) -> None:
        """Hide the pill."""
        self._q.put(("hide",))

    def stop(self) -> None:
        """Exit the tkinter event loop and let :meth:`run` return."""
        self._q.put(("stop",))

    # ------------------------------------------------------------------ #
    # Main-thread event loop
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        """Build the window and run the tkinter event loop (blocks the thread)."""
        root = tk.Tk()
        self._root = root
        root.title("VoxPilot")
        root.configure(bg=_CHROMA)
        root.withdraw()
        try:
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.attributes("-alpha", 0.97)
            root.attributes("-transparentcolor", _CHROMA)
        except Exception:
            pass

        self._canvas = tk.Canvas(
            root, width=self._w, height=self._h, bg=_CHROMA, highlightthickness=0, bd=0
        )
        self._canvas.pack()

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        x = (sw - self._w) // 2
        y = sh - self._h - 96
        root.geometry(f"{self._w}x{self._h}+{x}+{y}")

        _exclude_from_capture(root)

        self._running = True
        root.after(16, self._tick)
        root.mainloop()
        self._running = False
        self._root = None

    # ------------------------------------------------------------------ #
    # Internals (tkinter thread only)
    # ------------------------------------------------------------------ #

    def _tick(self) -> None:
        root = self._root
        if root is None:
            return
        # Apply queued commands.
        try:
            while True:
                self._apply(self._q.get_nowait())
        except queue.Empty:
            pass

        if self._visible:
            # Smooth the level toward the latest target for a fluid waveform.
            self._level += (self._target - self._level) * 0.35
            self._frame += 1
            self._render()

        if self._running and self._root is not None:
            self._root.after(33, self._tick)

    def _apply(self, cmd: tuple) -> None:
        root = self._root
        if root is None:
            return
        kind = cmd[0]
        if kind == "level":
            self._target = max(0.0, min(1.0, cmd[1]))
        elif kind in ("listen", "work"):
            self._mode = "listening" if kind == "listen" else "working"
            if not self._visible:
                root.deiconify()
                try:
                    root.attributes("-topmost", True)
                    root.lift()
                except Exception:
                    pass
                self._visible = True
        elif kind == "hide":
            if self._visible:
                root.withdraw()
                self._visible = False
            self._target = 0.0
            self._level = 0.0
        elif kind == "stop":
            try:
                root.quit()
            except Exception:
                pass

    # -- drawing --------------------------------------------------------- #

    def _rounded_rect(self, x1: int, y1: int, x2: int, y2: int, r: int, fill: str) -> None:
        c = self._canvas
        if c is None:
            return
        c.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=fill)
        c.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline=fill)
        c.create_oval(x1, y1, x1 + 2 * r, y1 + 2 * r, fill=fill, outline=fill)
        c.create_oval(x2 - 2 * r, y1, x2, y1 + 2 * r, fill=fill, outline=fill)
        c.create_oval(x1, y2 - 2 * r, x1 + 2 * r, y2, fill=fill, outline=fill)
        c.create_oval(x2 - 2 * r, y2 - 2 * r, x2, y2, fill=fill, outline=fill)

    def _render(self) -> None:
        c = self._canvas
        if c is None:
            return
        c.delete("all")
        h = self._h
        cy = h // 2
        self._rounded_rect(4, 4, self._w - 4, h - 4, (h - 8) // 2, _PILL)

        if self._mode == "listening":
            r = 5 + 1.6 * (0.5 + 0.5 * math.sin(self._frame * 0.25))
            c.create_oval(20 - r, cy - r, 20 + r, cy + r, fill=_REC, outline="")
            c.create_text(
                38, cy, text="Listening", anchor="w", fill=_FG, font=("Segoe UI", 12, "bold")
            )
            self._draw_waveform(132, self._w - 18, cy)
        else:
            self._draw_spinner(20, cy)
            c.create_text(
                38, cy, text="Working", anchor="w", fill=_FG, font=("Segoe UI", 12, "bold")
            )
            self._draw_traveling(120, self._w - 18, cy)

    def _draw_waveform(self, x0: int, x1: int, cy: int) -> None:
        c = self._canvas
        if c is None:
            return
        n = 16
        gap = (x1 - x0) / n
        for i in range(n):
            env = 0.35 + 0.65 * math.sin(math.pi * (i + 0.5) / n)
            wob = 0.55 + 0.45 * math.sin(self._frame * 0.4 + i * 0.7)
            mag = self._level * env * wob
            bar_h = 3 + mag * 30
            x = x0 + i * gap + gap * 0.22
            w = max(2.0, gap * 0.56)
            c.create_rectangle(x, cy - bar_h / 2, x + w, cy + bar_h / 2, fill=_ACCENT, outline="")

    def _draw_traveling(self, x0: int, x1: int, cy: int) -> None:
        c = self._canvas
        if c is None:
            return
        n = 16
        gap = (x1 - x0) / n
        for i in range(n):
            amp = max(0.0, math.sin(self._frame * 0.45 - i * 0.55))
            bar_h = 3 + amp * 24
            x = x0 + i * gap + gap * 0.22
            w = max(2.0, gap * 0.56)
            color = _ACCENT2 if amp > 0.6 else _ACCENT
            c.create_rectangle(x, cy - bar_h / 2, x + w, cy + bar_h / 2, fill=color, outline="")

    def _draw_spinner(self, cx: int, cy: int) -> None:
        c = self._canvas
        if c is None:
            return
        start = (self._frame * 11) % 360
        c.create_oval(cx - 8, cy - 8, cx + 8, cy + 8, outline=_TRACK, width=3)
        c.create_arc(
            cx - 8,
            cy - 8,
            cx + 8,
            cy + 8,
            start=start,
            extent=110,
            style="arc",
            outline=_ACCENT,
            width=3,
        )
