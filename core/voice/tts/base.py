"""
Abstract Text-to-Speech (TTS) Provider Interface for ORBIS Phase 11.
Defines the local-first speech synthesis contract.
"""

from abc import ABC, abstractmethod
from core.voice.models import SpeechResult


class BaseTTSProvider(ABC):
    """
    Abstract interface for local-first Text-to-Speech synthesis and playback.
    Operates offline without sending text to cloud providers.
    """

    @abstractmethod
    async def synthesize(self, text: str) -> SpeechResult:
        """Synthesizes text into speech without blocking execution."""
        pass

    @abstractmethod
    async def speak(self, text: str) -> SpeechResult:
        """Synthesizes text and plays audio through the active output device."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Immediately halts any active audio playback."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Checks whether the TTS provider and speech runtime are available."""
        pass
