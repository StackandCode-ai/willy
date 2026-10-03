"""
Willy Windows deterministic system tools and function execution registry.
"""

from .system_tools import (
    execute_powershell,
    launch_application,
    open_url,
    interact_ui,
    get_system_status,
    file_operations,
    volume_control,
)
from .registry import TOOL_DEFINITIONS, execute_tool

__all__ = [
    "execute_powershell",
    "launch_application",
    "open_url",
    "interact_ui",
    "get_system_status",
    "file_operations",
    "volume_control",
    "TOOL_DEFINITIONS",
    "execute_tool",
]
