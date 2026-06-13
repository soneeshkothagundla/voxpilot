"""Audio capture and hotkey control for VoxPilot.

Exposes the push-to-talk recorder and the hotkey controller that drives it.
"""

from .recorder import HotkeyController, PushToTalkRecorder, resolve_key

__all__ = ["HotkeyController", "PushToTalkRecorder", "resolve_key"]
