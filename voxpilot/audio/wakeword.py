"""Hands-free wake-word listening for VoxPilot's Jarvis mode.

Continuously listens to the microphone for a wake phrase ("Hey Jarvis") using
`openWakeWord <https://github.com/dscripka/openWakeWord>`_. On detection it
records the spoken command using simple energy-based endpointing (stop after a
short trailing silence) and dispatches the captured audio to ``on_utterance`` -
the same callback shape :class:`~voxpilot.audio.recorder.HotkeyController` uses,
so the rest of the pipeline (transcribe -> agent loop) is unchanged.

Inference runs on a dedicated worker thread fed by a queue so the latency-
sensitive ``sounddevice`` callback never blocks on model inference.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable

import numpy as np
import sounddevice as sd

from ..config import HotkeyConfig

__all__ = ["WakeWordListener"]

#: openWakeWord expects 80 ms chunks of 16 kHz int16 audio.
_FRAME_SAMPLES = 1280
_FRAME_SECONDS = _FRAME_SAMPLES / 16000.0


class WakeWordListener:
    """Listens for a wake word, then captures and dispatches a spoken command.

    The control surface mirrors :class:`~voxpilot.audio.recorder.HotkeyController`:
    ``on_utterance`` receives the captured float32 audio (run on a daemon thread),
    and optional ``on_listen_start`` / ``on_level`` / ``on_listen_stop`` hooks
    drive the overlay exactly as push-to-talk does. ``on_wake`` fires the instant
    the wake word is detected (before command capture begins).
    """

    def __init__(
        self,
        hotkey: HotkeyConfig,
        on_utterance: Callable[[np.ndarray], None],
        *,
        on_wake: Callable[[], None] | None = None,
        on_listen_start: Callable[[], None] | None = None,
        on_level: Callable[[float], None] | None = None,
        on_listen_stop: Callable[[], None] | None = None,
        sample_rate: int = 16000,
        end_silence_s: float = 0.8,
        max_command_s: float = 15.0,
        no_speech_s: float = 3.0,
        speech_level: float = 0.02,
        drain_after_wake: bool = False,
    ) -> None:
        """Initialize the wake-word listener.

        Args:
            hotkey: Hotkey configuration (provides ``wake_word`` / ``wake_threshold``).
            on_utterance: Callback invoked with captured audio; run on a daemon thread.
            on_wake: Optional hook fired when the wake word is detected.
            on_listen_start: Optional hook fired when command recording begins.
            on_level: Optional hook fired ~12x/sec with the live mic level (0-1).
            on_listen_stop: Optional hook fired when command recording ends.
            sample_rate: Capture sample rate; must be 16000 for openWakeWord.
            end_silence_s: Trailing silence that ends a command once speech started.
            max_command_s: Hard cap on a single command's length.
            no_speech_s: Abort capture if no speech is heard within this window.
            speech_level: Mic level above which a frame counts as voiced.
            drain_after_wake: Discard queued audio right after the wake word
                fires, before command capture. Use this when ``on_wake`` speaks a
                greeting so the spoken reply is not captured as the command.
        """
        self.hotkey = hotkey
        self.on_utterance = on_utterance
        self.on_wake = on_wake
        self.on_listen_start = on_listen_start
        self.on_level = on_level
        self.on_listen_stop = on_listen_stop
        self.sample_rate = sample_rate
        self.wake_word = getattr(hotkey, "wake_word", "hey_jarvis")
        self.threshold = float(getattr(hotkey, "wake_threshold", 0.5))
        self._end_silence_frames = max(1, int(end_silence_s / _FRAME_SECONDS))
        self._max_frames = max(1, int(max_command_s / _FRAME_SECONDS))
        self._no_speech_frames = max(1, int(no_speech_s / _FRAME_SECONDS))
        self._speech_level = speech_level
        self._drain_after_wake = drain_after_wake

        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=256)
        self._stream: sd.InputStream | None = None
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        # Muted while a captured command is being processed (agent loop / TTS), so
        # the agent's own actions or spoken reply cannot re-trigger the wake word.
        self._muted = threading.Event()
        self._model = None
        self._model_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Model / lifecycle
    # ------------------------------------------------------------------ #
    def _ensure_model(self) -> None:
        """Create the openWakeWord model lazily (downloads bundled models once)."""
        if self._model is not None:
            return
        with self._model_lock:
            if self._model is not None:  # another thread won the race
                return
            try:
                from openwakeword.model import Model
            except Exception as exc:  # pragma: no cover - import-time environment issue
                raise ImportError(
                    "openwakeword is required for Jarvis wake-word mode. Install it "
                    "with 'pip install openwakeword' (or 'pip install .[jarvis]')."
                ) from exc
            try:
                from openwakeword.utils import download_models

                download_models([self.wake_word])
            except Exception:  # noqa: BLE001 - models may already be present/offline
                pass
            self._model = Model(wakeword_models=[self.wake_word], inference_framework="onnx")

    def warm_up(self) -> None:
        """Preload the model and run one silent inference to prime it."""
        self._ensure_model()
        try:
            self._model.predict(np.zeros(_FRAME_SAMPLES, dtype=np.int16))  # type: ignore[union-attr]
            self._model.reset()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass

    def _audio_cb(self, indata, frames, time_info, status) -> None:
        """sounddevice callback: queue a mono int16 copy; never block."""
        try:
            mono = indata[:, 0] if indata.ndim == 2 else indata
            self._queue.put_nowait(np.asarray(mono, dtype=np.int16).copy())
        except queue.Full:
            pass  # drop a frame rather than stall the audio thread

    def start(self) -> None:
        """Load the model and begin listening (non-blocking)."""
        self._ensure_model()
        self._stop.clear()
        self._muted.clear()
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=_FRAME_SAMPLES,
            callback=self._audio_cb,
        )
        self._stream.start()
        self._worker = threading.Thread(target=self._run_worker, daemon=True)
        self._worker.start()

    def stop(self) -> None:
        """Stop the audio stream and worker thread."""
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None
        if self._worker is not None:
            self._worker.join(timeout=1.0)
            self._worker = None

    # ------------------------------------------------------------------ #
    # Worker
    # ------------------------------------------------------------------ #
    def _fire(self, hook: Callable[[], None] | None) -> None:
        """Invoke a no-arg UI hook, swallowing any error."""
        if hook is not None:
            try:
                hook()
            except Exception:  # noqa: BLE001 - a UI hook must never break listening
                pass

    def _fire_level(self, level: float) -> None:
        """Push the live mic level to the ``on_level`` hook, swallowing errors."""
        if self.on_level is not None:
            try:
                self.on_level(level)
            except Exception:  # noqa: BLE001
                pass

    def _wake_score(self, scores: dict) -> float:
        """Extract the wake-word confidence from an openWakeWord score dict."""
        if not scores:
            return 0.0
        for name, value in scores.items():
            if self.wake_word in name:
                return float(value)
        return float(max(scores.values()))

    def _dispatch(self, audio: np.ndarray) -> None:
        """Run ``on_utterance`` on a daemon thread; stay muted until it returns."""
        self._muted.set()

        def _run() -> None:
            try:
                self.on_utterance(audio)
            finally:
                self._drain_queue()
                self._muted.clear()

        threading.Thread(target=_run, daemon=True).start()

    def _drain_queue(self) -> None:
        """Discard any frames captured while muted so they cannot re-trigger."""
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    def _run_worker(self) -> None:
        """Consume audio frames: detect the wake word, then capture a command."""
        model = self._model
        assert model is not None
        recording = False
        buf: list[np.ndarray] = []
        speech_started = False
        silence_frames = 0
        voiced_frames = 0

        while not self._stop.is_set():
            try:
                frame = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if self._muted.is_set():
                continue

            if not recording:
                try:
                    scores = model.predict(frame)
                except Exception:  # noqa: BLE001
                    continue
                if self._wake_score(scores) >= self.threshold:
                    recording = True
                    buf = []
                    speech_started = False
                    silence_frames = 0
                    voiced_frames = 0
                    self._fire(self.on_wake)
                    self._fire(self.on_listen_start)
                    if self._drain_after_wake:
                        # Drop audio captured during the (blocking) greeting so
                        # the spoken reply is not recorded as the command.
                        self._drain_queue()
                continue

            # --- command capture (energy-based endpointing) ---
            f32 = frame.astype(np.float32) / 32768.0
            buf.append(f32)
            peak = float(np.max(np.abs(f32))) if f32.size else 0.0
            level = min(1.0, peak * 2.0)
            self._fire_level(level)

            if level >= self._speech_level:
                speech_started = True
                voiced_frames += 1
                silence_frames = 0
            elif speech_started:
                silence_frames += 1

            ended = speech_started and silence_frames >= self._end_silence_frames
            too_long = len(buf) >= self._max_frames
            no_speech = (not speech_started) and len(buf) >= self._no_speech_frames

            if ended or too_long or no_speech:
                self._fire(self.on_listen_stop)
                try:
                    model.reset()
                except Exception:  # noqa: BLE001
                    pass
                recording = False
                if voiced_frames >= 3 and not no_speech:
                    audio = (
                        np.concatenate(buf).astype(np.float32)
                        if buf
                        else np.zeros(0, dtype=np.float32)
                    )
                    self._dispatch(audio)
                # else: heard the wake word but no command -> just resume listening
