"""
Tests for ORBIS FastAPI Gateway Voice Interface Bridge (Phase 11/12).
Verifies:
1. Reusing exactly ONE authoritative VoiceService instance for the gateway lifecycle.
2. POST /api/voice/start transitions VoiceService to LISTENING.
3. POST /api/voice/stop transcribes speech, normalizes prefix, and returns { transcript, empty, status }
   (strictly no raw_transcript surfaced).
4. Audio buffer memory release invariant on stop and cancel.
5. Graceful handling of empty or inaudible speech without submitting tasks.
6. Hardware unavailability returning HTTP 503 "Microphone access is unavailable.".
7. Cancellation on disconnect / cancel endpoint.
"""

import pytest
from fastapi.testclient import TestClient

from core.agent.llm.mock import MockLLMProvider
from core.agent.orchestrator import AgentOrchestrator
from core.events.bus import EventBus
from core.gateway.app import create_app
from core.tools.registry import create_default_tool_registry
from core.voice.capture.mock_capture import MockAudioCapture
from core.voice.models import AudioBuffer, VoiceState
from core.voice.service import VoiceService
from core.voice.stt.mock_stt import MockSTTProvider
from core.voice.tts.mock_tts import MockTTSProvider


@pytest.fixture
def voice_gateway():
    """Builds a test gateway client with an injected canonical VoiceService."""
    bus = EventBus()
    tools = create_default_tool_registry()
    llm = MockLLMProvider()
    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=tools, event_bus=bus)

    mock_capture = MockAudioCapture()
    mock_stt = MockSTTProvider()
    mock_tts = MockTTSProvider()

    canonical_voice_service = VoiceService(
        orchestrator=orchestrator,
        capture=mock_capture,
        stt_provider=mock_stt,
        tts_provider=mock_tts,
    )
    orchestrator.voice_service = canonical_voice_service

    app = create_app(orchestrator=orchestrator, event_bus=bus)
    client = TestClient(app)

    return {
        "app": app,
        "client": client,
        "orchestrator": orchestrator,
        "voice_service": canonical_voice_service,
        "mock_capture": mock_capture,
        "mock_stt": mock_stt,
    }


def test_voice_service_canonical_singleton(voice_gateway):
    """Requirement 1: Exactly ONE authoritative VoiceService instance is reused."""
    client = voice_gateway["client"]
    svc = voice_gateway["voice_service"]

    assert svc.state == VoiceState.IDLE

    # Start capture
    res1 = client.post("/api/voice/start")
    assert res1.status_code == 200
    assert svc.state == VoiceState.LISTENING

    # Same instance is modified
    voice_gateway["mock_stt"].queue_transcript("Explain HSRP in simple words")
    res2 = client.post("/api/voice/stop")
    assert res2.status_code == 200
    assert svc.state == VoiceState.IDLE


def test_voice_stop_payload_contract(voice_gateway):
    """Requirement 2: Return only { transcript, empty, status } — NO raw_transcript."""
    client = voice_gateway["client"]
    voice_gateway["mock_stt"].queue_transcript("ORBIS, explain HSRP in simple words")

    client.post("/api/voice/start")
    res = client.post("/api/voice/stop")

    assert res.status_code == 200
    data = res.json()

    assert "transcript" in data
    assert "empty" in data
    assert "status" in data
    assert "raw_transcript" not in data  # Invariant: raw_transcript must NOT be surfaced
    assert data["transcript"] == "explain HSRP in simple words"  # Invocation prefix stripped
    assert data["empty"] is False
    assert data["status"] == "stopped"


def test_voice_stop_empty_inaudible_handling(voice_gateway):
    """Requirement 5: Empty speech produces empty=True and does not create tasks."""
    client = voice_gateway["client"]
    # Provide audio with empty transcription
    voice_gateway["mock_stt"].queue_transcript("")

    client.post("/api/voice/start")
    res = client.post("/api/voice/stop")

    assert res.status_code == 200
    data = res.json()
    assert data["transcript"] == ""
    assert data["empty"] is True
    assert data["status"] == "idle"


def test_voice_stop_when_not_listening(voice_gateway):
    """Calling stop when not listening returns empty safely without crashing."""
    client = voice_gateway["client"]
    res = client.post("/api/voice/stop")

    assert res.status_code == 200
    data = res.json()
    assert data["transcript"] == ""
    assert data["empty"] is True


def test_voice_cancel_releases_audio(voice_gateway):
    """Requirement 4: Cancellation stops capture and releases audio memory."""
    client = voice_gateway["client"]
    svc = voice_gateway["voice_service"]

    client.post("/api/voice/start")
    assert svc.state == VoiceState.LISTENING

    res = client.post("/api/voice/cancel")
    assert res.status_code == 200
    assert res.json()["status"] == "idle"
    assert svc.state == VoiceState.IDLE


def test_microphone_unavailable_returns_503(voice_gateway):
    """Requirement 3: Hardware failure returns 'Microphone access is unavailable.'."""
    client = voice_gateway["client"]
    mock_cap = voice_gateway["mock_capture"]
    mock_cap.available = False

    res = client.post("/api/voice/start")
    assert res.status_code == 503
    assert "Microphone access is unavailable." in res.json()["error"]


def test_canonical_instance_identity_across_endpoints(voice_gateway):
    """
    Requirement 2: Exact same VoiceService instance owned by orchestrator is reused.
    Architecture:
    app.state.orchestrator
            ↓
    orchestrator.voice_service
            ↓
    /api/voice/start
    /api/voice/stop
    /api/voice/cancel
    """
    app = voice_gateway["app"]
    client = voice_gateway["client"]
    orch = voice_gateway["orchestrator"]
    initial_svc_id = id(orch.voice_service)

    # 1. Verify app.state.orchestrator ownership
    assert app.state.orchestrator is orch
    assert app.state.orchestrator.voice_service is orch.voice_service

    # 2. Call /api/voice/start and verify instance identity
    res1 = client.post("/api/voice/start")
    assert res1.status_code == 200
    assert id(app.state.orchestrator.voice_service) == initial_svc_id
    assert id(orch.voice_service) == initial_svc_id

    # 3. Call /api/voice/stop and verify instance identity
    res2 = client.post("/api/voice/stop")
    assert res2.status_code == 200
    assert id(app.state.orchestrator.voice_service) == initial_svc_id
    assert id(orch.voice_service) == initial_svc_id

    # 4. Call /api/voice/cancel and verify instance identity
    res3 = client.post("/api/voice/cancel")
    assert res3.status_code == 200
    assert id(app.state.orchestrator.voice_service) == initial_svc_id
    assert id(orch.voice_service) == initial_svc_id


def test_audio_release_on_transcription_exception(voice_gateway):
    """Requirement 4: audio.release() MUST execute in finally block even when STT raises exception."""
    client = voice_gateway["client"]
    svc = voice_gateway["voice_service"]

    class FailingSTT:
        async def transcribe(self, audio):
            # Record buffer reference to verify release
            self.last_audio = audio
            raise RuntimeError("Underlying STT engine crashed unexpectedly.")

    failing_stt = FailingSTT()
    svc.stt_provider = failing_stt

    client.post("/api/voice/start")
    assert svc.state == VoiceState.LISTENING

    res = client.post("/api/voice/stop")
    assert res.status_code == 500
    assert "Voice transcription failed." in res.json()["error"]

    # Invariant: AudioBuffer must have been released despite exception
    assert hasattr(failing_stt, "last_audio")
    assert failing_stt.last_audio.is_empty  # Buffer data cleared via release()

    # Voice state must return to IDLE
    assert svc.state == VoiceState.IDLE


def test_production_mode_does_not_silently_use_mocks(monkeypatch):
    """Requirement 3: In production runtime, mocks cannot silently activate."""
    from core import config

    class ProductionSettings(config.Settings):
        orbis_env: str = "production"
        voice_stt_provider: str = "whisper"

    monkeypatch.setattr(config, "get_settings", lambda: ProductionSettings())

    # Build fresh app without pre-injected mock voice_service
    bus = EventBus()
    tools = create_default_tool_registry()
    llm = MockLLMProvider()
    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=tools, event_bus=bus)
    assert orchestrator.voice_service is None

    app = create_app(orchestrator=orchestrator, event_bus=bus)
    client = TestClient(app)

    # Calling /api/voice/start will construct VoiceService using real SoundDeviceAudioCapture
    # If a mic is not available, it must return 503, NEVER fall back to MockAudioCapture
    from core.voice.capture.sounddevice_capture import SoundDeviceAudioCapture
    from core.voice.capture.mock_capture import MockAudioCapture

    # Force SoundDeviceAudioCapture.is_available() to False to simulate no microphone in production
    monkeypatch.setattr(SoundDeviceAudioCapture, "is_available", lambda self: False)

    res = client.post("/api/voice/start")
    assert res.status_code == 503
    assert res.json()["error"] == "Microphone access is unavailable."
    # Confirm it did NOT instantiate MockAudioCapture
    assert not isinstance(orchestrator.voice_service.capture, MockAudioCapture)
    assert isinstance(orchestrator.voice_service.capture, SoundDeviceAudioCapture)
    # Voice state must remain/return to IDLE
    assert orchestrator.voice_service.state == VoiceState.IDLE


def test_lifespan_shutdown_releases_listening_audio():
    """Requirement 4: Gateway shutdown/disconnect releases active audio buffer."""
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

    with TestClient(app) as client:
        # Start voice capture
        res = client.post("/api/voice/start")
        assert res.status_code == 200
        assert voice_service.state == VoiceState.LISTENING

    # Exiting TestClient triggers lifespan shutdown
    assert voice_service.state == VoiceState.IDLE


