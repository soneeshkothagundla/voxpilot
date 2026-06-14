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
    parser.add_argument(
        "--windowed",
        action="store_true",
        help="Desktop mode: on-screen overlay + system tray, no terminal needed "
        "(launch with pythonw.exe to hide the console entirely).",
    )
    parser.add_argument(
        "--jarvis",
        action="store_true",
        help="Jarvis mode: hands-free wake word ('Hey Jarvis') instead of "
        "push-to-talk. Say the wake word, then speak your command.",
    )
    parser.add_argument(
        "--autonomy",
        choices=["supervised", "semi", "full"],
        default=None,
        help="Autonomy level: 'supervised' (confirm risky), 'semi', or 'full' "
        "(auto everything except the non-bypassable catastrophic floor).",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Headless web command UI (for Docker/VM): drive + watch in a browser.",
    )
    parser.add_argument(
        "--port",
        metavar="N",
        type=int,
        default=5000,
        help="Port for the --serve web UI (default 5000).",
    )
    return parser


def build_banner(cfg: Config, use_opus: bool, once_mode: bool, jarvis: bool = False) -> str:
    """Build the multi-line startup banner.

    Args:
        cfg: The fully loaded configuration.
        use_opus: Whether the Opus model was selected.
        once_mode: Whether VoxPilot is running a single ``--once`` instruction.
        jarvis: Whether hands-free wake-word ("Hey Jarvis") mode is active.

    Returns:
        A human-readable, multi-line banner string.
    """
    model = cfg.resolved_model(use_opus)
    safety_mode = "DRY-RUN" if cfg.safety.dry_run else "LIVE"
    confirm = "on" if cfg.safety.confirm_destructive else "off"
    failsafe = "on" if cfg.safety.failsafe_corner else "off"
    if once_mode:
        run_mode = "once (single instruction)"
    elif jarvis:
        run_mode = "jarvis (wake word, hands-free)"
    else:
        run_mode = "interactive (push-to-talk)"
    if jarvis:
        trigger = f"  Wake word    : say '{cfg.hotkey.wake_word}' (hands-free)"
    else:
        trigger = f"  Hotkey       : hold '{cfg.hotkey.ptt_key}'  [{cfg.hotkey.mode}]"

    lines = [
        "=" * 64,
        f"  VoxPilot v{__version__} - voice-controlled screen agent",
        "=" * 64,
        f"  Provider     : {cfg.agent.provider}",
        f"  Model        : {model}",
        f"  STT backend  : {cfg.stt.backend} ({cfg.stt.model})",
        trigger,
        (f"  Kill switch  : press '{cfg.hotkey.kill_key}' x{cfg.hotkey.kill_press_count} to abort"),
        f"  Safety       : {safety_mode}  (confirm={confirm}, failsafe={failsafe})",
        f"  Autonomy     : {cfg.safety.autonomy}  (catastrophic actions always need a human yes)",
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
    if args.autonomy:
        cfg.safety.autonomy = args.autonomy
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


def _redirect_stdio_to_log() -> None:
    """Point stdout/stderr at a log file for windowed/pythonw launches.

    Under ``pythonw.exe`` there is no console and ``sys.stdout``/``sys.stderr``
    are ``None``, so any ``print`` would raise. Redirect both to
    ``~/.voxpilot/logs/voxpilot.log`` so output is captured instead of crashing.
    """
    import io
    from pathlib import Path

    try:
        log_dir = Path.home() / ".voxpilot" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handle = open(  # noqa: SIM115 - kept open for the process lifetime
            log_dir / "voxpilot.log", "a", encoding="utf-8", errors="replace", buffering=1
        )
        sys.stdout = handle
        sys.stderr = handle
    except Exception:  # noqa: BLE001 - last resort: swallow output, never crash
        sink = io.StringIO()
        if sys.stdout is None:
            sys.stdout = sink
        if sys.stderr is None:
            sys.stderr = sink


def main(argv: list[str] | None = None) -> int:
    """VoxPilot entry point.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: 0 on success, 2 on configuration error.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # In windowed mode (often launched via pythonw.exe, which has no console)
    # redirect output to a log file so prints never crash; otherwise force UTF-8.
    if args.windowed:
        _redirect_stdio_to_log()
    else:
        _configure_stdio()

    # Configure DPI awareness BEFORE importing any pyautogui/mss-touching module.
    ensure_dpi_awareness()

    cfg = Config.load(args.config)
    _apply_overrides(cfg, args)

    print(build_banner(cfg, args.opus, once_mode=args.once is not None, jarvis=args.jarvis))
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
    from voxpilot.feedback import Feedback
    from voxpilot.safety import SafetyGuard
    from voxpilot.screen.actions import ActionExecutor
    from voxpilot.screen.screenshot import ScreenCapture

    feedback = Feedback(cfg.feedback)
    capture = ScreenCapture(cfg.agent.target_width, cfg.agent.target_height)
    guard = SafetyGuard(cfg.safety, cfg.log_dir, feedback=feedback)
    client = ComputerUseClient(cfg, use_opus=args.opus)
    executor = ActionExecutor(capture, guard, move_duration=cfg.agent.cursor_move_duration)
    loop = AgentLoop(client, capture, executor, guard, feedback, cfg)

    # ------------------------------------------------------------------ #
    # --windowed: desktop mode (on-screen overlay + tray, no terminal needed).
    # ------------------------------------------------------------------ #
    if args.windowed:
        return run_windowed(cfg, args, feedback, capture, guard, client, executor, loop)

    # ------------------------------------------------------------------ #
    # --serve: headless web command UI (for containers/VMs; type, don't speak).
    # ------------------------------------------------------------------ #
    if args.serve:
        from voxpilot import web

        feedback.say(f"Serving the VoxPilot web UI on port {args.port}.")
        web.serve(cfg, loop, feedback, guard, port=args.port)
        return 0

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
    from voxpilot.stt import create_stt

    stt = create_stt(cfg.stt, cfg.secrets)
    try:
        feedback.status("THINKING")
        stt.warm_up()
    except Exception as exc:  # noqa: BLE001 - degrade gracefully on STT load issues
        print(f"Warning: failed to warm up STT backend: {exc}", file=sys.stderr)
    feedback.status("IDLE")

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

    if args.jarvis:
        from voxpilot.audio import WakeWordListener

        recorder = None
        listener = WakeWordListener(cfg.hotkey, on_utterance)
        try:
            feedback.status("THINKING")
            listener.warm_up()
        except Exception as exc:  # noqa: BLE001 - degrade gracefully if model missing
            print(f"Warning: failed to load wake-word model: {exc}", file=sys.stderr)
        feedback.status("IDLE")
    else:
        from voxpilot.audio import HotkeyController, PushToTalkRecorder

        recorder = PushToTalkRecorder()
        listener = HotkeyController(
            cfg.hotkey, recorder, on_utterance, show_meter=cfg.feedback.verbose
        )

    guard.start_kill_switch(cfg.hotkey)
    listener.start()
    feedback.status("IDLE")
    if args.jarvis:
        print(f"\nSay '{cfg.hotkey.wake_word}', then speak your command. Ctrl+C to quit.\n")
    else:
        print(f"\nHold '{cfg.hotkey.ptt_key}' to talk. Ctrl+C to quit.\n")

    try:
        import time

        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        listener.stop()
        guard.stop_kill_switch()
        if recorder is not None:
            recorder.close()
        feedback.shutdown()
    return 0


def run_windowed(cfg, args, feedback, capture, guard, client, executor, loop) -> int:
    """Run VoxPilot in desktop mode: on-screen overlay + system tray, no terminal.

    The tkinter overlay event loop runs on this (main) thread; the global hotkey
    listener, STT warm-up, and agent loop all run on background threads. Quit via
    the tray menu or the Ctrl+Alt+Q hotkey.
    """
    import threading

    from pynput import keyboard as kb

    from voxpilot.audio import HotkeyController, PushToTalkRecorder, WakeWordListener
    from voxpilot.stt import create_stt
    from voxpilot.ui import Overlay, TrayIcon

    # There is no terminal to answer a confirmation prompt in windowed mode.
    cfg.safety.confirm_destructive = False
    guard.confirm_enabled = False

    overlay = Overlay()
    tray = TrayIcon(on_quit=overlay.stop)

    def on_status(state: str) -> None:
        """Mirror agent status onto the tray icon and the on-screen overlay."""
        s = state.lower()
        tray.set_state(s)
        if s in ("thinking", "acting"):
            overlay.show_working()
        elif s in ("idle", "done"):
            overlay.hide()

    feedback.status_sink = on_status

    stt = create_stt(cfg.stt, cfg.secrets)

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

    def stt_warm_up() -> None:
        try:
            feedback.status("THINKING")
            stt.warm_up()
        except Exception as exc:  # noqa: BLE001
            feedback.say(f"STT warm-up failed: {exc}")
        feedback.status("IDLE")

    if args.jarvis:
        recorder = None
        listener = WakeWordListener(
            cfg.hotkey,
            on_utterance,
            on_wake=overlay.show_listening,
            on_listen_start=overlay.show_listening,
            on_level=overlay.update_level,
            on_listen_stop=overlay.show_working,
        )

        def warm_up() -> None:
            stt_warm_up()
            try:
                listener.warm_up()
            except Exception as exc:  # noqa: BLE001
                feedback.say(f"Wake-word warm-up failed: {exc}")

    else:
        recorder = PushToTalkRecorder()
        listener = HotkeyController(
            cfg.hotkey,
            recorder,
            on_utterance,
            show_meter=False,
            on_listen_start=overlay.show_listening,
            on_level=overlay.update_level,
            on_listen_stop=overlay.show_working,
        )
        warm_up = stt_warm_up

    quit_hotkey = kb.GlobalHotKeys({"<ctrl>+<alt>+q": overlay.stop})

    tray.start()
    guard.start_kill_switch(cfg.hotkey)
    threading.Thread(target=warm_up, name="voxpilot-warmup", daemon=True).start()
    listener.start()
    quit_hotkey.start()
    if args.jarvis:
        feedback.say(f"Jarvis ready. Say {cfg.hotkey.wake_word.replace('_', ' ')}.")
    else:
        feedback.say(f"VoxPilot ready. Hold {cfg.hotkey.ptt_key.upper()} to talk.")
    feedback.status("IDLE")

    try:
        overlay.run()  # blocks on the main thread until overlay.stop()
    finally:
        listener.stop()
        try:
            quit_hotkey.stop()
        except Exception:  # noqa: BLE001
            pass
        guard.stop_kill_switch()
        if recorder is not None:
            recorder.close()
        tray.stop()
        feedback.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
