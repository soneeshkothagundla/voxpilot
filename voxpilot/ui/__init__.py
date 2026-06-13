"""UI subsystem for VoxPilot.

Exposes the on-screen recording :class:`Overlay`, the :class:`TrayIcon` used in
windowed mode, and the lighter :class:`StatusIndicator`. All are
dependency-optional and degrade gracefully when GUI libraries are unavailable.
"""

from .overlay import Overlay
from .tray import StatusIndicator, TrayIcon

__all__ = ["Overlay", "StatusIndicator", "TrayIcon"]
