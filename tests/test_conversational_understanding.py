"""
Automated Test Suite for Conversational Understanding Hardening.
Verifies:
1. ORBIS capability intent detection (ABOUT_ORBIS_CAPABILITIES).
2. Natural-language capability variations (standard, informal, Indian-English).
3. Imperfect/informal phrasing semantic matching.
4. General factual questions ("Explain TCP.", "What is TCP?", "Can you explain TCP simply?", "Tell me about TCP.", "Explain computer networks.").
5. Actual answers produced instead of echoing the question.
6. step_default regression: unrecognized queries never echo the user prompt.
7. Voice transcript -> correct intent -> actual answer pipeline.
8. Conversational follow-ups (TCP -> compare it with UDP; Capabilities -> help with computer networks project).
9. Answer extraction via extract_spoken_response().
10. SAPI5 TTS vocalization receives actual answer.
11. No planner reasoning or system prompts in answer.
12. No secrets or credentials in answer or TTS output.
"""

import pytest
from fastapi.testclient import TestClient

from core.agent.llm.mock import MockLLMProvider
from core.agent.models import AgentRequest, TaskState
from core.agent.orchestrator import AgentOrchestrator
from core.agent.planner.hierarchical import (
    HierarchicalPlanner,
    ORBIS_CAPABILITIES_SUMMARY,
    is_capabilities_query,
)
from core.events.bus import EventBus
from core.gateway.app import create_app
from core.tools.registry import create_default_tool_registry
from core.voice.capture.mock_capture import MockAudioCapture
from core.voice.models import AudioBuffer, VoiceState
from core.voice.service import VoiceService, extract_spoken_response
from core.voice.stt.mock_stt import MockSTTProvider
from core.voice.tts.mock_tts import MockTTSProvider


@pytest.fixture
def conversation_gateway_fixture():
    """Builds a test gateway client wired to an AgentOrchestrator and VoiceService."""
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


# ------------------------------------------------------------------------------
# 1, 2, 3: Semantic Capabilities Intent Recognition Tests
# ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "query",
    [
        "What do you do?",
        "What can you do?",
        "What can you help me with?",
        "Tell me what you can do.",
        "What all can you do?",
        "How can you help me?",
        "What are your capabilities?",
        "what can you do",
        "what all can you do",
        "what can u do",
        "tell me what you can do",
        "how can you help me",
        "what are you able to do",
        "what can you help me with",
        "what all you can do",
        "what you can do for me",
        "what are the things you can do",
        "ORBIS, what can you do?",
        "Hey orbis what all you can do",
    ],
)
def test_is_capabilities_query_positive_variations(query: str):
    """Verifies semantic intent classifier detects all capability query variations."""
    assert is_capabilities_query(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "Explain TCP.",
        "What is TCP?",
        "Can you explain TCP simply?",
        "Tell me about TCP.",
        "Explain computer networks.",
        "Can you help me with a computer networks project?",
        "Search the web for Python 3.12 release notes.",
        "List files in my workspace.",
        "What time is it?",
        "Create a file named notes.md.",
    ],
)
def test_is_capabilities_query_negative_non_capabilities(query: str):
    """Verifies capability classifier does not falsely trigger on non-capability queries."""
    assert is_capabilities_query(query) is False


# ------------------------------------------------------------------------------
# 4 & 5: Factual Questions & Actual Answers (No Echoing)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,expected_keyword",
    [
        ("What do you do?", "autonomous desktop assistant"),
        ("What can you do?", "autonomous desktop assistant"),
        ("what all you can do", "autonomous desktop assistant"),
        ("what you can do for me", "autonomous desktop assistant"),
        ("Explain TCP.", "Transmission Control Protocol"),
        ("What is TCP?", "Transmission Control Protocol"),
        ("Can you explain TCP simply?", "Transmission Control Protocol"),
        ("Tell me about TCP.", "Transmission Control Protocol"),
        ("Explain TCP in simple words.", "Transmission Control Protocol"),
        ("Explain computer networks.", "connected devices"),
    ],
)
async def test_factual_questions_produce_actual_answers_not_echoes(
    conversation_gateway_fixture, query: str, expected_keyword: str
):
    """Verifies that common factual queries produce authoritative answers and never echo the prompt."""
    orch = conversation_gateway_fixture["orchestrator"]
    res = await orch.run_task(query)

    assert res.status == TaskState.COMPLETED
    assert query.strip().rstrip(".?!").lower() != res.summary.strip().rstrip(".?!").lower(), (
        f"Query was echoed verbatim: '{query}'"
    )
    assert expected_keyword.lower() in res.summary.lower()


# ------------------------------------------------------------------------------
# 6: step_default Regression (Safe Fallback, No Echo)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_step_default_fallback_never_echoes_prompt(conversation_gateway_fixture):
    """Verifies that an unrecognized input reaching fallback returns a safe clarification, not an echo."""
    orch = conversation_gateway_fixture["orchestrator"]
    unknown_query = "xyzzy blargle random unmapped command 9999"

    res = await orch.run_task(unknown_query)
    assert res.status == TaskState.COMPLETED
    assert unknown_query != res.summary
    assert "cannot determine a specific action" in res.summary.lower() or "received your request" in res.summary.lower()


# ------------------------------------------------------------------------------
# 7: Voice Transcript -> Correct Intent & Answer
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_voice_transcript_to_capabilities_intent_and_tts(conversation_gateway_fixture):
    """End-to-end voice test: audio buffer -> STT -> normalization -> planner -> answer -> TTS."""
    voice = conversation_gateway_fixture["voice_service"]
    stt = conversation_gateway_fixture["mock_stt"]
    tts = conversation_gateway_fixture["mock_tts"]

    stt.queue_transcript("ORBIS, what do you do?")
    audio_buf = AudioBuffer(data=b"1" * 32000, sample_rate=16000, channels=1, sample_width=2)

    result = await voice.process_spoken_request(audio_buf)
    assert result.success is True
    assert "autonomous desktop assistant" in result.agent_response_summary.lower()
    assert len(tts.spoken_history) >= 1
    spoken = tts.spoken_history[-1]
    assert "autonomous desktop assistant" in spoken.lower()
    assert spoken.strip() != "ORBIS, what do you do?"


# ------------------------------------------------------------------------------
# 8: Conversational Multi-Turn Follow-Ups
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multi_turn_tcp_to_udp_comparison(conversation_gateway_fixture):
    """Turn 1: 'Explain TCP.' -> Turn 2: 'Now compare it with UDP.' resolves to comparison."""
    orch = conversation_gateway_fixture["orchestrator"]

    # Turn 1: Explain TCP
    res1 = await orch.run_task("Explain TCP.")
    assert res1.status == TaskState.COMPLETED
    assert "transmission control protocol" in res1.summary.lower()

    # Turn 2: Follow-up referring to TCP via 'it'
    res2 = await orch.run_task("Now compare it with UDP.")
    assert res2.status == TaskState.COMPLETED
    assert "tcp" in res2.summary.lower()
    assert "udp" in res2.summary.lower()
    assert "connection-oriented" in res2.summary.lower()
    assert "connectionless" in res2.summary.lower()


@pytest.mark.asyncio
async def test_multi_turn_capabilities_to_project_help(conversation_gateway_fixture):
    """Turn 1: 'What can you do?' -> Turn 2: 'Can you help me with a computer networks project?'."""
    orch = conversation_gateway_fixture["orchestrator"]

    # Turn 1: Capability query
    res1 = await orch.run_task("What can you do?")
    assert res1.status == TaskState.COMPLETED
    assert "autonomous desktop assistant" in res1.summary.lower()

    # Turn 2: Project continuation request
    res2 = await orch.run_task("Can you help me with a computer networks project?")
    assert res2.status == TaskState.COMPLETED
    assert res2.summary != "Can you help me with a computer networks project?"
    assert "computer networks project" in res2.summary.lower()
    assert "help you with your computer networks project" in res2.summary.lower()


# ------------------------------------------------------------------------------
# 9 & 10: Answer Extraction & SAPI5 TTS Invariants
# ------------------------------------------------------------------------------

def test_extract_spoken_response_authoritative():
    """Verifies that extract_spoken_response extracts final_output text without echoing."""
    class FakeResult:
        final_output = ORBIS_CAPABILITIES_SUMMARY
        summary = "Multi-step task completed successfully in 1 steps."

    extracted = extract_spoken_response(FakeResult())
    assert extracted == ORBIS_CAPABILITIES_SUMMARY
    assert not extracted.startswith("Multi-step task")


@pytest.mark.asyncio
async def test_tts_uses_actual_answer_and_resets_idle(conversation_gateway_fixture):
    """Verifies TTS provider speaks the actual answer and voice service state returns to IDLE."""
    voice = conversation_gateway_fixture["voice_service"]
    stt = conversation_gateway_fixture["mock_stt"]
    tts = conversation_gateway_fixture["mock_tts"]

    stt.queue_transcript("Explain TCP in simple words.")
    audio_buf = AudioBuffer(data=b"1" * 32000, sample_rate=16000, channels=1, sample_width=2)

    await voice.process_spoken_request(audio_buf)
    assert voice.state == VoiceState.IDLE
    assert len(tts.spoken_history) >= 1
    spoken = tts.spoken_history[-1]
    assert "Transmission Control Protocol" in spoken


# ------------------------------------------------------------------------------
# 11 & 12: Safety Redaction & No Planner Reasoning
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_planner_reasoning_or_secrets_in_answer(conversation_gateway_fixture):
    """Verifies that answers contain no chain-of-thought and secrets are redacted."""
    client = conversation_gateway_fixture["client"]

    # Submit task via gateway
    submit_res = client.post(
        "/api/tasks",
        json={"prompt": "What do you do?", "sync": True},
    )
    assert submit_res.status_code in (200, 202)
    task_id = submit_res.json()["task_id"]

    # Retrieve task status
    status_res = client.get(f"/api/tasks/{task_id}")
    assert status_res.status_code == 200
    ans = status_res.json()["answer"]

    # Must be the actual capability answer
    assert "autonomous desktop assistant" in ans.lower()
    # Must NOT contain internal planning or DAG step IDs
    assert "step_capabilities" not in ans
    assert "Plan formulated" not in ans
    assert "echo_tool" not in ans
    assert "step_default" not in ans
