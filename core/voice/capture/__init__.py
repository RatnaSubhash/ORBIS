"""
Audio Capture Subpackage for ORBIS Phase 11.
Provides abstract, mock, and production hardware audio capture providers.
"""

from core.voice.capture.base import BaseAudioCapture
from core.voice.capture.mock_capture import MockAudioCapture
from core.voice.capture.sounddevice_capture import SoundDeviceAudioCapture

__all__ = [
    "BaseAudioCapture",
    "MockAudioCapture",
    "SoundDeviceAudioCapture",
]
