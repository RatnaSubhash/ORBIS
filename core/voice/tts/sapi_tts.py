"""
Production Windows SAPI5 Text-to-Speech (TTS) Provider for ORBIS Phase 11.
Provides offline, local-first speech synthesis on Windows using Microsoft SAPI5
(e.g., Microsoft David Desktop / Microsoft Zira Desktop) without cloud dependencies.
"""

import asyncio
import logging
import subprocess
import sys
from typing import List, Optional

from core.voice.models import SpeechResult
from core.voice.tts.base import BaseTTSProvider

logger = logging.getLogger("orbis.voice.tts.sapi")


class WindowsSapiTTSProvider(BaseTTSProvider):
    """
    Local Windows SAPI5 Text-to-Speech provider.
    Runs speech synthesis completely offline using native Windows speech components.
    Uses discrete subprocess execution with stdin piping to ensure safe argument handling
    and instantaneous playback cancellation via process termination.
    """

    def __init__(
        self,
        voice_name: str = "Microsoft David Desktop",
        words_per_minute: int = 160,
    ) -> None:
        self.voice_name = voice_name
        self.words_per_minute = words_per_minute
        self._current_process: Optional[subprocess.Popen] = None
        self._is_stopped = False

    def is_available(self) -> bool:
        """Checks if running on Windows platform."""
        return sys.platform == "win32"

    def _estimate_duration(self, text: str) -> float:
        """Calculates estimated duration in seconds based on word count."""
        words = len(text.split())
        return max(0.3, (words / float(self.words_per_minute)) * 60.0)

    async def synthesize(self, text: str) -> SpeechResult:
        """Synthesizes speech metadata in memory without playing audio aloud."""
        if not self.is_available():
            return SpeechResult(
                text_spoken=text,
                duration_seconds=0.0,
                success=False,
                error="Windows SAPI TTS is only available on Windows.",
                provider_used="windows_sapi",
            )

        duration = self._estimate_duration(text)
        return SpeechResult(
            text_spoken=text,
            duration_seconds=duration,
            voice_name=self.voice_name,
            success=True,
            provider_used="windows_sapi",
        )

    def _speak_sync(self, text: str) -> SpeechResult:
        """Synchronous speech execution via PowerShell SAPI.SpVoice with stdin piping."""
        if not text.strip():
            return SpeechResult(
                text_spoken="",
                duration_seconds=0.0,
                success=True,
                provider_used="windows_sapi",
            )

        safe_voice = self.voice_name.replace("'", "")
        ps_script = (
            f"$targetVoice = '{safe_voice}'\n"
            "$text = [Console]::In.ReadToEnd()\n"
            "if (-not [string]::IsNullOrWhiteSpace($text)) {\n"
            "    $voice = New-Object -ComObject SAPI.SpVoice\n"
            "    if ($targetVoice) {\n"
            "        foreach ($v in $voice.GetVoices()) {\n"
            "            if ($v.GetDescription() -like \"*$targetVoice*\") {\n"
            "                $voice.Voice = $v\n"
            "                break\n"
            "            }\n"
            "        }\n"
            "    }\n"
            "    $voice.Speak($text) | Out-Null\n"
            "}\n"
        )

        cmd = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            ps_script,
        ]

        self._is_stopped = False
        duration = self._estimate_duration(text)

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._current_process = proc

            # Send speech text via stdin
            _, stderr = proc.communicate(input=text, timeout=60)
            ret = proc.returncode

            if self._is_stopped:
                return SpeechResult(
                    text_spoken=text,
                    duration_seconds=0.1,
                    voice_name=self.voice_name,
                    success=True,
                    was_cancelled=True,
                    provider_used="windows_sapi",
                )

            if ret != 0:
                logger.warning("Windows SAPI playback returned code %d: %s", ret, stderr)
                return SpeechResult(
                    text_spoken=text,
                    duration_seconds=0.0,
                    voice_name=self.voice_name,
                    success=False,
                    error=f"SAPI error: {stderr.strip()}",
                    provider_used="windows_sapi",
                )

            return SpeechResult(
                text_spoken=text,
                duration_seconds=duration,
                voice_name=self.voice_name,
                success=True,
                provider_used="windows_sapi",
            )
        except subprocess.TimeoutExpired:
            if self._current_process:
                self._current_process.kill()
            return SpeechResult(
                text_spoken=text,
                duration_seconds=duration,
                voice_name=self.voice_name,
                success=False,
                error="Playback timed out.",
                provider_used="windows_sapi",
            )
        except Exception as e:
            logger.error("Exception in Windows SAPI playback: %s", e)
            return SpeechResult(
                text_spoken=text,
                duration_seconds=0.0,
                voice_name=self.voice_name,
                success=False,
                error=str(e),
                provider_used="windows_sapi",
            )
        finally:
            self._current_process = None

    async def speak(self, text: str) -> SpeechResult:
        """Asynchronously plays audio aloud using the Windows SAPI voice."""
        if not self.is_available():
            return SpeechResult(
                text_spoken=text,
                duration_seconds=0.0,
                success=False,
                error="Windows SAPI TTS is only available on Windows.",
                provider_used="windows_sapi",
            )

        return await asyncio.to_thread(self._speak_sync, text)

    def stop(self) -> None:
        """Immediately halts active audio playback by terminating the speech process."""
        self._is_stopped = True
        proc = self._current_process
        if proc is not None:
            try:
                proc.terminate()
                proc.kill()
            except Exception as e:
                logger.debug("Error terminating SAPI process: %s", e)
            finally:
                self._current_process = None
