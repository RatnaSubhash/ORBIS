"""
Production Faster-Whisper Speech-to-Text (STT) Provider for ORBIS Phase 11.
Provides fast, local, GPU/CPU-accelerated speech transcription without sending
audio to any cloud or external network endpoint.
"""

import asyncio
import logging
from typing import Optional

from core.voice.models import AudioBuffer, TranscriptionResult
from core.voice.stt.base import BaseSTTProvider

logger = logging.getLogger("orbis.voice.stt.whisper")


class FasterWhisperSTTProvider(BaseSTTProvider):
    """
    Local-first STT provider powered by faster-whisper (CTranslate2).
    Processes in-memory PCM buffers directly without writing audio files to disk.
    Executes inference in a background thread to prevent blocking the async event loop.
    """

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        beam_size: int = 5,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self._model = None
        self._initialized = False

    def _load_model(self) -> None:
        """Lazily initializes the faster-whisper model."""
        if self._initialized:
            return

        try:
            from faster_whisper import WhisperModel
            logger.info("Loading faster-whisper model '%s' on %s (%s)...", self.model_size, self.device, self.compute_type)
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
            self._initialized = True
            logger.info("faster-whisper model '%s' loaded successfully.", self.model_size)
        except Exception as e:
            logger.warning("Failed to initialize faster-whisper: %s", e)
            self._initialized = False
            self._model = None

    def is_available(self) -> bool:
        """Checks if faster_whisper is importable and available."""
        try:
            import faster_whisper
            return True
        except ImportError:
            return False

    def _transcribe_sync(self, audio_data: bytes, sample_rate: int) -> TranscriptionResult:
        """Synchronous transcription executed in a thread worker."""
        import numpy as np

        duration = len(audio_data) / float(sample_rate * 2) if sample_rate else 0.0

        if not audio_data or duration < 0.1 or all(b == 0 for b in audio_data):
            return TranscriptionResult(
                text="",
                raw_text="",
                confidence=0.0,
                audio_duration_seconds=duration,
                is_empty_or_inaudible=True,
                provider_used="faster-whisper",
            )

        if not self._initialized:
            self._load_model()

        if self._model is None:
            raise RuntimeError("faster-whisper model could not be initialized.")

        # Convert 16-bit PCM bytes to float32 normalized array
        int16_arr = np.frombuffer(audio_data, dtype=np.int16)
        float32_arr = int16_arr.astype(np.float32) / 32768.0

        # Transcribe directly from the in-memory numpy array
        segments, info = self._model.transcribe(
            float32_arr,
            beam_size=self.beam_size,
            language="en",
            task="transcribe",
        )

        segment_texts = []
        confidences = []
        for s in segments:
            segment_texts.append(s.text)
            if hasattr(s, "avg_logprob") and s.avg_logprob is not None:
                # Convert log probability to approximate confidence [0, 1]
                confidences.append(max(0.0, min(1.0, float(np.exp(s.avg_logprob)))))

        combined_text = " ".join(segment_texts).strip()
        avg_confidence = float(np.mean(confidences)) if confidences else 0.90

        is_inaudible = len(combined_text) == 0

        return TranscriptionResult(
            text=combined_text,
            raw_text=combined_text,
            confidence=avg_confidence if not is_inaudible else 0.0,
            audio_duration_seconds=duration,
            language=info.language if info and hasattr(info, "language") else "en",
            is_empty_or_inaudible=is_inaudible,
            provider_used="faster-whisper",
        )

    async def transcribe(self, audio: AudioBuffer) -> TranscriptionResult:
        """Asynchronously transcribes in-memory audio without blocking the event loop."""
        return await asyncio.to_thread(self._transcribe_sync, audio.data, audio.sample_rate)
