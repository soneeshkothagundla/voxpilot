"""The VoxPilot orchestrator: choose the best way to fulfill each request.

Driving the on-screen GUI is powerful but slow, and for many requests there is a
faster, more reliable path. Rather than always controlling the screen, the router
lets the model pick per request:

* **answer directly** — a question / explanation / conversation: just reply (no
  tool, no screenshot — instant);
* **run a host command** — launch apps, file/folder ops, system queries, scripts,
  installs: :class:`~voxpilot.agent.shell.ShellExecutor` (gated by the safety
  floor), much faster than the GUI;
* **control the screen** — only when the task genuinely needs the GUI: hand off to
  the computer-use :class:`~voxpilot.agent.loop.AgentLoop`.

It is a small text-only tool-use loop (no screenshot is sent for the planning
step), so non-GUI tasks stay fast. The catastrophic safety floor, kill switch and
action log apply to every path.
"""

from __future__ import annotations

import platform
from typing import Any

from .loop import AgentLoop, _block_to_param
from .shell import ShellExecutor

#: Tool the model uses to run a host command instead of the slow GUI.
RUN_COMMAND_TOOL: dict[str, Any] = {
    "name": "run_command",
    "description": (
        "Run a shell command on the user's computer (PowerShell on Windows, bash "
        "otherwise). Use this for anything achievable without the GUI: launching "
        "apps, creating/moving/reading files and folders, system settings and "
        "queries, installing packages, scripting. It is MUCH faster than "
        "control_screen, so prefer it whenever it can do the job."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The exact command line to run."},
            "purpose": {
                "type": "string",
                "description": "One short sentence describing what this does.",
            },
        },
        "required": ["command"],
    },
}

#: Tool the model uses to fall back to driving the on-screen GUI.
CONTROL_SCREEN_TOOL: dict[str, Any] = {
    "name": "control_screen",
    "description": (
        "Control the mouse and keyboard to operate the on-screen GUI. Use this "
        "ONLY when the task genuinely requires interacting with a visible "
        "application (clicking specific on-screen elements, visual tasks inside a "
        "running program). It is slower than run_command."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "A clear natural-language description of the GUI task to perform.",
            },
        },
        "required": ["task"],
    },
}


def _system_prompt() -> str:
    """Build the orchestrator system prompt."""
    os_name = platform.system()
    return (
        f"You are VoxPilot's orchestrator on the user's real {os_name} computer. "
        "For each request, choose the FASTEST correct way to fulfill it:\n"
        "- If you can answer from your own knowledge (a question, explanation, or "
        "conversation), just reply with text and DO NOT call a tool.\n"
        "- If it can be done from the command line (launching apps, files and "
        "folders, system settings/queries, scripts, installs), call run_command. "
        "Prefer this over the GUI because it is much faster and more reliable.\n"
        "- Call control_screen ONLY when the task truly needs the on-screen GUI of "
        "a running application.\n"
        "Chain tools as needed: inspect a command's output, then continue. These "
        "actions affect a real machine, so be careful and precise. When the task "
        "is complete, reply with one concise sentence describing what you did."
    )


def _tool_uses(blocks: list[Any]) -> list[Any]:
    """Return the ``tool_use`` blocks from a response's content."""
    return [
        b
        for b in blocks
        if getattr(b, "type", None) == "tool_use"
        or (isinstance(b, dict) and b.get("type") == "tool_use")
    ]


def _get(block: Any, name: str) -> Any:
    """Read ``name`` from an SDK block or a plain dict."""
    if isinstance(block, dict):
        return block.get(name)
    return getattr(block, name, None)


class Router:
    """Pick and drive the best executor for each request."""

    def __init__(
        self,
        client: Any,
        screen: AgentLoop,
        shell: ShellExecutor,
        guard: Any,
        feedback: Any,
        cfg: Any,
    ) -> None:
        """Initialize the orchestrator.

        Args:
            client: The :class:`~voxpilot.agent.anthropic_client.ComputerUseClient`.
            screen: The computer-use loop used for ``control_screen``.
            shell: The host-command executor used for ``run_command``.
            guard: The safety guard (abort / reset / floor).
            feedback: User feedback (TTS + status + HUD).
            cfg: The fully-loaded configuration.
        """
        self.client = client
        self.screen = screen
        self.shell = shell
        self.guard = guard
        self.feedback = feedback
        self.cfg = cfg
        self.max_steps = int(getattr(cfg.agent, "router_max_steps", 12))

    def run(self, instruction: str) -> str:
        """Fulfill a request via the best path, returning a short result string."""
        self.feedback.status("THINKING")
        self.guard.reset()
        system = _system_prompt()
        tools = [RUN_COMMAND_TOOL, CONTROL_SCREEN_TOOL]
        messages: list[dict[str, Any]] = [{"role": "user", "content": instruction}]

        for _ in range(self.max_steps):
            if self.guard.aborted:
                self.feedback.say("Task aborted.")
                return "Aborted."

            try:
                resp = self.client.create(
                    messages=messages,
                    system=system,
                    tools=tools,
                    max_tokens=self.cfg.agent.max_tokens,
                )
            except Exception as exc:  # noqa: BLE001 - end cleanly, keep the app alive
                msg = (
                    f"Model request failed ({type(exc).__name__}). "
                    "Check your network/credentials, then try again."
                )
                self.feedback.say(msg)
                return msg
            blocks = resp.content
            assistant = [_block_to_param(b) for b in blocks]
            messages.append({"role": "assistant", "content": assistant})

            narration = " ".join(
                p.get("text", "") for p in assistant if p.get("type") == "text"
            ).strip()

            tool_uses = _tool_uses(blocks)
            if not tool_uses:
                self.feedback.status("DONE")
                final = narration or "Done."
                self.feedback.say(final)
                return final

            if narration:
                self.feedback.say(narration)
            self.feedback.status("ACTING")

            results: list[dict[str, Any]] = []
            for tu in tool_uses:
                if self.guard.aborted:
                    break
                output, is_error = self._dispatch(_get(tu, "name"), _get(tu, "input") or {})
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": _get(tu, "id"),
                        "content": [{"type": "text", "text": output}],
                        "is_error": is_error,
                    }
                )
            messages.append({"role": "user", "content": results})

        self.feedback.say("Reached the step limit.")
        return "Reached the step limit."

    def _dispatch(self, name: str | None, tool_input: dict) -> tuple[str, bool]:
        """Execute one orchestrator tool. Returns ``(output, is_error)``; never raises."""
        try:
            if name == "run_command":
                command = str(tool_input.get("command", "")).strip()
                purpose = str(tool_input.get("purpose", "")).strip()
                self.feedback.say(f"Running: {purpose or command}")
                out = self.shell.run(command)
                refused = out.startswith(
                    ("Refused", "Aborted", "Command timed out", "Failed to run", "No command")
                )
                if refused:
                    # Tell the user (the model also sees it), e.g. a blocked command.
                    self.feedback.say(out.splitlines()[0])
                return out, refused
            if name == "control_screen":
                task = str(tool_input.get("task", "")).strip()
                self.feedback.say(f"On screen: {task}")
                return self.screen.run(task), False
            return f"Unknown tool: {name}", True
        except Exception as exc:  # noqa: BLE001 - report back, keep the loop alive
            return f"Tool error: {exc}", True
