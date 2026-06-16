"""Tests for the prompt-caching fallback in ComputerUseClient.

The cached attempt must, on failure, fall back to an uncached request AND disable
caching for the rest of the run so we don't double-pay tokens every turn.
"""

from __future__ import annotations

from typing import Any

import pytest

from voxpilot.agent.anthropic_client import ComputerUseClient
from voxpilot.config import Config


def _client(monkeypatch: pytest.MonkeyPatch) -> ComputerUseClient:
    """Build a client (first-party provider, dummy key — no network at construct)."""
    cfg = Config.load(load_env=False)
    cfg.agent.provider = "anthropic"
    cfg.secrets.anthropic_api_key = "sk-test-not-real"
    client = ComputerUseClient(cfg)
    client.stream = False
    client.caching = True
    return client


def test_cached_request_falls_back_and_disables_caching(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cached failure returns the uncached result and turns caching off."""
    client = _client(monkeypatch)
    seen: list[str] = []

    def fake_invoke(
        *, messages: Any, system: Any, tools: Any, max_tokens: int, on_text: Any
    ) -> str:
        # The cached attempt passes system as a list of blocks; uncached as a str.
        if isinstance(system, list):
            seen.append("cached")
            raise RuntimeError("cache not supported here")
        seen.append("uncached")
        return "OK"

    monkeypatch.setattr(client, "_invoke", fake_invoke)
    out = client.create(messages=[], system="sys", tools=[{"name": "t"}], max_tokens=16)
    assert out == "OK"
    assert seen == ["cached", "uncached"]
    assert client.caching is False  # disabled for the rest of the run

    # Next call should skip the cached attempt entirely.
    seen.clear()
    out2 = client.create(messages=[], system="sys", tools=[{"name": "t"}], max_tokens=16)
    assert out2 == "OK"
    assert seen == ["uncached"]


def test_cached_request_used_when_it_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """When caching works, the cached attempt is used and caching stays on."""
    client = _client(monkeypatch)

    def fake_invoke(
        *, messages: Any, system: Any, tools: Any, max_tokens: int, on_text: Any
    ) -> str:
        return "cached-ok" if isinstance(system, list) else "uncached"

    monkeypatch.setattr(client, "_invoke", fake_invoke)
    out = client.create(messages=[], system="sys", tools=[{"name": "t"}], max_tokens=16)
    assert out == "cached-ok"
    assert client.caching is True
