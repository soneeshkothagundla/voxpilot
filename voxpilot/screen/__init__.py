"""Screen subsystem for VoxPilot.

Provides coordinate scaling, screenshot capture, and action execution against the
real screen. Heavy/optional libraries (pyautogui, mss, PIL) are imported only by
the modules that need them so importing this package stays cheap and side-effect
free.
"""

from .scaling import (
    ScaleResult,
    compute_scale,
    ensure_dpi_awareness,
    to_model,
    to_screen,
)

__all__ = [
    "ScaleResult",
    "compute_scale",
    "to_screen",
    "to_model",
    "ensure_dpi_awareness",
]
