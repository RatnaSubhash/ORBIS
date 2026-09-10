"""
Abstract Base Audio Capture Interface for ORBIS Phase 11.
Defines the contract for Push-to-Talk microphone recording.
"""

from abc import ABC, abstractmethod
from core.voice.models import AudioBuffer


class BaseAudioCapture(ABC):
    """
    Abstract interface for capturing microphone audio during Push-to-Talk windows.
    Audio is kept strictly in memory as an AudioBuffer and is never automatically written to disk.
    """

    @abstractmethod
    def start_capture(self) -> None:
        """Starts buffering incoming PCM audio frames from the microphone."""
        pass

    @abstractmethod
    def stop_capture(self) -> AudioBuffer:
        """
        Stops recording and returns the accumulated in-memory AudioBuffer.
        Releases live audio streams.
        """
        pass

    @abstractmethod
    def is_capturing(self) -> bool:
        """Checks whether the capture pipeline is actively recording."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Checks whether a valid audio input device is available."""
        pass
