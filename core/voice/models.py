"""
Domain Models for ORBIS Phase 11 Voice Interface.
Defines strongly-typed models for audio frames, in-memory buffers, transcription results,
speech synthesis results, voice states, and end-to-end voice interactions.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4
from pydantic import BaseModel, Field


class VoiceState(str, Enum):
    """Execution state of the voice interface subsystem."""
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    PROCESSING_AGENT = "PROCESSING_AGENT"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    SPEAKING = "SPEAKING"
    ERROR = "ERROR"


class AudioBuffer(BaseModel):
    """
    In-memory PCM audio buffer holding captured speech.
    Raw audio is held only in application memory, is not intentionally persisted,
    and is released after transcription.
    """
    data: bytes = Field(default=b"", description="Raw PCM audio bytes in memory")
    sample_rate: int = Field(default=16000, description="Sampling rate in Hz (e.g. 16000)")
    channels: int = Field(default=1, description="Number of audio channels (1 = mono)")
    sample_width: int = Field(default=2, description="Sample width in bytes (2 = 16-bit signed PCM)")
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def duration_seconds(self) -> float:
        """Calculates audio duration in seconds from byte length."""
        bytes_per_second = self.sample_rate * self.channels * self.sample_width
        if bytes_per_second <= 0:
            return 0.0
        return len(self.data) / float(bytes_per_second)

    @property
    def is_empty(self) -> bool:
        """Checks whether the buffer contains no audio data."""
        return len(self.data) == 0

    def release(self) -> None:
        """Releases the raw audio byte buffer from application memory."""
        self.data = b""


class TranscriptionResult(BaseModel):
    """Outcome of Speech-to-Text (STT) processing on an AudioBuffer."""
    text: str = Field(description="Normalized transcription text")
    raw_text: str = Field(description="Original verbatim transcription before prefix stripping")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Heuristic transcription confidence")
    audio_duration_seconds: float = 0.0
    language: str = "en"
    is_empty_or_inaudible: bool = False
    provider_used: str = "mock"


class SpeechResult(BaseModel):
    """Outcome of Text-to-Speech (TTS) synthesis and audio playback."""
    text_spoken: str
    duration_seconds: float = 0.0
    voice_name: str = "default"
    success: bool = True
    was_cancelled: bool = False
    error: Optional[str] = None
    provider_used: str = "mock"


class VoiceInteractionResult(BaseModel):
    """
    End-to-end outcome of a voice interaction turn:
    User speech -> STT -> Agent lifecycle -> TTS spoken response.
    """
    interaction_id: str = Field(default_factory=lambda: f"vint_{uuid4().hex[:8]}")
    raw_transcript: str = ""
    normalized_query: str = ""
    agent_response_summary: str = ""
    speech_result: Optional[SpeechResult] = None
    requires_approval: bool = False
    approval_prompt: Optional[str] = None
    task_id: Optional[str] = None
    success: bool = True
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
