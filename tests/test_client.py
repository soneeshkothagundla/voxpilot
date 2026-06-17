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


# --------------------------------------------------------------------------- #
# Transient-error retry / backoff
# --------------------------------------------------------------------------- #


class _NamedError(Exception):
    """An exception whose class name mimics an anthropic SDK error type."""


class RateLimitError(_NamedError):
    pass


class APIConnectionError(_NamedError):
    pass


def test_is_retryable_classifies_transient_errors() -> None:
    """429 / connection / 5xx are retryable; a plain 4xx is not."""
    assert ComputerUseClient._is_retryable(RateLimitError("nope"))
    assert ComputerUseClient._is_retryable(APIConnectionError("conn"))
    # Bedrock gateway 429 surfaces only as a message string.
    assert ComputerUseClient._is_retryable(
        RuntimeError("Error code: 429 - Too many requests, please wait")
    )
    assert ComputerUseClient._is_retryable(RuntimeError("Connection error."))

    class _StatusErr(Exception):
        status_code = 503

    assert ComputerUseClient._is_retryable(_StatusErr("server"))

    class _BadReq(Exception):
        status_code = 400

    assert not ComputerUseClient._is_retryable(_BadReq("bad request"))
    assert not ComputerUseClient._is_retryable(ValueError("totally unrelated"))


def test_create_retries_transient_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """A throttled request is retried with backoff and eventually returns."""
    client = _client(monkeypatch)
    client.caching = False
    client._retry_base_delay = 0.0  # no real waiting in tests
    monkeypatch.setattr("voxpilot.agent.anthropic_client.time.sleep", lambda _s: None)

    calls = {"n": 0}

    def fake_invoke(**_kw: Any) -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RateLimitError("Error code: 429 - Too many requests")
        return "finally-ok"

    monkeypatch.setattr(client, "_invoke", fake_invoke)
    out = client.create(messages=[], system="sys", tools=[], max_tokens=16)
    assert out == "finally-ok"
    assert calls["n"] == 3


def test_create_gives_up_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Persistent throttling raises after exhausting the retry budget."""
    client = _client(monkeypatch)
    client.caching = False
    client._max_attempts = 3
    client._retry_base_delay = 0.0
    monkeypatch.setattr("voxpilot.agent.anthropic_client.time.sleep", lambda _s: None)

    calls = {"n": 0}

    def fake_invoke(**_kw: Any) -> str:
        calls["n"] += 1
        raise RateLimitError("Error code: 429 - Too many requests")

    monkeypatch.setattr(client, "_invoke", fake_invoke)
    with pytest.raises(RateLimitError):
        client.create(messages=[], system="sys", tools=[], max_tokens=16)
    assert calls["n"] == 3  # exactly max_attempts tries


def test_non_retryable_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-transient error raises immediately without burning retries."""
    client = _client(monkeypatch)
    client.caching = False
    monkeypatch.setattr("voxpilot.agent.anthropic_client.time.sleep", lambda _s: None)

    calls = {"n": 0}

    def fake_invoke(**_kw: Any) -> str:
        calls["n"] += 1
        raise ValueError("400 bad request")

    monkeypatch.setattr(client, "_invoke", fake_invoke)
    with pytest.raises(ValueError):
        client.create(messages=[], system="sys", tools=[], max_tokens=16)
    assert calls["n"] == 1  # no retries for a non-transient error


def test_transient_error_during_cached_attempt_keeps_caching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 on a cached attempt is retried, NOT mistaken for a caching failure."""
    client = _client(monkeypatch)
    client.caching = True
    client._retry_base_delay = 0.0
    monkeypatch.setattr("voxpilot.agent.anthropic_client.time.sleep", lambda _s: None)

    calls = {"n": 0}

    def fake_invoke(*, system: Any, **_kw: Any) -> str:
        calls["n"] += 1
        # Always called with cached system blocks; fail once, then succeed.
        assert isinstance(system, list)
        if calls["n"] == 1:
            raise RateLimitError("Error code: 429 - Too many requests")
        return "cached-ok"

    monkeypatch.setattr(client, "_invoke", fake_invoke)
    out = client.create(messages=[], system="sys", tools=[{"name": "t"}], max_tokens=16)
    assert out == "cached-ok"
    assert client.caching is True  # transient error must NOT disable caching
    assert calls["n"] == 2
