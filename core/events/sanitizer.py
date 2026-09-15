"""
Event Telemetry Sanitizer for ORBIS Phase 12.
Ensures that user-facing agent events and metadata never expose sensitive credentials,
API keys, passwords, internal chain-of-thought, system prompts, raw HTML, or environment secrets.
"""

import re
from typing import Any, Dict, List, Optional, Set, Union
from datetime import datetime, timezone

from core.events.models import AgentEvent, AgentEventType
from core.tools.secret_redaction import redact_secrets
from core.types import RiskLevel

# Keys that must be completely dropped from event metadata (internal/private telemetry)
DROPPED_METADATA_KEYS: Set[str] = {
    "prompt",
    "system_prompt",
    "thought",
    "reasoning",
    "chain_of_thought",
    "internal_reasoning",
    "raw_html",
    "html",
    "raw_content",
    "raw_output",
    "raw_result",
    "env",
    "dot_env",
    "env_content",
    "authorization",
    "bearer",
    "cookie",
    "private_key",
}

# Key substrings that indicate credential fields to be masked with [REDACTED]
CREDENTIAL_KEY_SUBSTRINGS: Set[str] = {
    "password",
    "passwd",
    "api_key",
    "apikey",
    "secret",
    "token",
    "credential",
    "auth_token",
    "access_token",
    "secret_key",
    "private_key",
}

# Regex for detecting key=val or key: val credential assignments inside text
ASSIGNMENT_SECRET_PATTERN = re.compile(
    r"""(?i)\b(api[_-]?key|auth[_-]?token|secret[_-]?key|password|passwd|access[_-]?token|token|secret)\b\s*[:=]\s*['"]?([^\s'"\r\n]{3,})['"]?""",
)

# Regex for detecting HTML markup
HTML_TAG_PATTERN = re.compile(
    r"(?i)<(!DOCTYPE|html|head|body|script|style|div|span|p|a|iframe|table|form)[^>]*>",
)


class EventSanitizer:
    """
    Authoritative sanitizer for ORBIS event telemetry.
    Applies multi-layered sanitization: secret redaction, key drop policies,
    credential value masking, HTML scrubbing, and bounded length enforcement.
    """

    MAX_MESSAGE_LENGTH: int = 500
    MAX_METADATA_STRING_LENGTH: int = 300
    MAX_LIST_ITEMS: int = 25
    MAX_DEPTH: int = 5

    @classmethod
    def clean_message(cls, message: str) -> str:
        """
        Sanitizes a user-facing event message.
        Guarantees no credentials, passwords, tokens, or raw HTML leak into user-facing telemetry.
        """
        if not message:
            return ""

        text = str(message).strip()

        # 1. Apply authoritative ORBIS secret redaction
        text = redact_secrets(text)

        # 2. Mask any key=val or key: val credential assignments
        def _mask_assignment(match: re.Match) -> str:
            k = match.group(1)
            return f"{k}=[REDACTED]"

        text = ASSIGNMENT_SECRET_PATTERN.sub(_mask_assignment, text)

        # 3. Scrub HTML tags
        if HTML_TAG_PATTERN.search(text):
            text = HTML_TAG_PATTERN.sub("[HTML]", text)

        # 4. Enforce length boundary
        if len(text) > cls.MAX_MESSAGE_LENGTH:
            text = text[: cls.MAX_MESSAGE_LENGTH] + "... [TRUNCATED]"

        return text

    @classmethod
    def clean_metadata(
        cls,
        metadata: Optional[Dict[str, Any]],
        depth: int = 0,
    ) -> Dict[str, Any]:
        """
        Recursively sanitizes event metadata dictionary.
        Drops prohibited internal keys and masks sensitive values.
        """
        if not metadata or not isinstance(metadata, dict):
            return {}

        if depth >= cls.MAX_DEPTH:
            return {"_warning": "[MAX_METADATA_DEPTH_EXCEEDED]"}

        clean: Dict[str, Any] = {}

        for raw_k, v in metadata.items():
            k_str = str(raw_k).strip()
            k_lower = k_str.lower()

            # 1. Drop prohibited internal reasoning / prompt / raw dump keys
            if any(dropped in k_lower for dropped in DROPPED_METADATA_KEYS):
                continue

            # 2. Mask credential keys with [REDACTED]
            if any(cred in k_lower for cred in CREDENTIAL_KEY_SUBSTRINGS):
                clean[k_str] = "[REDACTED]"
                continue

            # 3. Recursively sanitize value
            clean[k_str] = cls._sanitize_value(v, depth=depth + 1)

        return clean

    @classmethod
    def _sanitize_value(cls, val: Any, depth: int) -> Any:
        """Sanitizes an individual metadata value across supported types."""
        if val is None or isinstance(val, (int, float, bool)):
            return val

        if isinstance(val, str):
            # Apply secret redaction
            cleaned = redact_secrets(val)

            # Mask assignment secrets
            def _mask_assignment(match: re.Match) -> str:
                k = match.group(1)
                return f"{k}=[REDACTED]"

            cleaned = ASSIGNMENT_SECRET_PATTERN.sub(_mask_assignment, cleaned)

            # Scrub HTML tags
            if HTML_TAG_PATTERN.search(cleaned):
                cleaned = "[HTML_CONTENT_REDACTED]"

            # Enforce string length
            if len(cleaned) > cls.MAX_METADATA_STRING_LENGTH:
                cleaned = cleaned[: cls.MAX_METADATA_STRING_LENGTH] + "... [TRUNCATED]"

            return cleaned

        if isinstance(val, dict):
            return cls.clean_metadata(val, depth=depth)

        if isinstance(val, (list, tuple, set)):
            if depth >= cls.MAX_DEPTH:
                return ["[MAX_DEPTH]"]
            items = list(val)[: cls.MAX_LIST_ITEMS]
            return [cls._sanitize_value(item, depth=depth + 1) for item in items]

        # For arbitrary objects, convert to string safely
        str_repr = str(val)
        if len(str_repr) > cls.MAX_METADATA_STRING_LENGTH:
            str_repr = str_repr[: cls.MAX_METADATA_STRING_LENGTH] + "... [TRUNCATED]"
        return redact_secrets(str_repr)

    @classmethod
    def create_safe_event(
        cls,
        event_type: AgentEventType,
        task_id: str,
        correlation_id: str,
        safe_user_message: str,
        status: str = "IN_PROGRESS",
        parent_task_id: Optional[str] = None,
        step_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        risk_level: Optional[RiskLevel] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[datetime] = None,
    ) -> AgentEvent:
        """
        Constructs a sanitized, immutable AgentEvent.
        Applies all safety boundaries before returning the frozen model.
        """
        clean_msg = cls.clean_message(safe_user_message)
        clean_meta = cls.clean_metadata(metadata)

        return AgentEvent(
            correlation_id=correlation_id,
            task_id=task_id,
            parent_task_id=parent_task_id,
            step_id=step_id,
            event_type=event_type,
            status=status,
            safe_user_message=clean_msg,
            tool_name=tool_name,
            risk_level=risk_level,
            metadata=clean_meta,
            timestamp=timestamp or datetime.now(timezone.utc),
        )
