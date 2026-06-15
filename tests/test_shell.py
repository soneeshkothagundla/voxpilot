"""Tests for :class:`voxpilot.agent.shell.ShellExecutor` and command gating.

These run real (harmless) shell commands via the platform shell, and verify the
safety floor: catastrophic commands are refused unless explicitly confirmed, even
under full autonomy; dry-run never executes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxpilot.agent.shell import ShellExecutor
from voxpilot.config import SafetyConfig
from voxpilot.safety import SafetyGuard


def _guard(
    tmp_path: Path, *, dry_run: bool = False, autonomy: str = "full", confirm: bool = False
) -> SafetyGuard:
    """Build a guard with the action log disabled."""
    cfg = SafetyConfig(
        dry_run=dry_run,
        confirm_destructive=confirm,
        autonomy=autonomy,
        confirmation_mode="onscreen",
        failsafe_corner=True,
        action_log=False,
    )
    return SafetyGuard(cfg, tmp_path)


def test_guard_command_classifiers(tmp_path: Path) -> None:
    """Command classifiers flag the catastrophic floor and leave safe ones alone."""
    g = _guard(tmp_path)
    assert g.is_catastrophic_command("rm -rf /important")
    assert g.is_catastrophic_command("format c:")
    assert g.is_catastrophic_command("echo my password is hunter2")
    assert not g.is_catastrophic_command("echo hello")


def test_shell_runs_safe_command(tmp_path: Path) -> None:
    """A harmless command executes and reports exit code 0 with its output."""
    sh = ShellExecutor(_guard(tmp_path), timeout=30)
    out = sh.run("echo voxpilot-shell-ok")
    assert "voxpilot-shell-ok" in out
    assert "exit code: 0" in out


def test_shell_dry_run_does_not_execute(tmp_path: Path) -> None:
    """Dry-run logs the command but never runs it."""
    sh = ShellExecutor(_guard(tmp_path, dry_run=True), timeout=30)
    out = sh.run("echo should-not-run")
    assert out.startswith("[dry-run]")


def test_shell_catastrophic_refused_even_in_full_auto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A catastrophic command is refused in full auto when confirmation is denied."""
    g = _guard(tmp_path, autonomy="full")
    monkeypatch.setattr(g, "confirm", lambda description: False)
    sh = ShellExecutor(g, timeout=30)
    out = sh.run("rm -rf /important/data")
    assert out.startswith("Refused")


def test_shell_catastrophic_runs_when_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A confirmed catastrophic command is allowed through the gate."""
    g = _guard(tmp_path, autonomy="full")
    asked: list[str] = []
    monkeypatch.setattr(g, "confirm", lambda description: bool(asked.append(description) or True))
    sh = ShellExecutor(g, timeout=30)
    out = sh.run("echo my password is hunter2")  # harmless echo, but matches the floor
    assert asked, "a catastrophic command must request confirmation"
    assert "exit code: 0" in out
