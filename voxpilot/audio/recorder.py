"""Audio recording and hotkey handling for VoxPilot.

Provides a push-to-talk audio recorder backed by ``sounddevice`` and a
``HotkeyController`` that listens for keyboard events via ``pynput`` and runs
the supplied utterance callback off the listener thread so the callback never
blocks the (latency-sensitive) pynput callback.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import numpy as np
import sounddevice as sd
from pynput import keyboard

from ..config import HotkeyConfig

__all__ = ["HotkeyController", "PushToTalkRecorder", "resolve_key"]


def resolve_key(name: str) -> keyboard.Key | keyboard.KeyCode:
    """Resolve a config key string to a pynput key object for comparison.

    Special names (e.g. ``ctrl_r``, ``esc``, ``f1``, ``space``) are looked up
    on :class:`pynput.keyboard.Key`. A single printable character is converted
    to a :class:`pynput.keyboard.KeyCode`.

    Args:
        name: The key name from configuration.

    Returns:
        A ``Key`` or ``KeyCode`` instance suitable for equality comparison.

    Raises:
        ValueError: If the name cannot be resolved to a key.
    """
    if not name:
        raise ValueError("Empty key name cannot be resolved.")
    special = getattr(keyboard.Key, name, None)
    if special is not None:
        return special
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    raise ValueError(f"Unresolvable key name: {name!r}")


class PushToTalkRecorder:
    """Records microphone audio as float32 mono at 16 kHz.

    The underlying :class:`sounddevice.InputStream` is created lazily on the
    first :meth:`start` call so that constructing the recorder on a machine
    without an audio device does not fail until recording is actually used.
    """

    def __init__(self, sample_rate: int = 16000, channels: int = 1) -> None:
        """Initialize the recorder.

        Args:
            sample_rate: Capture sample rate in Hz (default 16000).
            channels: Number of input channels (default 1, mono).
        """
        self.sample_rate = sample_rate
        self.channels = channels
        self._frames: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        self._active = False

    def _cb(self, indata, frames, time_info, status) -> None:
        """sounddevice callback: append a copy of the incoming audio block."""
        with self._lock:
            self._frames.append(indata.copy())

    def _ensure_stream(self) -> None:
        """Create the input stream lazily on first use."""
        if self._stream is None:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="float32",
                callback=self._cb,
            )

    def start(self) -> None:
        """Begin recording, clearing any previously captured frames."""
        with self._lock:
            self._frames = []
        self._ensure_stream()
        assert self._stream is not None
        self._stream.start()
        self._active = True

    def stop(self) -> np.ndarray:
        """Stop recording and return the captured audio.

        Returns:
            A 1-D ``np.float32`` array (mono, 16 kHz, values in [-1, 1]).
            An empty array is returned if nothing was captured.
        """
        if self._stream is not None:
            self._stream.stop()
        self._active = False
        with self._lock:
            frames = self._frames
            self._frames = []
        if not frames:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(frames, axis=0)
        audio = audio.reshape(-1).astype(np.float32, copy=False)
        return audio

    @property
    def active(self) -> bool:
        """Whether the recorder is currently capturing audio."""
        return self._active

    def close(self) -> None:
        """Stop and release the underlying audio stream, if any."""
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._active = False


class HotkeyController:
    """Drives a :class:`PushToTalkRecorder` from global keyboard events.

    Supports two modes:

    * ``push_to_talk``: records while the configured key is held; releasing the
      key stops recording and dispatches the audio to ``on_utterance``.
    * ``toggle``: a key press toggles recording on/off; turning recording off
      dispatches the audio to ``on_utterance``.

    The ``on_utterance`` callback is always run in a daemon thread so blocking
    work (transcription, the agent loop) never freezes the pynput listener.
    """

    def __init__(
        self,
        hotkey: HotkeyConfig,
        recorder: PushToTalkRecorder,
        on_utterance: Callable[[np.ndarray], None],
    ) -> None:
        """Initialize the controller.

        Args:
            hotkey: Hotkey configuration (mode and PTT key).
            recorder: The recorder to start/stop.
            on_utterance: Callback invoked with captured audio after each
                utterance; executed on a daemon thread.
        """
        self.hotkey = hotkey
        self.recorder = recorder
        self.on_utterance = on_utterance
        self._ptt_key = resolve_key(hotkey.ptt_key)
        self._listener: keyboard.Listener | None = None
        self._recording = False
        self._lock = threading.Lock()

    def _dispatch(self, audio: np.ndarray) -> None:
        """Run the utterance callback on a daemon thread."""
        thread = threading.Thread(target=self.on_utterance, args=(audio,), daemon=True)
        thread.start()

    def _matches(self, key) -> bool:
        """Return True if the pressed/released key is the PTT key."""
        return key == self._ptt_key

    def _on_press(self, key) -> None:
        """Handle a key press according to the configured mode."""
        if not self._matches(key):
            return
        if self.hotkey.mode == "toggle":
            with self._lock:
                if not self._recording:
                    self._recording = True
                    self.recorder.start()
                    return
                self._recording = False
            audio = self.recorder.stop()
            self._dispatch(audio)
            return
        # push_to_talk
        with self._lock:
            if self._recording:
                return
            self._recording = True
        self.recorder.start()

    def _on_release(self, key) -> None:
        """Handle a key release (only meaningful for push-to-talk)."""
        if self.hotkey.mode == "toggle":
            return
        if not self._matches(key):
            return
        with self._lock:
            if not self._recording:
                return
            self._recording = False
        audio = self.recorder.stop()
        self._dispatch(audio)

    def start(self) -> None:
        """Build and start the (non-blocking) keyboard listener."""
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()

    def stop(self) -> None:
        """Stop the keyboard listener if it is running."""
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
