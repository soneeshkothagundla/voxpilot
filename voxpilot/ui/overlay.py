"""The VoxPilot status overlay.

On Windows this renders the "Aurora Glass Capsule" (see :mod:`voxpilot.ui._aurora`)
into a true per-pixel-alpha **layered window** via ``UpdateLayeredWindow`` — giving
antialiased rounded corners, a soft drop shadow, translucency, and a glow that a
chroma-key window can't match. The window is click-through and marked
``WDA_EXCLUDEFROMCAPTURE`` so the agent never sees it in its screenshots.

On non-Windows (or if the layered window can't be created) it falls back to the
tkinter overlay in :mod:`voxpilot.ui.overlay_tk`.

Public API (thread-safe, identical across backends): ``run()`` (blocks on the main
thread), ``show_listening()``, ``show_working()``, ``update_level(level)``,
``hide()``, ``stop()``.
"""

from __future__ import annotations

import ctypes
import queue
import sys
import time
from ctypes import wintypes

from .overlay_tk import TkOverlay

FPS = 30
_FRAME_DT = 1.0 / FPS


# -- module-level structs (importable on any platform; no windll touched) --- #
class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


def _system_scale() -> float:
    """Return the primary-display DPI scale (1.0 = 96 DPI). Windows only."""
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        if dpi:
            return max(1.0, dpi / 96.0)
    except Exception:
        pass
    return 1.0


def _premultiplied_bgra(img) -> bytes:
    """Convert a straight-alpha RGBA Pillow image to premultiplied BGRA bytes."""
    import numpy as np

    a = np.asarray(img, dtype=np.uint8)
    alpha = a[:, :, 3].astype(np.uint16)
    out = np.empty_like(a)
    out[:, :, 0] = (a[:, :, 2].astype(np.uint16) * alpha // 255).astype(np.uint8)  # B
    out[:, :, 1] = (a[:, :, 1].astype(np.uint16) * alpha // 255).astype(np.uint8)  # G
    out[:, :, 2] = (a[:, :, 0].astype(np.uint16) * alpha // 255).astype(np.uint8)  # R
    out[:, :, 3] = a[:, :, 3]
    return out.tobytes()


class _WinOverlay:
    """Per-pixel-alpha layered-window overlay (Windows)."""

    def __init__(self) -> None:
        self._q: queue.Queue[tuple] = queue.Queue()
        self._running = False
        self._visible = False
        self._mode = "listening"
        self._level = 0.0
        self._target = 0.0
        self._t0 = 0.0
        self._hwnd = None
        self._memdc = None
        self._screendc = None
        self._hbitmap = None
        self._bits = None
        self._dib_size = 0
        self._renderer = None
        self._x = self._y = 0
        self._wndproc_ref = None

    # -- public, thread-safe API ------------------------------------------- #
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

    # -- lifecycle --------------------------------------------------------- #
    def run(self) -> None:
        try:
            self._init_window()
        except Exception as exc:  # noqa: BLE001 - degrade without crashing the app
            print(f"[overlay] layered window unavailable: {exc}", file=sys.stderr)
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
        from ._aurora import AuroraRenderer

        u = ctypes.windll.user32
        g = ctypes.windll.gdi32
        k = ctypes.windll.kernel32
        self._u, self._g = u, g
        lresult = ctypes.c_longlong

        k.GetModuleHandleW.restype = wintypes.HMODULE
        k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
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
        u.DestroyWindow.argtypes = [wintypes.HWND]
        u.GetDC.restype = wintypes.HDC
        u.GetDC.argtypes = [wintypes.HWND]
        u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        u.SystemParametersInfoW.argtypes = [
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            wintypes.UINT,
        ]
        u.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        u.PeekMessageW.argtypes = [
            ctypes.c_void_p,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
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
        g.DeleteDC.argtypes = [wintypes.HDC]
        g.DeleteObject.argtypes = [wintypes.HGDIOBJ]

        self._renderer = AuroraRenderer(scale=_system_scale())
        w, h = self._renderer.tw, self._renderer.th

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
        wc.lpszClassName = "VoxPilotOverlayWnd"
        u.RegisterClassW(ctypes.byref(wc))  # ignore "already registered"

        ex_style = 0x00080000 | 0x00000008 | 0x00000080 | 0x00000020 | 0x08000000
        ws_popup = 0x80000000

        rect = wintypes.RECT()
        u.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
        screen_w = u.GetSystemMetrics(0)  # SM_CXSCREEN
        self._x = (screen_w - w) // 2
        self._y = rect.bottom - h

        hwnd = u.CreateWindowExW(
            ex_style,
            "VoxPilotOverlayWnd",
            "VoxPilot",
            ws_popup,
            self._x,
            self._y,
            w,
            h,
            None,
            None,
            hinst,
            None,
        )
        if not hwnd:
            raise OSError("CreateWindowExW failed")
        self._hwnd = hwnd
        try:
            u.SetWindowDisplayAffinity(hwnd, 0x11)  # WDA_EXCLUDEFROMCAPTURE
        except Exception:
            pass
        self._make_dib(w, h)

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
            raise OSError("CreateDIBSection failed")
        self._bits = bits
        self._g.SelectObject(self._memdc, self._hbitmap)
        self._dib_size = w * h * 4

    # -- loop -------------------------------------------------------------- #
    def _loop(self) -> None:
        msg = wintypes.MSG()
        last = 0.0
        while self._running:
            while self._u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0x0001):  # PM_REMOVE
                self._u.TranslateMessage(ctypes.byref(msg))
                self._u.DispatchMessageW(ctypes.byref(msg))
            self._apply_commands()
            now = time.perf_counter()
            if self._visible and (now - last) >= _FRAME_DT:
                k = 0.55 if self._target > self._level else 0.18
                self._level += k * (self._target - self._level)
                self._render(now - self._t0)
                last = now
            time.sleep(0.004)

    def _apply_commands(self) -> None:
        try:
            while True:
                cmd = self._q.get_nowait()
                kind = cmd[0]
                if kind == "level":
                    raw = max(0.0, min(1.0, cmd[1]))
                    self._target = 0.0 if raw < 0.02 else raw
                elif kind in ("listen", "work"):
                    self._mode = "listening" if kind == "listen" else "working"
                    if not self._visible:
                        self._u.ShowWindow(self._hwnd, 4)  # SW_SHOWNOACTIVATE
                        self._visible = True
                elif kind == "hide":
                    if self._visible:
                        self._u.ShowWindow(self._hwnd, 0)  # SW_HIDE
                        self._visible = False
                    self._target = self._level = 0.0
                elif kind == "stop":
                    self._running = False
        except queue.Empty:
            pass

    def _render(self, t: float) -> None:
        img = self._renderer.frame(self._mode, self._level, t)
        buf = _premultiplied_bgra(img)
        ctypes.memmove(self._bits, buf, min(len(buf), self._dib_size))
        w, h = self._renderer.tw, self._renderer.th
        dst = _POINT(self._x, self._y)
        size = _SIZE(w, h)
        src = _POINT(0, 0)
        blend = _BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, SCA=255, AC_SRC_ALPHA
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
        except Exception:
            pass


# Pick the backend: layered window on Windows, tkinter elsewhere.
Overlay = _WinOverlay if sys.platform == "win32" else TkOverlay
