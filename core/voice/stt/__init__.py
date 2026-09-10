"""
Speech-to-Text (STT) Subpackage for ORBIS Phase 11.
Provides abstract, mock, and production faster-whisper speech transcription providers.
"""

from core.voice.stt.base import BaseSTTProvider
from core.voice.stt.mock_stt import MockSTTProvider
from core.voice.stt.whisper_stt import FasterWhisperSTTProvider

__all__ = [
    "BaseSTTProvider",
    "MockSTTProvider",
    "FasterWhisperSTTProvider",
]
