"""User-feedback subsystem for VoxPilot.

Exposes :class:`Feedback`, a thread-safe text-to-speech and status reporter
that degrades gracefully when TTS is disabled or unavailable.
"""

from .tts import Feedback

__all__ = ["Feedback"]
