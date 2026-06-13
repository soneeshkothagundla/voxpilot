"""Configuration loading and validation for VoxPilot.

This module is deliberately lightweight: it imports only the standard
library plus PyYAML and python-dotenv. It must NOT import heavy or
hardware-touching dependencies (pyautogui, mss, anthropic, ...), so that
``--help``, banners, and tests work without optional libraries or
credentials.

Loading is permissive: :meth:`Config.load` never raises on missing
secrets or a missing config file. Only :meth:`Config.validate` raises
:class:`ConfigError`, and only when something is actually required for the
selected providers.

Configuration sources, in order of precedence (highest last):
    1. Dataclass defaults.
    2. A YAML file (``config.yaml`` preferred, then ``config.example.yaml``).
    3. Environment variables (for secrets), loaded from ``.env`` if present.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised by :meth:`Config.validate` when configuration is invalid."""


@dataclass
class AgentConfig:
    """Settings for the Claude computer-use agent and its model."""

    provider: str = "bedrock"
    model: str = "us.anthropic.claude-sonnet-4-6"
    opus_model: str = "us.anthropic.claude-opus-4-8"
    region: str = "us-east-1"
    max_tokens: int = 4096
    max_iterations: int = 40
    target_width: int = 1280
    target_height: int = 800
    tool_version: str = "computer_20251124"
    beta_flag: str = "computer-use-2025-11-24"
    enable_zoom: bool = False


@dataclass
class STTConfig:
    """Speech-to-text backend settings."""

    backend: str = "faster_whisper"
    model: str = "base"
    device: str = "auto"
    compute_type: str = "auto"
    language: str | None = None
    openai_model: str = "whisper-1"


@dataclass
class HotkeyConfig:
    """Push-to-talk and kill-switch hotkey settings."""

    mode: str = "push_to_talk"
    ptt_key: str = "f9"
    kill_key: str = "esc"
    kill_press_count: int = 3
    kill_press_window_s: float = 1.0


@dataclass
class SafetyConfig:
    """Safety gating: dry-run, confirmations, fail-safe, and logging."""

    dry_run: bool = False
    confirm_destructive: bool = False
    confirmation_mode: str = "onscreen"
    failsafe_corner: bool = True
    action_log: bool = True


@dataclass
class FeedbackConfig:
    """Spoken and on-screen feedback settings."""

    tts: bool = True
    tts_rate: int = 180
    tts_volume: float = 1.0
    verbose: bool = True


@dataclass
class Secrets:
    """Secret values loaded from the environment (never from YAML)."""

    aws_bearer_token_bedrock: str | None = None
    aws_region: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None


def _overlay(cls: type, defaults_kwargs: dict[str, Any], section: dict[str, Any] | None) -> Any:
    """Build a dataclass instance from a YAML ``section`` over defaults.

    Only keys that correspond to fields of ``cls`` are applied; unknown
    keys are ignored so that comments/extra YAML keys never break loading.

    Args:
        cls: The dataclass type to instantiate.
        defaults_kwargs: Pre-seeded keyword arguments (already validated
            against ``cls`` fields) to apply on top of the YAML section.
        section: The YAML mapping for this section, or ``None``.

    Returns:
        An instance of ``cls``.
    """
    valid = {f.name for f in fields(cls)}
    kwargs: dict[str, Any] = {}
    if isinstance(section, dict):
        for key, value in section.items():
            if key in valid:
                kwargs[key] = value
    kwargs.update(defaults_kwargs)
    return cls(**kwargs)


def _env(name: str) -> str | None:
    """Return a non-empty environment variable value, else ``None``."""
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _find_config_path(config_path: str | Path | None) -> Path | None:
    """Locate a YAML config file.

    Search order:
        1. An explicit ``config_path`` (returned even if it does not exist;
           callers may treat a missing explicit path as "no overrides").
        2. ``config.yaml`` then ``config.example.yaml`` in the current
           working directory.
        3. ``config.yaml`` then ``config.example.yaml`` next to the package
           root (``Path(__file__).resolve().parent.parent``).

    Args:
        config_path: An explicit path, or ``None`` to auto-discover.

    Returns:
        The resolved path to a config file, or ``None`` if none was found.
    """
    if config_path is not None:
        path = Path(config_path)
        if path.exists():
            return path
        return None

    candidates = ["config.yaml", "config.example.yaml"]
    search_dirs = [Path.cwd(), Path(__file__).resolve().parent.parent]
    for directory in search_dirs:
        for name in candidates:
            candidate = directory / name
            if candidate.exists():
                return candidate
    return None


@dataclass
class Config:
    """Top-level VoxPilot configuration aggregating all sub-configs."""

    agent: AgentConfig
    stt: STTConfig
    hotkey: HotkeyConfig
    safety: SafetyConfig
    feedback: FeedbackConfig
    secrets: Secrets
    log_dir: Path
    config_source: str | None = None

    @classmethod
    def load(
        cls,
        config_path: str | Path | None = None,
        *,
        load_env: bool = True,
    ) -> Config:
        """Load configuration from YAML + environment, never raising on secrets.

        Args:
            config_path: Explicit path to a YAML config file, or ``None`` to
                auto-discover ``config.yaml`` / ``config.example.yaml``.
            load_env: When ``True`` (default), load a ``.env`` file from the
                current working directory if present (ignored if missing).

        Returns:
            A fully populated :class:`Config`. Missing secrets are left as
            ``None``; this method never raises for missing credentials.
        """
        if load_env:
            # Loads .env from cwd if present; silently does nothing otherwise.
            load_dotenv()

        path = _find_config_path(config_path)
        data: dict[str, Any] = {}
        if path is not None:
            with path.open("r", encoding="utf-8") as handle:
                loaded = yaml.safe_load(handle)
            if isinstance(loaded, dict):
                data = loaded

        agent_section = data.get("agent") if isinstance(data.get("agent"), dict) else {}
        stt_section = data.get("stt") if isinstance(data.get("stt"), dict) else {}
        hotkey_section = data.get("hotkey") if isinstance(data.get("hotkey"), dict) else {}
        safety_section = data.get("safety") if isinstance(data.get("safety"), dict) else {}
        feedback_section = data.get("feedback") if isinstance(data.get("feedback"), dict) else {}

        # --- Secrets from environment ---
        secrets = Secrets(
            aws_bearer_token_bedrock=_env("AWS_BEARER_TOKEN_BEDROCK"),
            aws_region=_env("AWS_REGION"),
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            openai_api_key=_env("OPENAI_API_KEY"),
        )

        # --- Agent: prefer yaml region; else env region; else default. ---
        agent_overrides: dict[str, Any] = {}
        yaml_region = agent_section.get("region") if isinstance(agent_section, dict) else None
        if not yaml_region:
            agent_overrides["region"] = secrets.aws_region or "us-east-1"
        agent = _overlay(AgentConfig, agent_overrides, agent_section)

        # --- STT: handle nested stt.openai.model -> STTConfig.openai_model. ---
        stt_overrides: dict[str, Any] = {}
        if isinstance(stt_section, dict):
            openai_block = stt_section.get("openai")
            if isinstance(openai_block, dict) and "model" in openai_block:
                stt_overrides["openai_model"] = openai_block["model"]
            elif "openai_model" in stt_section:
                stt_overrides["openai_model"] = stt_section["openai_model"]
        stt = _overlay(STTConfig, stt_overrides, stt_section)

        # --- Hotkey: accept both kill_press_window_s and kill_press_window. ---
        hotkey_overrides: dict[str, Any] = {}
        if isinstance(hotkey_section, dict):
            if "kill_press_window_s" in hotkey_section:
                hotkey_overrides["kill_press_window_s"] = hotkey_section["kill_press_window_s"]
            elif "kill_press_window" in hotkey_section:
                hotkey_overrides["kill_press_window_s"] = hotkey_section["kill_press_window"]
        hotkey = _overlay(HotkeyConfig, hotkey_overrides, hotkey_section)

        safety = _overlay(SafetyConfig, {}, safety_section)
        feedback = _overlay(FeedbackConfig, {}, feedback_section)

        log_dir = Path.home() / ".voxpilot" / "logs"
        config_source = str(path) if path is not None else None

        return cls(
            agent=agent,
            stt=stt,
            hotkey=hotkey,
            safety=safety,
            feedback=feedback,
            secrets=secrets,
            log_dir=log_dir,
            config_source=config_source,
        )

    def validate(self) -> None:
        """Validate that required settings/secrets are present and coherent.

        Raises:
            ConfigError: With an actionable message when configuration is
                invalid for the selected providers.
        """
        if self.agent.provider not in {"bedrock", "anthropic"}:
            raise ConfigError(
                f"agent.provider must be 'bedrock' or 'anthropic', got "
                f"{self.agent.provider!r}. Set it in config.yaml under "
                f"agent.provider."
            )

        if self.agent.provider == "bedrock" and not self.secrets.aws_bearer_token_bedrock:
            raise ConfigError(
                "Bedrock provider selected but AWS_BEARER_TOKEN_BEDROCK is not "
                "set. Add it to your .env file (see .env.example), e.g. "
                "AWS_BEARER_TOKEN_BEDROCK=your-bedrock-bearer-token."
            )

        if self.agent.provider == "anthropic" and not self.secrets.anthropic_api_key:
            raise ConfigError(
                "Anthropic provider selected but ANTHROPIC_API_KEY is not set. "
                "Add it to your .env file (see .env.example), e.g. "
                "ANTHROPIC_API_KEY=sk-ant-..."
            )

        if self.stt.backend not in {"faster_whisper", "openai"}:
            raise ConfigError(
                f"stt.backend must be 'faster_whisper' or 'openai', got "
                f"{self.stt.backend!r}. Set it in config.yaml under stt.backend."
            )

        if self.stt.backend == "openai" and not self.secrets.openai_api_key:
            raise ConfigError(
                "STT backend 'openai' selected but OPENAI_API_KEY is not set. "
                "Add it to your .env file (see .env.example), e.g. "
                "OPENAI_API_KEY=sk-...; or use stt.backend: faster_whisper for "
                "fully offline transcription."
            )

        if self.hotkey.mode not in {"push_to_talk", "toggle"}:
            raise ConfigError(
                f"hotkey.mode must be 'push_to_talk' or 'toggle', got "
                f"{self.hotkey.mode!r}. Set it in config.yaml under hotkey.mode."
            )

        if self.safety.confirmation_mode not in {"spoken", "onscreen", "both"}:
            raise ConfigError(
                f"safety.confirmation_mode must be 'spoken', 'onscreen', or "
                f"'both', got {self.safety.confirmation_mode!r}. Set it in "
                f"config.yaml under safety.confirmation_mode."
            )

    def resolved_model(self, use_opus: bool = False) -> str:
        """Return the model id to use for this run.

        Args:
            use_opus: When ``True``, return the more capable opus model.

        Returns:
            The configured model id.
        """
        return self.agent.opus_model if use_opus else self.agent.model
