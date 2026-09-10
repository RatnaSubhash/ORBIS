"""
Unit Tests for Content Generators and ContentGeneratorTool (ORBIS Phase 8).
Validates MockContentGenerator, LLMContentGenerator, ContentGrounding models,
parameter validation, length bounding, and independent step verification.
"""

import pytest
from pydantic import ValidationError

from core.agent.llm.mock import MockLLMProvider
from core.content.base import ContentGrounding, GeneratedContent
from core.content.llm import LLMContentGenerator
from core.content.mock import MockContentGenerator
from core.tools.content_tool import ContentGeneratorParameters, ContentGeneratorTool
from core.types import ExecutionContext, RiskLevel, ToolResult


# ------------------------------------------------------------------------------
# 1. MockContentGenerator Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mock_content_generator_markdown_report() -> None:
    """Verifies that MockContentGenerator synthesizes structured markdown reports."""
    generator = MockContentGenerator()
    result = await generator.generate_content(
        topic="Operating System Kernels",
        content_type="report",
        format="markdown",
    )

    assert isinstance(result, GeneratedContent)
    assert "Operating System Kernels" in result.title
    assert "# " in result.content
    assert "## 1. Executive Summary" in result.content
    assert result.format == "markdown"
    assert result.char_count == len(result.content)
    assert result.line_count > 5
    assert result.grounding.user_topic == "Operating System Kernels"
    assert result.grounding.is_generated is True
    assert result.grounding.has_context is False


@pytest.mark.asyncio
async def test_mock_content_generator_plain_text() -> None:
    """Verifies generating plain text formatted documents."""
    generator = MockContentGenerator()
    result = await generator.generate_content(
        topic="Network Protocols",
        content_type="document",
        format="text",
    )

    assert result.format == "text"
    assert "===" in result.content
    assert "1. OVERVIEW" in result.content
    assert "END OF DOCUMENT" in result.content


@pytest.mark.asyncio
async def test_mock_content_generator_readme_with_context() -> None:
    """Verifies generating a README document incorporating provided context."""
    generator = MockContentGenerator()
    context = "ORBIS is a desktop continuity agent tracking active applications."
    sources = ["https://github.com/orbis/desktop-agent"]

    result = await generator.generate_content(
        topic="ORBIS Agent",
        content_type="readme",
        format="markdown",
        context_data=context,
        source_references=sources,
    )

    assert "# Project README — Orbis Agent" in result.title or "README" in result.title
    assert "desktop continuity agent" in result.content
    assert "https://github.com/orbis/desktop-agent" in result.content
    assert result.grounding.has_context is True
    assert result.grounding.sources_used == sources


@pytest.mark.asyncio
async def test_mock_content_generator_length_bounding() -> None:
    """Verifies that content is truncated cleanly when exceeding max_length_chars."""
    generator = MockContentGenerator()
    result = await generator.generate_content(
        topic="Very Long Topic Discussion",
        content_type="report",
        max_length_chars=200,
    )

    assert len(result.content) <= 260  # includes truncation notice
    assert "truncated at maximum length bound" in result.content


@pytest.mark.asyncio
async def test_mock_content_generator_failure_simulation() -> None:
    """Verifies that generator failure raises an exception as configured."""
    generator = MockContentGenerator(should_fail=True, failure_message="Simulated synthesis fault")
    with pytest.raises(RuntimeError, match="Simulated synthesis fault"):
        await generator.generate_content(topic="Any Topic")


# ------------------------------------------------------------------------------
# 2. LLMContentGenerator Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_content_generator_with_mock_provider() -> None:
    """Verifies LLMContentGenerator execution through a MockLLMProvider backend."""
    mock_llm = MockLLMProvider(
        default_text_response="# Python Async Guide\n\nAsynchronous programming in Python."
    )
    generator = LLMContentGenerator(llm_provider=mock_llm)

    result = await generator.generate_content(
        topic="Python Async",
        content_type="report",
        format="markdown",
        context_data="asyncio is part of standard library.",
    )

    assert result.title == "Python Async Guide"
    assert "Asynchronous programming in Python." in result.content
    assert result.grounding.has_context is True

    # Confirm LLM prompt contained safety framing
    assert len(mock_llm.call_history) == 1
    prompt_record = mock_llm.call_history[0]["prompt"]
    assert "PASSIVE REFERENCE MATERIAL" in prompt_record or "Python Async" in prompt_record


# ------------------------------------------------------------------------------
# 3. ContentGeneratorTool Tool Contract & Verification Tests
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_content_tool_execution_success() -> None:
    """Verifies ContentGeneratorTool executes successfully and returns structured output."""
    generator = MockContentGenerator()
    tool = ContentGeneratorTool(generator=generator)
    ctx = ExecutionContext()

    params = ContentGeneratorParameters(
        topic="Distributed Consensus",
        content_type="report",
        format="markdown",
    )
    result = await tool.execute(params, ctx)

    assert result.success is True
    assert isinstance(result.output, dict)
    assert result.output["topic"] == "Distributed Consensus"
    assert result.output["format"] == "markdown"
    assert "Executive Summary" in result.output["content"]
    assert result.output["grounding"]["is_generated"] is True

    # Tool verification contract
    ver_report = await tool.verify(params, result, ctx)
    assert ver_report.verified is True
    assert "Content generated successfully" in ver_report.details


@pytest.mark.asyncio
async def test_content_tool_parameter_validation() -> None:
    """Verifies parameter validation rejects empty topics."""
    with pytest.raises(ValidationError):
        ContentGeneratorParameters(topic="")


@pytest.mark.asyncio
async def test_content_tool_verification_failures() -> None:
    """Verifies tool.verify() catches empty content or mismatched format."""
    tool = ContentGeneratorTool()
    ctx = ExecutionContext()
    params = ContentGeneratorParameters(topic="Test", format="markdown")

    # Failed result
    bad_res1 = ToolResult(success=False, error="Failed step")
    rep1 = await tool.verify(params, bad_res1, ctx)
    assert rep1.verified is False

    # Empty content
    bad_res2 = ToolResult(success=True, output={"content": "  ", "format": "markdown", "grounding": {}})
    rep2 = await tool.verify(params, bad_res2, ctx)
    assert rep2.verified is False
    assert "Generated content is empty" in rep2.discrepancies[0]

    # Format mismatch
    bad_res3 = ToolResult(success=True, output={"content": "Some text", "format": "text", "grounding": {}})
    rep3 = await tool.verify(params, bad_res3, ctx)
    assert rep3.verified is False
    assert "does not match expected" in rep3.discrepancies[0]
