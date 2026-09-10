"""
Deterministic Mock Audio Capture for ORBIS Phase 11 Testing.
Simulates Push-to-Talk microphone recording entirely in memory without hardware dependencies.
"""

from typing import Optional
from core.voice.capture.base import BaseAudioCapture
from core.voice.models import AudioBuffer


class MockAudioCapture(BaseAudioCapture):
    """
    Deterministic, in-memory audio capture provider for automated testing and demos.
    Allows injecting pre-configured synthetic PCM data or simulating hardware failures.
    """

    def __init__(
        self,
        default_pcm_data: Optional[bytes] = None,
        available: bool = True,
        sample_rate: int = 16000,
    ) -> None:
        # Default: 1 second of synthetic audible PCM (16000 samples * 2 bytes)
        self.default_pcm_data = default_pcm_data if default_pcm_data is not None else b"\x01\x00" * 16000
        self.available = available
        self.sample_rate = sample_rate
        self._is_capturing = False
        self._next_buffer: Optional[AudioBuffer] = None

    def queue_simulated_audio(self, pcm_data: bytes) -> None:
        """Sets the exact PCM byte content to be returned by the next stop_capture() call."""
        self._next_buffer = AudioBuffer(
            data=pcm_data,
            sample_rate=self.sample_rate,
            channels=1,
            sample_width=2,
        )

    def start_capture(self) -> None:
        """Simulates starting the Push-to-Talk audio recording window."""
        if not self.available:
            raise RuntimeError("Microphone device unavailable or disconnected.")
        self._is_capturing = True

    def stop_capture(self) -> AudioBuffer:
        """Stops capture and returns the simulated in-memory AudioBuffer."""
        self._is_capturing = False
        if self._next_buffer is not None:
            buf = self._next_buffer
            self._next_buffer = None
            return buf

        return AudioBuffer(
            data=self.default_pcm_data,
            sample_rate=self.sample_rate,
            channels=1,
            sample_width=2,
        )

    def is_capturing(self) -> bool:
        return self._is_capturing

    def is_available(self) -> bool:
        return self.available
