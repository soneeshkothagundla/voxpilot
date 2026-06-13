"""Speech-to-text backends for VoxPilot.

Importing this package is lightweight: the concrete backends (and their heavy
optional dependencies such as ``faster_whisper``) are imported lazily inside
:func:`create_stt`, so ``import voxpilot.stt`` does not pull in those libraries.
"""

from .base import STTBackend, create_stt

__all__ = ["STTBackend", "create_stt"]
