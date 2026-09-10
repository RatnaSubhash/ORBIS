"""
Deterministic Mock Text-to-Speech (TTS) Provider for ORBIS Phase 11.
Provides offline speech synthesis and playback tracking for automated testing.
"""

from typing import List, Optional
from core.voice.models import SpeechResult
from core.voice.tts.base import BaseTTSProvider


class MockTTSProvider(BaseTTSProvider):
    """
    In-tree, offline TTS provider that tracks spoken utterances in memory.
    Supports cancellation, error simulation, and duration estimation.
    """

    def __init__(
        self,
        voice_name: str = "Mock Voice (Offline)",
        available: bool = True,
        words_per_minute: int = 150,
    ) -> None:
        self.voice_name = voice_name
        self._available = available
        self.words_per_minute = words_per_minute
        self.spoken_history: List[str] = []
        self.synthesized_history: List[str] = []
        self._is_stopped = False

    def _estimate_duration(self, text: str) -> float:
        """Estimates speech duration in seconds based on word count."""
        words = len(text.split())
        return max(0.2, (words / float(self.words_per_minute)) * 60.0)

    async def synthesize(self, text: str) -> SpeechResult:
        """Synthesizes text in memory without hardware output."""
        if not self._available:
            return SpeechResult(
                text_spoken=text,
                duration_seconds=0.0,
                success=False,
                error="TTS provider unavailable.",
                provider_used="mock_tts",
            )

        self.synthesized_history.append(text)
        duration = self._estimate_duration(text)
        return SpeechResult(
            text_spoken=text,
            duration_seconds=duration,
            voice_name=self.voice_name,
            success=True,
            provider_used="mock_tts",
        )

    async def speak(self, text: str) -> SpeechResult:
        """Simulates audio playback and records the utterance in spoken history."""
        if not self._available:
            return SpeechResult(
                text_spoken=text,
                duration_seconds=0.0,
                success=False,
                error="TTS provider unavailable.",
                provider_used="mock_tts",
            )

        self.spoken_history.append(text)
        duration = self._estimate_duration(text)
        was_cancelled = self._is_stopped
        self._is_stopped = False

        return SpeechResult(
            text_spoken=text,
            duration_seconds=duration if not was_cancelled else 0.05,
            voice_name=self.voice_name,
            success=True,
            was_cancelled=was_cancelled,
            provider_used="mock_tts",
        )

    def stop(self) -> None:
        """Simulates stopping audio playback immediately."""
        self._is_stopped = True

    def is_available(self) -> bool:
        return self._available
