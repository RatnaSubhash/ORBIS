"""
Text-to-Speech (TTS) Subpackage for ORBIS Phase 11.
Provides abstract, mock, and production Windows SAPI5 speech synthesis providers.
"""

from core.voice.tts.base import BaseTTSProvider
from core.voice.tts.mock_tts import MockTTSProvider
from core.voice.tts.sapi_tts import WindowsSapiTTSProvider

__all__ = [
    "BaseTTSProvider",
    "MockTTSProvider",
    "WindowsSapiTTSProvider",
]
