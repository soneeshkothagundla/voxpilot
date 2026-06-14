"""Tests for :mod:`voxpilot.audio.wakeword`.

The openWakeWord model is replaced with a tiny fake so the worker's state
machine (wake-word detection -> energy-based command capture -> dispatch) is
exercised deterministically without a microphone, audio stream, or model
download. Frames are fed directly onto the listener's internal queue and the
worker loop is run on a thread.
"""

from __future__ import annotations

import threading

import numpy as np

from voxpilot.audio import WakeWordListener
from voxpilot.config import HotkeyConfig


class _FakeModel:
    """Reports a wake hit on its first ``predict`` call, then silence."""

    def __init__(self) -> None:
        """Start with a zero call count."""
        self.calls = 0

    def predict(self, frame: np.ndarray) -> dict:
        """Return a high score once (to trigger the wake), then low scores."""
        self.calls += 1
        return {"hey_jarvis": 0.9 if self.calls == 1 else 0.0}

    def reset(self) -> None:
        """No-op reset to match the openWakeWord interface."""


def _loud() -> np.ndarray:
    """An 80 ms int16 frame above the voiced threshold."""
    return (np.ones(1280, dtype=np.int16) * 6000).copy()


def _silent() -> np.ndarray:
    """An 80 ms int16 frame of silence."""
    return np.zeros(1280, dtype=np.int16)


def _run_worker_with(frames: list[np.ndarray]) -> WakeWordListener:
    """Build a listener wired to a fake model, enqueue frames, run the worker."""
    wl = WakeWordListener(HotkeyConfig(), lambda audio: None)
    wl._model = _FakeModel()
    for frame in frames:
        wl._queue.put(frame)
    return wl


def test_wake_then_speech_dispatches_command() -> None:
    """After the wake word, voiced audio followed by silence dispatches audio."""
    captured: list[np.ndarray] = []
    woke: list[bool] = []
    done = threading.Event()

    wl = WakeWordListener(
        HotkeyConfig(),
        lambda audio: (captured.append(audio), done.set()),
        on_wake=lambda: woke.append(True),
    )
    wl._model = _FakeModel()

    # frame 1 -> predict() fires the wake; then 5 voiced + 11 silent frames.
    wl._queue.put(_silent())
    for _ in range(5):
        wl._queue.put(_loud())
    for _ in range(11):
        wl._queue.put(_silent())

    worker = threading.Thread(target=wl._run_worker, daemon=True)
    worker.start()
    try:
        assert done.wait(2.0), "command was not dispatched after wake + trailing silence"
    finally:
        wl._stop.set()
        worker.join(timeout=1.0)

    assert woke == [True]
    audio = captured[0]
    assert audio.dtype == np.float32
    assert audio.size > 0


def test_drain_after_wake_discards_pending_audio() -> None:
    """With drain_after_wake, audio queued at wake time (e.g. a greeting) is dropped."""
    captured: list[np.ndarray] = []

    wl = WakeWordListener(
        HotkeyConfig(),
        lambda audio: captured.append(audio),
        drain_after_wake=True,
    )
    wl._model = _FakeModel()

    # All frames are already queued when the wake fires, so the post-wake drain
    # clears the "command" frames; nothing should be captured or dispatched.
    wl._queue.put(_silent())  # frame 1 -> wake
    for _ in range(5):
        wl._queue.put(_loud())
    for _ in range(11):
        wl._queue.put(_silent())

    worker = threading.Thread(target=wl._run_worker, daemon=True)
    worker.start()
    try:
        threading.Event().wait(0.6)
    finally:
        wl._stop.set()
        worker.join(timeout=1.0)

    assert captured == []


def test_wake_with_no_speech_does_not_dispatch() -> None:
    """Hearing the wake word but no command must not dispatch an utterance."""
    captured: list[np.ndarray] = []

    wl = WakeWordListener(HotkeyConfig(), lambda audio: captured.append(audio))
    wl._model = _FakeModel()

    # Wake, then only silence for longer than the no-speech window (~3 s).
    wl._queue.put(_silent())
    for _ in range(wl._no_speech_frames + 2):
        wl._queue.put(_silent())

    worker = threading.Thread(target=wl._run_worker, daemon=True)
    worker.start()
    try:
        # Give the worker time to consume every frame and reach the no-speech exit.
        threading.Event().wait(0.8)
    finally:
        wl._stop.set()
        worker.join(timeout=1.0)

    assert captured == []
