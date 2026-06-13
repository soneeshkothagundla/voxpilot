"""Shared pytest fixtures and fakes for the VoxPilot test suite.

These fixtures let the tests exercise the real ``voxpilot`` modules entirely
offline: no network, no model downloads, no real audio/screen access. Heavy
or hardware-bound libraries (``pyautogui``, ``mss``, ``faster_whisper``) are
replaced with lightweight fakes or simply never exercised.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure the project root (which contains the ``voxpilot`` package) is importable
# even when pytest is invoked from an unusual working directory.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from voxpilot.config import SafetyConfig  # noqa: E402
from voxpilot.safety import SafetyGuard  # noqa: E402
from voxpilot.screen.scaling import ScaleResult  # noqa: E402


class _FailSafeException(Exception):
    """Stand-in for ``pyautogui.FailSafeException`` used by the fake."""


class FakePyAutoGUI:
    """A recording fake that mimics the small slice of ``pyautogui`` we use.

    Every interesting call is appended to :attr:`calls` as a
    ``(name, args, kwargs)`` tuple so tests can make precise assertions about
    what the :class:`~voxpilot.screen.actions.ActionExecutor` did, without ever
    touching a real mouse or keyboard.
    """

    #: Exposed so ``actions.py`` can reference ``pyautogui.FailSafeException``.
    FailSafeException = _FailSafeException

    def __init__(self) -> None:
        """Initialise the call log and emulated module-level flags."""
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        # Emulate the module-level attributes ``actions.py`` sets at import.
        self.FAILSAFE = True
        self.PAUSE = 0.0

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        """Append a single call record."""
        self.calls.append((name, args, kwargs))

    # --- mouse -----------------------------------------------------------
    def moveTo(self, *args: Any, **kwargs: Any) -> None:  # noqa: N802
        """Record a ``moveTo`` call."""
        self._record("moveTo", *args, **kwargs)

    def click(self, *args: Any, **kwargs: Any) -> None:
        """Record a ``click`` call."""
        self._record("click", *args, **kwargs)

    def rightClick(self, *args: Any, **kwargs: Any) -> None:  # noqa: N802
        """Record a ``rightClick`` call."""
        self._record("rightClick", *args, **kwargs)

    def doubleClick(self, *args: Any, **kwargs: Any) -> None:  # noqa: N802
        """Record a ``doubleClick`` call."""
        self._record("doubleClick", *args, **kwargs)

    def dragTo(self, *args: Any, **kwargs: Any) -> None:  # noqa: N802
        """Record a ``dragTo`` call."""
        self._record("dragTo", *args, **kwargs)

    def mouseDown(self, *args: Any, **kwargs: Any) -> None:  # noqa: N802
        """Record a ``mouseDown`` call."""
        self._record("mouseDown", *args, **kwargs)

    def mouseUp(self, *args: Any, **kwargs: Any) -> None:  # noqa: N802
        """Record a ``mouseUp`` call."""
        self._record("mouseUp", *args, **kwargs)

    def scroll(self, *args: Any, **kwargs: Any) -> None:
        """Record a vertical ``scroll`` call."""
        self._record("scroll", *args, **kwargs)

    def hscroll(self, *args: Any, **kwargs: Any) -> None:
        """Record a horizontal ``hscroll`` call."""
        self._record("hscroll", *args, **kwargs)

    # --- keyboard --------------------------------------------------------
    def write(self, *args: Any, **kwargs: Any) -> None:
        """Record a ``write`` (typing) call."""
        self._record("write", *args, **kwargs)

    def press(self, *args: Any, **kwargs: Any) -> None:
        """Record a single key ``press`` call."""
        self._record("press", *args, **kwargs)

    def hotkey(self, *args: Any, **kwargs: Any) -> None:
        """Record a ``hotkey`` (key combo) call."""
        self._record("hotkey", *args, **kwargs)

    def keyDown(self, *args: Any, **kwargs: Any) -> None:  # noqa: N802
        """Record a ``keyDown`` call."""
        self._record("keyDown", *args, **kwargs)

    def keyUp(self, *args: Any, **kwargs: Any) -> None:  # noqa: N802
        """Record a ``keyUp`` call."""
        self._record("keyUp", *args, **kwargs)

    # --- queries ---------------------------------------------------------
    def position(self) -> tuple[int, int]:
        """Return a fixed cursor position."""
        self._record("position")
        return (0, 0)

    def size(self) -> tuple[int, int]:
        """Return a fixed screen size."""
        self._record("size")
        return (1920, 1080)

    # --- helpers ---------------------------------------------------------
    def names(self) -> list[str]:
        """Return the ordered list of recorded call names (test convenience)."""
        return [name for name, _, _ in self.calls]


@pytest.fixture
def fake_pyautogui() -> FakePyAutoGUI:
    """Provide a fresh :class:`FakePyAutoGUI` for a test."""
    return FakePyAutoGUI()


@pytest.fixture
def dummy_scale() -> ScaleResult:
    """A representative half-scale result (1920x1080 -> 960x540)."""
    return ScaleResult(
        scale=0.5,
        scaled_width=960,
        scaled_height=540,
        native_width=1920,
        native_height=1080,
    )


@pytest.fixture
def safety_guard_factory(tmp_path: Path):
    """Return a factory that builds dry-run :class:`SafetyGuard` instances.

    The returned guards default to ``dry_run=True`` with the action log
    disabled, so tests neither touch the real screen nor write log files.
    Keyword overrides are forwarded onto the :class:`SafetyConfig`.
    """

    def _make(**overrides: Any) -> SafetyGuard:
        params: dict[str, Any] = {
            "dry_run": True,
            "confirm_destructive": False,
            "confirmation_mode": "onscreen",
            "failsafe_corner": True,
            "action_log": False,
        }
        params.update(overrides)
        cfg = SafetyConfig(**params)
        return SafetyGuard(cfg, tmp_path)

    return _make
