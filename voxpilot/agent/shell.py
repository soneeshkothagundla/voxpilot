"""Host command execution for VoxPilot's orchestrator (the fast, non-GUI path).

Many requests ("open Chrome", "make a folder on the Desktop", "what's my IP",
"kill that process") are far faster to run as a shell command than by driving the
GUI one click at a time. The :class:`~voxpilot.agent.router.Router` calls
:class:`ShellExecutor` for those.

Every command passes the SAME safety floor as on-screen actions: catastrophic
commands (money / irreversible destruction / credential exposure) ALWAYS require a
human "yes", even in full autonomy; risky-but-reversible commands are gated unless
autonomy is "full"; dry-run logs without executing. Commands run with a timeout so
a hung process can never wedge the agent.
"""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..safety.guard import SafetyGuard

#: Cap on captured stdout/stderr returned to the model (keeps context bounded).
_MAX_OUTPUT = 4000


def _truncate(text: str, limit: int = _MAX_OUTPUT) -> str:
    """Truncate long command output with a marker so context stays bounded."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"


class ShellExecutor:
    """Run host shell commands behind the VoxPilot safety guard.

    Args:
        guard: The :class:`~voxpilot.safety.guard.SafetyGuard` enforcing the
            abort / dry-run / confirmation policy and the catastrophic floor.
        timeout: Seconds a single command may run before it is killed.
    """

    def __init__(self, guard: SafetyGuard, *, timeout: float = 60.0) -> None:
        """Store the guard and the per-command timeout."""
        self.guard = guard
        self.timeout = timeout

    def _gate(self, command: str) -> str:
        """Decide handling: ``"abort"``, ``"dry"``, ``"skip"`` or ``"proceed"``."""
        if self.guard.aborted:
            return "abort"
        if self.guard.dry_run:
            return "dry"
        # Catastrophic commands ALWAYS confirm, even in full auto (the floor).
        if self.guard.is_catastrophic_command(command):
            return "proceed" if self.guard.confirm(f"run command: {command}") else "skip"
        # Risky-but-reversible commands: gated unless autonomy is "full".
        if (
            self.guard.is_risky_command(command)
            and self.guard.confirm_enabled
            and not self.guard.full_auto
        ):
            return "proceed" if self.guard.confirm(f"run command: {command}") else "skip"
        return "proceed"

    def _argv(self, command: str) -> list[str]:
        """Build the argv to run ``command`` via the platform shell."""
        if sys.platform == "win32":
            return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
        return ["bash", "-lc", command]

    def run(self, command: str) -> str:
        """Run a command (subject to the safety gate) and return a result summary.

        Args:
            command: The command line to execute.

        Returns:
            A text summary (exit code + truncated stdout/stderr), or a message
            explaining why it was not run (aborted / refused / dry-run / error).
            Never raises.
        """
        command = (command or "").strip()
        if not command:
            return "No command provided."

        decision = self._gate(command)
        if decision == "abort":
            return "Aborted; command not run."
        if decision == "skip":
            return f"Refused (catastrophic or unconfirmed), not run: {command}"
        if decision == "dry":
            self._log(command, executed=False)
            return f"[dry-run] would run: {command}"

        try:
            proc = subprocess.run(
                self._argv(command),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return f"Command timed out after {int(self.timeout)}s: {command}"
        except Exception as exc:  # noqa: BLE001 - report, never crash the agent
            return f"Failed to run command: {exc}"

        self._log(command, executed=True)
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        parts = [f"exit code: {proc.returncode}"]
        if out:
            parts.append("stdout:\n" + _truncate(out))
        if err:
            parts.append("stderr:\n" + _truncate(err))
        if not out and not err:
            parts.append("(no output)")
        return "\n".join(parts)

    def _log(self, command: str, *, executed: bool) -> None:
        """Append the command to the action log, swallowing any error."""
        try:
            self.guard.log_action({"action": "shell", "text": command}, executed=executed)
        except Exception:  # noqa: BLE001
            pass
