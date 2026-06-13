"""Speech-to-text backend interface and factory.

Defines the abstract :class:`STTBackend` contract and the :func:`create_stt`
factory. The factory imports concrete backends lazily so that importing this
module (or the ``voxpilot.stt`` package) does not import heavy optional
dependencies like ``faster_whisper``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..config import Secrets, STTConfig

__all__ = ["STTBackend", "create_stt"]


class STTBackend(ABC):
    """Abstract base class for speech-to-text backends."""

    @abstractmethod
    def transcribe(self, audio: object) -> str:
        """Transcribe audio to text.

        Args:
            audio: Either an ``np.ndarray`` (float32 mono 16 kHz) or a path
                (``str``) to an audio file.

        Returns:
            The transcribed text (empty string if nothing was recognized).
        """
        raise NotImplementedError

    def warm_up(self) -> None:  # noqa: B027 - intentional optional no-op hook
        """Optionally pre-load the model so the first transcription is fast."""


def create_stt(stt_cfg: STTConfig, secrets: Secrets) -> STTBackend:
    """Create a speech-to-text backend from configuration.

    Concrete backend modules are imported lazily here so that importing the
    ``voxpilot.stt`` package never imports ``faster_whisper``.

    Args:
        stt_cfg: Speech-to-text configuration.
        secrets: Resolved secrets (used for API keys).

    Returns:
        An :class:`STTBackend` instance.

    Raises:
        ValueError: If ``stt_cfg.backend`` is not a recognized backend.
    """
    backend = stt_cfg.backend
    if backend == "faster_whisper":
        from .whisper_local import FasterWhisperSTT

        return FasterWhisperSTT(
            model=stt_cfg.model,
            device=stt_cfg.device,
            compute_type=stt_cfg.compute_type,
            language=stt_cfg.language,
        )
    if backend == "openai":
        from .whisper_openai import OpenAIWhisperSTT

        return OpenAIWhisperSTT(
            api_key=secrets.openai_api_key,
            model=stt_cfg.openai_model,
        )
    raise ValueError(f"Unknown STT backend: {backend!r}")
