"""
Production SoundDevice Audio Capture for ORBIS Phase 11.
Provides real-time Push-to-Talk microphone recording on Windows systems
using the sounddevice library. Audio is buffered strictly in memory.
"""

import io
import logging
import threading
from typing import List, Optional

from core.voice.capture.base import BaseAudioCapture
from core.voice.models import AudioBuffer

logger = logging.getLogger("orbis.voice.capture.sounddevice")


class SoundDeviceAudioCapture(BaseAudioCapture):
    """
    Microphone capture using sounddevice and PortAudio.
    Buffers raw PCM audio strictly in application memory during the Push-to-Talk window.
    No audio files are written to disk.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        device_index: Optional[int] = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.device_index = device_index
        self._is_capturing = False
        self._frames: List[bytes] = []
        self._stream = None
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        """Checks if sounddevice is installed and an input device is accessible."""
        try:
            import sounddevice as sd
            if self.device_index is not None:
                dev = sd.query_devices(self.device_index)
                if isinstance(dev, dict):
                    return dev.get("max_input_channels", 0) > 0
                return False
            devices = sd.query_devices()
            # Check if there is at least one input device
            return any(d.get("max_input_channels", 0) > 0 for d in devices if isinstance(d, dict))
        except Exception as e:
            logger.debug("SoundDevice unavailable: %s", e)
            return False

    def _audio_callback(self, indata, frames, time_info, status):
        """Callback invoked by PortAudio for every captured audio block."""
        if status:
            logger.warning("SoundDevice audio callback status: %s", status)
        with self._lock:
            if self._is_capturing:
                # Convert float32 or int16 numpy buffer to 16-bit signed PCM bytes
                import numpy as np
                if indata.dtype == np.int16:
                    pcm_bytes = indata.tobytes()
                elif indata.dtype == np.float32:
                    # Clip and scale to int16 range [-32768, 32767]
                    clipped = np.clip(indata, -1.0, 1.0)
                    pcm_bytes = (clipped * 32767).astype(np.int16).tobytes()
                else:
                    pcm_bytes = indata.astype(np.int16).tobytes()
                self._frames.append(pcm_bytes)

    def start_capture(self) -> None:
        """Starts Push-to-Talk audio recording."""
        if not self.is_available():
            raise RuntimeError("SoundDevice audio capture is not available on this system.")

        with self._lock:
            if self._is_capturing:
                return
            self._frames = []
            self._is_capturing = True

        try:
            import sounddevice as sd
            import numpy as np

            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                device=self.device_index,
                callback=self._audio_callback,
            )
            self._stream.start()
            logger.info("SoundDevice capture started (sample_rate=%d, channels=%d)", self.sample_rate, self.channels)
        except Exception as e:
            with self._lock:
                self._is_capturing = False
            logger.error("Failed to start SoundDevice capture stream: %s", e)
            raise RuntimeError(f"Microphone capture failed to start: {str(e)}") from e

    def stop_capture(self) -> AudioBuffer:
        """Stops recording and returns accumulated PCM frames in an AudioBuffer."""
        with self._lock:
            self._is_capturing = False

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                logger.warning("Error closing SoundDevice stream: %s", e)
            finally:
                self._stream = None

        with self._lock:
            pcm_data = b"".join(self._frames)
            self._frames = []

        logger.info("SoundDevice capture stopped; accumulated %d bytes (%.2fs)",
                    len(pcm_data), len(pcm_data) / (self.sample_rate * self.channels * 2) if self.sample_rate else 0)

        return AudioBuffer(
            data=pcm_data,
            sample_rate=self.sample_rate,
            channels=self.channels,
            sample_width=2,
        )

    def is_capturing(self) -> bool:
        with self._lock:
            return self._is_capturing
