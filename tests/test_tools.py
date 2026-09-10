"""
Unit tests for BaseTool, ToolRegistry, and EchoTool.
"""

import pytest
from pydantic import ValidationError

from core.tools.echo_tool import EchoParameters, EchoTool
from core.tools.registry import ToolRegistry
from core.types import ExecutionContext, RiskLevel, ToolResult


@pytest.mark.asyncio
async def test_echo_tool_parameter_validation_success() -> None:
    """Verifies valid parameters parse into EchoParameters."""
    tool = EchoTool()
    params = tool.validate_parameters({"message": "valid text"})
    assert isinstance(params, EchoParameters)
    assert params.message == "valid text"


@pytest.mark.asyncio
async def test_echo_tool_parameter_validation_failure() -> None:
    """Verifies invalid or empty parameters raise ValidationError."""
    tool = EchoTool()
    with pytest.raises(ValidationError):
        tool.validate_parameters({})

    with pytest.raises(ValidationError):
        tool.validate_parameters({"message": ""})


@pytest.mark.asyncio
async def test_echo_tool_execution_and_verification() -> None:
    """Verifies that EchoTool executes cleanly and passes verification."""
    tool = EchoTool()
    context = ExecutionContext(task_id="test_task_1")
    params = EchoParameters(message="hello world")

    result = await tool.execute(params, context)
    assert result.success is True
    assert result.output == "hello world"

    report = await tool.verify(params, result, context)
    assert report.verified is True
    assert len(report.discrepancies) == 0


@pytest.mark.asyncio
async def test_echo_tool_verification_failure_on_corrupted_output() -> None:
    """Verifies that verification fails if the output does not match input message."""
    tool = EchoTool()
    context = ExecutionContext(task_id="test_task_2")
    params = EchoParameters(message="original")

    corrupted_result = ToolResult(success=True, output="corrupted")
    report = await tool.verify(params, corrupted_result, context)
    assert report.verified is False
    assert len(report.discrepancies) == 1
    assert "Expected 'original'" in report.discrepancies[0]


@pytest.mark.asyncio
async def test_echo_tool_verification_failure_on_failed_result() -> None:
    """Verifies that verification fails if the tool result indicates success=False."""
    tool = EchoTool()
    context = ExecutionContext(task_id="test_task_3")
    params = EchoParameters(message="original")

    failed_result = ToolResult(success=False, error="Tool crashed")
    report = await tool.verify(params, failed_result, context)
    assert report.verified is False
    assert "Execution status success=False" in report.discrepancies[0]


def test_tool_registry_registration_and_lookup() -> None:
    """Verifies registering, listing, and retrieving tools."""
    registry = ToolRegistry()
    tool = EchoTool()

    assert len(registry.list_tools()) == 0
    registry.register(tool)
    assert len(registry.list_tools()) == 1

    retrieved = registry.get("echo")
    assert retrieved is tool

    retrieved_raise = registry.get_or_raise("echo")
    assert retrieved_raise is tool


def test_tool_registry_duplicate_rejection() -> None:
    """Verifies that registering a tool with an existing name raises ValueError."""
    registry = ToolRegistry()
    tool1 = EchoTool()
    tool2 = EchoTool()

    registry.register(tool1)
    with pytest.raises(ValueError) as exc_info:
        registry.register(tool2)
    assert "already registered" in str(exc_info.value)


def test_tool_registry_missing_lookup() -> None:
    """Verifies that looking up an unregistered tool behaves correctly."""
    registry = ToolRegistry()
    assert registry.get("nonexistent") is None

    with pytest.raises(KeyError):
        registry.get_or_raise("nonexistent")


def test_tool_registry_schema_export() -> None:
    """Verifies exporting tool schemas for LLM inspection."""
    registry = ToolRegistry()
    registry.register(EchoTool())

    schemas = registry.get_tool_schemas()
    assert len(schemas) == 1
    assert schemas[0]["name"] == "echo"
    assert schemas[0]["risk_level"] == RiskLevel.LOW.value
    assert "properties" in schemas[0]["parameters_schema"]
