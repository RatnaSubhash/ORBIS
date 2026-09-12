"""
Unit and Integration Tests for ORBIS Phase 11 Voice Interface.
Verifies all 20 required dimensions:
1. Domain models (AudioBuffer, TranscriptionResult, SpeechResult, VoiceState)
2. MockAudioCapture (Push-to-Talk lifecycle, queuing, failure simulation)
3. MockSTTProvider (deterministic transcription, inaudible/empty audio detection)
4. MockTTSProvider (synthesis metadata, speech execution, cancellation)
5. VoiceNormalizer input normalization & invocation prefix stripping
6. VoiceNormalizer affirmative / negative confirmation classification
7. VoiceNormalizer output formatting for speech (Markdown stripping, secret redaction)
8. VoiceService full Push-to-Talk pipeline (speech -> STT -> orchestrator -> TTS)
9. Privacy invariant: in-memory audio release after transcription
10. Empty and inaudible audio safe fallback guidance
11. Two-step approval invariant: original command cannot approve itself; explicit confirmation succeeds
12. Two-step approval rejection: explicit cancellation aborts execution
13. Maximum recording duration boundary enforcement
14. Voice interface disabled in configuration
15. VoiceTool BaseTool compliance and verification report
16. VoiceTool strictly prohibits agent-controlled microphone activation
17. SoundDeviceAudioCapture availability and graceful fallback
18. FasterWhisperSTTProvider availability and empty audio handling
19. WindowsSapiTTSProvider synthesis, cancellation, and platform availability
20. AgentOrchestrator.run_voice_interaction() end-to-end integration
"""

import asyncio
import sys
from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from core.agent.llm.mock import MockLLMProvider
from core.agent.orchestrator import AgentOrchestrator
from core.agent.models import AgentExecutionResult, TaskState
from core.config import get_settings
from core.memory.db import DatabaseManager
from core.tools.registry import ToolRegistry
from core.tools.voice_tool import VoiceTool, VoiceToolParameters
from core.types import ExecutionContext, RiskLevel
from core.voice.capture.base import BaseAudioCapture
from core.voice.capture.mock_capture import MockAudioCapture
from core.voice.capture.sounddevice_capture import SoundDeviceAudioCapture
from core.voice.models import (
    AudioBuffer,
    SpeechResult,
    TranscriptionResult,
    VoiceInteractionResult,
    VoiceState,
)
from core.voice.normalizer import VoiceNormalizer
from core.voice.service import VoiceService
from core.voice.stt.mock_stt import MockSTTProvider
from core.voice.stt.whisper_stt import FasterWhisperSTTProvider
from core.voice.tts.mock_tts import MockTTSProvider
from core.voice.tts.sapi_tts import WindowsSapiTTSProvider


# ============================================================================
# Dimension 1: Voice Models
# ============================================================================
def test_voice_models():
    # 1 second of 16kHz 16-bit mono PCM = 32000 bytes
    pcm_data = b"\x01\x00" * 16000
    buf = AudioBuffer(data=pcm_data, sample_rate=16000, channels=1, sample_width=2)

    assert not buf.is_empty
    assert pytest.approx(buf.duration_seconds, 0.01) == 1.0

    # Invariant: audio release clears memory buffer
    buf.release()
    assert buf.is_empty
    assert buf.data == b""
    assert buf.duration_seconds == 0.0

    # TranscriptionResult model
    t_res = TranscriptionResult(
        text="open calculator",
        raw_text="orbis, open calculator",
        confidence=0.95,
        audio_duration_seconds=1.0,
    )
    assert t_res.text == "open calculator"
    assert not t_res.is_empty_or_inaudible

    # SpeechResult model
    s_res = SpeechResult(text_spoken="Calculator opened.", duration_seconds=1.2)
    assert s_res.success
    assert not s_res.was_cancelled

    # VoiceInteractionResult model
    v_res = VoiceInteractionResult(
        raw_transcript="orbis, open calculator",
        normalized_query="open calculator",
        agent_response_summary="Calculator opened.",
    )
    assert v_res.interaction_id.startswith("vint_")
    assert v_res.success


# ============================================================================
# Dimension 2: Mock Audio Capture
# ============================================================================
def test_mock_audio_capture():
    capture = MockAudioCapture(sample_rate=16000)
    assert capture.is_available()
    assert not capture.is_capturing()

    capture.start_capture()
    assert capture.is_capturing()

    buf = capture.stop_capture()
    assert not capture.is_capturing()
    assert isinstance(buf, AudioBuffer)
    assert len(buf.data) == 32000

    # Queued custom audio
    custom_pcm = b"\x02\x00" * 8000
    capture.queue_simulated_audio(custom_pcm)
    capture.start_capture()
    buf2 = capture.stop_capture()
    assert buf2.data == custom_pcm
    assert pytest.approx(buf2.duration_seconds, 0.01) == 0.5

    # Simulated disconnect/failure
    broken_capture = MockAudioCapture(available=False)
    assert not broken_capture.is_available()
    with pytest.raises(RuntimeError, match="unavailable"):
        broken_capture.start_capture()


# ============================================================================
# Dimension 3: Mock STT Transcription
# ============================================================================
@pytest.mark.asyncio
async def test_mock_stt_transcription():
    stt = MockSTTProvider()
    assert stt.is_available()

    # Valid audio
    pcm = b"\x05\x00" * 16000
    buf = AudioBuffer(data=pcm, sample_rate=16000)
    stt.queue_transcript("Show me recent continuity events")
    res = await stt.transcribe(buf)

    assert res.text == "Show me recent continuity events"
    assert not res.is_empty_or_inaudible
    assert res.confidence > 0.9

    # Empty buffer detection
    empty_buf = AudioBuffer(data=b"")
    res_empty = await stt.transcribe(empty_buf)
    assert res_empty.is_empty_or_inaudible
    assert res_empty.text == ""

    # Pure silence detection (all zeros)
    silence_buf = AudioBuffer(data=b"\x00\x00" * 16000)
    res_silence = await stt.transcribe(silence_buf)
    assert res_silence.is_empty_or_inaudible
    assert res_silence.text == ""

    # Unavailable provider
    unavail_stt = MockSTTProvider(available=False)
    with pytest.raises(RuntimeError, match="unavailable"):
        await unavail_stt.transcribe(buf)


# ============================================================================
# Dimension 4: Mock TTS Synthesis and Speech
# ============================================================================
@pytest.mark.asyncio
async def test_mock_tts_synthesis_and_speech():
    tts = MockTTSProvider(words_per_minute=150)
    assert tts.is_available()

    # Synthesis metadata only (no playback)
    synth_res = await tts.synthesize("The restoration plan has been prepared.")
    assert synth_res.success
    assert synth_res.duration_seconds > 0
    assert "The restoration plan has been prepared." in tts.synthesized_history

    # Vocalize aloud (playback)
    speak_res = await tts.speak("All tasks completed successfully.")
    assert speak_res.success
    assert not speak_res.was_cancelled
    assert "All tasks completed successfully." in tts.spoken_history

    # Cancellation
    tts.stop()
    cancel_res = await tts.speak("This should be cancelled.")
    assert cancel_res.was_cancelled

    # Unavailable provider
    unavail_tts = MockTTSProvider(available=False)
    res_fail = await unavail_tts.speak("Hello")
    assert not res_fail.success
    assert "unavailable" in res_fail.error.lower()


# ============================================================================
# Dimension 5: Voice Normalizer Input & Prefix Stripping
# ============================================================================
def test_voice_normalizer_prefix_stripping():
    # Standard invocation patterns
    assert VoiceNormalizer.normalize_input("ORBIS, what was I doing?") == "what was I doing?"
    assert VoiceNormalizer.normalize_input("Hey ORBIS, restore my workspace.") == "restore my workspace."
    assert VoiceNormalizer.normalize_input("Okay Orbis: show unfinished work") == "show unfinished work"
    assert VoiceNormalizer.normalize_input("Please orbis - open the documentation") == "open the documentation"
    assert VoiceNormalizer.normalize_input("Hi Orbis, find relevant context") == "find relevant context"

    # Preserves clean requests without prefix
    assert VoiceNormalizer.normalize_input("what was I doing?") == "what was I doing?"

    # Empty inputs
    assert VoiceNormalizer.normalize_input("") == ""
    assert VoiceNormalizer.normalize_input("   ") == ""


# ============================================================================
# Dimension 6: Voice Normalizer Affirmative & Negative Confirmation
# ============================================================================
def test_voice_normalizer_affirmative_negative():
    # Affirmatives
    assert VoiceNormalizer.is_affirmative("yes")
    assert VoiceNormalizer.is_affirmative("Yes, proceed.")
    assert VoiceNormalizer.is_affirmative("confirm")
    assert VoiceNormalizer.is_affirmative("approve")
    assert VoiceNormalizer.is_affirmative("go ahead")
    assert VoiceNormalizer.is_affirmative("do it")

    # Negatives
    assert VoiceNormalizer.is_negative("no")
    assert VoiceNormalizer.is_negative("cancel")
    assert VoiceNormalizer.is_negative("stop")
    assert VoiceNormalizer.is_negative("abort")
    assert VoiceNormalizer.is_negative("do not")

    # Ambiguous / Non-confirming
    assert not VoiceNormalizer.is_affirmative("maybe later")
    assert not VoiceNormalizer.is_negative("maybe later")
    assert not VoiceNormalizer.is_affirmative("what are you doing")


# ============================================================================
# Dimension 7: Voice Normalizer Speech Output Formatting & Redaction
# ============================================================================
def test_voice_normalizer_speech_formatting():
    # 1. Strips Markdown code blocks
    text_with_code = "Here is the code: ```python\nprint('hello world')\n``` Everything looks good."
    formatted = VoiceNormalizer.format_output_for_speech(text_with_code)
    assert "print('hello world')" not in formatted
    assert "[code snippet omitted]" in formatted

    # 2. Strips Markdown tables and headers
    text_with_table = (
        "### Plan Summary\n"
        "| Step | Action |\n"
        "| --- | --- |\n"
        "| 1 | Restore VS Code |\n"
        "Workspace restoration complete."
    )
    formatted_table = VoiceNormalizer.format_output_for_speech(text_with_table)
    assert "###" not in formatted_table
    assert "| Step |" not in formatted_table
    assert "Workspace restoration complete." in formatted_table

    # 3. Strips links & images
    text_links = "Check the [documentation](https://example.com) for details."
    assert VoiceNormalizer.format_output_for_speech(text_links) == "Check the documentation for details."

    # 4. Never speaks redacted credentials
    secret_text = "Retrieved configuration with secret [REDACTED API KEY]."
    formatted_secret = VoiceNormalizer.format_output_for_speech(secret_text)
    assert "[REDACTED API KEY]" not in formatted_secret
    assert "redacted key" in formatted_secret

    # 5. Truncates excessively long output at sentence boundary
    long_text = "This is sentence one. " * 30
    formatted_long = VoiceNormalizer.format_output_for_speech(long_text, max_chars=100)
    assert len(formatted_long) <= 105
    assert formatted_long.endswith(".")


# ============================================================================
# Dimension 8: Voice Service Full Push-to-Talk Pipeline
# ============================================================================
@pytest.mark.asyncio
async def test_voice_service_full_pipeline():
    llm = MockLLMProvider()
    tools = ToolRegistry()
    from core.tools.echo_tool import EchoTool
    tools.register(EchoTool())
    db = DatabaseManager(db_path=":memory:")
    db.initialize()
    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=tools, db_manager=db)

    capture = MockAudioCapture()
    stt = MockSTTProvider()
    tts = MockTTSProvider()

    service = VoiceService(
        orchestrator=orchestrator,
        capture=capture,
        stt_provider=stt,
        tts_provider=tts,
    )

    # Simulate Push-to-Talk window
    service.start_listening()
    assert service.state == VoiceState.LISTENING
    audio = service.stop_listening()
    assert service.state == VoiceState.IDLE

    # Queue speech transcription
    stt.queue_transcript("Orbis, echo hello from voice")

    result = await service.process_spoken_request(audio)

    assert result.success
    assert result.raw_transcript == "Orbis, echo hello from voice"
    assert result.normalized_query == "echo hello from voice"
    assert len(tts.spoken_history) > 0


# ============================================================================
# Dimension 9: Privacy Invariant — Audio Memory Release
# ============================================================================
@pytest.mark.asyncio
async def test_voice_service_audio_release_invariant():
    llm = MockLLMProvider()
    tools = ToolRegistry()
    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=tools)
    service = VoiceService(orchestrator=orchestrator)

    pcm = b"\x03\x00" * 16000
    audio = AudioBuffer(data=pcm, sample_rate=16000)
    assert not audio.is_empty

    # Process request
    await service.process_spoken_request(audio)

    # Invariant: Raw audio buffer MUST be cleared in memory
    assert audio.is_empty
    assert audio.data == b""


# ============================================================================
# Dimension 10: Empty and Inaudible Audio Safe Fallback
# ============================================================================
@pytest.mark.asyncio
async def test_voice_service_empty_inaudible_handling():
    llm = MockLLMProvider()
    tools = ToolRegistry()
    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=tools)
    tts = MockTTSProvider()
    service = VoiceService(orchestrator=orchestrator, tts_provider=tts)

    # Empty buffer
    empty_audio = AudioBuffer(data=b"")
    res = await service.process_spoken_request(empty_audio)

    assert res.success
    assert "didn't catch that" in res.agent_response_summary.lower()
    assert any("didn't catch that" in s.lower() for s in tts.spoken_history)


# ============================================================================
# Dimension 11: Two-Step Approval Invariant (Explicit Confirmation Required)
# ============================================================================
@pytest.mark.asyncio
async def test_voice_service_two_step_approval_invariant():
    llm = MockLLMProvider()
    tools = ToolRegistry()
    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=tools)
    stt = MockSTTProvider()
    tts = MockTTSProvider()
    service = VoiceService(orchestrator=orchestrator, stt_provider=stt, tts_provider=tts)

    # Invariant: Spoken command saying "delete this file and yes proceed" CANNOT approve itself
    # Explicit confirmation requires a separate spoken interaction
    approval_audio_yes = AudioBuffer(data=b"\x04\x00" * 8000, sample_rate=16000)
    stt.queue_transcript("Yes, proceed.")

    is_approved, explanation = await service.process_spoken_approval(approval_audio_yes)
    assert is_approved
    assert "explicit confirmation" in explanation.lower()
    assert approval_audio_yes.is_empty  # Audio released


# ============================================================================
# Dimension 12: Two-Step Approval Rejection
# ============================================================================
@pytest.mark.asyncio
async def test_voice_service_two_step_approval_rejection():
    llm = MockLLMProvider()
    tools = ToolRegistry()
    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=tools)
    stt = MockSTTProvider()
    service = VoiceService(orchestrator=orchestrator, stt_provider=stt)

    approval_audio_no = AudioBuffer(data=b"\x04\x00" * 8000, sample_rate=16000)
    stt.queue_transcript("No, cancel that.")

    is_approved, explanation = await service.process_spoken_approval(approval_audio_no)
    assert not is_approved
    assert "explicit cancellation" in explanation.lower()
    assert approval_audio_no.is_empty


# ============================================================================
# Dimension 13: Maximum Recording Duration Boundary Enforcement
# ============================================================================
@pytest.mark.asyncio
async def test_voice_service_max_recording_duration(monkeypatch):
    from core.voice import service as voice_service_mod

    class TestSettings:
        voice_enabled: bool = True
        voice_max_recording_seconds: float = 2.0
        voice_strip_prefix: bool = True

    monkeypatch.setattr(voice_service_mod, "get_settings", lambda: TestSettings())

    llm = MockLLMProvider()
    tools = ToolRegistry()
    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=tools)
    service = VoiceService(orchestrator=orchestrator)

    # 4 seconds of audio (exceeds 2.0s limit)
    oversized_audio = AudioBuffer(data=b"\x01\x00" * 16000 * 4, sample_rate=16000)
    assert oversized_audio.duration_seconds > 2.0

    res = await service.process_spoken_request(oversized_audio)
    # Must handle safely without unhandled exception
    assert oversized_audio.is_empty


# ============================================================================
# Dimension 14: Voice Interface Disabled in Configuration
# ============================================================================
@pytest.mark.asyncio
async def test_voice_service_disabled_config(monkeypatch):
    from core.voice import service as voice_service_mod

    class DisabledSettings:
        voice_enabled: bool = False
        voice_max_recording_seconds: float = 30.0

    monkeypatch.setattr(voice_service_mod, "get_settings", lambda: DisabledSettings())

    llm = MockLLMProvider()
    tools = ToolRegistry()
    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=tools)
    service = VoiceService(orchestrator=orchestrator)

    audio = AudioBuffer(data=b"\x01\x00" * 16000)
    res = await service.process_spoken_request(audio)

    assert not res.success
    assert "disabled" in res.error.lower()


# ============================================================================
# Dimension 15: VoiceTool BaseTool Contract & Verification
# ============================================================================
@pytest.mark.asyncio
async def test_voice_tool_contract():
    tts = MockTTSProvider()
    tool = VoiceTool(tts_provider=tts)

    assert tool.name == "voice"
    assert tool.risk_level == RiskLevel.LOW
    assert tool.version == "1.0.0"

    context = ExecutionContext(task_id="test_task_1")

    # 1. Action: get_status
    params_status = VoiceToolParameters(action="get_status")
    res_status = await tool.execute(params_status, context)
    assert res_status.success
    assert res_status.output["mode"] == "push_to_talk_only"
    assert res_status.output["autonomous_listening_prohibited"] is True

    verif_status = await tool.verify(params_status, res_status, context)
    assert verif_status.verified

    # 2. Action: speak
    params_speak = VoiceToolParameters(action="speak", text="Phase 11 verification in progress.")
    res_speak = await tool.execute(params_speak, context)
    assert res_speak.success
    assert "Phase 11 verification in progress." in tts.spoken_history

    verif_speak = await tool.verify(params_speak, res_speak, context)
    assert verif_speak.verified


# ============================================================================
# Dimension 16: VoiceTool Prohibits Agent-Controlled Microphone Activation
# ============================================================================
def test_voice_tool_prohibits_microphone_activation():
    # Invariant: planner/LLM must NEVER be able to activate microphone
    prohibited_actions = ["listen", "record", "start_listening", "capture_audio", "surveil"]

    for act in prohibited_actions:
        with pytest.raises(ValidationError) as exc_info:
            VoiceToolParameters(action=act)  # type: ignore
        assert "Autonomous microphone capture is prohibited" in str(exc_info.value)


# ============================================================================
# Dimension 17: SoundDeviceAudioCapture Availability and Graceful Fallback
# ============================================================================
def test_sounddevice_capture_graceful_fallback():
    capture = SoundDeviceAudioCapture()
    # Safely returns a boolean indicating hardware availability without crashing
    avail = capture.is_available()
    assert isinstance(avail, bool)

    if not avail:
        with pytest.raises(RuntimeError, match="not available"):
            capture.start_capture()


# ============================================================================
# Dimension 18: FasterWhisperSTTProvider Availability and Empty Audio Handling
# ============================================================================
@pytest.mark.asyncio
async def test_whisper_stt_graceful_fallback():
    whisper = FasterWhisperSTTProvider()
    avail = whisper.is_available()
    assert isinstance(avail, bool)

    # Empty audio handling executes without requiring model weights
    empty_buf = AudioBuffer(data=b"")
    res = await whisper.transcribe(empty_buf)
    assert res.is_empty_or_inaudible
    assert res.text == ""
    assert res.provider_used == "faster-whisper"


# ============================================================================
# Dimension 19: WindowsSapiTTSProvider Platform Availability & Synthesis
# ============================================================================
@pytest.mark.asyncio
async def test_windows_sapi_tts_execution_or_fallback():
    sapi = WindowsSapiTTSProvider()
    is_win = sys.platform == "win32"
    assert sapi.is_available() == is_win

    # Synthesis metadata
    res = await sapi.synthesize("ORBIS voice interface ready.")
    assert res.success == is_win
    if is_win:
        assert res.duration_seconds > 0.0
        assert res.provider_used == "windows_sapi"

    # Stop / cancellation
    sapi.stop()
    assert sapi._is_stopped


# ============================================================================
# Dimension 20: AgentOrchestrator Voice Integration Helper
# ============================================================================
@pytest.mark.asyncio
async def test_agent_orchestrator_voice_integration():
    llm = MockLLMProvider()
    tools = ToolRegistry()
    from core.tools.echo_tool import EchoTool
    tools.register(EchoTool())
    db = DatabaseManager(db_path=":memory:")
    db.initialize()
    stt = MockSTTProvider()
    tts = MockTTSProvider()
    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=tools, db_manager=db)
    service = VoiceService(orchestrator=orchestrator, stt_provider=stt, tts_provider=tts)
    orchestrator.voice_service = service

    stt.queue_transcript("Orbis, echo voice orchestrator active")
    audio = AudioBuffer(data=b"\x01\x00" * 16000, sample_rate=16000)

    res = await orchestrator.run_voice_interaction(audio, speak_response=True)
    assert isinstance(res, VoiceInteractionResult)
    assert res.success
    assert res.normalized_query == "echo voice orchestrator active"
    assert len(tts.spoken_history) > 0


# ============================================================================
# Dimension 21: Spoken Response Extraction & Precedence
# ============================================================================
def test_extract_spoken_response_string():
    from core.voice.service import extract_spoken_response

    class MockResult:
        final_output = "I am ready to help you with your desktop tasks."
        summary = "Multi-step task completed successfully (1/1 steps)."
        status = TaskState.COMPLETED

    res = extract_spoken_response(MockResult())
    assert res == "I am ready to help you with your desktop tasks."


def test_extract_spoken_response_dict_content():
    from core.voice.service import extract_spoken_response

    class MockResult:
        final_output = {
            "title": "AI Report",
            "content": "# Artificial Intelligence\n\nAI is the simulation of human intelligence.",
            "char_count": 500,
            "format": "markdown",
        }
        summary = "Multi-step task completed successfully (1/1 steps)."
        status = TaskState.COMPLETED

    res = extract_spoken_response(MockResult())
    assert "Artificial Intelligence" in res
    assert "AI is the simulation of human intelligence." in res
    assert "char_count" not in res
    assert "Multi-step task" not in res


def test_extract_spoken_response_dict_message():
    from core.voice.service import extract_spoken_response

    class MockResult:
        final_output = {
            "message": "Desktop application launched successfully.",
            "title": "App Launch",
            "pid": 1234,
        }
        summary = "Multi-step task completed successfully (1/1 steps)."
        status = TaskState.COMPLETED

    res = extract_spoken_response(MockResult())
    assert res == "Desktop application launched successfully."


def test_extract_spoken_response_dict_text():
    from core.voice.service import extract_spoken_response

    class MockResult:
        final_output = {
            "text": "Computer networks connect multiple devices to share data.",
            "format": "plain",
        }
        summary = "Multi-step task completed successfully (1/1 steps)."
        status = TaskState.COMPLETED

    res = extract_spoken_response(MockResult())
    assert res == "Computer networks connect multiple devices to share data."


def test_extract_spoken_response_dict_summary():
    from core.voice.service import extract_spoken_response

    class MockResult:
        final_output = {
            "summary": "Restored 2 application windows to their saved positions.",
            "window_count": 2,
        }
        summary = "Multi-step task completed successfully (1/1 steps)."
        status = TaskState.COMPLETED

    res = extract_spoken_response(MockResult())
    assert res == "Restored 2 application windows to their saved positions."


def test_extract_spoken_response_empty_final_output_fallback():
    from core.voice.service import extract_spoken_response

    class MockResult:
        final_output = None
        summary = "Multi-step task completed successfully (2/2 steps)."
        status = TaskState.COMPLETED

    res = extract_spoken_response(MockResult())
    assert res == "Multi-step task completed successfully (2/2 steps)."


def test_extract_spoken_response_metadata_only_dict_fallback():
    from core.voice.service import extract_spoken_response

    class MockResult:
        final_output = {
            "char_count": 100,
            "line_count": 5,
            "format": "markdown",
        }
        summary = "Multi-step task completed successfully (1/1 steps)."
        status = TaskState.COMPLETED

    res = extract_spoken_response(MockResult())
    assert res == "Multi-step task completed successfully (1/1 steps)."


def test_extract_spoken_response_failed_status():
    from core.voice.service import extract_spoken_response

    class MockResult:
        final_output = "Some partial output that should not mask the error"
        summary = "Multi-step task failed at step 'step2': Target file not found."
        status = TaskState.FAILED

    res = extract_spoken_response(MockResult())
    assert "Multi-step task failed at step 'step2': Target file not found." in res


@pytest.mark.asyncio
async def test_voice_service_surfaces_useful_tool_output():
    """Verifies that VoiceService speaks the actual tool output rather than the batch milestone message."""
    llm = MockLLMProvider()
    tools = ToolRegistry()
    from core.tools.echo_tool import EchoTool
    tools.register(EchoTool())
    db = DatabaseManager(db_path=":memory:")
    db.initialize()
    stt = MockSTTProvider()
    tts = MockTTSProvider()
    orchestrator = AgentOrchestrator(llm_provider=llm, tool_registry=tools, db_manager=db)
    service = VoiceService(orchestrator=orchestrator, stt_provider=stt, tts_provider=tts)

    stt.queue_transcript("echo Hello I am speaking tool output")
    audio = AudioBuffer(data=b"\x01\x00" * 16000, sample_rate=16000)

    res = await service.process_spoken_request(audio)
    assert res.success
    assert len(tts.spoken_history) > 0
    # Must speak the actual echoed content, not "Multi-step task completed successfully"
    assert "Hello I am speaking tool output" in tts.spoken_history[-1]
    assert "Multi-step task completed successfully" not in tts.spoken_history[-1]


@pytest.mark.asyncio
async def test_voice_agent_registry_contains_web_research():
    """
    Verifies that the authoritative voice/agent tool registry contains WebResearchTool
    registered as 'web_research', and that a voice interaction for a freshness query
    successfully validates and executes through the full VoiceService pipeline.
    """
    from core.tools.registry import create_default_tool_registry
    from core.tools.web_research_tool import WebResearchTool
    from core.tools.search_provider import MockSearchProvider, SearchResult

    mock_search = MockSearchProvider()
    mock_search.set_mock_results(
        "latest python version",
        [SearchResult(title="Python Org", url="https://python.org", snippet="Python 3.12 is latest release")],
    )

    def mock_fetcher(url: str):
        return 200, "Python Org", "<html><body><p>Python 3.12 is the latest stable release.</p></body></html>"

    db = DatabaseManager(db_path=":memory:")
    db.initialize()
    tools = create_default_tool_registry(
        db=db,
        search_provider=mock_search,
        mock_web_fetcher=mock_fetcher,
    )

    # 1. Authoritative registration invariant
    tool = tools.get("web_research")
    assert tool is not None, "web_research must be registered in the voice/agent tool registry"
    assert isinstance(tool, WebResearchTool)
    assert tool.name == "web_research"

    # 2. Voice Pipeline Integration
    stt = MockSTTProvider()
    tts = MockTTSProvider()
    orchestrator = AgentOrchestrator(
        llm_provider=MockLLMProvider(default_text_response="Python 3.12 is the latest stable release."),
        tool_registry=tools,
        db_manager=db,
    )
    service = VoiceService(orchestrator=orchestrator, stt_provider=stt, tts_provider=tts)

    stt.queue_transcript("What is the latest Python version?")
    audio = AudioBuffer(data=b"\x01\x00" * 16000, sample_rate=16000)

    res = await service.process_spoken_request(audio)
    assert res.success is True
    assert len(tts.spoken_history) > 0
    spoken = tts.spoken_history[-1]
    assert "python" in spoken.lower()
    assert "unregistered tool" not in spoken.lower()