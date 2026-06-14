"""Tests for the platform-independent pieces of :mod:`voxpilot.ui.edgeglow`.

The Win32 layered window itself needs a real display and is not exercised here;
instead we test the pure helpers that drive it — the edge falloff mask, the
state -> visual-language mapping, premultiplied-alpha compositing, the ripple
lifecycle, and the no-op backend used off-Windows.
"""

from __future__ import annotations

import numpy as np

from voxpilot.ui.edgeglow import (
    _falloff_mask,
    _NoopEdgeGlow,
    _pbgra_from_array,
    _Ripple,
    _stamp,
    accent_for,
)


def test_accent_for_states() -> None:
    """Each state maps to a coherent visual language; unknown falls back."""
    assert accent_for("idle")["peak"] == 0
    assert accent_for("idle")["cursor"] is False
    assert accent_for("acting")["cursor"] is True
    assert accent_for("listening")["peak"] > 0
    # Unknown state falls back to 'thinking' rather than raising.
    assert accent_for("nonsense") == accent_for("thinking")


def test_falloff_mask_edges_hot_center_cold() -> None:
    """The mask is ~1 at the screen edge and 0 well inside the band."""
    m = _falloff_mask(40, 30, 8)
    assert m.shape == (30, 40)
    assert m.max() <= 1.0 and m.min() >= 0.0
    assert m[0, 0] > 0.9  # corner
    assert m[0, 20] > 0.9  # top edge, mid-width
    assert m[15, 20] == 0.0  # center is beyond the band -> fully transparent


def test_stamp_composites_and_clips() -> None:
    """Stamping a fully-opaque sprite paints it; off-screen stamps don't raise."""
    buf = np.zeros((10, 10, 4), dtype=np.uint8)
    sprite = np.empty((4, 4, 4), dtype=np.uint8)
    sprite[:, :, 0] = 10
    sprite[:, :, 1] = 20
    sprite[:, :, 2] = 30
    sprite[:, :, 3] = 255  # premultiplied, fully opaque
    _stamp(buf, sprite, 5, 5)
    assert buf[5, 5, 3] == 255
    assert tuple(int(v) for v in buf[5, 5, :3]) == (10, 20, 30)
    assert buf[0, 0, 3] == 0  # untouched far corner
    # Partially (and fully) off-buffer stamps are clipped, never crash.
    _stamp(buf, sprite, 0, 0)
    _stamp(buf, sprite, -50, -50)


def test_pbgra_from_array_premultiplies() -> None:
    """Premultiplication scales color by alpha and swaps R/B into BGRA order."""
    rgba = np.zeros((1, 2, 4), dtype=np.uint8)
    rgba[0, 0] = [200, 100, 50, 255]  # opaque -> unchanged color, B/R swapped
    rgba[0, 1] = [200, 100, 50, 0]  # transparent -> all zero
    out = _pbgra_from_array(rgba)
    assert tuple(int(v) for v in out[0, 0]) == (50, 100, 200, 255)  # B,G,R,A
    assert tuple(int(v) for v in out[0, 1]) == (0, 0, 0, 0)


def test_ripple_progress() -> None:
    """Ripple progress runs 0 -> 1 across its lifetime."""
    rp = _Ripple(x=10, y=10, rgb=(1, 2, 3), birth=100.0, lifetime=0.5)
    assert rp.progress(100.0) == 0.0
    assert rp.progress(100.25) == 0.5
    assert rp.progress(100.5) >= 1.0


def test_noop_edgeglow_is_safe() -> None:
    """The off-Windows stub accepts every call without raising."""
    g = _NoopEdgeGlow()
    g.start()
    g.set_state("acting")
    g.set_level(0.5)
    g.click_ripple(10, 20, "left")
    g.set_typing(True)
    g.hide()
    g.stop()
