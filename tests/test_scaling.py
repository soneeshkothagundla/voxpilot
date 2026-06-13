"""Unit tests for :mod:`voxpilot.screen.scaling`.

These cover the pure coordinate-scaling math: downscale computation, the
no-upscale clamp, and the round-trip / clamping behaviour of the
model<->screen coordinate converters.
"""

from __future__ import annotations

import pytest

from voxpilot.screen.scaling import compute_scale, to_model, to_screen


def test_compute_scale_downscale() -> None:
    """1920x1080 into a 1280x800 box downscales by 2/3."""
    result = compute_scale(1920, 1080, 1280, 800)
    assert result.scale == pytest.approx(2 / 3)
    assert result.scaled_width == 1280
    assert result.scaled_height == 720
    assert result.native_width == 1920
    assert result.native_height == 1080


def test_compute_scale_no_upscale() -> None:
    """A native size smaller than the target box is never upscaled."""
    result = compute_scale(1024, 768, 1280, 800)
    assert result.scale == pytest.approx(1.0)
    assert result.scaled_width == 1024
    assert result.scaled_height == 768


def test_compute_scale_rejects_nonpositive_dims() -> None:
    """Non-positive native dimensions raise ``ValueError``."""
    with pytest.raises(ValueError):
        compute_scale(0, 1080, 1280, 800)
    with pytest.raises(ValueError):
        compute_scale(1920, -5, 1280, 800)


def test_to_screen_round_trip(dummy_scale) -> None:
    """A model coordinate scales back up to the expected real pixel."""
    # dummy_scale.scale == 0.5, so model (100, 50) -> screen (200, 100).
    assert to_screen(100, 50, dummy_scale) == (200, 100)


def test_to_screen_clamps_negative(dummy_scale) -> None:
    """Negative coordinates clamp to the top-left origin."""
    assert to_screen(-10, -10, dummy_scale) == (0, 0)


def test_to_screen_clamps_to_native_bounds(dummy_scale) -> None:
    """Huge coordinates clamp to the last addressable native pixel."""
    x, y = to_screen(10_000, 10_000, dummy_scale)
    assert x == dummy_scale.native_width - 1
    assert y == dummy_scale.native_height - 1


def test_to_model(dummy_scale) -> None:
    """A native coordinate scales down into model space."""
    # scale 0.5: native (200, 100) -> model (100, 50).
    assert to_model(200, 100, dummy_scale) == (100, 50)
