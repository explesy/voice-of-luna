"""Public, domain-neutral API for separately installed Voice of Luna plugins.

External packages should import plugin contracts from this module instead of
reaching into host implementation modules.
"""

from .plugins.base import (
    OutputEvent,
    Plugin,
    PluginTurnResult,
    ResponseCandidate,
    ResponseDecision,
    ToolCallContext,
    ToolResult,
    ToolSpec,
    TurnContext,
)

__all__ = [
    "Plugin",
    "PluginTurnResult",
    "OutputEvent",
    "ResponseCandidate",
    "ResponseDecision",
    "ToolCallContext",
    "ToolResult",
    "ToolSpec",
    "TurnContext",
]
