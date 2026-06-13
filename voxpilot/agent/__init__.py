"""VoxPilot agent package.

Exposes the computer-use client and the agent sampling loop that drives
Claude's computer-use tool against the real screen.
"""

from .anthropic_client import ComputerUseClient, build_computer_tool
from .loop import AgentLoop

__all__ = ["ComputerUseClient", "build_computer_tool", "AgentLoop"]
