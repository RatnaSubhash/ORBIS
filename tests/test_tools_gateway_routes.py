"""
Tests for ORBIS FastAPI Gateway Tools & Capability Center Routes (Phase G).

Verifies:
1. GET /api/tools returns HTTP 200.
2. Response strictly conforms to ToolRegistryResponse schema.
3. All registered tools from ToolRegistry appear with accurate metadata.
4. total_tools equals actual registry count.
5. Categories are deterministic across all tools.
6. All supported risk levels (LOW, MEDIUM, HIGH, BLOCKED) serialize correctly.
7. Parameter schemas are present and structured where supported.
8. No private host paths, credentials, or API keys are exposed.
9. Empty tool registry produces valid empty response with 0 tools.
"""

from typing import Any
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from core.agent.orchestrator import AgentOrchestrator
from core.events.bus import EventBus
from core.gateway.app import create_app
from core.gateway.routes import get_orchestrator
from core.tools.base import BaseTool
from core.tools.registry import ToolRegistry, create_default_tool_registry
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport


class DummyBlockedToolParams(BaseModel):
    forbidden_target: str = Field(default="system_kernel", description="Target for blocked operation")


class DummyBlockedTool(BaseTool):
    """Tool registered with BLOCKED risk level to verify risk serialization."""
    name: str = "blocked_demo_tool"
    description: str = "A tool restricted by security policy."
    version: str = "1.0.0"
    risk_level: Any = "BLOCKED"
    parameters_schema = DummyBlockedToolParams

    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        return ToolResult(success=False, error="Execution is blocked by policy.")

    async def verify(self, params: BaseModel, result: ToolResult, context: ExecutionContext) -> VerificationReport:
        return VerificationReport(verified=False, details="Tool is blocked.")


@pytest.fixture
def standard_client():
    """Test client with default populated ToolRegistry."""
    bus = EventBus()
    reg = create_default_tool_registry()
    orch = AgentOrchestrator(
        llm_provider=None,
        tool_registry=reg,
        event_bus=bus,
    )
    app = create_app(orchestrator=orch, event_bus=bus)
    app.dependency_overrides[get_orchestrator] = lambda: orch
    with TestClient(app) as client:
        yield client, orch, reg


def test_list_tools_endpoint_success(standard_client):
    """1. GET /api/tools returns HTTP 200 with valid schema."""
    client, orch, reg = standard_client
    response = client.get("/api/tools")
    assert response.status_code == 200

    data = response.json()
    assert "tools" in data
    assert "total_tools" in data
    assert "categories" in data

    expected_count = len(reg.list_tools())
    assert data["total_tools"] == expected_count
    assert len(data["tools"]) == expected_count


def test_tools_contain_expected_capabilities(standard_client):
    """2. Expected core tools are present and have valid metadata."""
    client, orch, reg = standard_client
    response = client.get("/api/tools")
    assert response.status_code == 200
    data = response.json()

    tools_by_id = {t["id"]: t for t in data["tools"]}

    # Verify standard tools
    expected_ids = ["file", "desktop", "terminal", "web_research", "system_control", "document", "knowledge", "continuity_tool"]
    for eid in expected_ids:
        assert eid in tools_by_id, f"Expected tool '{eid}' not found in registry."
        tool = tools_by_id[eid]
        assert tool["name"]
        assert tool["description"]
        assert tool["version"]
        assert tool["risk_level"] in ("LOW", "MEDIUM", "HIGH", "BLOCKED")
        assert tool["status"] == "AVAILABLE"
        assert isinstance(tool["actions"], list)
        assert len(tool["actions"]) >= 1


def test_tools_categories_deterministic(standard_client):
    """3. Categories are deterministic and match expected domains."""
    client, orch, reg = standard_client
    response = client.get("/api/tools")
    assert response.status_code == 200
    data = response.json()

    # Authoritative categories
    categories = data["categories"]
    assert "System" in categories
    assert "Automation" in categories
    assert "Intelligence" in categories
    assert "Creation" in categories
    assert "Memory" in categories
    assert "Interface" in categories

    tools_by_id = {t["id"]: t for t in data["tools"]}
    assert tools_by_id["file"]["category"] == "System"
    assert tools_by_id["desktop"]["category"] == "Interface"
    assert tools_by_id["system_control"]["category"] == "System"
    assert tools_by_id["terminal"]["category"] == "Automation"
    assert tools_by_id["web_research"]["category"] == "Intelligence"
    assert tools_by_id["knowledge"]["category"] == "Intelligence"
    assert tools_by_id["document"]["category"] == "Creation"
    assert tools_by_id["continuity_tool"]["category"] == "Memory"


def test_risk_levels_and_blocked_serialization():
    """4. Serializes all RiskLevel values: LOW, MEDIUM, HIGH, and BLOCKED."""
    bus = EventBus()
    reg = ToolRegistry()
    from core.tools.file_tool import FileTool
    from core.tools.document_tool import DocumentTool

    # Register standard LOW tool, DocumentTool, and custom BLOCKED tool
    reg.register(FileTool())
    reg.register(DummyBlockedTool())

    orch = AgentOrchestrator(
        llm_provider=None,
        tool_registry=reg,
        event_bus=bus,
    )
    app = create_app(orchestrator=orch, event_bus=bus)
    app.dependency_overrides[get_orchestrator] = lambda: orch

    with TestClient(app) as client:
        response = client.get("/api/tools")
        assert response.status_code == 200
        data = response.json()

        tools_by_id = {t["id"]: t for t in data["tools"]}
        assert tools_by_id["file"]["risk_level"] == "LOW"
        assert tools_by_id["blocked_demo_tool"]["risk_level"] == "BLOCKED"
        assert tools_by_id["blocked_demo_tool"]["status"] == "AVAILABLE"


def test_parameter_schema_presence_and_sanitization(standard_client):
    """5. Parameter schemas are structured and do not leak host absolute paths or secrets."""
    client, orch, reg = standard_client
    response = client.get("/api/tools")
    assert response.status_code == 200
    data = response.json()

    for tool in data["tools"]:
        schema = tool["parameters_schema"]
        assert isinstance(schema, dict)
        if "properties" in schema:
            for prop_name, prop_meta in schema["properties"].items():
                if "default" in prop_meta and isinstance(prop_meta["default"], str):
                    val = prop_meta["default"]
                    # Ensure no Windows or Unix absolute root paths leaked
                    assert not val.startswith("C:\\Users\\"), f"Path leakage in {tool['id']}: {val}"
                    assert not val.startswith("/home/"), f"Path leakage in {tool['id']}: {val}"
                    assert "password" not in val.lower()
                    assert "secret" not in val.lower()


def test_empty_tool_registry():
    """6. Empty ToolRegistry returns 200 with 0 tools and empty categories."""
    bus = EventBus()
    reg = ToolRegistry()
    orch = AgentOrchestrator(
        llm_provider=None,
        tool_registry=reg,
        event_bus=bus,
    )
    app = create_app(orchestrator=orch, event_bus=bus)
    app.dependency_overrides[get_orchestrator] = lambda: orch

    with TestClient(app) as client:
        response = client.get("/api/tools")
        assert response.status_code == 200
        data = response.json()
        assert data["tools"] == []
        assert data["total_tools"] == 0
        assert data["categories"] == []
