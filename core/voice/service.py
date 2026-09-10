"""
Voice Service Coordinator for ORBIS Phase 11.
Integrates Audio Capture, STT Transcription, Agent Orchestration, Two-Step Approval,
and TTS Vocalization into a cohesive Push-to-Talk voice interface.
"""

import asyncio
import logging
from typing import Any, Callable, Optional, Tuple

from core.agent.orchestrator import AgentOrchestrator
from core.agent.models import AgentExecutionResult, TaskState
from core.config import get_settings
from core.voice.capture.base import BaseAudioCapture
from core.voice.capture.mock_capture import MockAudioCapture
from core.voice.models import (
    AudioBuffer,
    SpeechResult,
    TranscriptionResult,
    VoiceInteractionResult,
    VoiceState,
)
from core.voice.normalizer import VoiceNormalizer
from core.voice.stt.base import BaseSTTProvider
from core.voice.stt.mock_stt import MockSTTProvider
from core.voice.tts.base import BaseTTSProvider
from core.voice.tts.mock_tts import MockTTSProvider

logger = logging.getLogger("orbis.voice.service")


def extract_spoken_response(agent_exec_res: Any) -> str:
    """
    Extracts the most useful human-readable text from an AgentExecutionResult
    or AgentResponse for speech synthesis, adhering to strict precedence:
    1. If final_output is a non-empty string: use it.
    2. If final_output is a dictionary: prefer keys 'content', 'message', 'text', 'summary'
       in that exact order. Use the first non-empty human-readable value.
       Do NOT speak internal metadata (title, format, char_count, line_count, etc.)
       unless there is no better human-readable field.
    3. If final_output contains no usable human-readable text: fall back to agent_exec_res.summary.
    4. Always passes the selected text through VoiceNormalizer.format_output_for_speech().
    """
    candidate_text: Optional[str] = None

    # If execution failed, prioritize failure explanation from summary
    if hasattr(agent_exec_res, "status") and agent_exec_res.status == TaskState.FAILED:
        candidate_text = getattr(agent_exec_res, "summary", "Task execution failed.")

    final_output = getattr(agent_exec_res, "final_output", None)

    # 1. Non-empty string final_output
    if candidate_text is None and isinstance(final_output, str) and final_output.strip():
        candidate_text = final_output.strip()

    # 2. Dictionary final_output: prefer content, message, text, summary
    elif candidate_text is None and isinstance(final_output, dict):
        preferred_keys = ("content", "message", "text", "summary", "grounded_summary")
        for key in preferred_keys:
            val = final_output.get(key)
            if isinstance(val, str) and val.strip():
                candidate_text = val.strip()
                break

    # 3. Fallback to execution summary if no useful final_output text found
    if not candidate_text:
        summary = getattr(agent_exec_res, "summary", None)
        if isinstance(summary, str) and summary.strip():
            candidate_text = summary.strip()

    # 4. Final fallback default
    if not candidate_text:
        candidate_text = "Task completed successfully."

    return VoiceNormalizer.format_output_for_speech(candidate_text)


class VoiceService:
    """
    Central coordinator for the ORBIS voice interface.
    Operates strictly as an interface adapter layer:
    User speech -> Audio Capture -> STT -> Normalizer -> AgentOrchestrator -> Formatter -> TTS.
    """

    extract_spoken_response = staticmethod(extract_spoken_response)

    def __init__(
        self,
        orchestrator: AgentOrchestrator,
        capture: Optional[BaseAudioCapture] = None,
        stt_provider: Optional[BaseSTTProvider] = None,
        tts_provider: Optional[BaseTTSProvider] = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.capture = capture or MockAudioCapture()
        self.stt_provider = stt_provider or MockSTTProvider()
        self.tts_provider = tts_provider or MockTTSProvider()
        self.state = VoiceState.IDLE
        self._active_approval_pending = False

    def start_listening(self) -> None:
        """Starts Push-to-Talk microphone capture."""
        if not self.capture.is_available():
            self.state = VoiceState.ERROR
            raise RuntimeError("Audio input device is not available.")

        self.state = VoiceState.LISTENING
        self.capture.start_capture()

    def stop_listening(self) -> AudioBuffer:
        """Stops Push-to-Talk capture and returns the in-memory AudioBuffer."""
        self.state = VoiceState.IDLE
        return self.capture.stop_capture()

    async def process_spoken_request(
        self,
        audio: AudioBuffer,
        approval_callback: Optional[Callable[[Any], Any]] = None,
        speak_response: bool = True,
    ) -> VoiceInteractionResult:
        """
        Processes an in-memory AudioBuffer through the full voice lifecycle:
        1. STT Transcription
        2. In-memory buffer release (zero disk persistence)
        3. Query Normalization
        4. Submission to AgentOrchestrator.run_task()
        5. Spoken TTS Vocalization of concise summary
        """
        settings = get_settings()
        if not getattr(settings, "voice_enabled", True):
            return VoiceInteractionResult(
                success=False,
                error="Voice interface is disabled in configuration.",
            )

        # Enforce maximum recording duration check
        max_duration = getattr(settings, "voice_max_recording_seconds", 30.0)
        if audio.duration_seconds > max_duration:
            logger.warning(
                "Audio duration (%.2fs) exceeded max limit (%.2fs); truncating.",
                audio.duration_seconds,
                max_duration,
            )

        # 1. Transcribe Audio
        self.state = VoiceState.TRANSCRIBING
        try:
            transcription: TranscriptionResult = await self.stt_provider.transcribe(audio)
        except Exception as e:
            self.state = VoiceState.ERROR
            logger.error("STT transcription failed: %s", e)
            return VoiceInteractionResult(
                success=False,
                error=f"Transcription failed: {str(e)}",
            )
        finally:
            # Invariant: Raw audio is released from memory after transcription
            audio.release()

        # 2. Check for empty or inaudible audio
        if transcription.is_empty_or_inaudible or not transcription.text:
            self.state = VoiceState.SPEAKING
            guidance = "I didn't catch that. Please hold the push-to-talk button and speak clearly."
            speech_res = None
            if speak_response:
                speech_res = await self.tts_provider.speak(guidance)
            self.state = VoiceState.IDLE
            return VoiceInteractionResult(
                raw_transcript="",
                normalized_query="",
                agent_response_summary=guidance,
                speech_result=speech_res,
                success=True,
            )

        # 3. Normalize Spoken Input
        strip_prefix = getattr(settings, "voice_strip_prefix", True)
        normalized_query = VoiceNormalizer.normalize_input(
            transcription.text, strip_prefix=strip_prefix
        )

        if not normalized_query:
            self.state = VoiceState.IDLE
            return VoiceInteractionResult(
                raw_transcript=transcription.text,
                normalized_query="",
                agent_response_summary="Request was empty after normalization.",
                success=True,
            )

        # 4. Submit to Existing Agent Lifecycle
        self.state = VoiceState.PROCESSING_AGENT
        try:
            agent_exec_res = await self.orchestrator.run_task(
                user_request=normalized_query,
                approval_callback=approval_callback,
            )
        except Exception as e:
            self.state = VoiceState.ERROR
            logger.error("Agent execution failed for voice request: %s", e)
            err_msg = f"Sorry, I encountered an error executing your request: {str(e)}"
            speech_res = None
            if speak_response:
                speech_res = await self.tts_provider.speak(err_msg)
            return VoiceInteractionResult(
                raw_transcript=transcription.raw_text,
                normalized_query=normalized_query,
                agent_response_summary=err_msg,
                speech_result=speech_res,
                success=False,
                error=str(e),
            )

        # Check for approval pause
        requires_approval = False
        approval_prompt = None
        if hasattr(agent_exec_res, "status") and agent_exec_res.status == TaskState.BLOCKED_PENDING_APPROVAL:
            requires_approval = True
            approval_prompt = f"This action requires confirmation to proceed. Would you like me to proceed?"
            summary_to_speak = approval_prompt
            self.state = VoiceState.AWAITING_APPROVAL
        else:
            summary_to_speak = extract_spoken_response(agent_exec_res)
            self.state = VoiceState.SPEAKING

        # 5. Vocalize Result via TTS
        speech_res = None
        if speak_response and summary_to_speak:
            speech_res = await self.tts_provider.speak(summary_to_speak)

        self.state = VoiceState.IDLE
        task_id = getattr(agent_exec_res, "plan_id", None) or getattr(agent_exec_res, "task_id", None)

        if hasattr(agent_exec_res, "status"):
            is_success = agent_exec_res.status in (TaskState.COMPLETED, TaskState.BLOCKED_PENDING_APPROVAL)
        elif hasattr(agent_exec_res, "success"):
            is_success = bool(agent_exec_res.success)
        else:
            is_success = True

        return VoiceInteractionResult(
            raw_transcript=transcription.raw_text,
            normalized_query=normalized_query,
            agent_response_summary=summary_to_speak,
            speech_result=speech_res,
            requires_approval=requires_approval,
            approval_prompt=approval_prompt,
            task_id=task_id,
            success=is_success,
        )

    async def process_spoken_approval(
        self,
        audio: AudioBuffer,
    ) -> Tuple[bool, str]:
        """
        Processes an explicit second spoken interaction intended to satisfy a pending approval gate.
        Invariant: The original spoken command can never count as its own authorization.
        Returns: (is_approved, explanation)
        """
        try:
            transcription = await self.stt_provider.transcribe(audio)
        finally:
            audio.release()

        if transcription.is_empty_or_inaudible or not transcription.text:
            return False, "No audible response detected."

        text = transcription.text.strip()
        if VoiceNormalizer.is_affirmative(text):
            return True, f"Explicit confirmation received ('{text}')."
        elif VoiceNormalizer.is_negative(text):
            return False, f"Explicit cancellation received ('{text}')."
        else:
            return False, f"Unrecognized response ('{text}'). Confirmation not granted."

    def stop_speaking(self) -> None:
        """Immediately interrupts active TTS playback."""
        self.tts_provider.stop()
        if self.state == VoiceState.SPEAKING:
            self.state = VoiceState.IDLE
