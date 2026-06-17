"""The VoxPilot "Under Control" screen indicator (Comet-style edge glow).

When the agent is engaged, a soft aurora frame breathes around every edge of the
screen so the user (and anyone watching) can tell at a glance that the computer
is under AI control -- and in which mode (listening / thinking / acting). While
the agent is *acting*, the real cursor also gets a glowing halo with a comet
trail, every click emits a colored ripple, and typing pulses the halo, so each
action is legible instead of a cursor mysteriously moving on its own.

This is a single full-virtual-screen **layered window** on its own thread:

* ``WS_EX_TRANSPARENT`` -> click-through, so it never blocks the user's or the
  agent's input.
* ``SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)`` -> the agent never sees
  the glow in the screenshots it takes.

The expensive Win32 plumbing mirrors the proven :class:`voxpilot.ui.overlay`
capsule. On non-Windows a no-op stub is used so callers need no platform checks.

Public API (all thread-safe): ``start()``, ``set_state(state)``,
``set_level(level)``, ``click_ripple(x, y, button)``, ``set_typing(on)``,
``hide()``, ``stop()``.
"""

from __future__ import annotations

import ctypes
import math
import queue
import sys
import threading
import time
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass

from .overlay import (
    _BITMAPINFOHEADER,
    _BLENDFUNCTION,
    _POINT,
    _SIZE,
    _WNDCLASS,
    _system_scale,
)

FPS = 30
_FRAME_DT = 1.0 / FPS

#: State -> visual language. rgb is the accent; peak is max edge alpha (0-255);
#: period/amp drive the breathing sine; cursor=True turns on the cursor halo/trail.
_STATES: dict[str, dict] = {
    "idle": {"rgb": (99, 102, 241), "peak": 0, "period": 5.0, "amp": 0.0, "cursor": False},
    "listening": {"rgb": (6, 182, 212), "peak": 150, "period": 3.5, "amp": 0.40, "cursor": False},
    "thinking": {"rgb": (139, 92, 246), "peak": 150, "period": 4.5, "amp": 0.30, "cursor": False},
    "acting": {"rgb": (79, 70, 229), "peak": 185, "period": 2.0, "amp": 0.45, "cursor": True},
}

#: Click ripple color by mouse button (RGB).
_RIPPLE_RGB = {"left": (99, 102, 241), "right": (139, 92, 246), "middle": (6, 182, 212)}


def accent_for(state: str) -> dict:
    """Return the visual parameters for a state (falls back to 'thinking')."""
    return _STATES.get(state, _STATES["thinking"])


def _falloff_mask(w: int, h: int, band: int, gamma: float = 1.6):
    """Build a (h, w) float mask: 1.0 at the very screen edge, 0.0 by ``band`` in.

    Uses the distance to the *nearest* edge so the four sides blend into rounded
    corners rather than overlapping into bright L-shapes.
    """
    import numpy as np

    xs = np.minimum(np.arange(w), np.arange(w)[::-1])
    ys = np.minimum(np.arange(h), np.arange(h)[::-1])
    dist = np.minimum(xs[None, :], ys[:, None]).astype("float32")
    t = np.clip(1.0 - dist / max(1, band), 0.0, 1.0)
    return t**gamma


def _pbgra(img):
    """Convert a straight-alpha RGBA Pillow image to a premultiplied BGRA array."""
    import numpy as np

    a = np.asarray(img, dtype=np.uint8)
    alpha = a[:, :, 3].astype(np.uint16)
    out = np.empty_like(a)
    out[:, :, 0] = (a[:, :, 2].astype(np.uint16) * alpha // 255).astype(np.uint8)  # B
    out[:, :, 1] = (a[:, :, 1].astype(np.uint16) * alpha // 255).astype(np.uint8)  # G
    out[:, :, 2] = (a[:, :, 0].astype(np.uint16) * alpha // 255).astype(np.uint8)  # R
    out[:, :, 3] = a[:, :, 3]
    return out


def _load_font(size: int, bold: bool = False):
    """Load a Windows UI font at ``size`` px, falling back to PIL's default."""
    from PIL import ImageFont

    names = (
        ["segoeuisb.ttf", "segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except Exception:  # noqa: BLE001 - try the next candidate
            continue
    try:
        return ImageFont.load_default()
    except Exception:  # noqa: BLE001
        return None


def _fit_text(draw, text: str, font, max_w: float) -> str:
    """Collapse whitespace and ellipsis-truncate ``text`` to fit ``max_w`` pixels."""
    text = " ".join(str(text).split())
    if font is None or not text:
        return text[:64]
    if draw.textlength(text, font=font) <= max_w:
        return text
    ell = "…"
    n = len(text)
    while n > 0 and draw.textlength(text[:n] + ell, font=font) > max_w:
        n -= 1
    return (text[:n] + ell) if n > 0 else ell


def _stamp(buf, sprite, cx: int, cy: int) -> None:
    """Alpha-composite a premultiplied BGRA ``sprite`` centered at ``(cx, cy)``.

    Both ``buf`` and ``sprite`` are premultiplied, so this is a straight
    source-over: ``out = src + dst * (1 - src_alpha)``. Clipped to ``buf`` bounds.
    """
    import numpy as np

    bh, bw = buf.shape[:2]
    sh, sw = sprite.shape[:2]
    x0, y0 = cx - sw // 2, cy - sh // 2
    sx0, sy0 = max(0, -x0), max(0, -y0)
    dx0, dy0 = max(0, x0), max(0, y0)
    dx1, dy1 = min(bw, x0 + sw), min(bh, y0 + sh)
    if dx1 <= dx0 or dy1 <= dy0:
        return
    sx1, sy1 = sx0 + (dx1 - dx0), sy0 + (dy1 - dy0)
    src = sprite[sy0:sy1, sx0:sx1].astype(np.uint16)
    dst = buf[dy0:dy1, dx0:dx1].astype(np.uint16)
    inv = 255 - src[:, :, 3:4]
    buf[dy0:dy1, dx0:dx1] = (src + dst * inv // 255).astype(np.uint8)


@dataclass
class _Ripple:
    """An expanding, fading click ring."""

    x: int
    y: int
    rgb: tuple[int, int, int]
    birth: float
    lifetime: float = 0.45
    max_r: int = 64

    def progress(self, now: float) -> float:
        """0.0 at birth -> 1.0 at end of life."""
        return (now - self.birth) / self.lifetime


class _NoopEdgeGlow:
    """No-op stand-in used on non-Windows so callers need no platform checks."""

    def start(self) -> None:  # noqa: D102
        pass

    def set_state(self, state: str) -> None:  # noqa: D102
        pass

    def set_level(self, level: float) -> None:  # noqa: D102
        pass

    def click_ripple(self, x: int, y: int, button: str = "left") -> None:  # noqa: D102
        pass

    def set_typing(self, on: bool) -> None:  # noqa: D102
        pass

    def set_title(self, text: str) -> None:  # noqa: D102
        pass

    def push_line(self, text: str) -> None:  # noqa: D102
        pass

    def clear_lines(self) -> None:  # noqa: D102
        pass

    def hide(self) -> None:  # noqa: D102
        pass

    def stop(self) -> None:  # noqa: D102
        pass


class _WinEdgeGlow:
    """Full-virtual-screen layered window: breathing edge frame + cursor effects."""

    def __init__(self, band_px: int = 140) -> None:
        """Create the controller (no window yet); call :meth:`start` to run it."""
        self._q: queue.Queue[tuple] = queue.Queue()
        self._scale = _system_scale()
        self._band = max(24, int(band_px * self._scale))
        self._thread: threading.Thread | None = None
        self._running = False
        self._visible = False
        self._state = "idle"
        self._level = 0.0
        self._typing = False
        self._t0 = 0.0
        # Win32 handles
        self._hwnd = None
        self._memdc = None
        self._screendc = None
        self._hbitmap = None
        self._bits = None
        self._dib_size = 0
        self._vx = self._vy = 0
        self._vw = self._vh = 0
        self._wndproc_ref = None
        # render assets / caches
        self._buf = None
        self._bands: dict[str, dict] = {}  # state -> {top,bottom,left,right}
        self._halo = None
        self._ring = None
        self._ripples: list[_Ripple] = []
        self._trail: deque = deque(maxlen=12)
        self._composed_state: str | None = None
        # Activity HUD ("what it's doing" card).
        self._title = ""
        self._lines: deque = deque(maxlen=6)
        self._panel = None  # cached premultiplied BGRA sprite
        self._panel_dirty = True
        # Frame-skip state: skip the full-screen recompose when nothing visible
        # changed (cursor idle, no ripples/typing/panel change).
        self._frame_i = 0
        self._last_cursor: tuple[int, int] | None = None

    # -- public, thread-safe API ------------------------------------------- #
    def start(self) -> None:
        """Spawn the render thread (idempotent)."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="voxpilot-edgeglow", daemon=True)
        self._thread.start()

    def set_state(self, state: str) -> None:
        """Switch visual state and ensure the glow is visible (idle -> hidden)."""
        self._q.put(("state", state))

    def set_level(self, level: float) -> None:
        """Feed the live mic level (0-1); modulates the listening breathe."""
        self._q.put(("level", float(level)))

    def click_ripple(self, x: int, y: int, button: str = "left") -> None:
        """Emit a click ripple at native screen coordinates ``(x, y)``."""
        self._q.put(("ripple", int(x), int(y), button))

    def set_typing(self, on: bool) -> None:
        """Toggle the typing pulse on the cursor halo."""
        self._q.put(("typing", bool(on)))

    def set_title(self, text: str) -> None:
        """Set the HUD title line (e.g. 'Listening' / 'Thinking' / 'Working')."""
        self._q.put(("title", str(text)))

    def push_line(self, text: str) -> None:
        """Append a plain-English activity line to the HUD ('Typing ...', ...)."""
        if text:
            self._q.put(("line", str(text)))

    def clear_lines(self) -> None:
        """Clear the HUD activity log (e.g. at the start of a new command)."""
        self._q.put(("clear",))

    def hide(self) -> None:
        """Hide the glow (idle)."""
        self._q.put(("state", "idle"))

    def stop(self) -> None:
        """Tear down the window and stop the render thread."""
        self._q.put(("stop",))

    # -- thread entry ------------------------------------------------------ #
    def _run(self) -> None:
        try:
            self._init_window()
            self._build_assets()
        except Exception as exc:  # noqa: BLE001 - degrade to a no-op drain, never crash
            print(f"[edgeglow] unavailable: {exc}", file=sys.stderr)
            self._drain_only()
            return
        self._t0 = time.perf_counter()
        self._running = True
        self._loop()
        self._cleanup()

    def _drain_only(self) -> None:
        self._running = True
        while self._running:
            try:
                while True:
                    if self._q.get_nowait()[0] == "stop":
                        self._running = False
            except queue.Empty:
                pass
            time.sleep(0.05)

    # -- Win32 setup ------------------------------------------------------- #
    def _init_window(self) -> None:
        # Use PRIVATE WinDLL handles, NOT the cached ``ctypes.windll.*`` singletons.
        # We set ``argtypes``/``restype`` below, and those singletons are shared
        # process-wide with pyautogui's mouse/keyboard backend. In particular,
        # mutating ``GetCursorPos.argtypes`` on the shared user32 makes every later
        # pyautogui call raise "expected LP__POINT instead of pointer to POINT",
        # which silently breaks all on-screen control in windowed mode. Private
        # handles keep our type setup isolated to this window.
        u = ctypes.WinDLL("user32")
        g = ctypes.WinDLL("gdi32")
        k = ctypes.WinDLL("kernel32")
        self._u, self._g = u, g
        lresult = ctypes.c_longlong

        k.GetModuleHandleW.restype = wintypes.HMODULE
        u.DefWindowProcW.restype = lresult
        u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASS)]
        u.RegisterClassW.restype = wintypes.ATOM
        u.CreateWindowExW.restype = wintypes.HWND
        u.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetDC.restype = wintypes.HDC
        u.GetDC.argtypes = [wintypes.HWND]
        u.GetSystemMetrics.restype = ctypes.c_int
        u.GetSystemMetrics.argtypes = [ctypes.c_int]
        u.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
        u.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        u.UpdateLayeredWindow.restype = wintypes.BOOL
        u.UpdateLayeredWindow.argtypes = [
            wintypes.HWND,
            wintypes.HDC,
            ctypes.POINTER(_POINT),
            ctypes.POINTER(_SIZE),
            wintypes.HDC,
            ctypes.POINTER(_POINT),
            wintypes.DWORD,
            ctypes.POINTER(_BLENDFUNCTION),
            wintypes.DWORD,
        ]
        g.CreateCompatibleDC.restype = wintypes.HDC
        g.CreateCompatibleDC.argtypes = [wintypes.HDC]
        g.CreateDIBSection.restype = wintypes.HBITMAP
        g.CreateDIBSection.argtypes = [
            wintypes.HDC,
            ctypes.c_void_p,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        g.SelectObject.restype = wintypes.HGDIOBJ
        g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]

        # Virtual screen spans all monitors.
        self._vx = u.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        self._vy = u.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        self._vw = u.GetSystemMetrics(78) or u.GetSystemMetrics(0)  # SM_CXVIRTUALSCREEN
        self._vh = u.GetSystemMetrics(79) or u.GetSystemMetrics(1)  # SM_CYVIRTUALSCREEN

        wndproc_type = ctypes.WINFUNCTYPE(
            lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )
        self._wndproc_ref = wndproc_type(
            lambda hwnd, msg, wp, lp: u.DefWindowProcW(hwnd, msg, wp, lp)
        )
        hinst = k.GetModuleHandleW(None)
        wc = _WNDCLASS()
        wc.lpfnWndProc = ctypes.cast(self._wndproc_ref, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = "VoxPilotEdgeGlowWnd"
        u.RegisterClassW(ctypes.byref(wc))

        # WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE
        ex_style = 0x00080000 | 0x00000020 | 0x00000080 | 0x00000008 | 0x08000000
        ws_popup = 0x80000000

        hwnd = u.CreateWindowExW(
            ex_style,
            "VoxPilotEdgeGlowWnd",
            "VoxPilotEdgeGlow",
            ws_popup,
            self._vx,
            self._vy,
            self._vw,
            self._vh,
            None,
            None,
            hinst,
            None,
        )
        if not hwnd:
            raise OSError("CreateWindowExW (edge glow) failed")
        self._hwnd = hwnd
        try:
            u.SetWindowDisplayAffinity(hwnd, 0x11)  # WDA_EXCLUDEFROMCAPTURE
        except Exception:  # noqa: BLE001
            pass
        self._make_dib(self._vw, self._vh)

    def _make_dib(self, w: int, h: int) -> None:
        bmi = _BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h  # top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0  # BI_RGB
        self._screendc = self._u.GetDC(None)
        self._memdc = self._g.CreateCompatibleDC(self._screendc)
        bits = ctypes.c_void_p()
        self._hbitmap = self._g.CreateDIBSection(
            self._memdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0
        )
        if not self._hbitmap:
            raise OSError("CreateDIBSection (edge glow) failed")
        self._bits = bits
        self._g.SelectObject(self._memdc, self._hbitmap)
        self._dib_size = w * h * 4

    # -- render assets ----------------------------------------------------- #
    def _build_assets(self) -> None:
        import numpy as np

        self._buf = np.zeros((self._vh, self._vw, 4), dtype=np.uint8)
        mask = _falloff_mask(self._vw, self._vh, self._band)
        b = self._band
        for name, params in _STATES.items():
            if params["peak"] <= 0:
                continue
            a = (mask * params["peak"]).astype(np.uint8)
            full = np.empty((self._vh, self._vw, 4), dtype=np.uint8)
            r, gg, bb = params["rgb"]
            a16 = a.astype(np.uint16)
            full[:, :, 0] = (bb * a16 // 255).astype(np.uint8)
            full[:, :, 1] = (gg * a16 // 255).astype(np.uint8)
            full[:, :, 2] = (r * a16 // 255).astype(np.uint8)
            full[:, :, 3] = a
            self._bands[name] = {
                "top": full[0:b, :, :].copy(),
                "bottom": full[self._vh - b : self._vh, :, :].copy(),
                "left": full[b : self._vh - b, 0:b, :].copy(),
                "right": full[b : self._vh - b, self._vw - b : self._vw, :].copy(),
            }
        self._halo = self._build_halo()
        self._ring = self._build_ring()

    def _build_halo(self):
        import numpy as np
        from PIL import Image

        d = max(64, int(180 * self._scale))
        yy, xx = np.mgrid[0:d, 0:d].astype("float32")
        cx = cy = (d - 1) / 2.0
        r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / (d / 2.0)
        glow = np.clip(1.0 - r, 0.0, 1.0) ** 2.2
        rgba = np.zeros((d, d, 4), dtype=np.uint8)
        rgba[:, :, 0] = 150  # soft blue-white core
        rgba[:, :, 1] = 170
        rgba[:, :, 2] = 255
        rgba[:, :, 3] = (glow * 200).astype(np.uint8)
        return _pbgra(Image.fromarray(rgba, "RGBA"))

    def _build_ring(self):
        """Base ring sprite kept in STRAIGHT RGBA (resized + faded per ripple)."""
        import numpy as np
        from PIL import Image

        d = 256
        yy, xx = np.mgrid[0:d, 0:d].astype("float32")
        cx = cy = (d - 1) / 2.0
        r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / (d / 2.0)
        # Annulus peaking near the rim (r~0.85), soft on both sides.
        ring = np.exp(-((r - 0.85) ** 2) / (2 * 0.12**2))
        rgba = np.zeros((d, d, 4), dtype=np.uint8)
        rgba[:, :, 3] = (np.clip(ring, 0, 1) * 230).astype(np.uint8)
        return Image.fromarray(rgba, "RGBA")

    # -- loop -------------------------------------------------------------- #
    def _loop(self) -> None:
        msg = wintypes.MSG()
        last = 0.0
        while self._running:
            while self._u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0x0001):
                self._u.TranslateMessage(ctypes.byref(msg))
                self._u.DispatchMessageW(ctypes.byref(msg))
            self._apply_commands()
            now = time.perf_counter()
            if self._visible and (now - last) >= _FRAME_DT:
                self._render(now)
                last = now
            time.sleep(0.004)

    def _apply_commands(self) -> None:
        try:
            while True:
                cmd = self._q.get_nowait()
                kind = cmd[0]
                if kind == "state":
                    self._set_state(cmd[1])
                elif kind == "level":
                    self._level = max(0.0, min(1.0, cmd[1]))
                elif kind == "ripple":
                    rgb = _RIPPLE_RGB.get(cmd[3], _RIPPLE_RGB["left"])
                    self._ripples.append(
                        _Ripple(cmd[1] - self._vx, cmd[2] - self._vy, rgb, time.perf_counter())
                    )
                elif kind == "typing":
                    self._typing = cmd[1]
                elif kind == "title":
                    if cmd[1] != self._title:
                        self._title = cmd[1]
                        self._panel_dirty = True
                elif kind == "line":
                    self._lines.append(cmd[1])
                    self._panel_dirty = True
                elif kind == "clear":
                    self._lines.clear()
                    self._panel_dirty = True
                elif kind == "stop":
                    self._running = False
        except queue.Empty:
            pass

    def _set_state(self, state: str) -> None:
        if state not in _STATES:
            state = "thinking"
        self._state = state
        if state == "idle":
            if self._visible:
                self._u.ShowWindow(self._hwnd, 0)  # SW_HIDE
                self._visible = False
            self._ripples.clear()
            self._trail.clear()
            self._typing = False
            self._title = ""
            self._lines.clear()
            self._panel_dirty = True
            return
        self._composed_state = None  # force recompose for the new accent
        if not self._visible:
            self._u.ShowWindow(self._hwnd, 4)  # SW_SHOWNOACTIVATE
            self._visible = True

    def _breath(self, now: float) -> float:
        p = accent_for(self._state)
        phase = 0.5 * (1.0 + math.sin(2 * math.pi * (now - self._t0) / p["period"]))
        base = 1.0 - p["amp"]
        val = base + p["amp"] * phase
        if self._state == "listening":  # voice modulates intensity
            val = min(1.0, val + 0.4 * self._level)
        return max(0.0, min(1.0, val))

    def _write_bands(self, scale01: float) -> None:
        import numpy as np

        bands = self._bands.get(self._state)
        if bands is None:
            return
        b, vw, vh = self._band, self._vw, self._vh
        s = int(max(0, min(255, round(scale01 * 255))))
        buf = self._buf

        def put(dst_slice, strip):
            buf[dst_slice] = (strip.astype(np.uint16) * s // 255).astype(np.uint8)

        put((slice(0, b), slice(None)), bands["top"])
        put((slice(vh - b, vh), slice(None)), bands["bottom"])
        put((slice(b, vh - b), slice(0, b)), bands["left"])
        put((slice(b, vh - b), slice(vw - b, vw)), bands["right"])

    def _render(self, now: float) -> None:
        breath = self._breath(now)
        acting = accent_for(self._state)["cursor"]
        has_panel = bool(self._title or self._lines)

        if acting or has_panel:
            # Recompose the full-screen buffer only when something visible changed
            # (cursor moved, a ripple is animating, typing, or the HUD changed); a
            # periodic tick keeps the edge breathing while everything is idle. This
            # avoids tens of MB/s of pointless numpy work when the cursor is still.
            self._frame_i += 1
            cur: tuple[int, int] | None = None
            moved = False
            if acting:
                pt = _POINT()
                self._u.GetCursorPos(ctypes.byref(pt))
                cur = (pt.x - self._vx, pt.y - self._vy)
                moved = self._last_cursor is None or (
                    abs(cur[0] - self._last_cursor[0]) + abs(cur[1] - self._last_cursor[1]) > 1
                )
            tick = self._frame_i % 4 == 0  # ~7.5 fps breathe floor when idle
            need = (
                moved
                or bool(self._ripples)
                or self._typing
                or self._panel_dirty
                or tick
                or self._composed_state is not None  # state just changed -> recompose
            )
            if not need:
                return
            self._last_cursor = cur
            self._buf.fill(0)
            self._write_bands(breath)
            if acting:
                self._render_cursor(now, cur)
                self._render_ripples(now)
            if has_panel:
                self._render_panel()
            ctypes.memmove(self._bits, self._buf.ctypes.data, self._dib_size)
            self._composed_state = None  # invalidate the cheap-path cache
            self._blit(255)
        else:
            # Static edge frame; breathe cheaply via the blend constant-alpha so we
            # only recompose pixels when the accent (state) actually changes.
            if self._composed_state != self._state:
                self._buf.fill(0)
                self._write_bands(1.0)
                ctypes.memmove(self._bits, self._buf.ctypes.data, self._dib_size)
                self._composed_state = self._state
            self._blit(int(max(0, min(255, round(breath * 255)))))

    def _render_panel(self) -> None:
        """Stamp the activity HUD card (top-center). Rebuilt only when text changes."""
        if self._panel_dirty or self._panel is None:
            self._panel = self._build_panel()
            self._panel_dirty = False
        if self._panel is None:
            return
        ph, pw = self._panel.shape[:2]
        cx = self._vw // 2
        cy = int(self._band * 0.36) + ph // 2  # nestled just inside the top edge band
        _stamp(self._buf, self._panel, cx, cy)

    def _build_panel(self):
        """Render the 'what it's doing' card to a premultiplied BGRA sprite."""
        from PIL import Image, ImageDraw

        s = self._scale
        pad = int(16 * s)
        title_h = int(28 * s)
        line_h = int(24 * s)
        gap = int(6 * s)
        width = int(min(720 * s, self._vw * 0.62))
        lines = list(self._lines)[-4:]
        height = pad + title_h + (line_h * len(lines) if lines else 0) + pad
        r, g, b = accent_for(self._state)["rgb"]

        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        radius = int(16 * s)
        d.rounded_rectangle(
            [0, 0, width - 1, height - 1],
            radius=radius,
            fill=(16, 18, 26, 210),
            outline=(r, g, b, 200),
            width=max(1, int(1.5 * s)),
        )
        title_font = _load_font(int(19 * s), bold=True)
        body_font = _load_font(int(15 * s), bold=False)
        max_text_w = width - 2 * pad

        # State dot + title.
        dot = int(5 * s)
        ty = pad
        dcy = ty + title_h // 2
        d.ellipse([pad, dcy - dot, pad + 2 * dot, dcy + dot], fill=(r, g, b, 255))
        tx = pad + 3 * dot + int(6 * s)
        d.text(
            (tx, ty),
            _fit_text(d, self._title or "VoxPilot", title_font, max_text_w - (tx - pad)),
            font=title_font,
            fill=(238, 240, 247, 255),
        )

        # Most-recent activity lines (newest brightest).
        y = pad + title_h + (gap if lines else 0)
        for i, ln in enumerate(lines):
            bright = 245 if i == len(lines) - 1 else 165
            d.text(
                (pad, y),
                _fit_text(d, ln, body_font, max_text_w),
                font=body_font,
                fill=(bright, bright, bright, 255),
            )
            y += line_h

        return _pbgra(img)

    def _render_cursor(self, now: float, cur: tuple[int, int]) -> None:
        cx, cy = cur
        # Comet trail: fade older samples.
        self._trail.append((cx, cy))
        n = len(self._trail)
        for i, (tx, ty) in enumerate(self._trail):
            if i == n - 1:
                continue
            fade = (i + 1) / n * 0.5
            _stamp(self._buf, self._scaled_halo(fade * 0.7), tx, ty)
        # Live halo (typing pulse brightens it rhythmically).
        amp = 1.0
        if self._typing:
            amp = 0.7 + 0.3 * (0.5 * (1 + math.sin(2 * math.pi * 7.0 * (now - self._t0))))
        _stamp(self._buf, self._scaled_halo(amp), cx, cy)

    def _scaled_halo(self, amp: float):
        import numpy as np

        if amp >= 0.999:
            return self._halo
        return (self._halo.astype(np.uint16) * int(max(0, min(255, amp * 255))) // 255).astype(
            np.uint8
        )

    def _render_ripples(self, now: float) -> None:
        import numpy as np

        alive: list[_Ripple] = []
        for rp in self._ripples:
            p = rp.progress(now)
            if p >= 1.0:
                continue
            alive.append(rp)
            d = max(8, int(2 * rp.max_r * self._scale * (0.2 + 0.8 * p)))
            fade = (1.0 - p) ** 1.5
            sprite = self._ring.resize((d, d))
            arr = np.asarray(sprite, dtype=np.uint8).copy()
            arr[:, :, 0] = rp.rgb[0]
            arr[:, :, 1] = rp.rgb[1]
            arr[:, :, 2] = rp.rgb[2]
            arr[:, :, 3] = (arr[:, :, 3].astype(np.uint16) * int(fade * 255) // 255).astype(
                np.uint8
            )
            _stamp(self._buf, _pbgra_from_array(arr), rp.x, rp.y)
        self._ripples = alive

    def _blit(self, sca: int) -> None:
        dst = _POINT(self._vx, self._vy)
        size = _SIZE(self._vw, self._vh)
        src = _POINT(0, 0)
        blend = _BLENDFUNCTION(0, 0, sca, 1)  # AC_SRC_OVER, SCA, AC_SRC_ALPHA
        self._u.UpdateLayeredWindow(
            self._hwnd,
            self._screendc,
            ctypes.byref(dst),
            ctypes.byref(size),
            self._memdc,
            ctypes.byref(src),
            0,
            ctypes.byref(blend),
            0x02,  # ULW_ALPHA
        )

    def _cleanup(self) -> None:
        try:
            if self._hbitmap:
                self._g.DeleteObject(self._hbitmap)
            if self._memdc:
                self._g.DeleteDC(self._memdc)
            if self._screendc:
                self._u.ReleaseDC(None, self._screendc)
            if self._hwnd:
                self._u.DestroyWindow(self._hwnd)
        except Exception:  # noqa: BLE001
            pass


def _pbgra_from_array(rgba):
    """Premultiply a straight-alpha RGBA numpy array into a BGRA array."""
    import numpy as np

    alpha = rgba[:, :, 3].astype(np.uint16)
    out = np.empty_like(rgba)
    out[:, :, 0] = (rgba[:, :, 2].astype(np.uint16) * alpha // 255).astype(np.uint8)
    out[:, :, 1] = (rgba[:, :, 1].astype(np.uint16) * alpha // 255).astype(np.uint8)
    out[:, :, 2] = (rgba[:, :, 0].astype(np.uint16) * alpha // 255).astype(np.uint8)
    out[:, :, 3] = rgba[:, :, 3]
    return out


# Pick the backend: layered window on Windows, no-op elsewhere.
EdgeGlow = _WinEdgeGlow if sys.platform == "win32" else _NoopEdgeGlow
