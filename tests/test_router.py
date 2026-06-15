"""Tests for :class:`voxpilot.agent.router.Router` (the orchestrator).

A scripted fake client drives the router through each path — direct answer, host
command, and screen control — fully offline, with fake shell/screen executors so
nothing touches the machine.
"""

from __future__ import annotations

from typing import Any

from voxpilot.agent.router import Router
from voxpilot.config import Config


class _Block:
    """Minimal SDK-content-block stand-in (read via getattr by the router)."""

    def __init__(
        self,
        type: str,  # noqa: A002 - mirrors the SDK attribute
        *,
        text: str | None = None,
        id: str | None = None,  # noqa: A002
        name: str | None = None,
        input: dict[str, Any] | None = None,  # noqa: A002
    ) -> None:
        """Store block attributes."""
        self.type = type
        self.text = text
        self.id = id
        self.name = name
        self.input = input


class _Response:
    """Minimal message-response stand-in."""

    def __init__(self, content: list[_Block]) -> None:
        """Store the content blocks."""
        self.content = content


class _FakeClient:
    """Hands out scripted responses in order."""

    def __init__(self, responses: list[_Response]) -> None:
        """Queue scripted responses."""
        self._responses = responses
        self.calls = 0

    def create(self, *, messages: list, system: str, tools: list, max_tokens: int) -> _Response:
        """Return the next scripted response."""
        resp = self._responses[self.calls]
        self.calls += 1
        return resp


class _FakeShell:
    """Records run_command invocations."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.calls: list[str] = []

    def run(self, command: str) -> str:
        """Record and acknowledge the command."""
        self.calls.append(command)
        return f"ran: {command}\nexit code: 0"


class _FakeScreen:
    """Records control_screen invocations."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.calls: list[str] = []

    def run(self, task: str) -> str:
        """Record and acknowledge the GUI task."""
        self.calls.append(task)
        return f"did on screen: {task}"


class _FakeFeedback:
    """No-op feedback."""

    def say(self, *args: Any, **kwargs: Any) -> None:  # noqa: D102
        pass

    def status(self, *args: Any, **kwargs: Any) -> None:  # noqa: D102
        pass


class _FakeGuard:
    """Minimal guard: never aborted."""

    aborted = False

    def reset(self) -> None:  # noqa: D102
        pass


def _router(client: _FakeClient, shell: _FakeShell, screen: _FakeScreen) -> Router:
    """Build a Router around the fakes with a real (defaults) config."""
    cfg = Config.load(load_env=False)
    return Router(client, screen, shell, _FakeGuard(), _FakeFeedback(), cfg)


def test_router_direct_answer_uses_no_tools() -> None:
    """A pure question is answered directly — no shell, no screen."""
    client = _FakeClient([_Response([_Block("text", text="Paris.")])])
    shell, screen = _FakeShell(), _FakeScreen()
    out = _router(client, shell, screen).run("what is the capital of France?")
    assert out == "Paris."
    assert shell.calls == [] and screen.calls == []


def test_router_runs_command_then_answers() -> None:
    """The router runs a host command, sees the result, then finishes."""
    client = _FakeClient(
        [
            _Response(
                [
                    _Block(
                        "tool_use",
                        id="c1",
                        name="run_command",
                        input={"command": "echo hi", "purpose": "say hi"},
                    )
                ]
            ),
            _Response([_Block("text", text="Done.")]),
        ]
    )
    shell, screen = _FakeShell(), _FakeScreen()
    out = _router(client, shell, screen).run("say hi in the terminal")
    assert out == "Done."
    assert shell.calls == ["echo hi"]
    assert screen.calls == []


def test_router_controls_screen_when_needed() -> None:
    """A GUI task is routed to the screen executor."""
    client = _FakeClient(
        [
            _Response(
                [_Block("tool_use", id="s1", name="control_screen", input={"task": "click Save"})]
            ),
            _Response([_Block("text", text="Clicked Save.")]),
        ]
    )
    shell, screen = _FakeShell(), _FakeScreen()
    out = _router(client, shell, screen).run("click the save button")
    assert out == "Clicked Save."
    assert screen.calls == ["click Save"]
    assert shell.calls == []
