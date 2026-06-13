"""Unit tests for :mod:`voxpilot.config`.

All tests pass an explicit ``config_path`` (an existing tmp YAML, or a
non-existent path that disables auto-discovery) so the repository's
``config.example.yaml`` is never picked up implicitly. Secrets are injected via
``monkeypatch.setenv``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxpilot.config import Config, ConfigError


def _clear_secret_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove any ambient credential env vars so tests are deterministic."""
    for name in (
        "AWS_BEARER_TOKEN_BEDROCK",
        "AWS_REGION",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def _no_config_path(tmp_path: Path) -> Path:
    """Return an explicit, non-existent config path.

    ``Config.load`` resolves an explicit missing path to "no overrides"
    *without* falling back to the repo's ``config.example.yaml`` next to the
    package root. Using this keeps the "defaults" tests hermetic regardless of
    the current working directory.
    """
    return tmp_path / "does_not_exist.yaml"


def test_load_no_file_returns_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Loading with no config file yields dataclass defaults."""
    _clear_secret_env(monkeypatch)
    cfg = Config.load(config_path=_no_config_path(tmp_path), load_env=False)
    assert cfg.agent.provider == "bedrock"
    assert cfg.agent.model == "us.anthropic.claude-sonnet-4-6"
    assert cfg.stt.backend == "faster_whisper"
    assert cfg.hotkey.ptt_key == "ctrl_r"
    assert cfg.safety.dry_run is False


def test_load_yaml_merges_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """YAML sections overlay onto the defaults, ignoring unknown keys."""
    _clear_secret_env(monkeypatch)
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "agent:\n"
        "  model: us.anthropic.claude-opus-4-8\n"
        "  bogus_key: ignored\n"
        "stt:\n"
        "  backend: openai\n",
        encoding="utf-8",
    )
    cfg = Config.load(config_path=yaml_path, load_env=False)
    assert cfg.agent.model == "us.anthropic.claude-opus-4-8"
    assert cfg.stt.backend == "openai"
    # Untouched defaults stay put.
    assert cfg.agent.region == "us-east-1"
    assert cfg.config_source == str(yaml_path)


def test_load_env_populates_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Credential env vars flow into the ``Secrets`` dataclass."""
    _clear_secret_env(monkeypatch)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "tok-123")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    cfg = Config.load(config_path=_no_config_path(tmp_path), load_env=False)
    assert cfg.secrets.aws_bearer_token_bedrock == "tok-123"
    assert cfg.secrets.aws_region == "us-west-2"
    assert cfg.secrets.openai_api_key == "sk-openai"
    # Env region is preferred when YAML did not override the default.
    assert cfg.agent.region == "us-west-2"


def test_validate_bedrock_requires_bearer_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bedrock provider without a bearer token fails validation."""
    _clear_secret_env(monkeypatch)
    cfg = Config.load(config_path=_no_config_path(tmp_path), load_env=False)
    with pytest.raises(ConfigError):
        cfg.validate()


def test_validate_passes_with_bearer_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Bedrock provider validates once a bearer token is present."""
    _clear_secret_env(monkeypatch)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "tok-abc")
    cfg = Config.load(config_path=_no_config_path(tmp_path), load_env=False)
    # Should not raise.
    cfg.validate()


def test_resolved_model_opus_vs_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``resolved_model`` switches between the default and opus models."""
    _clear_secret_env(monkeypatch)
    cfg = Config.load(config_path=_no_config_path(tmp_path), load_env=False)
    assert cfg.resolved_model(use_opus=False) == cfg.agent.model
    assert cfg.resolved_model(use_opus=True) == cfg.agent.opus_model
    assert cfg.agent.opus_model == "us.anthropic.claude-opus-4-8"
