"""VoxPilot command-line entry point (``python -m voxpilot``).

This module wires together configuration, the speech-to-text backend, the
push-to-talk recorder, the computer-use agent loop, and the safety guard.

Order of operations matters: :func:`ensure_dpi_awareness` MUST be called before
any module that touches ``pyautogui``/``mss`` is imported, so that those
libraries agree on physical pixels on high-DPI Windows displays. For that
reason the heavy, ``pyautogui``-touching imports (``voxpilot.screen.*``,
``voxpilot.agent.*``, ``voxpilot.audio.*``) are performed lazily *inside*
:func:`main` after DPI awareness has been configured.

Only light, side-effect-free modules (``argparse``, ``sys``, ``platform`` and
:mod:`voxpilot.config`) are imported at module scope so that ``-h``/``--help``
and the startup banner work even without credentials or network access.
"""

from __future__ import annotations

import argparse
import platform
import sys

from voxpilot import __version__
from voxpilot.config import Config, ConfigError
from voxpilot.screen.scaling import ensure_dpi_awareness


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser.

    Returns:
        A configured :class:`argparse.ArgumentParser`. ``-h``/``--help`` works
        without any credentials or network access.
    """
    parser = argparse.ArgumentParser(
        prog="voxpilot",
        description=(
            "VoxPilot - a voice-controlled screen agent. Hold a hotkey, speak a "
            "command, release; it transcribes locally, screenshots the screen, "
            "and drives Claude's computer-use tool to control your real "
            "mouse and keyboard."
        ),
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="Path to a config.yaml file (defaults to ./config.yaml if present).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log actions instead of executing them (nothing touches the screen).",
    )
    parser.add_argument(
        "--opus",
        action="store_true",
        help="Use the Opus model instead of the default Sonnet model.",
    )
    parser.add_argument(
        "--provider",
        choices=["bedrock", "anthropic"],
        default=None,
        help="Override the model provider (bedrock or anthropic).",
    )
    parser.add_argument(
        "--model",
        metavar="STR",
        default=None,
        help="Override the model id to use.",
    )
    parser.add_argument(
        "--no-tts",
        action="store_true",
        help="Disable text-to-speech feedback.",
    )
    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Disable the confirmation prompt before destructive actions (use with care).",
    )
    parser.add_argument(
        "--once",
        metavar="TEXT",
        default=None,
        help="Run a single instruction then exit (no microphone needed).",
    )
    parser.add_argument(
        "--max-iter",
        metavar="N",
        type=int,
        default=None,
        help="Override the maximum number of agent iterations.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce console output (disables verbose feedback).",
    )
    return parser


def build_banner(cfg: Config, use_opus: bool, once_mode: bool) -> str:
    """Build the multi-line startup banner.

    Args:
        cfg: The fully loaded configuration.
        use_opus: Whether the Opus model was selected.
        once_mode: Whether VoxPilot is running a single ``--once`` instruction.

    Returns:
        A human-readable, multi-line banner string.
    """
    model = cfg.resolved_model(use_opus)
    safety_mode = "DRY-RUN" if cfg.safety.dry_run else "LIVE"
    confirm = "on" if cfg.safety.confirm_destructive else "off"
    failsafe = "on" if cfg.safety.failsafe_corner else "off"
    run_mode = "once (single instruction)" if once_mode else "interactive (push-to-talk)"

    lines = [
        "=" * 64,
        f"  VoxPilot v{__version__} - voice-controlled screen agent",
        "=" * 64,
        f"  Provider     : {cfg.agent.provider}",
        f"  Model        : {model}",
        f"  STT backend  : {cfg.stt.backend} ({cfg.stt.model})",
        f"  Hotkey       : hold '{cfg.hotkey.ptt_key}'  [{cfg.hotkey.mode}]",
        (
            f"  Kill switch  : press '{cfg.hotkey.kill_key}' "
            f"x{cfg.hotkey.kill_press_count} to abort"
        ),
        f"  Safety       : {safety_mode}  (confirm={confirm}, failsafe={failsafe})",
        f"  Platform     : {platform.system()} ({platform.platform()})",
        f"  Run mode     : {run_mode}",
        "-" * 64,
        "  WARNING: VoxPilot controls your REAL mouse and keyboard.",
        "  Supervise it at all times. Use --dry-run to preview safely.",
        "=" * 64,
    ]
    return "\n".join(lines)


def print_macos_permissions() -> None:
    """Print the macOS permission notes (only on Darwin)."""
    if sys.platform != "darwin":
        return
    notes = [
        "",
        "macOS permissions required (System Settings > Privacy & Security):",
        "  - Accessibility       : Privacy & Security > Accessibility",
        "  - Screen Recording    : Privacy & Security > Screen Recording",
        "  - Microphone          : Privacy & Security > Microphone",
        "  - Input Monitoring    : Privacy & Security > Input Monitoring",
        "Add your terminal/Python to each list. After granting, fully quit",
        "(Cmd-Q) and relaunch the app so the new permissions take effect.",
        "",
    ]
    print("\n".join(notes))


def _apply_overrides(cfg: Config, args: argparse.Namespace) -> None:
    """Apply command-line overrides onto a loaded :class:`Config` in place.

    Args:
        cfg: The configuration to mutate.
        args: Parsed command-line arguments.
    """
    if args.dry_run:
        cfg.safety.dry_run = True
    if args.provider:
        cfg.agent.provider = args.provider
    if args.model:
        cfg.agent.model = args.model
    if args.no_tts:
        cfg.feedback.tts = False
    if args.no_confirm:
        cfg.safety.confirm_destructive = False
    if args.max_iter is not None:
        cfg.agent.max_iterations = args.max_iter
    if args.quiet:
        cfg.feedback.verbose = False


def _configure_stdio() -> None:
    """Force UTF-8 stdout/stderr so arbitrary model text never crashes the console.

    On Windows the default console encoding (cp1252) raises ``UnicodeEncodeError``
    when asked to print characters it cannot represent. Reconfiguring to UTF-8 with
    ``errors="replace"`` keeps output flowing instead of crashing the agent.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - best effort, never fatal
                pass


def main(argv: list[str] | None = None) -> int:
    """VoxPilot entry point.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: 0 on success, 2 on configuration error.
    """
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    # Configure DPI awareness BEFORE importing any pyautogui/mss-touching module.
    ensure_dpi_awareness()

    cfg = Config.load(args.config)
    _apply_overrides(cfg, args)

    print(build_banner(cfg, args.opus, once_mode=args.once is not None))
    print_macos_permissions()

    try:
        cfg.validate()
    except ConfigError as exc:
        print(f"\nConfiguration error: {exc}", file=sys.stderr)
        print(
            "\nTo fix this, create a .env file in your working directory with "
            "the required credentials, e.g.:\n"
            "  AWS_BEARER_TOKEN_BEDROCK=your-bedrock-bearer-token\n"
            "  AWS_REGION=us-east-1\n"
            "See README.md and .env.example for details.",
            file=sys.stderr,
        )
        return 2

    # Heavy / pyautogui-touching imports happen AFTER ensure_dpi_awareness().
    from voxpilot.agent import AgentLoop, ComputerUseClient
    from voxpilot.audio import HotkeyController, PushToTalkRecorder
    from voxpilot.feedback import Feedback
    from voxpilot.safety import SafetyGuard
    from voxpilot.screen.actions import ActionExecutor
    from voxpilot.screen.screenshot import ScreenCapture
    from voxpilot.stt import create_stt

    feedback = Feedback(cfg.feedback)
    capture = ScreenCapture(cfg.agent.target_width, cfg.agent.target_height)
    guard = SafetyGuard(cfg.safety, cfg.log_dir, feedback=feedback)
    client = ComputerUseClient(cfg, use_opus=args.opus)
    executor = ActionExecutor(capture, guard)
    loop = AgentLoop(client, capture, executor, guard, feedback, cfg)

    # ------------------------------------------------------------------ #
    # --once: run a single instruction then exit (non-microphone path).
    # ------------------------------------------------------------------ #
    if args.once:
        try:
            feedback.say(f"Running: {args.once}")
            result = loop.run(args.once)
            print(result)
        finally:
            feedback.shutdown()
        return 0

    # ------------------------------------------------------------------ #
    # Interactive push-to-talk mode.
    # ------------------------------------------------------------------ #
    feedback.say("Loading speech recognition...")
    stt = create_stt(cfg.stt, cfg.secrets)
    try:
        feedback.status("THINKING")
        stt.warm_up()
    except Exception as exc:  # noqa: BLE001 - degrade gracefully on STT load issues
        print(f"Warning: failed to warm up STT backend: {exc}", file=sys.stderr)
    feedback.status("IDLE")

    recorder = PushToTalkRecorder()

    def on_utterance(audio) -> None:
        """Transcribe a captured utterance and drive the agent loop."""
        feedback.status("THINKING")
        try:
            text = stt.transcribe(audio)
        except Exception as exc:  # noqa: BLE001 - keep the listener alive
            feedback.say(f"Transcription failed: {exc}")
            feedback.status("IDLE")
            return
        if not text or not text.strip():
            feedback.status("IDLE")
            return
        feedback.say(f"Heard: {text}")
        try:
            loop.run(text)
        except Exception as exc:  # noqa: BLE001 - keep the listener alive
            feedback.say(f"Error: {exc}")
        feedback.status("IDLE")

    controller = HotkeyController(
        cfg.hotkey, recorder, on_utterance, show_meter=cfg.feedback.verbose
    )

    guard.start_kill_switch(cfg.hotkey)
    controller.start()
    feedback.status("IDLE")
    print(f"\nHold '{cfg.hotkey.ptt_key}' to talk. Ctrl+C to quit.\n")

    try:
        import time

        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        controller.stop()
        guard.stop_kill_switch()
        recorder.close()
        feedback.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
