"""
Secret and Credential Redaction Utility for ORBIS Phase 7.
Protects sensitive tokens, API keys, private keys, passwords, and environment secrets
from unintentional exposure in tool outputs, LLM contexts, or audit persistence.
"""

import re
from typing import Any, Dict, List, Union

# Common API key and credential patterns
SECRET_PATTERNS: List[re.Pattern] = [
    # Private Key blocks
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----", re.MULTILINE),
    # OpenAI and general LLM sk- tokens
    re.compile(r"\b(sk-[a-zA-Z0-9_\-]{20,})\b"),
    # Anthropic sk-ant- tokens
    re.compile(r"\b(sk-ant-[a-zA-Z0-9_\-]{20,})\b"),
    # GitHub Personal Access Tokens
    re.compile(r"\b(ghp_[a-zA-Z0-9]{30,}|github_pat_[a-zA-Z0-9_]{30,})\b"),
    # AWS Access Key IDs
    re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
    # Authorization: Bearer tokens
    re.compile(r"(?i)\bBearer\s+([a-zA-Z0-9_\-\.]{20,})\b"),
    # Generic key/secret/password assignment patterns: key=val or "key": "val"
    re.compile(
        r"""(?i)\b(api[_-]?key|auth[_-]?token|secret[_-]?key|password|passwd|access[_-]?token)\b\s*[:=]\s*['"]?([^'"\s\r\n]{6,})['"]?""",
    ),
]


def redact_secrets(text: str) -> str:
    """
    Scans a string and masks identifiable secrets, keys, and tokens with '[REDACTED]'.
    Safe to call on arbitrary text or code output.
    """
    if not text:
        return text

    sanitized = text

    # Handle PEM blocks first
    sanitized = re.sub(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]",
        sanitized,
    )

    # Specific token formats
    sanitized = re.sub(r"\b(sk-[a-zA-Z0-9_\-]{20,})\b", "[REDACTED API KEY]", sanitized)
    sanitized = re.sub(r"\b(sk-ant-[a-zA-Z0-9_\-]{20,})\b", "[REDACTED API KEY]", sanitized)
    sanitized = re.sub(r"\b(ghp_[a-zA-Z0-9]{30,}|github_pat_[a-zA-Z0-9_]{30,})\b", "[REDACTED GITHUB TOKEN]", sanitized)
    sanitized = re.sub(r"\b(AKIA[0-9A-Z]{16})\b", "[REDACTED AWS KEY]", sanitized)
    sanitized = re.sub(r"(?i)\bBearer\s+[a-zA-Z0-9_\-\.]{20,}\b", "Bearer [REDACTED TOKEN]", sanitized)

    # Generic key/secret assignments (redact only the value)
    def _mask_assignment(match: re.Match) -> str:
        key_name = match.group(1)
        return f"{key_name}=[REDACTED]"

    sanitized = re.sub(
        r"""(?i)\b(api[_-]?key|auth[_-]?token|secret[_-]?key|password|passwd|access[_-]?token)\b\s*[:=]\s*['"]?([^'"\s\r\n]{6,})['"]?""",
        _mask_assignment,
        sanitized,
    )

    return sanitized


def sanitize_data_structure(data: Union[Dict[str, Any], List[Any], str, Any]) -> Any:
    """
    Recursively redacts secrets from dictionary values, lists, and string fields.
    """
    if isinstance(data, str):
        return redact_secrets(data)
    elif isinstance(data, dict):
        sanitized_dict = {}
        for k, v in data.items():
            if any(secret_term in k.lower() for secret_term in ("secret", "token", "password", "api_key")):
                sanitized_dict[k] = "[REDACTED]"
            else:
                sanitized_dict[k] = sanitize_data_structure(v)
        return sanitized_dict
    elif isinstance(data, list):
        return [sanitize_data_structure(item) for item in data]
    return data
