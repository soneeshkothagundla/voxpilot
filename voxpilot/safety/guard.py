"""Safety guard for VoxPilot.

The :class:`SafetyGuard` is the single chokepoint between the agent's
intended actions and the real mouse/keyboard. It provides:

* ``dry_run`` / ``confirm_enabled`` flags consumed by the action executor.
* A :class:`threading.Event`-backed abort mechanism (:meth:`abort` /
  :meth:`aborted` / :meth:`reset`).
* Destructive-action detection (:meth:`is_destructive`).
* A confirmation gate (:meth:`confirm`) that never raises (deny on error).
* A rotating JSON action log (:meth:`log_action`).
* A triple-press kill-switch listener built on ``pynput``
  (:meth:`make_kill_listener` / :meth:`start_kill_switch` /
  :meth:`stop_kill_switch`).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from ..config import SafetyConfig

# --------------------------------------------------------------------------- #
# Destructive-action heuristics
# --------------------------------------------------------------------------- #

#: Translated key names (lower-case) that count as destructive when pressed.
_DESTRUCTIVE_KEYS: frozenset[str] = frozenset(
    {"return", "enter", "kp_enter", "delete", "backspace"}
)

#: Risky substrings/patterns for ``type`` actions (compiled, case-insensitive).
_RISKY_TYPE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"rm\s+-rf",
        r"\bsudo\b",
        r"\bdel\s",
        r"\brmdir\b",
        r"\bformat\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r"drop\s+table",
        r"\bmkfs\b",
        r">\s*/dev",
        r"git\s+push\s+--force",
        r"git\s+push\s+-f\b",
        r":wq",
        r"\bdiskpart\b",
        r"\bfdisk\b",
    )
)

# --------------------------------------------------------------------------- #
# Tiny key resolver (duplicated to avoid a circular import with audio.recorder)
# --------------------------------------------------------------------------- #


def _resolve_kill_key(name: str) -> Any:
    """Resolve a config key string to a ``pynput`` key object for comparison.

    Supports special ``keyboard.Key`` attribute names (e.g. ``esc``,
    ``ctrl_r``) and single printable characters. Imports ``pynput`` lazily so
    importing this module never requires the dependency.

    Args:
        name: The configured key name.

    Returns:
        A ``pynput`` ``Key`` or ``KeyCode`` object.

    Raises:
        ValueError: If the key name cannot be resolved.
    """
    from pynput import keyboard  # lazy import

    special = getattr(keyboard.Key, name, None)
    if special is not None:
        return special
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    raise ValueError(f"Unresolvable kill key: {name!r}")


class SafetyGuard:
    """Central safety gate for action execution and aborting.

    Args:
        safety: The resolved :class:`SafetyConfig`.
        log_dir: Directory under which the rotating action log is written.
        feedback: Optional feedback object exposing ``say(text)``.
        recorder: Optional recorder for spoken confirmation (``start``/``stop``).
        stt: Optional speech-to-text backend (``transcribe``).
    """

    def __init__(
        self,
        safety: SafetyConfig,
        log_dir: Path,
        feedback: Any | None = None,
        recorder: Any | None = None,
        stt: Any | None = None,
    ) -> None:
        self.safety = safety
        self.log_dir = Path(log_dir)
        self.feedback = feedback
        self.recorder = recorder
        self.stt = stt

        self.dry_run: bool = safety.dry_run
        self.confirm_enabled: bool = safety.confirm_destructive

        self._abort = threading.Event()

        # Kill-switch state.
        self._kill_listener: Any | None = None
        self._kill_times: deque[float] = deque()
        self._kill_lock = threading.Lock()

        # Rotating JSON action logger.
        self._logger: logging.Logger | None = None
        if safety.action_log:
            self._setup_logger()

    # ------------------------------------------------------------------ #
    # Logging setup
    # ------------------------------------------------------------------ #

    def _setup_logger(self) -> None:
        """Create a per-instance rotating file logger under ``log_dir``."""
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            logger = logging.getLogger(f"voxpilot.safety.actions.{id(self)}")
            logger.setLevel(logging.INFO)
            logger.propagate = False
            handler = logging.handlers.RotatingFileHandler(
                self.log_dir / "actions.log",
                maxBytes=1_000_000,
                backupCount=5,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
            self._logger = logger
        except Exception:  # noqa: BLE001 - logging must never break execution
            self._logger = None

    # ------------------------------------------------------------------ #
    # Abort / kill-switch state
    # ------------------------------------------------------------------ #

    @property
    def aborted(self) -> bool:
        """Whether an abort has been requested."""
        return self._abort.is_set()

    def abort(self) -> None:
        """Request an abort of the current agent loop."""
        self._abort.set()
        if self.feedback is not None:
            try:
                self.feedback.say("Aborting.")
            except Exception:  # noqa: BLE001 - feedback must never crash abort
                pass

    def reset(self) -> None:
        """Clear a previously requested abort so a new task can run."""
        self._abort.clear()

    # ------------------------------------------------------------------ #
    # Destructive-action detection
    # ------------------------------------------------------------------ #

    def is_destructive(self, action_input: dict) -> bool:
        """Return ``True`` when an action is potentially destructive.

        A ``key`` action is destructive when any of its (split) key tokens is
        one of Enter/Return/Delete/Backspace. A ``type`` action is destructive
        when its text matches a risky command pattern.

        Args:
            action_input: The computer-tool action dict.

        Returns:
            ``True`` if the action warrants a confirmation gate.
        """
        action = str(action_input.get("action", "")).lower()

        if action in ("key", "hold_key", "key_down", "key_up"):
            text = action_input.get("text", "") or ""
            for token in re.split(r"[+\s]+", str(text)):
                if token and token.lower() in _DESTRUCTIVE_KEYS:
                    return True
            return False

        if action == "type":
            text = str(action_input.get("text", "") or "")
            return any(p.search(text) for p in _RISKY_TYPE_PATTERNS)

        return False

    # ------------------------------------------------------------------ #
    # Confirmation gate (must never raise)
    # ------------------------------------------------------------------ #

    def confirm(self, description: str) -> bool:
        """Ask the user to confirm a destructive action.

        Honors :attr:`SafetyConfig.confirmation_mode`:

        * ``"onscreen"`` - prompt and read ``input()``.
        * ``"spoken"`` - prompt aloud, record, transcribe; fall back to
          on-screen if recorder/STT are unavailable.
        * ``"both"`` - require an on-screen yes (spoken is best-effort).

        This method never raises; any error results in denial (``False``).

        Args:
            description: Human-readable description of the pending action.

        Returns:
            ``True`` only if the user explicitly confirmed.
        """
        try:
            mode = self.safety.confirmation_mode
            if mode == "spoken":
                spoken = self._confirm_spoken(description)
                if spoken is not None:
                    return spoken
                return self._confirm_onscreen(description)
            if mode == "both":
                # On-screen is authoritative; spoken is a best-effort extra.
                return self._confirm_onscreen(description)
            # Default / "onscreen".
            return self._confirm_onscreen(description)
        except Exception:  # noqa: BLE001 - deny on any error
            return False

    def _confirm_onscreen(self, description: str) -> bool:
        """Prompt on the console and return whether the user typed yes."""
        try:
            answer = input(f"Proceed with: {description}? [y/N] ")
        except (EOFError, KeyboardInterrupt, Exception):  # noqa: BLE001
            return False
        return answer.strip().lower() in ("y", "yes")

    def _confirm_spoken(self, description: str) -> bool | None:
        """Attempt a spoken confirmation.

        Returns ``True``/``False`` if a spoken answer was obtained, or ``None``
        if spoken confirmation is not possible (so the caller can fall back).
        """
        if self.recorder is None or self.stt is None:
            return None
        try:
            if self.feedback is not None:
                self.feedback.say(f"Confirm: {description}. Say yes to proceed.")
            self.recorder.start()
            time.sleep(2.5)
            audio = self.recorder.stop()
            text = self.stt.transcribe(audio) or ""
            return "yes" in text.strip().lower()
        except Exception:  # noqa: BLE001 - signal fallback to caller
            return None

    # ------------------------------------------------------------------ #
    # Action logging
    # ------------------------------------------------------------------ #

    def log_action(self, action_input: dict, executed: bool, extra: dict | None = None) -> None:
        """Append a JSON record describing an action to the rotating log.

        No-op (and never raises) when action logging is disabled.

        Args:
            action_input: The computer-tool action dict.
            executed: Whether the action was actually performed.
            extra: Optional extra fields merged into the record.
        """
        if self._logger is None:
            return
        try:
            text = action_input.get("text")
            if isinstance(text, str) and len(text) > 80:
                text = text[:80]
            record: dict[str, Any] = {
                "ts": time.time(),
                "action": action_input.get("action"),
                "coordinate": action_input.get("coordinate"),
                "text": text,
                "executed": executed,
                "dry_run": self.dry_run,
            }
            if extra:
                record.update(extra)
            self._logger.info(json.dumps(record, default=str))
        except Exception:  # noqa: BLE001 - logging must never crash execution
            pass

    # ------------------------------------------------------------------ #
    # Kill switch
    # ------------------------------------------------------------------ #

    def make_kill_listener(self, kill_key: str, count: int, window_s: float) -> Any:
        """Build (but do not start) a ``pynput`` kill-switch listener.

        The listener triggers :meth:`abort` when the kill key is pressed
        ``count`` times within ``window_s`` seconds.

        Args:
            kill_key: Config key name (e.g. ``"esc"``).
            count: Number of presses required to trigger.
            window_s: Sliding window, in seconds.

        Returns:
            An un-started ``pynput.keyboard.Listener``.
        """
        from pynput import keyboard  # lazy import

        target = _resolve_kill_key(kill_key)

        def on_press(key: Any) -> None:
            if key != target:
                return
            now = time.monotonic()
            with self._kill_lock:
                self._kill_times.append(now)
                while self._kill_times and now - self._kill_times[0] > window_s:
                    self._kill_times.popleft()
                triggered = len(self._kill_times) >= count
                if triggered:
                    self._kill_times.clear()
            if triggered:
                self.abort()

        return keyboard.Listener(on_press=on_press)

    def start_kill_switch(self, hotkey_cfg: Any) -> None:
        """Build and start the kill-switch listener from a hotkey config.

        Args:
            hotkey_cfg: Object exposing ``kill_key``, ``kill_press_count`` and
                ``kill_press_window_s``.
        """
        try:
            self.stop_kill_switch()
            listener = self.make_kill_listener(
                hotkey_cfg.kill_key,
                hotkey_cfg.kill_press_count,
                hotkey_cfg.kill_press_window_s,
            )
            with self._kill_lock:
                self._kill_times.clear()
            listener.start()
            self._kill_listener = listener
        except Exception:  # noqa: BLE001 - kill switch is best-effort
            self._kill_listener = None

    def stop_kill_switch(self) -> None:
        """Stop the kill-switch listener if running. Never raises."""
        listener = self._kill_listener
        self._kill_listener = None
        if listener is not None:
            try:
                listener.stop()
            except Exception:  # noqa: BLE001
                pass
