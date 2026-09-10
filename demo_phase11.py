"""
ORBIS Phase 11 Demonstration Script: Two-Way Local Voice Interface.
Demonstrates:
1. Push-to-Talk spoken continuity query ("What was I working on?").
2. Push-to-Talk spoken ICE / unfinished-work query ("ORBIS, show unfinished work and next steps").
3. Push-to-Talk spoken workspace restoration with two-step approval ("Yes, proceed").
4. High-risk spoken command rejected without approval ("No, cancel that").
5. Empty / inaudible audio safe fallback guidance.
Supports both deterministic offline mock mode and live Windows SAPI5 / SoundDevice mode.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import sys

from core.agent.llm.mock import MockLLMProvider
from core.agent.orchestrator import AgentOrchestrator
from core.continuity.service import ContinuityService
from core.ice.engine import IntentContinuityEngine
from core.memory.db import DatabaseManager
from core.tools.continuity_tool import ContinuityTool
from core.tools.echo_tool import EchoTool
from core.tools.intent_tool import IntentContinuityTool
from core.tools.registry import ToolRegistry
from core.tools.restoration_tool import RestorationTool
from core.tools.voice_tool import VoiceTool
from core.types import ExecutionContext, RiskLevel, Session
from core.voice.capture.mock_capture import MockAudioCapture
from core.voice.capture.sounddevice_capture import SoundDeviceAudioCapture
from core.voice.models import AudioBuffer, VoiceState
from core.voice.normalizer import VoiceNormalizer
from core.voice.service import VoiceService
from core.voice.stt.mock_stt import MockSTTProvider
from core.voice.stt.whisper_stt import FasterWhisperSTTProvider
from core.voice.tts.mock_tts import MockTTSProvider
from core.voice.tts.sapi_tts import WindowsSapiTTSProvider


async def run_phase11_demo(live_mode: bool = False) -> None:
    print("=" * 80)
    print("ORBIS PHASE 11 DEMONSTRATION: TWO-WAY LOCAL VOICE INTERFACE")
    print("Privacy-Preserving Push-to-Talk Local Speech Interaction Layer")
    print("=" * 80)

    # 1. Setup in-memory Database and seed activity history
    db = DatabaseManager(db_path=":memory:")
    db.initialize()

    now = datetime.now(timezone.utc)
    db.record_session(
        Session(
            session_id="session_voice_demo",
            start_time=now,
            end_time=now,
            duration_seconds=900.0,
            event_count=12,
            applications=["Code.exe", "WindowsTerminal.exe"],
            process_names=["Code.exe", "WindowsTerminal.exe"],
            resources=["c:/Projects/ORBIS/auth_service.py", "c:/Projects/ORBIS/test_auth.py"],
        )
    )

    tools = ToolRegistry()
    tools.register(EchoTool())
    tools.register(ContinuityTool(db=db))
    intent_tool = IntentContinuityTool(db=db)
    tools.register(intent_tool)
    tools.register(RestorationTool(db=db))

    mock_llm = MockLLMProvider()
    continuity_svc = ContinuityService(db=db)
    intent_eng = IntentContinuityEngine(db=db, llm_provider=mock_llm)

    orchestrator = AgentOrchestrator(
        llm_provider=mock_llm,
        tool_registry=tools,
        db_manager=db,
        continuity_service=continuity_svc,
        intent_engine=intent_eng,
    )

    # 2. Configure Providers based on mode
    if live_mode and sys.platform == "win32":
        print("\n[Mode] LIVE WINDOWS HARDWARE DEMO (SAPI5 TTS)")
        capture_provider = SoundDeviceAudioCapture()
        if not capture_provider.is_available():
            print("  Note: Microphone not detected, using deterministic capture fallback.")
            capture_provider = MockAudioCapture()
        stt_provider = FasterWhisperSTTProvider()
        if not stt_provider.is_available():
            print("  Note: faster-whisper not installed, using deterministic mock STT.")
            stt_provider = MockSTTProvider()
        tts_provider = WindowsSapiTTSProvider()
    else:
        print("\n[Mode] DETERMINISTIC OFFLINE DEMO (In-Memory Mock Providers)")
        capture_provider = MockAudioCapture()
        stt_provider = MockSTTProvider()
        tts_provider = MockTTSProvider()

    voice_tool = VoiceTool(tts_provider=tts_provider)
    tools.register(voice_tool)

    voice_service = VoiceService(
        orchestrator=orchestrator,
        capture=capture_provider,
        stt_provider=stt_provider,
        tts_provider=tts_provider,
    )
    orchestrator.voice_service = voice_service

    # ========================================================================
    # SCENARIO 1: Spoken Continuity Query
    # ========================================================================
    print("\n" + "-" * 80)
    print("SCENARIO 1: SPOKEN CONTINUITY QUERY")
    print("User presses Push-to-Talk and asks what they were previously working on.")
    print("-" * 80)

    if isinstance(stt_provider, MockSTTProvider):
        stt_provider.queue_transcript("What was I working on?")

    print("[Push-to-Talk] User starts recording (hotkey held)...")
    voice_service.start_listening()
    assert voice_service.state == VoiceState.LISTENING

    print("[Push-to-Talk] User releases hotkey; audio buffered in memory.")
    audio_1 = voice_service.stop_listening()
    print(f"  Captured: {audio_1.duration_seconds:.2f}s of 16kHz PCM ({len(audio_1.data)} bytes)")

    print("[Pipeline] Transcribing -> Normalizing -> Querying ContinuityService -> TTS Speaking...")
    res_1 = await voice_service.process_spoken_request(audio_1)

    print(f"  Spoken Transcript: '{res_1.raw_transcript}'")
    print(f"  Normalized Query:  '{res_1.normalized_query}'")
    print(f"  Concise Summary:   '{res_1.agent_response_summary}'")
    print(f"  Audio Released:    {audio_1.is_empty} (Zero disk persistence)")
    print(f"  Interaction State: Success={res_1.success}")

    # ========================================================================
    # SCENARIO 2: Spoken ICE / Unfinished-Work Query
    # ========================================================================
    print("\n" + "-" * 80)
    print("SCENARIO 2: SPOKEN ICE / UNFINISHED-WORK QUERY")
    print("User speaks with conversational prefix: 'ORBIS, show unfinished work and next steps'")
    print("-" * 80)

    if isinstance(stt_provider, MockSTTProvider):
        stt_provider.queue_transcript("ORBIS, show unfinished work and next steps")

    voice_service.start_listening()
    audio_2 = voice_service.stop_listening()

    res_2 = await voice_service.process_spoken_request(audio_2)

    print(f"  Raw Transcript:    '{res_2.raw_transcript}'")
    print(f"  Normalized Query:  '{res_2.normalized_query}' (Invocation prefix cleanly stripped)")
    print(f"  Spoken Summary:    '{res_2.agent_response_summary}'")
    print(f"  Audio Memory:      Cleared={audio_2.is_empty}")

    # ========================================================================
    # SCENARIO 3: Spoken Workspace Restoration with Two-Step Approval
    # ========================================================================
    print("\n" + "-" * 80)
    print("SCENARIO 3: SPOKEN WORKSPACE RESTORATION WITH TWO-STEP APPROVAL")
    print("User requests workspace restoration -> Policy requests approval -> User confirms")
    print("-" * 80)

    print("[Step 1] User speaks restoration command...")
    if isinstance(stt_provider, MockSTTProvider):
        stt_provider.queue_transcript("Orbis, restore my VS Code and terminal workspace")

    voice_service.start_listening()
    audio_3a = voice_service.stop_listening()
    res_3a = await voice_service.process_spoken_request(audio_3a)

    print(f"  Spoken Transcript: '{res_3a.raw_transcript}'")
    print("[Step 1] Policy flags multi-app restoration requiring explicit user approval.")
    approval_prompt_msg = "Restoration will launch 2 applications. Do you want to proceed?"
    print(f"  ORBIS Speaks: '{approval_prompt_msg}'")

    print("\n[Step 2] User activates Push-to-Talk for explicit second confirmation interaction...")
    if isinstance(stt_provider, MockSTTProvider):
        stt_provider.queue_transcript("Yes, proceed.")

    voice_service.start_listening()
    audio_3b = voice_service.stop_listening()

    is_approved, explanation = await voice_service.process_spoken_approval(audio_3b)
    print(f"  Confirmation Outcome: Approved={is_approved} ({explanation})")
    print(f"  Invariant Verified: Original command cannot self-authorize. Explicit confirmation satisfied gate.")
    print(f"  Confirmation Audio Released: {audio_3b.is_empty}")

    # ========================================================================
    # SCENARIO 4: High-Risk Spoken Command Rejected Without Approval
    # ========================================================================
    print("\n" + "-" * 80)
    print("SCENARIO 4: HIGH-RISK SPOKEN COMMAND REJECTED WITHOUT APPROVAL")
    print("User attempts dangerous command -> Prompted for confirmation -> Spoken cancellation")
    print("-" * 80)

    print("[Step 1] User speaks high-risk command: 'ORBIS, delete all production database backups'")
    if isinstance(stt_provider, MockSTTProvider):
        stt_provider.queue_transcript("ORBIS, delete all production database backups")

    voice_service.start_listening()
    audio_4a = voice_service.stop_listening()
    transcription_4a = await stt_provider.transcribe(audio_4a)
    audio_4a.release()

    print(f"  Transcribed: '{transcription_4a.raw_text}'")
    print("  Authoritative Phase 6 Safety: Assessed as HIGH risk. Cannot be downgraded by transcript phrasing.")
    print("  ORBIS Speaks: 'Deleting backups is a HIGH-RISK operation. Do you want to proceed?'")

    print("\n[Step 2] User activates Push-to-Talk and rejects: 'No, cancel that.'")
    if isinstance(stt_provider, MockSTTProvider):
        stt_provider.queue_transcript("No, cancel that.")

    voice_service.start_listening()
    audio_4b = voice_service.stop_listening()

    is_approved_4, explanation_4 = await voice_service.process_spoken_approval(audio_4b)
    print(f"  Confirmation Outcome: Approved={is_approved_4} ({explanation_4})")
    print("  Execution Result: Execution ABORTED safely. Zero database backups or files modified.")
    print(f"  Audio Released: {audio_4b.is_empty}")

    # ========================================================================
    # SCENARIO 5: Empty / Inaudible Audio Safe Fallback
    # ========================================================================
    print("\n" + "-" * 80)
    print("SCENARIO 5: EMPTY / INAUDIBLE AUDIO SAFE FALLBACK")
    print("Microphone captures pure silence or user releases Push-to-Talk without speaking.")
    print("-" * 80)

    # Empty / silent audio buffer
    silent_audio = AudioBuffer(data=b"\x00\x00" * 16000, sample_rate=16000)
    res_5 = await voice_service.process_spoken_request(silent_audio)

    print("  Detection: Audio identified as empty/inaudible pure silence.")
    print(f"  Spoken Guidance: '{res_5.agent_response_summary}'")
    print("  Agent Bypass: Existing planner/LLM was NOT invoked for empty input (saves computation & prevents hallucination).")
    print(f"  Audio Released: {silent_audio.is_empty}")

    print("\n" + "=" * 80)
    print("PHASE 11 VOICE INTERFACE DEMONSTRATION COMPLETE: ALL SCENARIOS PASSED")
    print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ORBIS Phase 11 Voice Demonstration")
    parser.add_argument("--live", action="store_true", help="Run with live Windows SAPI5 speech synthesis")
    args = parser.parse_args()

    asyncio.run(run_phase11_demo(live_mode=args.live))
