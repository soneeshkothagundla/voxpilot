"""On-screen recording overlay (a Wispr-Flow-style pill) built with tkinter.

A small, borderless, always-on-top window is shown near the bottom-center of the
primary display while the user holds the push-to-talk key. It shows a "Listening"
label and a live microphone-level bar that reacts to the user's voice.

tkinter requires its event loop to run on the main thread, so :meth:`Overlay.run`
blocks. Drive everything else (hotkeys, the agent loop) from other threads. All
public methods are thread-safe: they enqueue commands that are applied on the
tkinter thread via ``after`` polling.

The overlay is shown only while *listening* (before the agent takes any
screenshots), so it never appears in the screenshots the agent sends to the model.
"""

from __future__ import annotations

import queue
import tkinter as tk

_BG = "#16181d"
_FG = "#ffffff"
_ACCENT = "#1a73e8"
_DOT = "#e8451a"
_TRACK = "#2a2d35"


class Overlay:
    """Thread-safe, always-on-top recording pill rendered with tkinter."""

    def __init__(self, width: int = 300, height: int = 64) -> None:
        """Create the overlay (no window is shown until :meth:`run` is called).

        Args:
            width: Pill width in pixels.
            height: Pill height in pixels.
        """
        self._q: queue.Queue[tuple] = queue.Queue()
        self._root: tk.Tk | None = None
        self._canvas: tk.Canvas | None = None
        self._width = width
        self._height = height
        self._bar_w = 150
        self._bar_h = 14
        self._running = False

    # ------------------------------------------------------------------ #
    # Public, thread-safe API
    # ------------------------------------------------------------------ #

    def show_listening(self) -> None:
        """Show the pill and reset the level bar (call when recording starts)."""
        self._q.put(("show",))

    def update_level(self, level: float) -> None:
        """Update the live mic-level bar (0.0-1.0)."""
        self._q.put(("level", float(level)))

    def hide(self) -> None:
        """Hide the pill (call when recording stops)."""
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
        root.withdraw()
        try:
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.attributes("-alpha", 0.95)
        except Exception:
            pass

        frame = tk.Frame(root, bg=_BG)
        frame.pack(fill="both", expand=True)

        dot = tk.Canvas(frame, width=16, height=16, bg=_BG, highlightthickness=0)
        dot.create_oval(3, 3, 13, 13, fill=_DOT, outline="")
        dot.pack(side="left", padx=(16, 8), pady=10)

        label = tk.Label(frame, text="Listening", fg=_FG, bg=_BG, font=("Segoe UI", 13, "bold"))
        label.pack(side="left", padx=(0, 12), pady=10)

        self._canvas = tk.Canvas(
            frame, width=self._bar_w, height=self._bar_h, bg=_BG, highlightthickness=0
        )
        self._canvas.pack(side="left", padx=(0, 16), pady=10)
        self._draw_level(0.0)

        self._running = True
        root.after(30, self._drain)
        root.mainloop()
        self._running = False
        self._root = None

    # ------------------------------------------------------------------ #
    # Internals (tkinter thread only)
    # ------------------------------------------------------------------ #

    def _position(self) -> None:
        root = self._root
        if root is None:
            return
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        x = (sw - self._width) // 2
        y = sh - self._height - 90
        root.geometry(f"{self._width}x{self._height}+{x}+{y}")

    def _draw_level(self, level: float) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.delete("all")
        level = max(0.0, min(1.0, level))
        canvas.create_rectangle(0, 2, self._bar_w, self._bar_h - 2, fill=_TRACK, outline="")
        filled = int(self._bar_w * level)
        if filled > 0:
            canvas.create_rectangle(0, 2, filled, self._bar_h - 2, fill=_ACCENT, outline="")

    def _apply(self, cmd: tuple) -> None:
        root = self._root
        if root is None:
            return
        kind = cmd[0]
        if kind == "show":
            self._position()
            root.deiconify()
            try:
                root.attributes("-topmost", True)
                root.lift()
            except Exception:
                pass
            self._draw_level(0.0)
        elif kind == "level":
            self._draw_level(cmd[1])
        elif kind == "hide":
            root.withdraw()
        elif kind == "stop":
            try:
                root.quit()
            except Exception:
                pass

    def _drain(self) -> None:
        try:
            while True:
                self._apply(self._q.get_nowait())
        except queue.Empty:
            pass
        if self._root is not None and self._running:
            self._root.after(30, self._drain)
