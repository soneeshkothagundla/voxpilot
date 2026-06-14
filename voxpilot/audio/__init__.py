"""Audio capture and hotkey control for VoxPilot.

Exposes the push-to-talk recorder, the hotkey controller that drives it, and the
hands-free wake-word listener used by Jarvis mode.
"""

from .recorder import HotkeyController, PushToTalkRecorder, resolve_key
from .wakeword import WakeWordListener

__all__ = [
    "HotkeyController",
    "PushToTalkRecorder",
    "WakeWordListener",
    "resolve_key",
]
