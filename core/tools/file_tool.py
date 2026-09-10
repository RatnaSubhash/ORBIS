"""
FileTool for ORBIS Phase 2.
Provides safe, strictly READ-ONLY inspection of file and directory metadata
within a strictly enforced allowed workspace root.
"""

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Type
from pydantic import BaseModel, Field

from core.config import get_settings
from core.tools.base import BaseTool
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport


class FileActionParameters(BaseModel):
    """Parameters for safe read-only FileTool operations."""
    action: Literal["inspect_path", "list_directory", "check_exists", "read_file"] = Field(
        ...,
        description="The read-only filesystem action to execute.",
    )
    path: str = Field(
        ...,
        min_length=1,
        description="Target relative or absolute path within the allowed workspace root.",
    )
    max_entries: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum entries to return when listing directory contents (bounded).",
    )
    max_bytes: int = Field(
        default=65536,
        ge=1,
        le=1048576,
        description="Maximum bytes to read when reading file content.",
    )


class FileTool(BaseTool):
    """
    Tool providing strictly read-only file and directory metadata inspection.
    Enforces a strict path safety boundary: operations outside the allowed workspace
    root or attempting directory traversal (e.g. '../') are rejected.
    """

    name: str = "file"
    description: str = (
        "Safe read-only inspection of file and directory metadata within the allowed workspace root."
    )
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    parameters_schema: Type[BaseModel] = FileActionParameters

    def __init__(self, allowed_workspace_root: Optional[str] = None) -> None:
        settings = get_settings()
        root_str = allowed_workspace_root or settings.allowed_workspace_root
        self.allowed_root = Path(root_str).resolve()

    def resolve_safe_path(self, raw_path: str) -> Path:
        """
        Resolves the provided path against the allowed workspace root and enforces
        that it does not escape the boundary via traversal (../), junctions, or symlinks.
        Raises ValueError if the path escapes the permitted root.
        """
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            resolved = (self.allowed_root / candidate).resolve()
        else:
            resolved = candidate.resolve()

        # Enforce canonical path boundary containment
        try:
            # Check if self.allowed_root is a parent or identical to resolved
            if resolved != self.allowed_root and self.allowed_root not in resolved.parents:
                raise ValueError(
                    f"Path safety violation: '{raw_path}' resolves outside allowed root '{self.allowed_root}'."
                )
        except Exception as e:
            raise ValueError(f"Path boundary check failed: {str(e)}")

        return resolved

    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        """Executes read-only file/directory inspection."""
        if not isinstance(params, FileActionParameters):
            params = self.validate_parameters(params.model_dump())

        try:
            target = self.resolve_safe_path(params.path)
        except ValueError as err:
            return ToolResult(
                success=False,
                error=str(err),
                metadata={"action": params.action, "path": params.path},
            )

        try:
            if params.action == "check_exists":
                exists = target.exists()
                output = {
                    "path": str(target),
                    "exists": exists,
                    "is_file": target.is_file() if exists else False,
                    "is_dir": target.is_dir() if exists else False,
                }

            elif params.action == "inspect_path":
                if not target.exists():
                    return ToolResult(
                        success=False,
                        error=f"Target path does not exist: {target}",
                        metadata={"action": params.action, "path": str(target)},
                    )
                stat = target.stat()
                output = {
                    "path": str(target),
                    "name": target.name,
                    "is_file": target.is_file(),
                    "is_dir": target.is_dir(),
                    "size_bytes": stat.st_size if target.is_file() else None,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "extension": target.suffix.lower() if target.is_file() else None,
                }

            elif params.action == "list_directory":
                if not target.exists():
                    return ToolResult(
                        success=False,
                        error=f"Directory does not exist: {target}",
                    )
                if not target.is_dir():
                    return ToolResult(
                        success=False,
                        error=f"Target is not a directory: {target}",
                    )

                entries = []
                # Safely bounded iteration over immediate directory entries
                with os.scandir(target) as it:
                    for entry in it:
                        if len(entries) >= params.max_entries:
                            break
                        try:
                            is_file = entry.is_file()
                            is_dir = entry.is_dir()
                            size = entry.stat().st_size if is_file else None
                        except OSError:
                            is_file = False
                            is_dir = False
                            size = None

                        entries.append(
                            {
                                "name": entry.name,
                                "is_file": is_file,
                                "is_dir": is_dir,
                                "size_bytes": size,
                            }
                        )

                output = {
                    "path": str(target),
                    "total_entries_sampled": len(entries),
                    "entries": entries,
                }

            elif params.action == "read_file":
                if not target.exists():
                    return ToolResult(
                        success=False,
                        error=f"Target file does not exist: {target}",
                        metadata={"action": params.action, "path": str(target)},
                    )
                if not target.is_file():
                    return ToolResult(
                        success=False,
                        error=f"Target is not a file: {target}",
                        metadata={"action": params.action, "path": str(target)},
                    )
                try:
                    with open(target, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read(params.max_bytes)
                except Exception as read_err:
                    return ToolResult(
                        success=False,
                        error=f"Failed to read file: {read_err}",
                        metadata={"action": params.action, "path": str(target)},
                    )

                output = {
                    "path": str(target),
                    "name": target.name,
                    "content": content,
                    "bytes_read": len(content.encode("utf-8")),
                }

            else:
                return ToolResult(success=False, error=f"Unknown file action: {params.action}")

            return ToolResult(
                success=True,
                output=output,
                metadata={"action": params.action, "path": str(target)},
            )

        except Exception as e:
            return ToolResult(success=False, error=f"File operation failed: {str(e)}")

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        """Independently verifies that the file metadata matches real filesystem state."""
        if not result.success:
            return VerificationReport(
                verified=False,
                details="Tool execution returned failure.",
                discrepancies=[result.error or "Unknown error"],
            )

        if not isinstance(result.output, dict):
            return VerificationReport(
                verified=False,
                details="Invalid output format.",
                discrepancies=["Output is not a dictionary"],
            )

        # Independent disk verification
        try:
            target = self.resolve_safe_path(params.path)
        except ValueError as err:
            return VerificationReport(
                verified=False,
                details=f"Verification failed on path resolution: {err}",
                discrepancies=[str(err)],
            )

        if params.action == "check_exists":
            real_exists = target.exists()
            reported_exists = result.output.get("exists")
            if real_exists != reported_exists:
                return VerificationReport(
                    verified=False,
                    details="Existence mismatch during verification.",
                    discrepancies=[f"Real exists={real_exists}, reported={reported_exists}"],
                )

        elif params.action == "inspect_path":
            if not target.exists():
                return VerificationReport(
                    verified=False,
                    details="Path vanished before verification.",
                    discrepancies=["Target does not exist on disk"],
                )
            stat = target.stat()
            if target.is_file() and stat.st_size != result.output.get("size_bytes"):
                return VerificationReport(
                    verified=False,
                    details="Size mismatch during verification.",
                    discrepancies=[f"Real size={stat.st_size}, reported={result.output.get('size_bytes')}"],
                )

        elif params.action == "list_directory":
            if not target.is_dir():
                return VerificationReport(
                    verified=False,
                    details="Target is not a directory during verification.",
                    discrepancies=["Target is not a directory"],
                )

        elif params.action == "read_file":
            if not target.is_file():
                return VerificationReport(
                    verified=False,
                    details="Target is not a file during verification.",
                    discrepancies=["Target is not a file"],
                )
            if "content" not in result.output:
                return VerificationReport(
                    verified=False,
                    details="Missing content in read_file output.",
                    discrepancies=["Output dictionary does not contain 'content' key"],
                )

        return VerificationReport(
            verified=True,
            details="Filesystem inspection verified independently against disk state.",
        )
