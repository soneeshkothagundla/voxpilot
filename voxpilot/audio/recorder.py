"""Audio recording and hotkey handling for VoxPilot.

Provides a push-to-talk audio recorder backed by ``sounddevice`` and a
``HotkeyController`` that listens for keyboard events via ``pynput`` and runs
the supplied utterance callback off the listener thread so the callback never
blocks the (latency-sensitive) pynput callback.
"""

from __future__ import annotations

import sys
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
        self.level: float = 0.0

    def _cb(self, indata, frames, time_info, status) -> None:
        """sounddevice callback: append a copy of the block and update the level."""
        with self._lock:
            self._frames.append(indata.copy())
        try:
            peak = float(np.max(np.abs(indata))) if indata.size else 0.0
        except Exception:
            peak = 0.0
        # Scaled peak amplitude (0..1) used to render the live mic meter.
        self.level = min(1.0, peak * 2.0)

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
        self.level = 0.0
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
        self.level = 0.0
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
        show_meter: bool = True,
        on_listen_start: Callable[[], None] | None = None,
        on_level: Callable[[float], None] | None = None,
        on_listen_stop: Callable[[], None] | None = None,
    ) -> None:
        """Initialize the controller.

        Args:
            hotkey: Hotkey configuration (mode and PTT key).
            recorder: The recorder to start/stop.
            on_utterance: Callback invoked with captured audio after each
                utterance; executed on a daemon thread.
            show_meter: Render the terminal mic-level bar (ignored when
                ``on_level`` is supplied).
            on_listen_start: Optional hook fired when recording begins (e.g.
                show an on-screen overlay).
            on_level: Optional hook fired ~16x/sec with the live mic level
                (0.0-1.0) while recording; when set, the terminal bar is skipped.
            on_listen_stop: Optional hook fired when recording ends (e.g. hide
                the overlay).
        """
        self.hotkey = hotkey
        self.recorder = recorder
        self.on_utterance = on_utterance
        self._ptt_key = resolve_key(hotkey.ptt_key)
        self._listener: keyboard.Listener | None = None
        self._recording = False
        self._lock = threading.Lock()
        self.show_meter = show_meter
        self.on_listen_start = on_listen_start
        self.on_level = on_level
        self.on_listen_stop = on_listen_stop
        self._meter_stop = threading.Event()
        self._meter_thread: threading.Thread | None = None

    def _dispatch(self, audio: np.ndarray) -> None:
        """Run the utterance callback on a daemon thread."""
        thread = threading.Thread(target=self.on_utterance, args=(audio,), daemon=True)
        thread.start()

    def _start_meter(self) -> None:
        """Fire the listen-start hook and begin live-level rendering."""
        if self.on_listen_start is not None:
            try:
                self.on_listen_start()
            except Exception:  # noqa: BLE001 - a UI hook must never break recording
                pass
        if self.on_level is None and not self.show_meter:
            return
        self._meter_stop.clear()
        self._meter_thread = threading.Thread(target=self._meter_loop, daemon=True)
        self._meter_thread.start()

    def _stop_meter(self) -> None:
        """Stop live-level rendering and fire the listen-stop hook."""
        if self._meter_thread is not None:
            self._meter_stop.set()
            self._meter_thread.join(timeout=0.4)
            self._meter_thread = None
        if self.on_listen_stop is not None:
            try:
                self.on_listen_stop()
            except Exception:  # noqa: BLE001
                pass

    def _meter_loop(self) -> None:
        """Push the live mic level to the ``on_level`` hook, or a stdout bar."""
        use_cb = self.on_level is not None
        if not use_cb:
            key = self.hotkey.ptt_key.upper()
            sys.stdout.write(f"\n  Listening - speak now (release {key} to send)\n")
            sys.stdout.flush()
        while not self._meter_stop.is_set():
            level = max(0.0, min(1.0, float(getattr(self.recorder, "level", 0.0) or 0.0)))
            if use_cb:
                try:
                    self.on_level(level)
                except Exception:  # noqa: BLE001
                    pass
            else:
                width = 28
                filled = int(level * width)
                bar = "#" * filled + "-" * (width - filled)
                sys.stdout.write(f"\r  mic [{bar}]")
                sys.stdout.flush()
            self._meter_stop.wait(0.06)
        if not use_cb:
            width = 28
            sys.stdout.write(f"\r  mic [{'-' * width}]  captured.\n")
            sys.stdout.flush()

    def _matches(self, key) -> bool:
        """Return True if the pressed/released key is the PTT key."""
        return key == self._ptt_key

    def _on_press(self, key) -> None:
        """Handle a key press according to the configured mode."""
        if not self._matches(key):
            return
        if self.hotkey.mode == "toggle":
            with self._lock:
                toggle_on = not self._recording
                self._recording = toggle_on
            if toggle_on:
                self.recorder.start()
                self._start_meter()
                return
            audio = self.recorder.stop()
            self._stop_meter()
            self._dispatch(audio)
            return
        # push_to_talk
        with self._lock:
            if self._recording:
                return
            self._recording = True
        self.recorder.start()
        self._start_meter()

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
        self._stop_meter()
        self._dispatch(audio)

    def start(self) -> None:
        """Build and start the (non-blocking) keyboard listener."""
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()

    def stop(self) -> None:
        """Stop the keyboard listener and live meter if running."""
        self._stop_meter()
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
