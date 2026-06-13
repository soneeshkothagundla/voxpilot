"""Primary-monitor screen capture with downscaling for VoxPilot.

Captures the primary display via ``mss``, builds a Pillow image, downscales it to
fit the configured target box (so Claude's computer-use tool sees a token-cheap
image), and returns the PNG bytes alongside the :class:`ScaleResult` needed to map
coordinates back to native pixels.

``mss`` instances are not thread-safe, so a fresh ``mss.mss()`` is created inside a
``with`` block for every capture rather than being cached on the instance.
"""

from __future__ import annotations

import base64
import io

import mss
from PIL import Image

from .scaling import ScaleResult, compute_scale


class ScreenCapture:
    """Capture and downscale screenshots of the primary monitor.

    Args:
        target_width: Maximum width, in pixels, of the downscaled screenshot.
        target_height: Maximum height, in pixels, of the downscaled screenshot.
    """

    def __init__(self, target_width: int, target_height: int) -> None:
        """Store the target downscale box dimensions."""
        self.target_width = target_width
        self.target_height = target_height

    def native_size(self) -> tuple[int, int]:
        """Return the native ``(width, height)`` of the primary monitor.

        Returns:
            A ``(width, height)`` tuple in physical pixels.

        Raises:
            RuntimeError: If the display cannot be queried (e.g. missing screen
                permissions or no display available).
        """
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                return int(monitor["width"]), int(monitor["height"])
        except Exception as exc:  # noqa: BLE001 - normalize to a clear error
            raise RuntimeError(_capture_error_message(exc)) from exc

    def current_scale(self) -> ScaleResult:
        """Compute the :class:`ScaleResult` for the current native resolution.

        Returns:
            A :class:`ScaleResult` for the primary monitor against the target box.

        Raises:
            RuntimeError: If the display cannot be queried.
        """
        native_width, native_height = self.native_size()
        return compute_scale(
            native_width,
            native_height,
            self.target_width,
            self.target_height,
        )

    def capture(self) -> tuple[bytes, ScaleResult]:
        """Capture and downscale the primary monitor to PNG bytes.

        Returns:
            A tuple ``(png_bytes, scale)`` where ``png_bytes`` is the downscaled
            screenshot encoded as PNG and ``scale`` describes the scaling applied.

        Raises:
            RuntimeError: If the screen cannot be captured (commonly a missing
                Screen Recording permission on macOS).
        """
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                shot = sct.grab(monitor)
                image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        except Exception as exc:  # noqa: BLE001 - normalize to a clear error
            raise RuntimeError(_capture_error_message(exc)) from exc

        native_width, native_height = image.size
        scale = compute_scale(
            native_width,
            native_height,
            self.target_width,
            self.target_height,
        )
        if (scale.scaled_width, scale.scaled_height) != image.size:
            image = image.resize(
                (scale.scaled_width, scale.scaled_height),
                Image.LANCZOS,
            )

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue(), scale

    def capture_base64(self) -> tuple[str, ScaleResult]:
        """Capture and downscale the primary monitor to base64 PNG.

        Returns:
            A tuple ``(b64_png, scale)`` where ``b64_png`` is the base64-encoded
            PNG (no data-URI prefix) and ``scale`` describes the scaling applied.

        Raises:
            RuntimeError: If the screen cannot be captured.
        """
        png_bytes, scale = self.capture()
        return base64.b64encode(png_bytes).decode("ascii"), scale


def _capture_error_message(exc: Exception) -> str:
    """Build a clear, actionable message for a screen-capture failure.

    Args:
        exc: The underlying exception raised by ``mss``.

    Returns:
        A human-readable error string with a likely-cause hint.
    """
    return (
        "Failed to capture the screen "
        f"({type(exc).__name__}: {exc}). "
        "On macOS grant Screen Recording permission to your terminal/app in "
        "System Settings > Privacy & Security > Screen Recording, then relaunch. "
        "Otherwise ensure a display is connected and accessible."
    )
