"""
Tool Registry for ORBIS.
Manages tool registration, lookup, duplicate name prevention, and catalog discovery.
"""

from typing import Any, Dict, List, Optional

from core.tools.base import BaseTool


class ToolRegistry:
    """Registry maintaining available tools for the Agent Brain."""

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """
        Registers a tool.
        Raises ValueError if a tool with the same name is already registered.
        """
        if not tool.name or not tool.name.strip():
            raise ValueError("Tool name must be a non-empty string.")

        name = tool.name.strip()
        if name in self._tools:
            raise ValueError(
                f"Tool with name '{name}' is already registered. "
                f"Existing: {self._tools[name].__class__.__name__}, New: {tool.__class__.__name__}"
            )
        self._tools[name] = tool

    def unregister(self, name: str) -> None:
        """Removes a tool from the registry if present."""
        self._tools.pop(name.strip(), None)

    def get(self, name: str) -> Optional[BaseTool]:
        """Retrieves a tool by name, returning None if not found."""
        return self._tools.get(name.strip())

    def get_or_raise(self, name: str) -> BaseTool:
        """
        Retrieves a tool by name or raises KeyError if not found.
        """
        tool = self.get(name)
        if tool is None:
            raise KeyError(f"Tool '{name}' is not registered.")
        return tool

    def list_tools(self) -> List[BaseTool]:
        """Returns a list of all registered tools."""
        return list(self._tools.values())

    def get_tool_schemas(self) -> List[Dict[str, str]]:
        """
        Returns high-level metadata and schemas for all tools.
        Suitable for informing the LLM of available capabilities.
        """
        schemas = []
        for tool in self._tools.values():
            schemas.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "version": tool.version,
                    "risk_level": tool.risk_level.value,
                    "parameters_schema": tool.get_schema_json(),
                }
            )
        return schemas


def create_default_tool_registry(
    db: Optional[Any] = None,
    search_provider: Optional[Any] = None,
    mock_web_fetcher: Optional[Any] = None,
    allowed_workspace_root: Optional[str] = None,
    mock_terminal_runner: Optional[Any] = None,
    content_generator: Optional[Any] = None,
    tts_provider: Optional[Any] = None,
    llm_provider: Optional[Any] = None,
    mock_app_launcher: Optional[Any] = None,
    mock_network_backend: Optional[Any] = None,
    mock_messaging_backend: Optional[Any] = None,
    mock_hardware_backend: Optional[Any] = None,
) -> ToolRegistry:
    """
    Constructs an authoritative, fully populated ToolRegistry containing all
    implemented Phase 1 through Phase 12 tools with their standard configurations.
    Ensures WebResearchTool, SystemUtilityTool, KnowledgeTool, and extended system control tools
    are registered across all agent pipelines.
    """
    from core.tools.echo_tool import EchoTool
    from core.tools.file_tool import FileTool
    from core.tools.desktop_tool import DesktopTool
    from core.tools.continuity_tool import ContinuityTool
    from core.tools.terminal_tool import TerminalTool
    from core.tools.web_research_tool import WebResearchTool
    from core.tools.content_tool import ContentGeneratorTool
    from core.tools.document_tool import DocumentTool
    from core.tools.intent_tool import IntentContinuityTool
    from core.tools.restoration_tool import RestorationTool
    from core.tools.system_tool import SystemUtilityTool
    from core.tools.knowledge_tool import KnowledgeTool
    from core.tools.system_control_tool import SystemControlTool
    from core.tools.app_launch_tool import AppLaunchTool
    from core.tools.network_control_tool import NetworkControlTool
    from core.tools.messaging_tool import MessagingTool
    from core.tools.hardware_control_tool import HardwareControlTool

    if llm_provider is None:
        try:
            from core.agent.llm.factory import get_llm_provider
            llm_provider = get_llm_provider()
        except Exception:
            llm_provider = None

    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.register(FileTool())
    registry.register(DesktopTool())
    registry.register(SystemUtilityTool())
    registry.register(SystemControlTool())
    registry.register(KnowledgeTool(llm_provider=llm_provider))
    registry.register(
        AppLaunchTool(
            launcher=mock_app_launcher,
            allowed_workspace_root=allowed_workspace_root,
        )
    )
    registry.register(NetworkControlTool(backend=mock_network_backend))
    registry.register(MessagingTool(dispatcher=mock_messaging_backend))
    registry.register(HardwareControlTool(rgb_controller=mock_hardware_backend))

    if db is not None:
        registry.register(ContinuityTool(db=db))
        registry.register(IntentContinuityTool(db=db))
        registry.register(RestorationTool(db=db))
    else:
        registry.register(ContinuityTool())
        registry.register(IntentContinuityTool())
        registry.register(RestorationTool())

    registry.register(
        TerminalTool(
            allowed_workspace_root=allowed_workspace_root,
            mock_runner=mock_terminal_runner,
        )
    )
    if search_provider is None:
        from core.tools.search_provider import get_search_provider

        search_provider = get_search_provider()

    registry.register(WebResearchTool(search_provider=search_provider, mock_fetcher=mock_web_fetcher))
    registry.register(ContentGeneratorTool(generator=content_generator))
    registry.register(DocumentTool())

    if tts_provider is not None:
        from core.tools.voice_tool import VoiceTool
        registry.register(VoiceTool(tts_provider=tts_provider))

    return registry

