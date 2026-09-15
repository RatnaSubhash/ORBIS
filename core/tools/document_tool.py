"""
Document Tool for ORBIS Phase 8.
Controlled filesystem persistence for generated documents within the allowed workspace root.
Enforces workspace boundaries, extension allowlists, traversal prevention,
protected file blocking, overwrite safety, secret redaction, and independent verification.
"""

from datetime import datetime, timezone
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Literal, Optional, Type
from pydantic import BaseModel, Field, field_validator

from core.config import get_settings
from core.tools.base import BaseTool
from core.tools.secret_redaction import redact_secrets
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport

# Windows reserved device names
RESERVED_DEVICE_NAMES = {
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
}

# Sensitive / protected file patterns that must never be created or overwritten
PROTECTED_PATTERNS = [
    re.compile(r"^\.env(\..+)?$", re.IGNORECASE),
    re.compile(r"^\.git($|[\\/])", re.IGNORECASE),
    re.compile(r"id_rsa|id_ed25519|\.pem$|\.key$", re.IGNORECASE),
    re.compile(r"^credentials(\.json)?$", re.IGNORECASE),
    re.compile(r"system32|windows|sam$", re.IGNORECASE),
]


class DocumentActionParameters(BaseModel):
    """Parameters schema for safe document creation and persistence."""
    file_path: str = Field(
        ...,
        min_length=1,
        description="Target relative or absolute path for the document within the allowed workspace root.",
    )
    content: str = Field(
        ...,
        min_length=1,
        description="Text or Markdown content to write to the document.",
    )
    format: Literal["markdown", "text"] = Field(
        default="markdown",
        description="Document format ('markdown' or 'text').",
    )
    overwrite: bool = Field(
        default=False,
        description="Whether to permit overwriting an existing file (requires high-risk approval).",
    )

    @field_validator("file_path")
    @classmethod
    def validate_file_path_syntax(cls, v: str) -> str:
        clean = v.strip()
        if ".." in clean:
            raise ValueError(f"Path traversal ('..') is strictly forbidden in file path: '{v}'")
        if clean.startswith("//") or clean.startswith(r"\\"):
            raise ValueError(f"UNC network paths are prohibited: '{v}'")
        return clean


class DocumentTool(BaseTool):
    """
    Controlled filesystem persistence tool for Markdown and plain text documents.
    Enforces strict path containment, protected file safety, overwrite gating,
    and independent verification.
    """

    name: str = "document"
    description: str = (
        "Safely creates or updates Markdown (.md) and plain text (.txt) documents within the authorized workspace."
    )
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    parameters_schema: Type[BaseModel] = DocumentActionParameters

    def __init__(self, allowed_workspace_root: Optional[str] = None) -> None:
        settings = get_settings()
        root_str = allowed_workspace_root or settings.allowed_workspace_root
        self.allowed_root = Path(root_str).resolve()
        self.max_bytes = settings.document_max_bytes
        self.allowed_extensions = set(ext.lower() for ext in settings.document_allowed_extensions)

    def resolve_safe_path(self, raw_path: str) -> Path:
        """
        Resolves raw_path against the allowed workspace root and strictly enforces
        canonical containment within the root boundary.
        """
        # 1. Syntactic path checks
        if ".." in raw_path:
            raise ValueError(f"Path traversal ('..') is strictly forbidden: '{raw_path}'.")
        if raw_path.startswith("//") or raw_path.startswith(r"\\"):
            raise ValueError(f"UNC paths are strictly prohibited: '{raw_path}'.")

        norm_path = raw_path.strip()
        if norm_path in ("/", "\\", ".", ""):
            resolved = self.allowed_root
        else:
            candidate = Path(norm_path)
            if not candidate.is_absolute():
                rel_parts = norm_path.lstrip("/\\")
                resolved = (self.allowed_root / rel_parts).resolve()
            else:
                resolved = candidate.resolve()

        # 2. Containment boundary check
        if resolved != self.allowed_root and self.allowed_root not in resolved.parents:
            raise ValueError(
                f"Path safety violation: '{raw_path}' resolves outside workspace root '{self.allowed_root}'."
            )

        # 3. Extension allowlist check
        ext = resolved.suffix.lower()
        if ext not in self.allowed_extensions:
            raise ValueError(
                f"File extension '{ext}' is not permitted. Allowed document extensions: {sorted(self.allowed_extensions)}."
            )

        # 4. Reserved device name check (Windows safety)
        base_name = resolved.stem.lower()
        if base_name in RESERVED_DEVICE_NAMES:
            raise ValueError(f"Filename '{resolved.name}' uses a reserved operating system device name.")

        # 5. Protected / sensitive file pattern check
        name_to_check = resolved.name.lower()
        for pat in PROTECTED_PATTERNS:
            if pat.search(name_to_check) or pat.search(str(resolved).lower()):
                raise ValueError(f"Target path '{raw_path}' is a protected or sensitive file and cannot be modified.")

        return resolved

    def get_effective_risk(self, params: Any) -> RiskLevel:
        """
        Dynamically derives the authoritative RiskLevel.
        Writing a new file is LOW risk; overwriting an existing file is HIGH risk
        and triggers the approval gate.
        """
        if isinstance(params, dict):
            raw_path = params.get("file_path", "")
            overwrite = params.get("overwrite", False)
        elif isinstance(params, DocumentActionParameters):
            raw_path = params.file_path
            overwrite = params.overwrite
        else:
            return RiskLevel.HIGH

        try:
            resolved = self.resolve_safe_path(raw_path)
            if resolved.exists():
                # Writing to an existing file requires explicit overwrite approval (HIGH risk)
                return RiskLevel.HIGH
            return RiskLevel.LOW
        except Exception:
            return RiskLevel.HIGH

    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        """Safely persists the document to the authorized workspace."""
        if not isinstance(params, DocumentActionParameters):
            params = self.validate_parameters(params.model_dump())

        # 1. Resolve and validate path
        try:
            target = self.resolve_safe_path(params.file_path)
        except ValueError as err:
            return ToolResult(
                success=False,
                error=f"Document path safety violation: {str(err)}",
                metadata={"file_path": params.file_path},
            )

        # 2. Check overwrite policy
        file_existed = target.exists()
        if file_existed and not params.overwrite:
            return ToolResult(
                success=False,
                error=f"File already exists at '{params.file_path}'. Set overwrite=True to allow overwriting.",
                metadata={"file_path": str(target), "exists": True},
            )

        # 3. Secret redaction before persisting
        clean_content = redact_secrets(params.content)
        encoded_bytes = clean_content.encode("utf-8")

        # 4. Content size bounding
        if len(encoded_bytes) > self.max_bytes:
            return ToolResult(
                success=False,
                error=f"Content size ({len(encoded_bytes)} bytes) exceeds maximum permitted limit ({self.max_bytes} bytes).",
                metadata={"size_bytes": len(encoded_bytes), "max_bytes": self.max_bytes},
            )

        # 5. Write to filesystem
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(clean_content, encoding="utf-8")

            output = {
                "file_path": str(target),
                "file_name": target.name,
                "size_bytes": len(encoded_bytes),
                "format": params.format,
                "overwritten": file_existed,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

            return ToolResult(
                success=True,
                output=output,
                metadata={
                    "file_path": str(target),
                    "overwritten": file_existed,
                    "size_bytes": len(encoded_bytes),
                    "risk_level": RiskLevel.HIGH.value if file_existed else RiskLevel.LOW.value,
                },
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Failed to write document to '{target}': {str(e)}",
                metadata={"file_path": str(target)},
            )

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        """
        Independently verifies that the real-world file on disk matches expectations:
        file exists, is within allowed root, non-empty, and contents match written data.
        """
        if not result.success:
            return VerificationReport(
                verified=False,
                details="Document persistence reported failure.",
                discrepancies=[result.error or "Unknown failure"],
            )

        raw_path = getattr(params, "file_path", None)
        if not raw_path:
            return VerificationReport(
                verified=False,
                details="Missing file_path parameter for verification.",
                discrepancies=["No file_path provided in params."],
            )

        discrepancies = []
        try:
            target = self.resolve_safe_path(raw_path)
        except Exception as err:
            return VerificationReport(
                verified=False,
                details=f"Verification failed path resolution: {err}",
                discrepancies=[str(err)],
            )

        # 1. Existence and type check
        if not target.exists():
            discrepancies.append(f"Target document does not exist on disk: '{target}'.")
        elif not target.is_file():
            discrepancies.append(f"Target path exists but is not a regular file: '{target}'.")

        if discrepancies:
            return VerificationReport(
                verified=False,
                details="Document filesystem existence verification failed.",
                discrepancies=discrepancies,
            )

        # 2. Size bounds check
        file_size = target.stat().st_size
        if file_size == 0:
            discrepancies.append(f"Target document on disk is empty (0 bytes): '{target}'.")
        if file_size > self.max_bytes:
            discrepancies.append(f"Target document size ({file_size} bytes) exceeds maximum limit ({self.max_bytes} bytes).")

        # 3. Content accuracy check
        try:
            disk_content = target.read_text(encoding="utf-8")
            expected_content = redact_secrets(getattr(params, "content", ""))
            if disk_content != expected_content:
                discrepancies.append("Content on disk does not match expected sanitized document text.")
        except Exception as read_err:
            discrepancies.append(f"Failed to read document from disk for verification: {read_err}")

        if discrepancies:
            return VerificationReport(
                verified=False,
                details="Document verification detected discrepancies on disk.",
                discrepancies=discrepancies,
            )

        return VerificationReport(
            verified=True,
            details=f"Document successfully verified on disk ({file_size} bytes at '{target.name}').",
        )
