"""Local speech-to-text using faster-whisper.

The ``faster_whisper`` model is loaded lazily (in :meth:`FasterWhisperSTT.warm_up`,
or on the first :meth:`FasterWhisperSTT.transcribe` call) so importing this
module never imports ``faster_whisper`` or downloads a model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from .base import STTBackend

if TYPE_CHECKING:  # pragma: no cover - typing only
    from faster_whisper import WhisperModel

__all__ = ["FasterWhisperSTT"]


class FasterWhisperSTT(STTBackend):
    """Speech-to-text backend backed by faster-whisper.

    The model is loaded lazily so that constructing this backend is cheap and
    side-effect free.
    """

    def __init__(
        self,
        model: str = "base",
        device: str = "auto",
        compute_type: str = "auto",
        language: str | None = None,
    ) -> None:
        """Initialize the backend without loading the model.

        Args:
            model: faster-whisper model size or path (e.g. ``"base"``).
            device: ``"auto"``, ``"cpu"``, or ``"cuda"``.
            compute_type: ``"auto"`` or an explicit compute type
                (e.g. ``"int8"``, ``"float16"``).
            language: Optional language hint (e.g. ``"en"``); ``None`` to
                auto-detect.
        """
        self.model_size = model
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self._model: WhisperModel | None = None

    def _resolve_device(self) -> str:
        """Resolve the effective device string."""
        if self.device == "auto":
            return "cpu"
        return self.device

    def _resolve_compute_type(self, device: str) -> str:
        """Resolve the effective compute type for the given device."""
        if self.compute_type != "auto":
            return self.compute_type
        return "int8" if device == "cpu" else "float16"

    def warm_up(self) -> None:
        """Load the faster-whisper model into memory."""
        if self._model is not None:
            return
        device = self._resolve_device()
        compute_type = self._resolve_compute_type(device)
        from faster_whisper import WhisperModel

        self._model = WhisperModel(self.model_size, device=device, compute_type=compute_type)

    def transcribe(self, audio: np.ndarray | str) -> str:
        """Transcribe audio to text.

        Args:
            audio: A float32 mono 16 kHz ``np.ndarray`` or a path to an audio
                file.

        Returns:
            The transcribed text, stripped (empty string for empty audio).
        """
        if isinstance(audio, np.ndarray) and audio.size == 0:
            return ""
        if self._model is None:
            self.warm_up()
        assert self._model is not None
        segments, _info = self._model.transcribe(
            audio,
            beam_size=5,
            language=self.language,
            vad_filter=True,
        )
        return "".join(segment.text for segment in segments).strip()
