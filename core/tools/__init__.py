"""
Tool Subsystem for ORBIS.
Phase 1: BaseTool contract, ToolRegistry, and EchoTool demonstration tool.
Phase 2: DesktopTool (safe read-only desktop inspection) and FileTool (safe read-only file metadata inspection).
"""

from core.tools.base import BaseTool
from core.tools.continuity_tool import ContinuityTool, ContinuityToolParameters
from core.tools.desktop_tool import DesktopTool
from core.tools.echo_tool import EchoTool
from core.tools.file_tool import FileTool
from core.tools.registry import ToolRegistry
from core.tools.search_provider import (
    BaseSearchProvider,
    ConfigurableSearchProvider,
    MockSearchProvider,
    SearchResult,
)
from core.tools.terminal_tool import CommandPolicy, TerminalActionParameters, TerminalTool
from core.tools.web_research_tool import (
    SafeHTMLTextExtractor,
    WebResearchParameters,
    WebResearchTool,
    validate_and_sanitize_url,
)

__all__ = [
    "BaseTool",
    "ToolRegistry",
    "EchoTool",
    "DesktopTool",
    "FileTool",
    "ContinuityTool",
    "ContinuityToolParameters",
    "BaseSearchProvider",
    "MockSearchProvider",
    "ConfigurableSearchProvider",
    "SearchResult",
    "WebResearchTool",
    "WebResearchParameters",
    "SafeHTMLTextExtractor",
    "validate_and_sanitize_url",
    "TerminalTool",
    "TerminalActionParameters",
    "CommandPolicy",
]
