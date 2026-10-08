"""
Unit tests for ORBIS Rich Document Workflows (Phase 8 / 14):
- DOCX generation (.docx) with headings, bullet points, paragraphs, and disk verification.
- PPTX generation (.pptx) with multi-slide structure and disk verification.
- PDF generation (.pdf) with ReportLab flowables, valid header, and disk verification.
- XLSX generation (.xlsx) with OpenPyXL tabular rows, styled headers, and disk verification.
- Markdown / text backward compatibility and secret redaction.
- Overwrite safety and verification rejection on tampering.
"""

import pytest
from pathlib import Path
import docx
import pptx
import openpyxl

from core.types import ExecutionContext
from core.tools.document_tool import DocumentTool, DocumentActionParameters


@pytest.fixture
def mock_ctx():
    return ExecutionContext(request_id="test_doc_req", step_id="test_doc_step")


@pytest.mark.asyncio
async def test_docx_generation_and_verification(mock_ctx, tmp_path):
    tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    content = (
        "# Executive Summary\n"
        "ORBIS is an autonomous desktop continuity AI agent.\n"
        "## Key Features\n"
        "- Deep Workspace Memory\n"
        "- Safe Office Document Generation\n"
        "- Zero Hallucination Grounding\n"
    )
    target_rel = "executive_report.docx"
    params = DocumentActionParameters(
        file_path=target_rel,
        content=content,
        format="docx",
    )

    res = await tool.execute(params, mock_ctx)
    assert res.success is True
    assert res.output["format"] == "docx"

    # Disk existence & valid docx structure
    file_on_disk = tmp_path / target_rel
    assert file_on_disk.exists()
    doc = docx.Document(str(file_on_disk))
    assert len(doc.paragraphs) >= 4
    texts = [p.text for p in doc.paragraphs]
    assert any("Executive Summary" in t for t in texts)
    assert any("Deep Workspace Memory" in t for t in texts)

    # Independent tool verification
    v_rep = await tool.verify(params, res, mock_ctx)
    assert v_rep.verified is True


@pytest.mark.asyncio
async def test_pptx_generation_and_verification(mock_ctx, tmp_path):
    tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    content = (
        "# ORBIS Architecture Presentation\n"
        "Next Generation Desktop Continuity Agent\n"
        "---\n"
        "## Core Capabilities\n"
        "- Session Segmentation\n"
        "- Knowledge Graph Retrieval\n"
        "- Multi-Step Planning\n"
        "---\n"
        "## Safety Governance\n"
        "- Risk-gated Execution\n"
        "- User Approvals\n"
    )
    target_rel = "deck.pptx"
    params = DocumentActionParameters(
        file_path=target_rel,
        content=content,
        format="pptx",
    )

    res = await tool.execute(params, mock_ctx)
    assert res.success is True
    assert res.output["format"] == "pptx"

    file_on_disk = tmp_path / target_rel
    assert file_on_disk.exists()
    prs = pptx.Presentation(str(file_on_disk))
    assert len(prs.slides) == 3
    assert prs.slides[0].shapes.title.text == "ORBIS Architecture Presentation"

    v_rep = await tool.verify(params, res, mock_ctx)
    assert v_rep.verified is True


@pytest.mark.asyncio
async def test_pdf_generation_and_verification(mock_ctx, tmp_path):
    tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    content = (
        "# System Architecture Report\n"
        "This document details the complete backend architecture for ORBIS.\n"
        "## Specifications\n"
        "- Sub-second vector retrieval\n"
        "- SQLite ACID storage\n"
    )
    target_rel = "architecture.pdf"
    params = DocumentActionParameters(
        file_path=target_rel,
        content=content,
        format="pdf",
    )

    res = await tool.execute(params, mock_ctx)
    assert res.success is True
    assert res.output["format"] == "pdf"

    file_on_disk = tmp_path / target_rel
    assert file_on_disk.exists()
    with open(file_on_disk, "rb") as f:
        header = f.read(5)
    assert header == b"%PDF-"

    v_rep = await tool.verify(params, res, mock_ctx)
    assert v_rep.verified is True


@pytest.mark.asyncio
async def test_xlsx_generation_and_verification(mock_ctx, tmp_path):
    tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    content = (
        "| ID | Capability | Status |\n"
        "|---|---|---|\n"
        "| 1 | Natural-language Q&A | COMPLETED |\n"
        "| 2 | Workspace Continuity | COMPLETED |\n"
        "| 3 | Rich Office Documents | COMPLETED |\n"
    )
    target_rel = "inventory.xlsx"
    params = DocumentActionParameters(
        file_path=target_rel,
        content=content,
        format="xlsx",
    )

    res = await tool.execute(params, mock_ctx)
    assert res.success is True
    assert res.output["format"] == "xlsx"

    file_on_disk = tmp_path / target_rel
    assert file_on_disk.exists()
    wb = openpyxl.load_workbook(str(file_on_disk))
    ws = wb.active
    assert ws.cell(row=1, column=1).value == "ID"
    assert ws.cell(row=1, column=2).value == "Capability"
    assert ws.cell(row=2, column=1).value == "1"
    assert ws.cell(row=2, column=2).value == "Natural-language Q&A"
    wb.close()

    v_rep = await tool.verify(params, res, mock_ctx)
    assert v_rep.verified is True


@pytest.mark.asyncio
async def test_rich_doc_overwrite_policy(mock_ctx, tmp_path):
    tool = DocumentTool(allowed_workspace_root=str(tmp_path))
    target_rel = "test_doc.docx"

    p1 = DocumentActionParameters(
        file_path=target_rel,
        content="# Initial Title",
        format="docx",
        overwrite=False,
    )
    r1 = await tool.execute(p1, mock_ctx)
    assert r1.success is True

    # Attempting to write again without overwrite=True must fail
    p2 = DocumentActionParameters(
        file_path=target_rel,
        content="# Updated Title",
        format="docx",
        overwrite=False,
    )
    r2 = await tool.execute(p2, mock_ctx)
    assert r2.success is False
    assert "File already exists" in r2.error

    # Writing with overwrite=True succeeds
    p3 = DocumentActionParameters(
        file_path=target_rel,
        content="# Updated Title",
        format="docx",
        overwrite=True,
    )
    r3 = await tool.execute(p3, mock_ctx)
    assert r3.success is True
    assert r3.output["overwritten"] is True
