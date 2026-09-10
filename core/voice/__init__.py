"""
ORBIS Phase 11 Voice Interface Subsystem.
Provides Push-to-Talk audio capture, local-first Speech-to-Text (STT),
voice normalization, and safe local Text-to-Speech (TTS) vocalization.
"""

from core.voice.models import (
    AudioBuffer,
    SpeechResult,
    TranscriptionResult,
    VoiceInteractionResult,
    VoiceState,
)
from core.voice.capture.base import BaseAudioCapture
from core.voice.capture.mock_capture import MockAudioCapture
from core.voice.capture.sounddevice_capture import SoundDeviceAudioCapture
from core.voice.normalizer import VoiceNormalizer
from core.voice.service import VoiceService
from core.voice.stt.base import BaseSTTProvider
from core.voice.stt.mock_stt import MockSTTProvider
from core.voice.stt.whisper_stt import FasterWhisperSTTProvider
from core.voice.tts.base import BaseTTSProvider
from core.voice.tts.mock_tts import MockTTSProvider
from core.voice.tts.sapi_tts import WindowsSapiTTSProvider

__all__ = [
    "AudioBuffer",
    "BaseAudioCapture",
    "BaseSTTProvider",
    "BaseTTSProvider",
    "FasterWhisperSTTProvider",
    "MockAudioCapture",
    "MockSTTProvider",
    "MockTTSProvider",
    "SoundDeviceAudioCapture",
    "SpeechResult",
    "TranscriptionResult",
    "VoiceInteractionResult",
    "VoiceNormalizer",
    "VoiceService",
    "VoiceState",
    "WindowsSapiTTSProvider",
]
