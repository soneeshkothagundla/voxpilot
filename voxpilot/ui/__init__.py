"""UI subsystem for VoxPilot.

Exposes :class:`StatusIndicator`, a lightweight, dependency-optional status
display (console, plus an optional system-tray icon if ``pystray`` is
installed).
"""

from .tray import StatusIndicator

__all__ = ["StatusIndicator"]
