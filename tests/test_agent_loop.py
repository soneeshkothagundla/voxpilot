"""End-to-end test of :class:`voxpilot.agent.loop.AgentLoop`.

A :class:`FakeClient` returns a scripted sequence of computer-use responses so
the loop runs to completion fully offline. A dummy screen capture and the fake
``pyautogui`` keep everything off the real machine; the guard runs in dry-run
mode so no actions are actually performed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from voxpilot.agent.loop import AgentLoop
from voxpilot.config import Config, FeedbackConfig, SafetyConfig
from voxpilot.feedback import Feedback
from voxpilot.safety import SafetyGuard
from voxpilot.screen import actions as actions_module
from voxpilot.screen.actions import ActionExecutor
from voxpilot.screen.scaling import ScaleResult


class _Block:
    """A minimal stand-in for an SDK content block."""

    def __init__(
        self,
        type: str,  # noqa: A002 - mirrors the SDK attribute name
        *,
        text: str | None = None,
        id: str | None = None,  # noqa: A002
        name: str | None = None,
        input: dict[str, Any] | None = None,  # noqa: A002
    ) -> None:
        """Store the block attributes the loop reads via ``getattr``."""
        self.type = type
        self.text = text
        self.id = id
        self.name = name
        self.input = input


class _Response:
    """A minimal stand-in for an SDK message response."""

    def __init__(self, content: list[_Block], stop_reason: str) -> None:
        """Store the content blocks and stop reason."""
        self.content = content
        self.stop_reason = stop_reason


class FakeClient:
    """A scripted computer-use client recording how often it was called."""

    def __init__(self, responses: list[_Response]) -> None:
        """Queue the scripted responses to hand out, oldest first."""
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def create(
        self,
        *,
        messages: list,
        system: str,
        tools: list,
        max_tokens: int,
    ) -> _Response:
        """Record the call and return the next scripted response."""
        self.calls.append(
            {
                "messages": messages,
                "system": system,
                "tools": tools,
                "max_tokens": max_tokens,
            }
        )
        return self._responses[len(self.calls) - 1]


class _DummyCapture:
    """A screen capture stub returning a fixed scale and image."""

    def __init__(self, scale: ScaleResult) -> None:
        """Remember the scale to report."""
        self._scale = scale

    def current_scale(self) -> ScaleResult:
        """Return the configured scale."""
        return self._scale

    def capture_base64(self) -> tuple[str, ScaleResult]:
        """Return a canned base64 image and the configured scale."""
        return ("b64", self._scale)


def _scripted_responses() -> list[_Response]:
    """Build the three-step screenshot -> click -> done sequence."""
    return [
        _Response(
            content=[
                _Block(
                    type="tool_use",
                    id="t1",
                    name="computer",
                    input={"action": "screenshot"},
                )
            ],
            stop_reason="tool_use",
        ),
        _Response(
            content=[
                _Block(
                    type="tool_use",
                    id="t2",
                    name="computer",
                    input={"action": "left_click", "coordinate": [10, 10]},
                )
            ],
            stop_reason="tool_use",
        ),
        _Response(
            content=[_Block(type="text", text="All done.")],
            stop_reason="end_turn",
        ),
    ]


def test_agent_loop_runs_to_completion(
    monkeypatch: pytest.MonkeyPatch,
    fake_pyautogui,
    dummy_scale,
    tmp_path: Path,
) -> None:
    """The loop drives the scripted tools and returns the final text."""
    monkeypatch.setattr(actions_module, "pyautogui", fake_pyautogui)

    client = FakeClient(_scripted_responses())
    capture = _DummyCapture(dummy_scale)

    guard = SafetyGuard(
        SafetyConfig(
            dry_run=True,
            confirm_destructive=False,
            confirmation_mode="onscreen",
            failsafe_corner=True,
            action_log=False,
        ),
        tmp_path,
    )
    executor = ActionExecutor(capture, guard)
    feedback = Feedback(FeedbackConfig(tts=False, verbose=False))

    cfg = Config.load(load_env=False)
    cfg.agent.max_iterations = 5

    loop = AgentLoop(client, capture, executor, guard, feedback, cfg)
    result = loop.run("do thing")

    assert result == "All done."
    assert len(client.calls) == 3

    # The screenshot tool_result must have produced an image block in the
    # message history that was sent back to the model.
    last_messages = client.calls[-1]["messages"]
    found_image = False
    for message in last_messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            inner = block.get("content")
            if not isinstance(inner, list):
                continue
            for piece in inner:
                if isinstance(piece, dict) and piece.get("type") == "image":
                    src = piece.get("source", {})
                    if src.get("type") == "base64" and src.get("data") == "b64":
                        found_image = True
    assert found_image, "expected a base64 image block in a tool_result"
