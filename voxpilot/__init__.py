"""VoxPilot - a hold-to-talk, voice-controlled screen agent.

Hold a hotkey, speak a command, release; VoxPilot transcribes locally,
screenshots the screen, asks Claude's computer-use tool what to do, and
executes the returned mouse/keyboard actions on the real screen.

This top-level package intentionally avoids importing heavy or optional
dependencies (pyautogui, mss, anthropic, faster-whisper, ...) at import
time so that lightweight uses (``--help``, banners, tests) stay cheap and
never fail because of missing hardware or native libraries.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
