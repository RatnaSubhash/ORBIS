"""
Unit tests for LLM abstraction and MockLLMProvider.
"""

import pytest

from core.agent.llm.mock import MockLLMProvider
from core.tools.echo_tool import EchoTool
from core.types import ToolCall


@pytest.mark.asyncio
async def test_mock_llm_default_behavior() -> None:
    """Verifies default text generation and tool selection."""
    mock_call = ToolCall(tool_name="echo", parameters={"message": "test"})
    llm = MockLLMProvider(
        default_tool_call=mock_call,
        default_text_response="Default mock answer",
    )

    text = await llm.generate("Hello?")
    assert text == "Default mock answer"

    selected = await llm.select_tool("Echo test", [EchoTool()])
    assert selected is not None
    assert selected.tool_name == "echo"
    assert selected.parameters == {"message": "test"}

    assert len(llm.call_history) == 2


@pytest.mark.asyncio
async def test_mock_llm_request_mapping() -> None:
    """Verifies that mapped requests return specific tool calls."""
    call_a = ToolCall(tool_name="echo", parameters={"message": "A"})
    call_b = ToolCall(tool_name="echo", parameters={"message": "B"})

    llm = MockLLMProvider(
        default_tool_call=None,
        tool_call_map={
            "Request A": call_a,
            "Request B": call_b,
        },
    )

    res_a = await llm.select_tool("Request A", [EchoTool()])
    assert res_a == call_a

    res_b = await llm.select_tool("Request B", [EchoTool()])
    assert res_b == call_b

    res_unknown = await llm.select_tool("Unknown", [EchoTool()])
    assert res_unknown is None


@pytest.mark.asyncio
async def test_mock_llm_simulated_failure() -> None:
    """Verifies that should_fail=True raises simulated exceptions."""
    llm = MockLLMProvider(should_fail=True, failure_error_message="Connection lost")

    with pytest.raises(RuntimeError) as exc_info:
        await llm.generate("Test prompt")
    assert "Connection lost" in str(exc_info.value)

    with pytest.raises(RuntimeError) as exc_info:
        await llm.select_tool("Test request", [EchoTool()])
    assert "Connection lost" in str(exc_info.value)
