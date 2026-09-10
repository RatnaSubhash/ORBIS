"""
Voice Normalizer and Speech Text Formatting for ORBIS Phase 11.
Normalizes raw spoken transcripts into standard agent queries and formats
rich agent responses into concise, safe spoken executive summaries.
"""

import re
from typing import List, Optional


# Invocation prefix patterns to strip from the beginning of spoken requests
INVOCATION_PREFIX_REGEX = re.compile(
    r"^(?:(?:hey|ok|okay|please|hi|hello)\s+)?orbis[,:\s-]+\s*",
    re.IGNORECASE,
)


class VoiceNormalizer:
    """
    Handles bidirectional speech text processing:
    1. Inbound: Normalizes raw spoken transcripts into clean agent requests.
    2. Outbound: Formats agent responses into concise, safe text suitable for TTS vocalization.
    """

    @classmethod
    def normalize_input(cls, raw_transcript: str, strip_prefix: bool = True) -> str:
        """
        Cleans and normalizes spoken input text:
        - Strips conversational 'ORBIS' invocation prefixes.
        - Trims leading/trailing whitespace and redundant punctuation.
        """
        if not raw_transcript:
            return ""

        text = raw_transcript.strip()

        if strip_prefix:
            text = INVOCATION_PREFIX_REGEX.sub("", text).strip()

        # Clean leading punctuation that may result from transcription
        text = re.sub(r"^[,:;\s.-]+", "", text).strip()

        return text

    @classmethod
    def is_affirmative(cls, text: str) -> bool:
        """
        Determines whether a spoken response represents an explicit affirmative confirmation.
        Used exclusively during active two-step approval gates.
        """
        if not text:
            return False
        cleaned = re.sub(r"[^\w\s]", " ", text.lower()).strip()
        tokens = cleaned.split()
        if not tokens:
            return False
        affirmatives = {
            "yes", "yes proceed", "proceed", "confirm", "confirmed",
            "approve", "approved", "go ahead", "do it", "sure", "yes please",
            "affirmative", "ok", "okay",
        }
        if cleaned in affirmatives:
            return True
        if tokens[0] in {"yes", "proceed", "confirm", "approve"}:
            return True
        return False

    @classmethod
    def is_negative(cls, text: str) -> bool:
        """Determines whether a spoken response represents an explicit cancellation/rejection."""
        if not text:
            return False
        cleaned = re.sub(r"[^\w\s]", " ", text.lower()).strip()
        tokens = cleaned.split()
        if not tokens:
            return False
        negatives = {
            "no", "cancel", "stop", "abort", "do not", "dont", "no stop",
            "reject", "rejected", "negative", "cancel that", "no cancel that",
        }
        if cleaned in negatives:
            return True
        if tokens[0] in {"no", "cancel", "stop", "abort", "reject", "dont"}:
            return True
        return False

    @classmethod
    def format_output_for_speech(cls, text: str, max_chars: int = 300) -> str:
        """
        Prepares agent output for safe Text-to-Speech vocalization:
        - Strips Markdown code fences, tables, headers, and bullet points.
        - Suppresses raw JSON or large technical data.
        - Ensures redacted secrets are never spoken aloud.
        - Formats a concise, natural executive summary.
        """
        if not text:
            return ""

        spoken = text.strip()

        # 1. Remove Markdown code blocks ```...```
        spoken = re.sub(r"```[\s\S]*?```", " [code snippet omitted] ", spoken)

        # 2. Remove inline code snippets `...`
        spoken = re.sub(r"`([^`]+)`", r"\1", spoken)

        # 3. Remove Markdown image tags ![alt](url) and links [text](url)
        spoken = re.sub(r"!\[.*?\]\(.*?\)", "", spoken)
        spoken = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", spoken)

        # 4. Remove Markdown table syntax (| ... |)
        spoken = re.sub(r"\|.*\|", " ", spoken)

        # 5. Remove Markdown headers (### etc.) and bullet markers (*, -, >)
        spoken = re.sub(r"^[#\*\->\s]+", "", spoken, flags=re.MULTILINE)

        # 6. Replace redacted secrets with explicit safe marker
        spoken = spoken.replace("[REDACTED API KEY]", "redacted key")

        # 7. Normalize redundant whitespace
        spoken = re.sub(r"\s+", " ", spoken).strip()

        # 8. Truncate excessively long spoken text at sentence boundaries if over limit
        if len(spoken) > max_chars:
            sentences = re.split(r"(?<=[.!?])\s+", spoken)
            truncated = ""
            for s in sentences:
                if len(truncated) + len(s) <= max_chars:
                    truncated = (truncated + " " + s).strip()
                else:
                    break

            if truncated:
                spoken = truncated
            else:
                spoken = spoken[:max_chars].rstrip() + "..."

        return spoken
