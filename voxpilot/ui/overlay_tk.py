"""Cross-platform tkinter fallback overlay (used when the Win32 layered window
is unavailable, e.g. non-Windows). Simpler look than the Aurora layered overlay,
but the same public API: run/show_listening/show_working/update_level/hide/stop.
"""

from __future__ import annotations

import math
import queue
import tkinter as tk

_CHROMA = "#ff00ff"
_PILL = "#15171c"
_FG = "#eef1f6"
_ACCENT = "#4c8dff"
_ACCENT2 = "#8a6bff"
_REC = "#ff5a52"


class TkOverlay:
    """Thread-safe animated status pill rendered with tkinter (fallback)."""

    def __init__(self, width: int = 296, height: int = 60) -> None:
        self._q: queue.Queue[tuple] = queue.Queue()
        self._root: tk.Tk | None = None
        self._canvas: tk.Canvas | None = None
        self._w = width
        self._h = height
        self._mode = "hidden"
        self._visible = False
        self._level = 0.0
        self._target = 0.0
        self._frame = 0
        self._running = False

    def show_listening(self) -> None:
        self._q.put(("listen",))

    def show_working(self) -> None:
        self._q.put(("work",))

    def update_level(self, level: float) -> None:
        self._q.put(("level", float(level)))

    def hide(self) -> None:
        self._q.put(("hide",))

    def stop(self) -> None:
        self._q.put(("stop",))

    def run(self) -> None:
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
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{self._w}x{self._h}+{(sw - self._w) // 2}+{sh - self._h - 96}")
        self._running = True
        root.after(16, self._tick)
        root.mainloop()
        self._running = False
        self._root = None

    def _tick(self) -> None:
        root = self._root
        if root is None:
            return
        try:
            while True:
                self._apply(self._q.get_nowait())
        except queue.Empty:
            pass
        if self._visible:
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
            self._target = self._level = 0.0
        elif kind == "stop":
            try:
                root.quit()
            except Exception:
                pass

    def _rounded_rect(self, x1: int, y1: int, x2: int, y2: int, r: int, fill: str) -> None:
        c = self._canvas
        if c is None:
            return
        c.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=fill)
        c.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline=fill)
        for ox, oy in ((x1, y1), (x2 - 2 * r, y1), (x1, y2 - 2 * r), (x2 - 2 * r, y2 - 2 * r)):
            c.create_oval(ox, oy, ox + 2 * r, oy + 2 * r, fill=fill, outline=fill)

    def _render(self) -> None:
        c = self._canvas
        if c is None:
            return
        c.delete("all")
        cy = self._h // 2
        self._rounded_rect(4, 4, self._w - 4, self._h - 4, (self._h - 8) // 2, _PILL)
        if self._mode == "listening":
            c.create_oval(15, cy - 5, 25, cy + 5, fill=_REC, outline="")
            c.create_text(
                38, cy, text="Listening", anchor="w", fill=_FG, font=("Segoe UI", 12, "bold")
            )
            x0, x1 = 132, self._w - 18
            n = 16
            gap = (x1 - x0) / n
            for i in range(n):
                env = 0.35 + 0.65 * math.sin(math.pi * (i + 0.5) / n)
                wob = 0.55 + 0.45 * math.sin(self._frame * 0.4 + i * 0.7)
                bar_h = 3 + self._level * env * wob * 30
                x = x0 + i * gap + gap * 0.22
                c.create_rectangle(
                    x, cy - bar_h / 2, x + gap * 0.56, cy + bar_h / 2, fill=_ACCENT, outline=""
                )
        else:
            start = (self._frame * 11) % 360
            c.create_arc(
                12,
                cy - 8,
                28,
                cy + 8,
                start=start,
                extent=110,
                style="arc",
                outline=_ACCENT,
                width=3,
            )
            c.create_text(
                38, cy, text="Working", anchor="w", fill=_FG, font=("Segoe UI", 12, "bold")
            )
            x0, x1 = 120, self._w - 18
            n = 16
            gap = (x1 - x0) / n
            for i in range(n):
                amp = max(0.0, math.sin(self._frame * 0.45 - i * 0.55))
                bar_h = 3 + amp * 24
                x = x0 + i * gap + gap * 0.22
                col = _ACCENT2 if amp > 0.6 else _ACCENT
                c.create_rectangle(
                    x, cy - bar_h / 2, x + gap * 0.56, cy + bar_h / 2, fill=col, outline=""
                )
