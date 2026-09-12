"""
Unit and Security Tests for DocumentTool (ORBIS Phase 8).
Validates Markdown/text creation, workspace boundary containment, path traversal prevention,
extension allowlists, protected file blocking, overwrite gates, secret masking,
and independent filesystem verification.
"""

from pathlib import Path
import pytest
from pydantic import ValidationError

from core.tools.document_tool import DocumentActionParameters, DocumentTool
from core.types import ExecutionContext, RiskLevel, ToolResult


# ------------------------------------------------------------------------------
# 1. Document Creation & Format Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_markdown_document_success(tmp_path: Path) -> None:
    """Verifies creating a valid Markdown document within the workspace root."""
    tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    ctx = ExecutionContext()

    content = "# Project Report\n\nThis is a test report in Markdown."
    params = DocumentActionParameters(
        file_path="reports/project_report.md",
        content=content,
        format="markdown",
    )
    result = await tool.execute(params, ctx)

    assert result.success is True
    assert result.output["file_name"] == "project_report.md"
    assert result.output["size_bytes"] > 0
    assert result.output["format"] == "markdown"
    assert result.output["overwritten"] is False

    # Check physical file on disk
    target = tmp_path / "reports" / "project_report.md"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == content

    # Independent verification
    ver_report = await tool.verify(params, result, ctx)
    assert ver_report.verified is True
    assert "Document successfully verified on disk" in ver_report.details


@pytest.mark.asyncio
async def test_create_text_document_success(tmp_path: Path) -> None:
    """Verifies creating a valid plain text (.txt) document."""
    tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    ctx = ExecutionContext()

    text_content = "Plain text log entry.\nTimestamp: 2026-09-10\n"
    params = DocumentActionParameters(
        file_path="notes.txt",
        content=text_content,
        format="text",
    )
    result = await tool.execute(params, ctx)

    assert result.success is True
    target = tmp_path / "notes.txt"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == text_content


# ------------------------------------------------------------------------------
# 2. Security & Filesystem Boundary Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_boundary_containment(tmp_path: Path) -> None:
    """Verifies that attempts to resolve or write outside the workspace root are blocked."""
    tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    ctx = ExecutionContext()

    outside_path = str(tmp_path.parent / "escape.md")
    params = DocumentActionParameters(file_path=outside_path, content="Hacked content")
    result = await tool.execute(params, ctx)

    assert result.success is False
    assert "safety violation" in result.error.lower()
    assert not (tmp_path.parent / "escape.md").exists()


def test_path_traversal_syntax_rejection(tmp_path: Path) -> None:
    """Verifies that path traversal sequences ('..') are rejected during parameter parsing."""
    with pytest.raises(ValidationError, match="Path traversal"):
        DocumentActionParameters(file_path="../escape.md", content="payload")

    with pytest.raises(ValidationError, match="Path traversal"):
        DocumentActionParameters(file_path="docs/../../escape.md", content="payload")


@pytest.mark.asyncio
async def test_protected_files_blocklist(tmp_path: Path) -> None:
    """Verifies that protected configuration and credential files cannot be written or targeted."""
    tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    ctx = ExecutionContext()

    protected_files = [
        ".env",
        ".env.local",
        "id_rsa.txt",
        "id_ed25519.md",
        "credentials.json",
        "server.pem",
    ]

    for pfile in protected_files:
        params = DocumentActionParameters(file_path=pfile, content="malicious key content")
        result = await tool.execute(params, ctx)
        assert result.success is False
        assert "protected or sensitive file" in result.error or "extension" in result.error.lower()
        assert not (tmp_path / pfile).exists()


@pytest.mark.asyncio
async def test_disallowed_extensions_rejection(tmp_path: Path) -> None:
    """Verifies that script and executable extensions are rejected."""
    tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    ctx = ExecutionContext()

    for disallowed in ["exploit.py", "script.bat", "run.ps1", "app.exe", "data.json"]:
        params = DocumentActionParameters(file_path=disallowed, content="code")
        result = await tool.execute(params, ctx)
        assert result.success is False
        assert "not permitted" in result.error.lower()


@pytest.mark.asyncio
async def test_windows_reserved_device_names_rejection(tmp_path: Path) -> None:
    """Verifies that Windows special device names (CON, NUL, AUX) are rejected."""
    tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    ctx = ExecutionContext()

    for dev in ["con.md", "nul.txt", "aux.md", "com1.txt"]:
        params = DocumentActionParameters(file_path=dev, content="device attack")
        result = await tool.execute(params, ctx)
        assert result.success is False
        assert "reserved" in result.error.lower()


# ------------------------------------------------------------------------------
# 3. Overwrite Policy & Authoritative Dynamic Risk Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_overwrite_policy_enforcement(tmp_path: Path) -> None:
    """Verifies overwrite=False fails when file exists, while overwrite=True succeeds."""
    tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    ctx = ExecutionContext()

    doc_path = "document.md"

    # 1. First write -> succeeds, effective risk is LOW
    p1 = DocumentActionParameters(file_path=doc_path, content="Initial text", overwrite=False)
    assert tool.get_effective_risk(p1) == RiskLevel.LOW
    res1 = await tool.execute(p1, ctx)
    assert res1.success is True

    # 2. Second write to existing file -> proactively evaluates to RiskLevel.HIGH; execute fails if overwrite=False
    p2 = DocumentActionParameters(file_path=doc_path, content="Overwritten text", overwrite=False)
    assert tool.get_effective_risk(p2) == RiskLevel.HIGH
    res2 = await tool.execute(p2, ctx)
    assert res2.success is False
    assert "already exists" in res2.error

    # 3. Third write with overwrite=True -> risk is HIGH, execution succeeds
    p3 = DocumentActionParameters(file_path=doc_path, content="Overwritten text", overwrite=True)
    assert tool.get_effective_risk(p3) == RiskLevel.HIGH
    res3 = await tool.execute(p3, ctx)
    assert res3.success is True
    assert res3.output["overwritten"] is True
    assert (tmp_path / doc_path).read_text(encoding="utf-8") == "Overwritten text"


# ------------------------------------------------------------------------------
# 4. Secret Redaction Before Persistence Test
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_secret_redaction_before_persistence(tmp_path: Path) -> None:
    """Verifies that API keys or secrets in document body are redacted before saving to disk."""
    tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    ctx = ExecutionContext()

    leaked_content = (
        "# Configuration\n\n"
        "API Key: sk-live123456789012345678901234567890\n"
        "Auth Token: ghp_123456789012345678901234567890123456\n"
    )

    params = DocumentActionParameters(file_path="secrets.md", content=leaked_content)
    result = await tool.execute(params, ctx)
    assert result.success is True

    disk_content = (tmp_path / "secrets.md").read_text(encoding="utf-8")
    assert "sk-live123456789012345678901234567890" not in disk_content
    assert "[REDACTED API KEY]" in disk_content
    assert "ghp_123456789012345678901234567890123456" not in disk_content
    assert "[REDACTED GITHUB TOKEN]" in disk_content


# ------------------------------------------------------------------------------
# 5. Independent Verification Failure Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verification_detects_tampered_or_missing_file(tmp_path: Path) -> None:
    """Verifies that tool.verify() catches deleted, tampered, or empty files on disk."""
    tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    ctx = ExecutionContext()

    params = DocumentActionParameters(file_path="verified.md", content="Original content")
    result = await tool.execute(params, ctx)
    assert result.success is True

    # 1. Clean verify passes
    rep1 = await tool.verify(params, result, ctx)
    assert rep1.verified is True

    # 2. Tampered content on disk fails verify
    target = tmp_path / "verified.md"
    target.write_text("Tampered adversarial text", encoding="utf-8")
    rep2 = await tool.verify(params, result, ctx)
    assert rep2.verified is False
    assert "does not match expected" in rep2.discrepancies[0]

    # 3. Empty file fails verify
    target.write_text("", encoding="utf-8")
    rep3 = await tool.verify(params, result, ctx)
    assert rep3.verified is False
    assert "empty" in rep3.discrepancies[0]

    # 4. Deleted file fails verify
    target.unlink()
    rep4 = await tool.verify(params, result, ctx)
    assert rep4.verified is False
    assert "does not exist on disk" in rep4.discrepancies[0]
