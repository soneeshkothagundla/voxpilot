"""Tests for :mod:`voxpilot.screen.actions`.

The real ``pyautogui`` module is monkeypatched with the recording
:class:`~tests.conftest.FakePyAutoGUI` so no real input is ever generated. A
dummy capture object stands in for :class:`ScreenCapture`, and real
:class:`SafetyGuard` instances drive the dry-run / confirmation logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from voxpilot.config import SafetyConfig
from voxpilot.safety import SafetyGuard
from voxpilot.screen import actions as actions_module
from voxpilot.screen.actions import ActionExecutor
from voxpilot.screen.scaling import ScaleResult


class _DummyCapture:
    """Minimal capture stub returning a fixed base64 image and scale."""

    def __init__(self, scale: ScaleResult) -> None:
        """Remember the scale to report alongside captures."""
        self._scale = scale

    def capture_base64(self) -> tuple[str, ScaleResult]:
        """Return a canned base64 payload and the configured scale."""
        return ("b64data", self._scale)


def _make_guard(
    tmp_path: Path,
    *,
    dry_run: bool = False,
    confirm_destructive: bool = False,
) -> SafetyGuard:
    """Build a :class:`SafetyGuard` with the action log disabled."""
    cfg = SafetyConfig(
        dry_run=dry_run,
        confirm_destructive=confirm_destructive,
        confirmation_mode="onscreen",
        failsafe_corner=True,
        action_log=False,
    )
    return SafetyGuard(cfg, tmp_path)


@pytest.fixture
def patched_pyautogui(monkeypatch: pytest.MonkeyPatch, fake_pyautogui):
    """Swap the ``pyautogui`` reference inside ``actions`` for the fake."""
    monkeypatch.setattr(actions_module, "pyautogui", fake_pyautogui)
    return fake_pyautogui


def _executor(
    tmp_path: Path,
    scale: ScaleResult,
    guard: SafetyGuard,
) -> ActionExecutor:
    """Construct an :class:`ActionExecutor` around a dummy capture."""
    return ActionExecutor(_DummyCapture(scale), guard)


def _find_call(fake, name: str) -> tuple[str, tuple[Any, ...], dict[str, Any]] | None:
    """Return the first recorded call with the given name, if any."""
    for record in fake.calls:
        if record[0] == name:
            return record
    return None


def test_left_click_scales_coordinate(patched_pyautogui, dummy_scale, tmp_path: Path) -> None:
    """A click at model (100,100) at scale 0.5 moves to real (200,200)."""
    guard = _make_guard(tmp_path)
    ex = _executor(tmp_path, dummy_scale, guard)
    result = ex.execute({"action": "left_click", "coordinate": [100, 100]}, dummy_scale)
    assert result.is_error is False
    move = _find_call(patched_pyautogui, "moveTo")
    assert move is not None
    assert move[1][0] == 200
    assert move[1][1] == 200
    # A click of some kind must have been issued.
    assert "click" in patched_pyautogui.names()


def test_type_action_writes_text(patched_pyautogui, dummy_scale, tmp_path: Path) -> None:
    """A ``type`` action funnels the text into ``pyautogui.write``."""
    guard = _make_guard(tmp_path)
    ex = _executor(tmp_path, dummy_scale, guard)
    ex.execute({"action": "type", "text": "hello"}, dummy_scale)
    write = _find_call(patched_pyautogui, "write")
    assert write is not None
    # The full text (single chunk) is written.
    assert write[1][0] == "hello"


def test_key_combo_uses_hotkey(patched_pyautogui, dummy_scale, tmp_path: Path) -> None:
    """``ctrl+s`` dispatches to ``pyautogui.hotkey('ctrl', 's')``."""
    guard = _make_guard(tmp_path)
    ex = _executor(tmp_path, dummy_scale, guard)
    ex.execute({"action": "key", "text": "ctrl+s"}, dummy_scale)
    hotkey = _find_call(patched_pyautogui, "hotkey")
    assert hotkey is not None
    assert hotkey[1] == ("ctrl", "s")


def test_key_return_presses_enter(patched_pyautogui, dummy_scale, tmp_path: Path) -> None:
    """A single ``Return`` key translates to ``press('enter')``."""
    # confirm_destructive defaults to False here so the Enter key is allowed.
    guard = _make_guard(tmp_path)
    ex = _executor(tmp_path, dummy_scale, guard)
    ex.execute({"action": "key", "text": "Return"}, dummy_scale)
    press = _find_call(patched_pyautogui, "press")
    assert press is not None
    assert press[1][0] == "enter"


def test_scroll_down_is_negative(patched_pyautogui, dummy_scale, tmp_path: Path) -> None:
    """Scrolling down produces a negative vertical scroll amount."""
    guard = _make_guard(tmp_path)
    ex = _executor(tmp_path, dummy_scale, guard)
    ex.execute(
        {
            "action": "scroll",
            "coordinate": [10, 10],
            "scroll_direction": "down",
            "scroll_amount": 3,
        },
        dummy_scale,
    )
    scroll = _find_call(patched_pyautogui, "scroll")
    assert scroll is not None
    assert scroll[1][0] < 0


def test_screenshot_returns_image(patched_pyautogui, dummy_scale, tmp_path: Path) -> None:
    """A ``screenshot`` action returns a base64 image and scale."""
    guard = _make_guard(tmp_path)
    ex = _executor(tmp_path, dummy_scale, guard)
    result = ex.execute({"action": "screenshot"}, dummy_scale)
    assert result.base64_image == "b64data"
    assert result.scale == dummy_scale
    assert result.is_error is False


def test_dry_run_does_not_act(patched_pyautogui, dummy_scale, tmp_path: Path) -> None:
    """In dry-run mode a click is logged only; no pyautogui calls happen."""
    guard = _make_guard(tmp_path, dry_run=True)
    ex = _executor(tmp_path, dummy_scale, guard)
    result = ex.execute({"action": "left_click", "coordinate": [10, 10]}, dummy_scale)
    assert result.output is not None
    assert result.output.startswith("[dry-run]")
    assert _find_call(patched_pyautogui, "moveTo") is None
    assert _find_call(patched_pyautogui, "click") is None


def test_destructive_confirm_denied_skips(
    patched_pyautogui, dummy_scale, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A denied confirmation skips a destructive Enter key press."""
    guard = _make_guard(tmp_path, confirm_destructive=True)
    monkeypatch.setattr(guard, "confirm", lambda description: False)
    ex = _executor(tmp_path, dummy_scale, guard)
    result = ex.execute({"action": "key", "text": "Return"}, dummy_scale)
    # The Enter key is destructive; with confirmation denied it must be skipped.
    assert _find_call(patched_pyautogui, "press") is None
    assert result.output is not None
