"""Coordinate scaling and DPI awareness helpers for VoxPilot.

This module is intentionally dependency-light: it uses only the standard library
(plus ``ctypes`` on Windows for DPI awareness). It MUST NOT import pyautogui, mss
or PIL so that pure-logic consumers and tests can import it without any GUI or
screen-capture stack present.

The model (Claude's computer-use tool) sees a *downscaled* screenshot and returns
coordinates in that scaled space. We scale those coordinates back *up* to real
(native) pixels before driving the mouse/keyboard, and scale native coordinates
*down* when reporting positions back to the model. The origin is the top-left of
the primary display.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class ScaleResult:
    """Result of computing a downscale factor for a screenshot.

    Attributes:
        scale: Multiplicative factor mapping native pixels to scaled pixels
            (``scaled = native * scale``). Always ``<= 1.0`` (never upscale).
        scaled_width: Width, in pixels, of the downscaled screenshot.
        scaled_height: Height, in pixels, of the downscaled screenshot.
        native_width: Width, in pixels, of the real display.
        native_height: Height, in pixels, of the real display.
    """

    scale: float
    scaled_width: int
    scaled_height: int
    native_width: int
    native_height: int


def compute_scale(
    native_width: int,
    native_height: int,
    target_width: int,
    target_height: int,
) -> ScaleResult:
    """Compute the downscale factor to fit a native screen into a target box.

    The scale is the largest factor that fits the native dimensions inside the
    target box without exceeding it, capped at ``1.0`` so the screenshot is never
    upscaled (which would waste tokens without adding detail).

    Args:
        native_width: Real display width in pixels (must be > 0).
        native_height: Real display height in pixels (must be > 0).
        target_width: Target box width in pixels (must be > 0).
        target_height: Target box height in pixels (must be > 0).

    Returns:
        A :class:`ScaleResult` describing the chosen scale and resulting dims.

    Raises:
        ValueError: If any of the supplied dimensions is not strictly positive.
    """
    if native_width <= 0 or native_height <= 0:
        raise ValueError(f"native dimensions must be > 0, got {native_width}x{native_height}")
    if target_width <= 0 or target_height <= 0:
        raise ValueError(f"target dimensions must be > 0, got {target_width}x{target_height}")

    scale = min(
        target_width / native_width,
        target_height / native_height,
        1.0,
    )
    scaled_width = max(1, round(native_width * scale))
    scaled_height = max(1, round(native_height * scale))
    return ScaleResult(
        scale=scale,
        scaled_width=scaled_width,
        scaled_height=scaled_height,
        native_width=native_width,
        native_height=native_height,
    )


def to_screen(x: float, y: float, scale: ScaleResult) -> tuple[int, int]:
    """Convert model (scaled) coordinates to real (native) screen pixels.

    The result is clamped to the valid pixel range of the native display so a
    slightly out-of-bounds coordinate from the model never drives the cursor off
    screen (which could also trip the pyautogui fail-safe).

    Args:
        x: Horizontal coordinate in the scaled/model space.
        y: Vertical coordinate in the scaled/model space.
        scale: The :class:`ScaleResult` describing the active scaling.

    Returns:
        A ``(x, y)`` tuple of integer native-pixel coordinates, clamped to
        ``[0, native_width - 1]`` and ``[0, native_height - 1]``.
    """
    real_x = round(x / scale.scale)
    real_y = round(y / scale.scale)
    real_x = max(0, min(real_x, scale.native_width - 1))
    real_y = max(0, min(real_y, scale.native_height - 1))
    return int(real_x), int(real_y)


def to_model(x: float, y: float, scale: ScaleResult) -> tuple[int, int]:
    """Convert real (native) screen coordinates to model (scaled) coordinates.

    Args:
        x: Horizontal coordinate in native-pixel space.
        y: Vertical coordinate in native-pixel space.
        scale: The :class:`ScaleResult` describing the active scaling.

    Returns:
        A ``(x, y)`` tuple of integer coordinates in the scaled/model space.
    """
    return round(x * scale.scale), round(y * scale.scale)


def ensure_dpi_awareness() -> None:
    """Make the current process DPI-aware on Windows (no-op elsewhere).

    This must run BEFORE pyautogui/mss are imported so that they observe physical
    pixels and agree on the display geometry. We first try the per-monitor v2
    aware mode via ``shcore.SetProcessDpiAwareness(2)`` and fall back to the
    legacy ``user32.SetProcessDPIAware()``. All failures are swallowed: DPI
    awareness is best-effort and must never crash startup.
    """
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:  # noqa: BLE001 - best-effort, fall through to legacy API
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001 - best-effort, never raise
        pass
