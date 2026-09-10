"""
Abstract Speech-to-Text (STT) Provider Interface for ORBIS Phase 11.
Defines the local-first speech transcription contract.
"""

from abc import ABC, abstractmethod
from core.voice.models import AudioBuffer, TranscriptionResult


class BaseSTTProvider(ABC):
    """
    Abstract interface for local-first Speech-to-Text transcription.
    Operates strictly on in-memory AudioBuffers.
    """

    @abstractmethod
    async def transcribe(self, audio: AudioBuffer) -> TranscriptionResult:
        """
        Transcribes the in-memory AudioBuffer into a TranscriptionResult.
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Checks whether the STT provider model and runtime are ready."""
        pass
