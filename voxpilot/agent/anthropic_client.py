"""Anthropic / Bedrock computer-use client wrapper.

Wraps either :class:`anthropic.AnthropicBedrock` (AWS Bedrock, using a bearer
token) or :class:`anthropic.Anthropic` (first-party API key) and exposes a thin
``create`` method that calls the beta messages endpoint with the computer-use
beta flag enabled.
"""

from __future__ import annotations

from typing import Any

from anthropic import Anthropic, AnthropicBedrock

from ..config import Config


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

        if cfg.agent.provider == "bedrock":
            self._client: Anthropic | AnthropicBedrock = AnthropicBedrock(
                api_key=cfg.secrets.aws_bearer_token_bedrock,
                aws_region=cfg.agent.region or cfg.secrets.aws_region or "us-east-1",
            )
        else:
            self._client = Anthropic(api_key=cfg.secrets.anthropic_api_key)

    def create(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> Any:
        """Call the beta messages endpoint with the computer-use beta flag.

        Args:
            messages: The running conversation (user/assistant turns).
            system: The system prompt (a plain string is accepted by the SDK).
            tools: The tool specs (typically a single computer-use tool).
            max_tokens: Maximum tokens to generate for this turn.

        Returns:
            The SDK response object (its ``.content`` is a list of content
            blocks).
        """
        return self._client.beta.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
            betas=self.betas,
        )
