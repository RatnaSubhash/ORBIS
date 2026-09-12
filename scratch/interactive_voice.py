"""
Interactive Push-to-Talk Voice Interface Launcher (Manual Testing Utility).
Runs outside the core ORBIS architecture without modifying any project files.

Flow:
Press [Enter] -> Speak -> Press [Enter] -> Faster-Whisper STT -> Normalizer ->
AgentOrchestrator -> Windows SAPI5 TTS -> Hear ORBIS speak response.
Type 'q' or 'quit' to exit.
"""

import asyncio
import os
import sys
from pathlib import Path

# Add ORBIS project root to sys.path
ORBIS_ROOT = r"C:\Projects\ORBIS"
if ORBIS_ROOT not in sys.path:
    sys.path.insert(0, ORBIS_ROOT)

from core.agent.llm.ollama import OllamaProvider
from core.agent.orchestrator import AgentOrchestrator
from core.continuity.service import ContinuityService
from core.ice.engine import IntentContinuityEngine
from core.memory.db import DatabaseManager
from core.tools.continuity_tool import ContinuityTool
from core.tools.document_tool import DocumentTool
from core.tools.echo_tool import EchoTool
from core.tools.file_tool import FileTool
from core.tools.registry import ToolRegistry, create_default_tool_registry
from core.tools.restoration_tool import RestorationTool
from core.tools.voice_tool import VoiceTool
from core.tools.web_research_tool import WebResearchTool
from core.voice.capture.sounddevice_capture import SoundDeviceAudioCapture
from core.voice.models import AudioBuffer, VoiceState
from core.voice.normalizer import VoiceNormalizer
from core.voice.service import VoiceService
from core.voice.stt.whisper_stt import FasterWhisperSTTProvider
from core.voice.tts.sapi_tts import WindowsSapiTTSProvider


async def interactive_voice_session():
    print("=" * 75)
    print("ORBIS INTERACTIVE TWO-WAY LOCAL VOICE LAUNCHER")
    print("Direct Hardware Integration: Microphone -> Whisper -> Agent -> SAPI5")
    print("=" * 75)

    # 1. Initialize Database & Context Engine
    db = DatabaseManager(db_path="orbis.db")
    db.initialize()
    real_llm = OllamaProvider()
    continuity_svc = ContinuityService(db=db)
    intent_eng = IntentContinuityEngine(db=db, llm_provider=real_llm)

    # 2. Setup Authoritative Tool Registry with all capabilities (WebResearch, File, Terminal, etc.)
    tools = create_default_tool_registry(db=db)

    # 3. Setup Hardware Providers (strictly real hardware, no mocks)
    print("\n[1/3] Initializing physical microphone capture (sounddevice)...")
    capture = SoundDeviceAudioCapture(sample_rate=16000, channels=1)
    if not capture.is_available():
        print("ERROR: Physical microphone / sounddevice is not available.")
        return

    print("[2/3] Initializing local Faster-Whisper STT (model: base.en)...")
    stt_provider = FasterWhisperSTTProvider(model_size="base.en", device="cpu", compute_type="int8")
    if not stt_provider.is_available():
        print("ERROR: faster_whisper is not available.")
        return
    # Preload model weights so the first voice turn has zero delay
    stt_provider._load_model()
    print("      Faster-Whisper model ready in memory.")

    print("[3/3] Initializing local Windows SAPI5 TTS (Microsoft David Desktop)...")
    tts_provider = WindowsSapiTTSProvider(voice_name="Microsoft David Desktop")
    if not tts_provider.is_available():
        print("ERROR: Windows SAPI TTS is only available on Windows.")
        return

    tools.register(VoiceTool(tts_provider=tts_provider))

    # 4. Setup Agent Orchestrator & Voice Service Coordinator
    orchestrator = AgentOrchestrator(
        llm_provider=real_llm,
        tool_registry=tools,
        db_manager=db,
        continuity_service=continuity_svc,
        intent_engine=intent_eng,
    )

    voice_service = VoiceService(
        orchestrator=orchestrator,
        capture=capture,
        stt_provider=stt_provider,
        tts_provider=tts_provider,
    )
    orchestrator.voice_service = voice_service

    print("\n" + "-" * 75)
    print("READY FOR VOICE INTERACTION (PUSH-TO-TALK MODE)")
    print("Controls:")
    print("  1. Press [Enter] to START recording.")
    print("  2. Speak clearly into your microphone.")
    print("  3. Press [Enter] to STOP recording.")
    print("  4. Listen to ORBIS speak back through your speakers.")
    print("  * Type 'q' or 'quit' at the prompt anytime to exit.")
    print("-" * 75)

    # Vocal welcome greeting through physical speakers
    await tts_provider.speak("ORBIS voice interface is ready. Push to talk whenever you want to speak.")

    turn_count = 0

    while True:
        turn_count += 1
        prompt = input(f"\n[Turn {turn_count}] Press [Enter] to speak (or 'q' to exit): ").strip().lower()
        if prompt in ("q", "quit", "exit"):
            print("\nExiting interactive voice interface.")
            await tts_provider.speak("Goodbye.")
            break

        # Start Push-to-Talk Recording
        print("\n>>> [RECORDING ACTIVE] Speak now...")
        voice_service.start_listening()

        # Wait for user to finish speaking
        input(">>> Press [Enter] when finished speaking...")

        # Stop Push-to-Talk Recording
        audio_buf: AudioBuffer = voice_service.stop_listening()
        duration = audio_buf.duration_seconds
        print(f">>> [RECORDING STOPPED] Captured {duration:.2f}s of audio ({len(audio_buf.data)} bytes in memory).")

        if audio_buf.is_empty or duration < 0.2:
            print("[Warning] No audible speech captured. Please hold PTT and speak clearly.")
            continue

        # Process through full ORBIS Voice Pipeline
        print(">>> [TRANSCRIBING] Running local Faster-Whisper transcription...")
        result = await voice_service.process_spoken_request(audio_buf, speak_response=True)

        print("\n" + "=" * 50)
        print(f"  Raw Transcript:  \"{result.raw_transcript}\"")
        print(f"  Normalized:      \"{result.normalized_query}\"")
        print(f"  Agent Response:  \"{result.agent_response_summary}\"")
        print("=" * 50)

        # Handle Two-Step Approval Gate if action requires confirmation
        if result.requires_approval:
            print(f"\n[APPROVAL REQUIRED] {result.approval_prompt}")
            conf_prompt = input("Press [Enter] to speak confirmation (or type 'cancel'): ").strip().lower()
            if conf_prompt in ("c", "cancel"):
                print("Action cancelled.")
                continue

            print(">>> [RECORDING CONFIRMATION] Speak 'Yes, proceed' or 'No, cancel'...")
            voice_service.start_listening()
            input(">>> Press [Enter] when finished speaking...")
            conf_audio = voice_service.stop_listening()

            is_approved, explanation = await voice_service.process_spoken_approval(conf_audio)
            print(f"  Confirmation Result: Approved={is_approved} ({explanation})")
            if is_approved:
                await tts_provider.speak("Confirmation received. Proceeding with action.")
            else:
                await tts_provider.speak("Action cancelled.")


if __name__ == "__main__":
    try:
        asyncio.run(interactive_voice_session())
    except KeyboardInterrupt:
        print("\nSession interrupted by user. Exiting cleanly.")
