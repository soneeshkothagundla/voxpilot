"""Safety subsystem for VoxPilot.

Exposes :class:`SafetyGuard`, which enforces dry-run/confirmation gating,
destructive-action detection, an abortable kill switch, and a rotating
JSON action log.
"""

from .guard import SafetyGuard

__all__ = ["SafetyGuard"]
