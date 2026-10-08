"""
Tests for Conversation Management Gateway Endpoints and Multi-Session Continuity.
Verifies:
- POST /api/tasks with conversation_id routes turns to that session
- Contextual pronoun and comparison continuity within a conversation session
- POST /api/conversation/reset clears the active conversation
- POST /api/conversation/new initializes a fresh conversation session
- GET /api/conversation/context retrieves active state and turn history
"""

import pytest
from fastapi.testclient import TestClient

from core.agent.llm.mock import MockLLMProvider
from core.agent.orchestrator import AgentOrchestrator
from core.events.bus import EventBus
from core.gateway.app import create_app
from core.gateway.task_manager import TaskManager
from core.tools.registry import create_default_tool_registry


@pytest.fixture
def client():
    bus = EventBus()
    llm = MockLLMProvider()
    reg = create_default_tool_registry(llm_provider=llm)
    orch = AgentOrchestrator(
        llm_provider=llm,
        tool_registry=reg,
        event_bus=bus,
    )
    mgr = TaskManager(max_tasks=50)
    app = create_app(
        orchestrator=orch,
        event_bus=bus,
        task_manager=mgr,
    )
    with TestClient(app) as test_client:
        yield test_client


def test_conversation_lifecycle_and_continuity(client):
    # 1. Start new conversation
    res_new = client.post("/api/conversation/new")
    assert res_new.status_code == 200
    data_new = res_new.json()
    assert data_new["status"] == "ok"
    conv_id = data_new["conversation_id"]
    assert conv_id.startswith("conv_")

    # 2. Submit Turn 1 in this conversation
    res_t1 = client.post(
        "/api/tasks",
        json={
            "prompt": "What about HSRP?",
            "conversation_id": conv_id,
            "sync": True,
        },
    )
    assert res_t1.status_code in (200, 202)

    # 3. Check conversation context contains Turn 1
    res_ctx1 = client.get(f"/api/conversation/context?conversation_id={conv_id}")
    assert res_ctx1.status_code == 200
    data_ctx1 = res_ctx1.json()
    assert data_ctx1["turn_count"] >= 1
    assert data_ctx1["active_entity"] == "HSRP"

    # 4. Submit Turn 2 in the same conversation with pronoun reference
    res_t2 = client.post(
        "/api/tasks",
        json={
            "prompt": "Explain its architecture.",
            "conversation_id": conv_id,
            "sync": True,
        },
    )
    assert res_t2.status_code in (200, 202)

    # Check that context updated with Turn 2
    res_ctx2 = client.get(f"/api/conversation/context?conversation_id={conv_id}")
    assert res_ctx2.status_code == 200
    data_ctx2 = res_ctx2.json()
    assert data_ctx2["turn_count"] == 2
    assert "HSRP" in data_ctx2["turns"][-1]["resolved_query"]

    # 5. Reset the conversation
    res_reset = client.post(
        "/api/conversation/reset",
        json={"conversation_id": conv_id},
    )
    assert res_reset.status_code == 200
    data_reset = res_reset.json()
    assert data_reset["status"] == "ok"

    # Context should now be empty for that conversation
    res_ctx3 = client.get(f"/api/conversation/context?conversation_id={conv_id}")
    assert res_ctx3.status_code == 200
    data_ctx3 = res_ctx3.json()
    assert data_ctx3["turn_count"] == 0
    assert data_ctx3["active_topic"] is None
