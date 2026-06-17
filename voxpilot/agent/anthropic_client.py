"""Anthropic / Bedrock computer-use client wrapper.

Wraps either :class:`anthropic.AnthropicBedrock` (AWS Bedrock, using a bearer
token) or :class:`anthropic.Anthropic` (first-party API key) and exposes a thin
``create`` method that calls the beta messages endpoint with the computer-use
beta flag enabled.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from anthropic import Anthropic, AnthropicBedrock

from ..config import Config

logger = logging.getLogger(__name__)

#: Exception class names treated as transient (worth retrying with backoff).
#: Matched by name so we stay robust across anthropic SDK versions.
_RETRYABLE_EXC_NAMES = frozenset(
    {
        "RateLimitError",
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "OverloadedError",
        "ServiceUnavailableError",
    }
)

#: Substrings (lowercased) that mark a transient failure when the exception type
#: is opaque (e.g. the Bedrock bearer-token gateway returns a bare 429 message).
_RETRYABLE_MESSAGE_HINTS = (
    "too many requests",
    "rate limit",
    "overloaded",
    "throttl",
    "connection error",
    "timed out",
    "timeout",
    "service unavailable",
    "bad gateway",
    "temporarily unavailable",
)


def build_computer_tool(
    scaled_width: int,
    scaled_height: int,
    tool_version: str,
    enable_zoom: bool = False,
) -> dict[str, Any]:
    """Build the computer-use tool spec dict for ``messages.create``.

    Args:
        scaled_width: Display width in pixels (the downscaled screenshot width).
        scaled_height: Display height in pixels (the downscaled screenshot height).
        tool_version: The tool ``type`` string, e.g. ``"computer_20251124"``.
        enable_zoom: Whether to advertise the zoom action (only valid for the
            ``computer_20251124`` tool version).

    Returns:
        A tool spec dict matching the computer-use API contract. The reported
        ``display_*_px`` values are the SCALED dimensions that match the
        screenshot sent to the model.
    """
    tool: dict[str, Any] = {
        "type": tool_version,
        "name": "computer",
        "display_width_px": scaled_width,
        "display_height_px": scaled_height,
        "display_number": 1,
    }
    if enable_zoom and tool_version == "computer_20251124":
        tool["enable_zoom"] = True
    return tool


class ComputerUseClient:
    """Thin wrapper over the Anthropic SDK for computer-use sampling.

    Selects the Bedrock or first-party client based on ``cfg.agent.provider``
    and always calls ``client.beta.messages.create`` with the configured beta
    flag so the computer-use tool is available.
    """

    def __init__(self, cfg: Config, use_opus: bool = False) -> None:
        """Initialize the client.

        Args:
            cfg: The fully-loaded VoxPilot configuration.
            use_opus: If True, resolve to the Opus model instead of the default.
        """
        self.cfg = cfg
        self.model = cfg.resolved_model(use_opus)
        self.betas = [cfg.agent.beta_flag]
        self.caching = bool(getattr(cfg.agent, "prompt_caching", False))
        # Transient-error retry policy (429 throttling, connection blips, 5xx).
        self._max_attempts = max(1, int(getattr(cfg.agent, "request_max_retries", 5)))
        self._retry_base_delay = float(getattr(cfg.agent, "retry_base_delay", 1.0))
        self._retry_max_delay = float(getattr(cfg.agent, "retry_max_delay", 30.0))

        if cfg.agent.provider == "bedrock":
            self._client: Anthropic | AnthropicBedrock = AnthropicBedrock(
                api_key=cfg.secrets.aws_bearer_token_bedrock,
                aws_region=cfg.agent.region or cfg.secrets.aws_region or "us-east-1",
            )
        else:
            self._client = Anthropic(api_key=cfg.secrets.anthropic_api_key)

        # Use the streaming helper only if the active SDK/provider exposes it
        # (the Bedrock beta namespace may not); otherwise fall back to a plain
        # create() so the run never breaks on a missing attribute.
        self.stream = bool(getattr(cfg.agent, "stream", False)) and hasattr(
            self._client.beta.messages, "stream"
        )

    def _cached(self, system: str, tools: list[dict[str, Any]]) -> tuple[Any, list[dict[str, Any]]]:
        """Return (system, tools) with a cache breakpoint on the static prefix.

        The system prompt and tool spec are identical every turn, so caching them
        (ephemeral) lets Bedrock skip re-reading them, cutting per-turn latency.
        """
        sys_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        tools_c = [dict(t) for t in tools]
        if tools_c:
            tools_c[-1] = {**tools_c[-1], "cache_control": {"type": "ephemeral"}}
        return sys_blocks, tools_c

    def _invoke(
        self,
        *,
        messages: list[dict[str, Any]],
        system: Any,
        tools: list[dict[str, Any]],
        max_tokens: int,
        on_text: Any,
    ) -> Any:
        """Call the beta messages endpoint, streaming when enabled."""
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
            betas=self.betas,
        )
        if self.stream:
            with self._client.beta.messages.stream(**kwargs) as stream:
                if on_text is not None:
                    for delta in stream.text_stream:
                        on_text(delta)
                return stream.get_final_message()
        return self._client.beta.messages.create(**kwargs)

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Whether an exception is a transient failure worth retrying."""
        if type(exc).__name__ in _RETRYABLE_EXC_NAMES:
            return True
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and (status == 429 or status >= 500):
            return True
        msg = str(exc).lower()
        return any(hint in msg for hint in _RETRYABLE_MESSAGE_HINTS)

    @staticmethod
    def _retry_after_seconds(exc: Exception) -> float | None:
        """Honor a ``Retry-After`` header if the SDK exposes one."""
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if not headers:
            return None
        try:
            value = headers.get("retry-after")
        except Exception:  # noqa: BLE001 - headers may not be a mapping
            return None
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return None

    def _backoff_delay(self, exc: Exception, attempt: int) -> float:
        """Compute the wait before the next attempt (Retry-After or exp backoff)."""
        retry_after = self._retry_after_seconds(exc)
        if retry_after is not None:
            return min(retry_after, self._retry_max_delay)
        base = min(self._retry_base_delay * (2**attempt), self._retry_max_delay)
        return base + random.uniform(0.0, base * 0.25)  # jitter to de-sync retries

    def _create_once(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        max_tokens: int,
        on_text: Any,
    ) -> Any:
        """One attempt: cached if enabled, falling back to uncached on cache errors."""
        if self.caching:
            sys_param, tools_param = self._cached(system, tools)
            try:
                return self._invoke(
                    messages=messages,
                    system=sys_param,
                    tools=tools_param,
                    max_tokens=max_tokens,
                    on_text=on_text,
                )
            except Exception as exc:  # noqa: BLE001 - degrade, but not for transient errors
                # A throttle/connection blip is NOT a caching problem: let the
                # retry loop handle it instead of needlessly disabling caching.
                if self._is_retryable(exc):
                    raise
                # Disable caching for the rest of the run so we don't pay the
                # (doomed) cached attempt's tokens on every subsequent turn.
                logger.warning(
                    "prompt cache request failed (%s); disabling caching for this run", exc
                )
                self.caching = False
        return self._invoke(
            messages=messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
            on_text=on_text,
        )

    def create(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        max_tokens: int,
        on_text: Any = None,
    ) -> Any:
        """Call the beta messages endpoint with the computer-use beta flag.

        Streams the response when ``cfg.agent.stream`` is set (recommended:
        avoids request timeouts on long turns and lets the UI react sooner) and
        caches the static system+tools prefix when ``cfg.agent.prompt_caching``
        is set. If a cached request fails, it retries once without caching so an
        unsupported model/region degrades gracefully instead of breaking the run.

        Transient failures (HTTP 429 throttling, connection blips, 5xx) are
        retried with exponential backoff + jitter (honoring ``Retry-After`` when
        present) up to ``cfg.agent.request_max_retries`` attempts, so a brief rate
        limit no longer aborts the whole request.

        Args:
            messages: The running conversation (user/assistant turns).
            system: The system prompt (a plain string).
            tools: The tool specs (typically a single computer-use tool).
            max_tokens: Maximum tokens to generate for this turn.
            on_text: Optional callback invoked with streamed text deltas.

        Returns:
            The final SDK response object (its ``.content`` is a list of blocks).
        """
        last_exc: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                return self._create_once(
                    messages=messages,
                    system=system,
                    tools=tools,
                    max_tokens=max_tokens,
                    # Only stream text on the first attempt; a retry that re-streams
                    # would emit the same narration twice into the HUD/TTS.
                    on_text=on_text if attempt == 0 else None,
                )
            except Exception as exc:  # noqa: BLE001 - retry transient, re-raise the rest
                last_exc = exc
                if attempt < self._max_attempts - 1 and self._is_retryable(exc):
                    delay = self._backoff_delay(exc, attempt)
                    logger.warning(
                        "transient model error (%s); retrying in %.1fs (attempt %d/%d)",
                        type(exc).__name__,
                        delay,
                        attempt + 1,
                        self._max_attempts,
                    )
                    time.sleep(delay)
                    continue
                raise
        # Loop always returns or raises; this satisfies type-checkers.
        assert last_exc is not None
        raise last_exc
