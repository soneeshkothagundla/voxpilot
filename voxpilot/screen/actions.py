"""Execution of Claude computer-use (``computer_20251124``) actions for VoxPilot.

This module turns the structured action dictionaries Claude returns into real
mouse/keyboard events via ``pyautogui``. Every coordinate the model produces is in
the *scaled* screenshot space and is mapped back to native pixels with
:func:`voxpilot.screen.scaling.to_screen` before any movement. Key names follow
xdotool/Claude conventions and are translated to pyautogui names via
:data:`XDOTOOL_TO_PYAUTOGUI`.

Safety:
    - ``pyautogui.FAILSAFE`` is enabled at import time; slamming the cursor into a
      screen corner aborts the agent.
    - All mutating actions are gated through the supplied ``guard``: dry-run logs
      only, destructive actions can require confirmation, and an aborted guard
      short-circuits everything.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pyautogui

from .scaling import ScaleResult, to_model, to_screen
from .screenshot import ScreenCapture

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids circular import
    from ..safety.guard import SafetyGuard

# Enable the fail-safe (corner-slam abort) and a small inter-call pause so the
# host UI has time to react between synthesized events.
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.0

#: Maps xdotool/Claude key tokens to the names pyautogui expects.
XDOTOOL_TO_PYAUTOGUI: dict[str, str] = {
    "Return": "enter",
    "KP_Enter": "enter",
    "Tab": "tab",
    "Escape": "esc",
    "BackSpace": "backspace",
    "Delete": "delete",
    "space": "space",
    "Up": "up",
    "Down": "down",
    "Left": "left",
    "Right": "right",
    "Page_Up": "pageup",
    "Page_Down": "pagedown",
    "Home": "home",
    "End": "end",
    "Insert": "insert",
    "Print": "printscreen",
    "Control_L": "ctrl",
    "Control_R": "ctrl",
    "ctrl": "ctrl",
    "Alt_L": "alt",
    "Alt_R": "alt",
    "alt": "alt",
    "Shift_L": "shift",
    "Shift_R": "shift",
    "shift": "shift",
    "super": "command" if sys.platform == "darwin" else "win",
    "Super_L": "command" if sys.platform == "darwin" else "win",
    "Super_R": "command" if sys.platform == "darwin" else "win",
    "cmd": "command" if sys.platform == "darwin" else "win",
    "Meta_L": "command" if sys.platform == "darwin" else "win",
}


def _translate_key(token: str) -> str:
    """Translate a single xdotool/Claude key token to a pyautogui key name.

    Args:
        token: A raw key token (e.g. ``"Return"``, ``"ctrl"``, ``"a"``).

    Returns:
        The pyautogui key name, or ``token.lower()`` if not in the map.
    """
    if token in XDOTOOL_TO_PYAUTOGUI:
        return XDOTOOL_TO_PYAUTOGUI[token]
    return token.lower()


def _parse_combo(text: str) -> list[str]:
    """Split a key combination string on ``+`` into individual tokens.

    Args:
        text: A combo such as ``"ctrl+s"`` or a single key like ``"Return"``.

    Returns:
        A list of non-empty key tokens preserving order.
    """
    return [part for part in text.split("+") if part != ""]


@dataclass
class ActionResult:
    """Outcome of executing a single computer-use action.

    Attributes:
        output: Human-readable text describing what happened (or ``None``).
        base64_image: Base64-encoded PNG (no prefix) for screenshot/zoom results.
        is_error: Whether the action failed and should be reported as an error.
        scale: The :class:`ScaleResult` of any image returned (screenshot/zoom),
            so the agent loop can update its active coordinate scaling.
    """

    output: str | None = None
    base64_image: str | None = None
    is_error: bool = False
    scale: ScaleResult | None = None


class ActionExecutor:
    """Execute Claude computer-use actions against the real screen.

    Args:
        capture: A :class:`ScreenCapture` used for screenshot/zoom actions.
        guard: A safety guard exposing ``dry_run``, ``aborted``,
            ``confirm_enabled``, ``is_destructive``, ``confirm``, ``log_action``
            and ``abort`` (see :class:`voxpilot.safety.guard.SafetyGuard`).
        type_interval: Per-character delay used when typing text.
        type_chunk: Number of characters typed per ``write`` call.
    """

    def __init__(
        self,
        capture: ScreenCapture,
        guard: SafetyGuard,
        *,
        type_interval: float = 0.0,
        type_chunk: int = 50,
        move_duration: float = 0.18,
    ) -> None:
        """Store collaborators and typing parameters."""
        self.capture = capture
        self.guard = guard
        self.type_interval = type_interval
        self.type_chunk = type_chunk
        self.move_duration = move_duration
        self.dry_run = guard.dry_run
        #: Optional visual hooks (set by the windowed UI) so the on-screen "Under
        #: Control" indicator can show click ripples and a typing pulse. Both are
        #: best-effort and must never raise into the action path.
        self.on_click: Any = None  # callable(native_x, native_y, button)
        self.on_type: Any = None  # callable(active: bool)

    def _emit_click(self, x: int, y: int, button: str) -> None:
        """Fire the click visual hook, swallowing any error."""
        if self.on_click is not None:
            try:
                self.on_click(x, y, button)
            except Exception:  # noqa: BLE001 - a UI hook must never break actions
                pass

    def _emit_type(self, active: bool) -> None:
        """Fire the typing visual hook, swallowing any error."""
        if self.on_type is not None:
            try:
                self.on_type(active)
            except Exception:  # noqa: BLE001
                pass

    def execute(self, action_input: dict, scale: ScaleResult) -> ActionResult:
        """Dispatch and execute a single computer-use action.

        Read-only actions (``screenshot``, ``cursor_position``, ``wait``) always
        run. Mutating actions are routed through :meth:`_gate` which enforces
        abort, dry-run and confirmation policy. ``pyautogui`` fail-safe triggers
        abort the agent; any other error is returned as a non-fatal error result.

        Args:
            action_input: The action dict from Claude (must contain ``"action"``).
            scale: The active :class:`ScaleResult` for coordinate conversion.

        Returns:
            An :class:`ActionResult` describing the outcome.
        """
        name = action_input.get("action", "")

        # Read-only actions always execute.
        if name == "screenshot":
            return self._do_screenshot()
        if name == "cursor_position":
            return self._do_cursor_position(scale)
        if name == "wait":
            duration = float(action_input.get("duration", 1))
            time.sleep(min(duration, 10))
            return ActionResult(output=f"waited {min(duration, 10)}s")

        description = self._describe(action_input, scale)
        decision = self._gate(action_input, description)
        if decision == "abort":
            return ActionResult(output="Aborted; not executing.")
        if decision == "skip":
            return ActionResult(output=f"Skipped (not confirmed): {description}")
        if decision == "dry":
            self.guard.log_action(action_input, executed=False)
            return ActionResult(output=f"[dry-run] {description}")

        # decision == "proceed"
        try:
            result = self._perform(name, action_input, scale, description)
        except pyautogui.FailSafeException:
            self.guard.abort()
            return ActionResult(output="Fail-safe triggered; aborting", is_error=True)
        except Exception as exc:  # noqa: BLE001 - report, do not crash the loop
            return ActionResult(output=str(exc), is_error=True)

        self.guard.log_action(action_input, executed=True)
        return result

    # -- gating ---------------------------------------------------------------

    def _gate(self, action_input: dict, description: str) -> str:
        """Decide handling: ``"abort"``, ``"dry"``, ``"skip"`` or ``"proceed"``."""
        if self.guard.aborted:
            return "abort"
        if self.guard.dry_run:
            return "dry"
        # Catastrophic actions (money / irreversible / credentials) ALWAYS confirm,
        # even under full autonomy. Non-bypassable safety floor.
        if self.guard.is_catastrophic(action_input):
            return "proceed" if self.guard.confirm(description) else "skip"
        # Risky-but-reversible actions: gated unless autonomy is "full".
        if (
            self.guard.is_destructive(action_input)
            and self.guard.confirm_enabled
            and not self.guard.full_auto
        ):
            return "proceed" if self.guard.confirm(description) else "skip"
        return "proceed"

    # -- read-only actions ----------------------------------------------------

    def _do_screenshot(self) -> ActionResult:
        """Capture a fresh screenshot and return it as a base64 image result."""
        img, scale = self.capture.capture_base64()
        return ActionResult(base64_image=img, scale=scale, output="screenshot taken")

    def _do_cursor_position(self, scale: ScaleResult) -> ActionResult:
        """Report the current cursor position in model (scaled) coordinates."""
        x, y = pyautogui.position()
        mx, my = to_model(x, y, scale)
        return ActionResult(output=f"X={mx},Y={my}")

    # -- mutating dispatch ----------------------------------------------------

    def _perform(
        self,
        name: str,
        action_input: dict,
        scale: ScaleResult,
        description: str,
    ) -> ActionResult:
        """Perform a confirmed mutating action via pyautogui.

        Returns an :class:`ActionResult`; handlers returning ``None`` default to
        ``output=description``, while zoom returns its own image result.
        """
        handler = self._HANDLERS.get(name)
        if handler is None:
            return ActionResult(output=f"Unsupported action: {name}", is_error=True)
        outcome = handler(self, action_input, scale)
        if isinstance(outcome, ActionResult):
            return outcome
        return ActionResult(output=description)

    # -- coordinate helpers ---------------------------------------------------

    def _xy(self, coord: Any, scale: ScaleResult) -> tuple[int, int]:
        """Convert a model ``[x, y]`` coordinate to native screen pixels."""
        return to_screen(coord[0], coord[1], scale)

    def _move(self, x: int, y: int) -> None:
        """Move the cursor with a visible, eased glide so the user can follow it."""
        tween = getattr(pyautogui, "easeInOutQuad", None)
        if tween is not None:
            pyautogui.moveTo(x, y, duration=self.move_duration, tween=tween)
        else:
            pyautogui.moveTo(x, y, duration=self.move_duration)

    def _click_with_modifier(
        self,
        x: int,
        y: int,
        button: str,
        clicks: int,
        modifier_text: str | None,
    ) -> None:
        """Click at ``(x, y)`` optionally holding a translated modifier key."""
        # Show a ripple at the click point as it happens (best-effort UI hook).
        self._emit_click(x, y, button)
        if not modifier_text:
            pyautogui.click(x, y, clicks=clicks, interval=0.05, button=button)
            return

        mods = [_translate_key(t) for t in _parse_combo(modifier_text)]
        hold = getattr(pyautogui, "hold", None)
        if callable(hold):
            with hold(mods):
                pyautogui.click(x, y, clicks=clicks, interval=0.05, button=button)
            return

        for mod in mods:
            pyautogui.keyDown(mod)
        try:
            pyautogui.click(x, y, clicks=clicks, interval=0.05, button=button)
        finally:
            for mod in reversed(mods):
                pyautogui.keyUp(mod)

    # -- individual action handlers -------------------------------------------

    def _act_mouse_move(self, action_input: dict, scale: ScaleResult) -> None:
        """Move the mouse to the given coordinate."""
        x, y = self._xy(action_input["coordinate"], scale)
        self._move(x, y)

    def _click_at(self, action_input: dict, scale: ScaleResult, button: str, clicks: int) -> None:
        """Move to the coordinate and click, honoring an optional modifier."""
        x, y = self._xy(action_input["coordinate"], scale)
        self._move(x, y)
        self._click_with_modifier(x, y, button, clicks, action_input.get("text"))

    def _act_left_click(self, action_input: dict, scale: ScaleResult) -> None:
        """Left-click at a coordinate, optionally with a modifier key held."""
        self._click_at(action_input, scale, "left", 1)

    def _act_right_click(self, action_input: dict, scale: ScaleResult) -> None:
        """Right-click at a coordinate."""
        self._click_at(action_input, scale, "right", 1)

    def _act_middle_click(self, action_input: dict, scale: ScaleResult) -> None:
        """Middle-click at a coordinate."""
        self._click_at(action_input, scale, "middle", 1)

    def _act_double_click(self, action_input: dict, scale: ScaleResult) -> None:
        """Double-click at a coordinate."""
        self._click_at(action_input, scale, "left", 2)

    def _act_triple_click(self, action_input: dict, scale: ScaleResult) -> None:
        """Triple-click at a coordinate."""
        self._click_at(action_input, scale, "left", 3)

    def _act_left_click_drag(self, action_input: dict, scale: ScaleResult) -> None:
        """Drag with the left button from a start coordinate to an end one."""
        start = self._xy(action_input["start_coordinate"], scale)
        end = self._xy(action_input["coordinate"], scale)
        self._move(*start)
        pyautogui.dragTo(*end, duration=max(self.move_duration, 0.3), button="left")

    def _act_left_mouse_down(self, action_input: dict, scale: ScaleResult) -> None:
        """Press the left mouse button down (optionally at a coordinate)."""
        coord = action_input.get("coordinate")
        if coord is not None:
            x, y = self._xy(coord, scale)
            self._move(x, y)
        pyautogui.mouseDown(button="left")

    def _act_left_mouse_up(self, action_input: dict, scale: ScaleResult) -> None:
        """Release the left mouse button (optionally at a coordinate)."""
        coord = action_input.get("coordinate")
        if coord is not None:
            x, y = self._xy(coord, scale)
            self._move(x, y)
        pyautogui.mouseUp(button="left")

    def _act_key(self, action_input: dict, scale: ScaleResult) -> None:
        """Press a key or key combination (e.g. ``"ctrl+s"`` or ``"Return"``)."""
        names = [_translate_key(t) for t in _parse_combo(action_input["text"])]
        if not names:
            return
        if len(names) == 1:
            pyautogui.press(names[0])
        else:
            pyautogui.hotkey(*names)

    def _act_hold_key(self, action_input: dict, scale: ScaleResult) -> None:
        """Hold a key down for a bounded duration then release it."""
        name = _translate_key(action_input["text"])
        duration = float(action_input.get("duration", 1))
        pyautogui.keyDown(name)
        try:
            time.sleep(min(duration, 10))
        finally:
            pyautogui.keyUp(name)

    def _act_type(self, action_input: dict, scale: ScaleResult) -> None:
        """Type text, chunked to keep each ``write`` call bounded in size."""
        text = action_input.get("text", "")
        self._emit_type(True)
        try:
            for i in range(0, len(text), self.type_chunk):
                chunk = text[i : i + self.type_chunk]
                pyautogui.write(chunk, interval=self.type_interval)
        finally:
            self._emit_type(False)

    def _act_scroll(self, action_input: dict, scale: ScaleResult) -> None:
        """Scroll vertically or horizontally at the given coordinate."""
        coord = action_input.get("coordinate")
        if coord is not None:
            x, y = self._xy(coord, scale)
            self._move(x, y)
        else:
            x, y = pyautogui.position()

        amount = int(action_input.get("scroll_amount", 1))
        direction = str(action_input.get("scroll_direction", "down")).lower()
        modifier = action_input.get("text")
        mods = [_translate_key(t) for t in _parse_combo(modifier)] if modifier else []

        for mod in mods:
            pyautogui.keyDown(mod)
        try:
            if direction in ("up", "down"):
                sign = 1 if direction == "up" else -1
                pyautogui.scroll(sign * amount * 100, x=x, y=y)
            else:
                sign = 1 if direction == "right" else -1
                hscroll = getattr(pyautogui, "hscroll", None)
                if callable(hscroll):
                    hscroll(sign * amount * 100, x=x, y=y)
                else:
                    pyautogui.scroll(sign * amount * 100, x=x, y=y)
        finally:
            for mod in reversed(mods):
                pyautogui.keyUp(mod)

    def _act_zoom(self, action_input: dict, scale: ScaleResult) -> ActionResult:
        """Zoom into a region of the screen.

        Implementation choice: crop the region from a fresh *native* capture and
        re-encode it as PNG at native resolution (1:1 scale), giving the model a
        high-detail view. ``region`` corners are in scaled/model space and are
        converted to native pixels via :func:`to_screen`. The returned
        :class:`ActionResult` carries a :class:`ScaleResult` with ``scale=1.0`` so
        subsequent coordinates are read against the crop's native pixels. On any
        failure we fall back to a full fresh screenshot.
        """
        region = action_input.get("region")
        try:
            import base64 as _base64
            import io as _io

            from PIL import Image

            png_bytes, _shot_scale = self.capture.capture()
            image = Image.open(_io.BytesIO(png_bytes)).convert("RGB")
            full_w, full_h = image.size

            x1, y1 = to_screen(region[0], region[1], scale)
            x2, y2 = to_screen(region[2], region[3], scale)
            left, right = sorted((x1, x2))
            top, bottom = sorted((y1, y2))
            left = max(0, min(left, full_w - 1))
            right = max(left + 1, min(right, full_w))
            top = max(0, min(top, full_h - 1))
            bottom = max(top + 1, min(bottom, full_h))

            crop = image.crop((left, top, right, bottom))
            crop_w, crop_h = crop.size
            buffer = _io.BytesIO()
            crop.save(buffer, format="PNG")
            b64 = _base64.b64encode(buffer.getvalue()).decode("ascii")
            crop_scale = ScaleResult(
                scale=1.0,
                scaled_width=crop_w,
                scaled_height=crop_h,
                native_width=crop_w,
                native_height=crop_h,
            )
            return ActionResult(
                base64_image=b64,
                scale=crop_scale,
                output=f"zoomed region {region}",
            )
        except Exception:  # noqa: BLE001 - fall back to a full screenshot
            img, full_scale = self.capture.capture_base64()
            return ActionResult(
                base64_image=img,
                scale=full_scale,
                output=f"zoom requested {region}; returned full screenshot",
            )

    # -- description helper ---------------------------------------------------

    def _describe(self, action_input: dict, scale: ScaleResult) -> str:
        """Build a concise description of a mutating action for logs/prompts."""
        name = action_input.get("action", "")
        coord = action_input.get("coordinate")
        if name == "type":
            text = action_input.get("text", "")
            preview = text if len(text) <= 80 else text[:77] + "..."
            return f"type {preview!r}"
        if name in ("key", "hold_key"):
            return f"{name} {action_input.get('text', '')!r}"
        if name == "scroll":
            return (
                f"scroll {action_input.get('scroll_direction', 'down')} "
                f"x{action_input.get('scroll_amount', 1)}"
            )
        if name == "left_click_drag":
            start = action_input.get("start_coordinate")
            return f"drag from {start} to {coord}"
        if coord is not None:
            x, y = self._xy(coord, scale)
            return f"{name} at scaled {coord} -> native ({x},{y})"
        return name


#: Dispatch table from action name to bound handler. Defined after the class body
#: so the handler functions resolve as unbound methods called with ``self``.
ActionExecutor._HANDLERS = {  # type: ignore[attr-defined]
    "mouse_move": ActionExecutor._act_mouse_move,
    "left_click": ActionExecutor._act_left_click,
    "right_click": ActionExecutor._act_right_click,
    "middle_click": ActionExecutor._act_middle_click,
    "double_click": ActionExecutor._act_double_click,
    "triple_click": ActionExecutor._act_triple_click,
    "left_click_drag": ActionExecutor._act_left_click_drag,
    "left_mouse_down": ActionExecutor._act_left_mouse_down,
    "left_mouse_up": ActionExecutor._act_left_mouse_up,
    "key": ActionExecutor._act_key,
    "hold_key": ActionExecutor._act_hold_key,
    "type": ActionExecutor._act_type,
    "scroll": ActionExecutor._act_scroll,
    "zoom": ActionExecutor._act_zoom,
}
