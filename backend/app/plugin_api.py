"""Public, domain-neutral API for separately installed Voice of Luna plugins.

External packages should import plugin contracts from this module instead of
reaching into host implementation modules.
"""

from .plugins.base import Plugin, PluginTurnResult, ToolCallContext, ToolResult, ToolSpec, TurnContext

__all__ = [
    "Plugin",
    "PluginTurnResult",
    "ToolCallContext",
    "ToolResult",
    "ToolSpec",
    "TurnContext",
]
