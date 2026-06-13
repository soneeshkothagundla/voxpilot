"""The VoxPilot agent sampling loop.

Mirrors the reference computer-use sampling loop: send the user's instruction
plus an initial screenshot, append the assistant's content as param dicts, run
every ``tool_use`` block via the :class:`ActionExecutor`, return one
``tool_result`` per ``tool_use`` in a single user message, and repeat until the
assistant stops calling tools (or the iteration cap / kill switch fires).
"""

from __future__ import annotations

import platform
from datetime import date
from typing import Any

from ..config import Config
from ..screen.actions import ActionExecutor, ActionResult
from ..screen.scaling import ScaleResult  # noqa: F401  (documented contract import)
from ..screen.screenshot import ScreenCapture
from .anthropic_client import ComputerUseClient, build_computer_tool


def _block_to_param(block: Any) -> dict[str, Any]:
    """Convert an SDK content block or a dict into a param dict.

    Tolerant of both SDK objects (read via ``getattr``) and plain dicts (read
    via ``.get``) so the loop composes regardless of how the response was built.

    Args:
        block: A content block from the model response.

    Returns:
        A JSON-serializable param dict suitable for echoing back as assistant
        content on the next request.
    """

    def get(name: str, default: Any = None) -> Any:
        if isinstance(block, dict):
            return block.get(name, default)
        return getattr(block, name, default)

    block_type = get("type")

    if block_type == "text":
        return {"type": "text", "text": get("text", "")}

    if block_type == "tool_use":
        return {
            "type": "tool_use",
            "id": get("id"),
            "name": get("name"),
            "input": get("input"),
        }

    if block_type == "thinking":
        param: dict[str, Any] = {"type": "thinking", "thinking": get("thinking", "")}
        signature = get("signature")
        if signature is not None:
            param["signature"] = signature
        return param

    # Best-effort fallback for unknown block types.
    if isinstance(block, dict):
        return dict(block)
    fallback: dict[str, Any] = {"type": block_type}
    for attr in ("text", "id", "name", "input", "thinking", "signature"):
        value = getattr(block, attr, None)
        if value is not None:
            fallback[attr] = value
    return fallback


def _iter_blocks(response: Any) -> list[Any]:
    """Return the list of content blocks from a response object.

    Args:
        response: The SDK response object.

    Returns:
        ``response.content`` (a list of content blocks).
    """
    return response.content


def _make_tool_result(result: ActionResult, tool_use_id: str) -> dict[str, Any]:
    """Build a ``tool_result`` content block for a single ``tool_use``.

    Args:
        result: The outcome of executing the tool action.
        tool_use_id: The id of the ``tool_use`` block this result answers.

    Returns:
        A ``tool_result`` param dict. Carries an optional text block (from
        ``result.output``) and an optional base64 image block (from
        ``result.base64_image``). If both are empty, a placeholder text block is
        used so the content list is never empty.
    """
    content: list[dict[str, Any]] = []

    if result.output:
        content.append({"type": "text", "text": result.output})

    if result.base64_image:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": result.base64_image,
                },
            }
        )

    if not content:
        content.append({"type": "text", "text": "(no output)"})

    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
        "is_error": result.is_error,
    }


class AgentLoop:
    """Drives Claude's computer-use tool against the real screen."""

    def __init__(
        self,
        client: ComputerUseClient,
        capture: ScreenCapture,
        executor: ActionExecutor,
        guard: Any,
        feedback: Any,
        cfg: Config,
    ) -> None:
        """Initialize the loop.

        Args:
            client: The computer-use client used to sample the model.
            capture: Screen capture helper (screenshots + scaling).
            executor: Executes the model's mouse/keyboard actions.
            guard: Safety guard (abort/reset/dry-run/confirmation).
            feedback: User feedback (TTS + status).
            cfg: The fully-loaded VoxPilot configuration.
        """
        self.client = client
        self.capture = capture
        self.executor = executor
        self.guard = guard
        self.feedback = feedback
        self.cfg = cfg

    def _system_prompt(self) -> str:
        """Build the system prompt for the agent.

        Returns:
            A short prompt describing the real-computer context, safety
            expectations, and the stop condition.
        """
        os_name = platform.system()
        today = date.today().isoformat()
        return (
            f"You are VoxPilot, an agent controlling the user's REAL {os_name} "
            "computer via the computer tool. The current screen is shown to you "
            "as a screenshot whose pixel dimensions match the tool's "
            "display_width_px/display_height_px; return coordinates in that same "
            "space. Take a screenshot first whenever you are unsure of the "
            "current state. Be careful and precise: these actions affect a live "
            "machine. Work step by step, verifying with screenshots as needed. "
            "When the task is complete, STOP calling tools and reply with a "
            "single-sentence confirmation of what you did. "
            f"Today's date is {today}."
        )

    def run(self, instruction: str) -> str:
        """Run the agent loop for a single instruction.

        Args:
            instruction: The transcribed user command.

        Returns:
            A short human-readable result string (the model's final message,
            ``"Aborted."``, or ``"Reached max iterations."``).
        """
        self.feedback.status("THINKING")
        self.guard.reset()

        scale = self.capture.current_scale()
        tool = build_computer_tool(
            scale.scaled_width,
            scale.scaled_height,
            self.cfg.agent.tool_version,
            self.cfg.agent.enable_zoom,
        )
        tools = [tool]

        # Initial user message: instruction text + a fresh screenshot.
        img, current_scale = self.capture.capture_base64()
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": instruction},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": img,
                        },
                    },
                ],
            }
        ]

        for _ in range(self.cfg.agent.max_iterations):
            if self.guard.aborted:
                self.feedback.say("Task aborted.")
                return "Aborted."

            resp = self.client.create(
                messages=messages,
                system=self._system_prompt(),
                tools=tools,
                max_tokens=self.cfg.agent.max_tokens,
            )

            blocks = _iter_blocks(resp)
            assistant_params = [_block_to_param(b) for b in blocks]
            messages.append({"role": "assistant", "content": assistant_params})

            # Collect any narration text for the user.
            texts = [p.get("text", "") for p in assistant_params if p.get("type") == "text"]
            narration = " ".join(t for t in texts if t).strip()

            tool_uses = [
                b
                for b in blocks
                if getattr(b, "type", None) == "tool_use"
                or (isinstance(b, dict) and b.get("type") == "tool_use")
            ]

            if not tool_uses:
                self.feedback.status("DONE")
                final = narration or "Done."
                self.feedback.say(final)
                return final

            if narration:
                self.feedback.say(narration)

            self.feedback.status("ACTING")

            tool_results: list[dict[str, Any]] = []
            for tu in tool_uses:
                if self.guard.aborted:
                    break
                tool_use_id = tu.get("id") if isinstance(tu, dict) else getattr(tu, "id", None)
                tool_input = (
                    tu.get("input") if isinstance(tu, dict) else getattr(tu, "input", None)
                ) or {}
                result = self.executor.execute(tool_input, current_scale)
                if result.scale is not None:
                    current_scale = result.scale
                tool_results.append(_make_tool_result(result, tool_use_id))

            messages.append({"role": "user", "content": tool_results})

        self.feedback.say("Reached max iterations.")
        return "Reached max iterations."
