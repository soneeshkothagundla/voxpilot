"""Contract tests for :mod:`voxpilot.stt`.

These verify the abstract backend contract and the :func:`create_stt` factory
without downloading any models or hitting the network. In particular,
``warm_up``/``transcribe`` are never called on the real backends.
"""

from __future__ import annotations

from voxpilot.config import Secrets, STTConfig
from voxpilot.stt import STTBackend, create_stt
from voxpilot.stt.whisper_local import FasterWhisperSTT
from voxpilot.stt.whisper_openai import OpenAIWhisperSTT


class DummySTT(STTBackend):
    """A trivial backend that returns a canned transcript."""

    def __init__(self, canned: str = "hello world") -> None:
        """Store the canned transcript to return."""
        self.canned = canned

    def transcribe(self, audio) -> str:  # noqa: ANN001
        """Return the canned transcript regardless of input."""
        return self.canned


def test_dummy_backend_satisfies_contract() -> None:
    """A subclass returns its transcript and inherits a callable ``warm_up``."""
    stt = DummySTT("the quick brown fox")
    assert isinstance(stt, STTBackend)
    assert stt.transcribe(None) == "the quick brown fox"
    # warm_up is a no-op on the base class but must be callable.
    assert stt.warm_up() is None


def test_create_stt_faster_whisper() -> None:
    """The factory builds a lazy :class:`FasterWhisperSTT` (no model load)."""
    stt_cfg = STTConfig(backend="faster_whisper", model="base")
    secrets = Secrets()
    backend = create_stt(stt_cfg, secrets)
    assert isinstance(backend, FasterWhisperSTT)
    assert isinstance(backend, STTBackend)


def test_create_stt_openai() -> None:
    """The factory builds an :class:`OpenAIWhisperSTT` with a dummy key."""
    stt_cfg = STTConfig(backend="openai", openai_model="whisper-1")
    secrets = Secrets(openai_api_key="sk-dummy")
    backend = create_stt(stt_cfg, secrets)
    assert isinstance(backend, OpenAIWhisperSTT)
    assert isinstance(backend, STTBackend)
