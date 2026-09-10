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


@pytest.mark.asyncio
async def test_mock_llm_format_parameter() -> None:
    """Verifies that format parameter is accepted and recorded by MockLLMProvider."""
    llm = MockLLMProvider(default_text_response='{"status": "ok"}')

    res = await llm.generate("Prompt", system_prompt="System", format="json")
    assert res == '{"status": "ok"}'
    assert len(llm.call_history) == 1
    assert llm.call_history[0]["format"] == "json"


@pytest.mark.asyncio
async def test_ollama_provider_payload_construction(monkeypatch) -> None:
    """Verifies OllamaProvider payload generation with and without format parameter."""
    from unittest.mock import AsyncMock, MagicMock
    import httpx
    from core.agent.llm.ollama import OllamaProvider

    captured_payloads = []

    class MockResponse:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"response": '{"tool": "echo", "parameters": {}}'}

    async def mock_post(url, json=None):
        captured_payloads.append(json)
        return MockResponse()

    mock_client = AsyncMock()
    mock_client.post = mock_post
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: mock_client)

    provider = OllamaProvider(model="test_model")

    # 1. Normal text generation without format
    await provider.generate("Hello world", system_prompt="Be helpful")
    assert len(captured_payloads) == 1
    assert captured_payloads[0]["prompt"] == "Hello world"
    assert captured_payloads[0]["system"] == "Be helpful"
    assert "format" not in captured_payloads[0]

    # 2. Structured JSON generation with format="json"
    await provider.generate("Return json", system_prompt="JSON only", format="json")
    assert len(captured_payloads) == 2
    assert captured_payloads[1]["format"] == "json"

    # 3. select_tool preserves its existing execution path
    tool_call = await provider.select_tool("Echo hello", [EchoTool()])
    assert tool_call is not None
    assert tool_call.tool_name == "echo"