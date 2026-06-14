"""Text-to-speech and status feedback for VoxPilot.

``pyttsx3`` is not thread-safe and must be driven from a single dedicated
worker thread. :class:`Feedback` therefore runs all speech through a daemon
worker that reads from a queue. The library is imported lazily inside the
worker so that importing this module never fails in headless/test
environments, and any failure to initialise TTS silently degrades to
print-only output.
"""

from __future__ import annotations

import queue
import sys
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import FeedbackConfig

#: Sentinel enqueued to ask the worker thread to stop.
_STOP = None


class Feedback:
    """Spoken and printed feedback to the user.

    Args:
        cfg: The resolved :class:`FeedbackConfig`.
    """

    def __init__(self, cfg: FeedbackConfig) -> None:
        self.cfg = cfg
        # Items are either a str to speak, a (str, Event) pair for blocking
        # speech (the worker sets the Event when done), or the _STOP sentinel.
        self._queue: queue.Queue[object] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._tts_ok: bool = False
        #: Optional sinks so a GUI (tray/overlay) can mirror feedback. Set by the
        #: caller after construction; both are best-effort and may be None.
        self.status_sink: Callable[[str], None] | None = None
        self.message_sink: Callable[[str], None] | None = None

        if cfg.tts:
            self._tts_ok = True
            self._worker = threading.Thread(
                target=self._run_worker, name="voxpilot-tts", daemon=True
            )
            self._worker.start()

    # ------------------------------------------------------------------ #
    # Worker thread
    # ------------------------------------------------------------------ #

    def _run_worker(self) -> None:
        """Worker loop: lazily init pyttsx3 and speak queued text."""
        engine = None
        try:
            import pyttsx3  # lazy import inside the worker thread

            engine = pyttsx3.init()
            engine.setProperty("rate", self.cfg.tts_rate)
            engine.setProperty("volume", self.cfg.tts_volume)
        except Exception:  # noqa: BLE001 - degrade to print-only on any error
            self._tts_ok = False
            # Drain queue until the stop sentinel so producers never block.
            self._drain_until_stop()
            return

        while True:
            item = self._queue.get()
            if item is _STOP:
                break
            text, done = item if isinstance(item, tuple) else (item, None)
            try:
                engine.say(text)
                engine.runAndWait()
            except Exception:  # noqa: BLE001 - one bad utterance must not kill TTS
                self._tts_ok = False
            finally:
                if done is not None:
                    done.set()  # release a say_sync() waiter

        try:
            engine.stop()
        except Exception:  # noqa: BLE001
            pass

    def _drain_until_stop(self) -> None:
        """Consume queued items until the stop sentinel (used when TTS dies)."""
        while True:
            try:
                item = self._queue.get()
            except Exception:  # noqa: BLE001
                return
            if item is _STOP:
                return
            if isinstance(item, tuple) and item[1] is not None:
                item[1].set()  # never leave a say_sync() waiter hanging

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def say(self, text: str) -> None:
        """Speak (if enabled) and optionally print a message.

        Args:
            text: The message to communicate to the user.
        """
        if self.cfg.verbose:
            print(f"VoxPilot: {text}")
        if self.message_sink is not None:
            try:
                self.message_sink(text)
            except Exception:  # noqa: BLE001
                pass
        if self.cfg.tts and self._tts_ok and self._worker is not None:
            try:
                self._queue.put(text)
            except Exception:  # noqa: BLE001
                pass

    def say_sync(self, text: str, timeout: float = 12.0) -> None:
        """Speak and BLOCK until the utterance finishes (used for wake greetings).

        Falls back to a plain print when TTS is unavailable so callers never hang.

        Args:
            text: The message to speak.
            timeout: Max seconds to wait for speech to finish before returning.
        """
        if not text:
            return
        if self.cfg.verbose:
            print(f"VoxPilot: {text}")
        if self.message_sink is not None:
            try:
                self.message_sink(text)
            except Exception:  # noqa: BLE001
                pass
        if not (self.cfg.tts and self._tts_ok and self._worker is not None):
            return
        done = threading.Event()
        try:
            self._queue.put((text, done))
        except Exception:  # noqa: BLE001
            return
        done.wait(timeout)

    def status(self, state: str) -> None:
        """Print a one-line status indicator when verbose.

        Args:
            state: One of IDLE, LISTENING, THINKING, ACTING, DONE.
        """
        if self.cfg.verbose:
            print(f"[{state}]", file=sys.stderr)
        if self.status_sink is not None:
            try:
                self.status_sink(state)
            except Exception:  # noqa: BLE001
                pass

    def shutdown(self) -> None:
        """Stop the worker thread, draining any pending speech briefly."""
        if self._worker is None:
            return
        try:
            self._queue.put(_STOP)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._worker.join(timeout=5.0)
        except Exception:  # noqa: BLE001
            pass
        self._worker = None
