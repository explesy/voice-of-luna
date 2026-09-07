"""Plugin subsystem for Voice of Luna."""

from .base import Plugin, PluginTurnResult, TurnContext
from .focus_sprint import FocusSprintPlugin
from .manager import LunaCorePlugin, PluginManager, TestContextPlugin, plugin_manager
from .spanish_buddy import SpanishBuddyPlugin

__all__ = [
    "FocusSprintPlugin",
    "LunaCorePlugin",
    "Plugin",
    "PluginManager",
    "PluginTurnResult",
    "SpanishBuddyPlugin",
    "TestContextPlugin",
    "TurnContext",
    "plugin_manager",
]
