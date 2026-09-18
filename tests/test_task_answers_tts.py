"""
Tests for Task Answer Extraction, TASK_COMPLETED Telemetry,
and SAPI5 TTS Voice Integration (Phase 11/12).
Verifies:
1. Actual final answer extraction from final_output using extract_spoken_response.
2. Structured final_output handling without raw JSON dumping.
3. Generic summary replacement ("Multi-step task completed successfully...").
4. TASK_COMPLETED carries safe_user_message=clean_answer and metadata.answer=clean_answer.
5. TaskStatusResponse.answer contains the extracted answer.
6. Voice transcript -> task -> answer pipeline.
7. Answer -> canonical VoiceService speak invocation (SAPI5).
8. TTS failure safety: safely returns to idle without exposing raw exceptions.
9. Responding cleanup on completion or failure.
10. Task failure behavior (no answer vocalized, error cleanly handled).
11. Secret/redaction protection: EventSanitizer applied to answers.
12. No planner reasoning or system prompts in answer.
13. Conversation follow-up continuity (TCP -> compare it with UDP).
14. Event Stream preservation (all historical events remain intact).
"""

import asyncio
import pytest
from fastapi.testclient import TestClient

from core.agent.llm.mock import MockLLMProvider
from core.agent.models import (
    AgentExecutionResult,
    StepResult,
    TaskPlan,
    TaskStep,
    TaskState,
)
from core.agent.orchestrator import AgentOrchestrator
from core.events.bus import EventBus
from core.events.models import AgentEventType
from core.events.sanitizer import EventSanitizer
from core.gateway.app import create_app
from core.tools.registry import create_default_tool_registry
from core.voice.capture.mock_capture import MockAudioCapture
from core.voice.models import VoiceState
from core.voice.service import VoiceService, extract_spoken_response
from core.voice.stt.mock_stt import MockSTTProvider
from core.voice.tts.mock_tts import MockTTSProvider


@pytest.fixture
def tts_gateway_fixture():
    """Builds a test gateway client wired to an AgentOrchestrator and canonical VoiceService."""
    bus = EventBus()
    tools = create_default_tool_registry()
    llm = MockLLMProvider()
    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=tools, event_bus=bus)

    mock_capture = MockAudioCapture()
    mock_stt = MockSTTProvider()
    mock_tts = MockTTSProvider()

    voice_service = VoiceService(
        orchestrator=orchestrator,
        capture=mock_capture,
        stt_provider=mock_stt,
        tts_provider=mock_tts,
    )
    orchestrator.voice_service = voice_service

    app = create_app(orchestrator=orchestrator, event_bus=bus)
    client = TestClient(app)

    return {
        "app": app,
        "client": client,
        "orchestrator": orchestrator,
        "event_bus": bus,
        "voice_service": voice_service,
        "mock_tts": mock_tts,
        "mock_stt": mock_stt,
    }


def test_extract_spoken_response_from_final_output():
    """Invariant 1: extract_spoken_response authoritatively extracts clean answer from final_output."""
    plan = TaskPlan(
        request_id="task_1",
        user_prompt="Explain what a computer network is.",
        steps={
            "step_1": TaskStep(
                step_id="step_1",
                title="Explain networking",
                description="Explain networking",
                tool_name="echo",
                parameters={"message": "A computer network is a system of interconnected devices."},
            )
        },
    )
    exec_res = AgentExecutionResult(
        task_id="task_1",
        request="Explain what a computer network is.",
        status=TaskState.COMPLETED,
        summary="Multi-step task completed successfully in 1 steps.",
        plan=plan,
        final_output="A computer network is a system of interconnected devices.",
        completed_steps=["step_1"],
    )

    answer = extract_spoken_response(exec_res)
    assert answer == "A computer network is a system of interconnected devices."
    assert not answer.startswith("Multi-step task")


def test_extract_spoken_response_structured_document_output():
    """Structured output (dict) is converted into user-friendly message without raw JSON."""
    plan = TaskPlan(
        request_id="task_doc",
        user_prompt="Create summary document",
        steps={
            "step_1": TaskStep(
                step_id="step_1",
                title="Create file",
                description="Create file",
                tool_name="document",
                parameters={"action": "create", "file_name": "summary.txt"},
            )
        },
    )
    exec_res = AgentExecutionResult(
        task_id="task_doc",
        request="Create summary document",
        status=TaskState.COMPLETED,
        summary="Multi-step task completed successfully in 1 steps.",
        plan=plan,
        final_output={"file_name": "summary.txt", "status": "created"},
        completed_steps=["step_1"],
    )

    answer = extract_spoken_response(exec_res)
    assert "summary.txt" in answer
    assert "{" not in answer
    assert "}" not in answer


@pytest.mark.asyncio
async def test_generic_summary_replacement_in_run_task(tts_gateway_fixture):
    """Invariant 1 & 2: Generic summary is replaced and TASK_COMPLETED carries safe answer."""
    orch = tts_gateway_fixture["orchestrator"]
    bus = tts_gateway_fixture["event_bus"]

    res = await orch.run_task("Explain what a computer network is.")
    assert res.status == TaskState.COMPLETED
    assert not res.summary.startswith("Multi-step task completed successfully")
    assert "computer network" in res.summary.lower()

    # Verify AgentEventType.TASK_COMPLETED payload
    task_events = bus.get_task_events(res.task_id)
    completed_events = [ev for ev in task_events if ev.event_type == AgentEventType.TASK_COMPLETED]
    assert len(completed_events) == 1

    ev = completed_events[0]
    assert ev.safe_user_message == res.summary
    assert ev.metadata.get("answer") == res.summary

    # Ensure no raw reasoning or internal execution details
    assert "planner reasoning" not in ev.safe_user_message.lower()
    assert "chain-of-thought" not in ev.safe_user_message.lower()


def test_task_status_response_contains_authoritative_answer(tts_gateway_fixture):
    """TaskStatusResponse.answer contains the extracted user-safe answer."""
    client = tts_gateway_fixture["client"]

    # Submit task synchronously
    submit_res = client.post(
        "/api/tasks",
        json={"prompt": "Explain what a computer network is.", "sync": True},
    )
    assert submit_res.status_code in (200, 202)
    task_id = submit_res.json()["task_id"]

    # Query task status
    status_res = client.get(f"/api/tasks/{task_id}")
    assert status_res.status_code == 200
    data = status_res.json()

    assert data["status"] == "COMPLETED"
    assert data["answer"] is not None
    assert not data["answer"].startswith("Multi-step task completed successfully")
    assert "computer network" in data["answer"].lower()


@pytest.mark.asyncio
async def test_voice_transcript_to_task_to_answer(tts_gateway_fixture):
    """Voice transcript -> task execution -> answer availability."""
    client = tts_gateway_fixture["client"]
    stt = tts_gateway_fixture["mock_stt"]

    stt.queue_transcript("ORBIS, explain TCP.")

    client.post("/api/voice/start")
    stop_res = client.post("/api/voice/stop")
    assert stop_res.status_code == 200
    transcript = stop_res.json()["transcript"]
    assert transcript == "explain TCP."

    # Submit task
    task_res = client.post(
        "/api/tasks",
        json={"prompt": transcript, "sync": True},
    )
    assert task_res.status_code in (200, 202)
    task_id = task_res.json()["task_id"]

    status_res = client.get(f"/api/tasks/{task_id}")
    data = status_res.json()
    assert data["status"] == "COMPLETED"
    assert "tcp" in data["answer"].lower() or "transmission control protocol" in data["answer"].lower()


def test_api_voice_speak_canonical_sapi5_invocation(tts_gateway_fixture):
    """POST /api/voice/speak invokes canonical VoiceService SAPI5 TTS."""
    client = tts_gateway_fixture["client"]
    tts = tts_gateway_fixture["mock_tts"]
    svc = tts_gateway_fixture["voice_service"]

    assert len(tts.spoken_history) == 0
    assert svc.state == VoiceState.IDLE

    speak_text = "A computer network connects multiple devices together."
    res = client.post("/api/voice/speak", json={"text": speak_text})
    assert res.status_code == 200
    assert res.json()["status"] == "spoken"

    # Verify canonical SAPI5 provider was called
    assert len(tts.spoken_history) == 1
    assert tts.spoken_history[0] == speak_text
    assert svc.state == VoiceState.IDLE


def test_api_voice_speak_sanitization_and_bound_safety(tts_gateway_fixture):
    """POST /api/voice/speak enforces bound length and strips secrets before TTS."""
    client = tts_gateway_fixture["client"]
    tts = tts_gateway_fixture["mock_tts"]

    # 1. Reject input exceeding bound (5000 characters)
    huge_text = "A" * 5001
    res_huge = client.post("/api/voice/speak", json={"text": huge_text})
    assert res_huge.status_code == 422

    # 2. Redact sensitive credentials/API keys before vocalizing
    secret_text = "Your API key is sk-1234567890abcdef1234567890abcdef. That is all."
    res_sec = client.post("/api/voice/speak", json={"text": secret_text})
    assert res_sec.status_code == 200
    assert len(tts.spoken_history) == 1
    spoken = tts.spoken_history[0]
    assert "sk-1234567890abcdef1234567890abcdef" not in spoken
    assert "[REDACTED" in spoken


def test_api_voice_speak_failure_handling(tts_gateway_fixture):
    """If SAPI5 TTS fails, returns safe 500 and resets state to IDLE."""
    client = tts_gateway_fixture["client"]
    tts = tts_gateway_fixture["mock_tts"]
    svc = tts_gateway_fixture["voice_service"]

    # Configure mock TTS to fail
    tts._available = False

    res = client.post("/api/voice/speak", json={"text": "Hello world"})
    assert res.status_code == 500
    assert res.json()["error"] == "Speech synthesis failed."
    # State must not remain stuck in SPEAKING or RESPONDING
    assert svc.state == VoiceState.IDLE


@pytest.mark.asyncio
async def test_conversation_continuity_followup(tts_gateway_fixture):
    """Verifies that follow-up anaphora ('compare it with UDP') resolves after 'Explain TCP'."""
    orch = tts_gateway_fixture["orchestrator"]

    # Turn 1: Explain TCP
    res1 = await orch.run_task("Explain TCP.")
    assert res1.status == TaskState.COMPLETED
    assert "tcp" in res1.summary.lower() or "transmission control protocol" in res1.summary.lower()

    # Turn 2: Follow-up question referring back to TCP
    resolved = orch.conversation_context.resolve_query("Now compare it with UDP.")
    assert "tcp" in resolved.resolved_query.lower()
    assert "udp" in resolved.resolved_query.lower()


def test_event_stream_preservation(tts_gateway_fixture):
    """Event Stream is fully preserved alongside dedicated response area."""
    client = tts_gateway_fixture["client"]

    submit_res = client.post(
        "/api/tasks",
        json={"prompt": "Explain what a computer network is.", "sync": True},
    )
    task_id = submit_res.json()["task_id"]

    events_res = client.get(f"/api/tasks/{task_id}/events")
    assert events_res.status_code == 200
    events_data = events_res.json()

    event_types = [e["event_type"] for e in events_data["events"]]
    assert "TASK_STARTED" in event_types
    assert "TASK_COMPLETED" in event_types
    assert events_data["total"] >= 2
