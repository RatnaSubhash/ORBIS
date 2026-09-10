"""
Deterministic Mock Speech-to-Text (STT) Provider for ORBIS Phase 11.
Provides offline, deterministic transcription for testing and demonstrations.
"""

from typing import Dict, List, Optional
from core.voice.models import AudioBuffer, TranscriptionResult
from core.voice.stt.base import BaseSTTProvider


class MockSTTProvider(BaseSTTProvider):
    """
    In-tree, offline STT provider that returns pre-configured or deterministic transcriptions.
    Enforces inaudible/empty audio detection without external network dependencies.
    """

    def __init__(
        self,
        default_transcript: str = "What was I working on?",
        transcript_queue: Optional[List[str]] = None,
        available: bool = True,
    ) -> None:
        self.default_transcript = default_transcript
        self._queue = list(transcript_queue) if transcript_queue else []
        self._available = available
        self.transcription_history: List[TranscriptionResult] = []

    def queue_transcript(self, text: str) -> None:
        """Queues a transcription text for the next transcribe() call."""
        self._queue.append(text)

    async def transcribe(self, audio: AudioBuffer) -> TranscriptionResult:
        """Transcribes the in-memory AudioBuffer deterministically."""
        if not self._available:
            raise RuntimeError("STT provider is unavailable or model failed to load.")

        duration = audio.duration_seconds

        # Inaudible / Empty audio detection:
        # Buffer is empty, or under 0.1s, or all bytes are zero (pure silence)
        if audio.is_empty or duration < 0.1 or all(b == 0 for b in audio.data):
            res = TranscriptionResult(
                text="",
                raw_text="",
                confidence=0.0,
                audio_duration_seconds=duration,
                is_empty_or_inaudible=True,
                provider_used="mock_stt",
            )
            self.transcription_history.append(res)
            return res

        # Retrieve next queued text or default
        if self._queue:
            raw_text = self._queue.pop(0)
        else:
            raw_text = self.default_transcript

        res = TranscriptionResult(
            text=raw_text.strip(),
            raw_text=raw_text,
            confidence=0.98,
            audio_duration_seconds=duration,
            is_empty_or_inaudible=False,
            provider_used="mock_stt",
        )
        self.transcription_history.append(res)
        return res

    def is_available(self) -> bool:
        return self._available
