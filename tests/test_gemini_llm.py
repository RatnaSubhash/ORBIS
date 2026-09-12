"""
Unit and Integration Tests for Gemini LLM Provider (ORBIS).
Validates Google Interactions API integration, non-blocking async execution,
key redaction, JSON formatting, and factory resolution without silent fallbacks.
"""

import os
from unittest.mock import AsyncMock, MagicMock
import pytest

from core.agent.llm.base import BaseLLMProvider
from core.agent.llm.factory import get_llm_provider
from core.agent.llm.gemini import GeminiProvider
from core.agent.llm.mock import MockLLMProvider
from core.agent.llm.ollama import OllamaProvider
from core.tools.echo_tool import EchoTool
from core.types import ToolCall


class MockInteraction:
    def __init__(self, output_text: str = "", steps: list = None):
        self.output_text = output_text
        self.steps = steps or []


@pytest.fixture
def mock_gemini_client():
    client = MagicMock()
    client.aio = MagicMock()
    client.aio.interactions = MagicMock()
    client.aio.interactions.create = AsyncMock()
    return client


# ============================================================================
# Unit Tests (Offline / Mocked)
# ============================================================================

@pytest.mark.asyncio
async def test_gemini_generate_text(mock_gemini_client):
    """Verifies that generate() calls aio.interactions.create and extracts output_text."""
    mock_gemini_client.aio.interactions.create.return_value = MockInteraction(
        output_text="Hello from Gemini Interactions API"
    )

    provider = GeminiProvider(api_key="fake_key", model="gemini-test", client=mock_gemini_client)
    res = await provider.generate("Hi Gemini")

    assert res == "Hello from Gemini Interactions API"
    mock_gemini_client.aio.interactions.create.assert_awaited_once_with(
        model="gemini-test",
        input="Hi Gemini",
    )


@pytest.mark.asyncio
async def test_gemini_generate_json_format(mock_gemini_client):
    """Verifies that format='json' sets response_mime_type='application/json'."""
    expected_json = '{"task": "completed", "status": "ok"}'
    mock_gemini_client.aio.interactions.create.return_value = MockInteraction(output_text=expected_json)

    provider = GeminiProvider(api_key="fake_key", client=mock_gemini_client)
    res = await provider.generate("Return JSON", format="json", system_prompt="Be concise")

    assert res == expected_json
    call_kwargs = mock_gemini_client.aio.interactions.create.await_args.kwargs
    assert call_kwargs["response_format"] == {"type": "text", "mime_type": "application/json"}
    assert call_kwargs["system_instruction"] == "Be concise"


@pytest.mark.asyncio
async def test_gemini_generate_steps_fallback(mock_gemini_client):
    """Verifies fallback text extraction when output_text is not directly populated on the interaction."""
    step = {
        "type": "model_output",
        "content": [
            {"type": "text", "text": "Part 1. "},
            {"type": "text", "text": "Part 2."},
        ],
    }
    mock_gemini_client.aio.interactions.create.return_value = MockInteraction(
        output_text="", steps=[step]
    )

    provider = GeminiProvider(api_key="fake_key", client=mock_gemini_client)
    res = await provider.generate("Test prompt")
    assert res == "Part 1. Part 2."


@pytest.mark.asyncio
async def test_gemini_select_tool_success(mock_gemini_client):
    """Verifies structured tool selection from Gemini JSON output."""
    json_response = '{"tool": "echo", "parameters": {"message": "Hello from tool"}}'
    mock_gemini_client.aio.interactions.create.return_value = MockInteraction(output_text=json_response)

    provider = GeminiProvider(api_key="fake_key", client=mock_gemini_client)
    tool_call = await provider.select_tool("Say hello", [EchoTool()])

    assert isinstance(tool_call, ToolCall)
    assert tool_call.tool_name == "echo"
    assert tool_call.parameters == {"message": "Hello from tool"}


@pytest.mark.asyncio
async def test_gemini_select_tool_markdown_fences(mock_gemini_client):
    """Verifies defensive stripping of markdown fences during tool selection."""
    json_response = '```json\n{"tool": "echo", "parameters": {"message": "Defensive parsing"}}\n```'
    mock_gemini_client.aio.interactions.create.return_value = MockInteraction(output_text=json_response)

    provider = GeminiProvider(api_key="fake_key", client=mock_gemini_client)
    tool_call = await provider.select_tool("Say hello", [EchoTool()])

    assert isinstance(tool_call, ToolCall)
    assert tool_call.tool_name == "echo"
    assert tool_call.parameters == {"message": "Defensive parsing"}


@pytest.mark.asyncio
async def test_gemini_select_tool_none(mock_gemini_client):
    """Verifies None is returned when model responds with tool: null."""
    json_response = '{"tool": null, "parameters": {}}'
    mock_gemini_client.aio.interactions.create.return_value = MockInteraction(output_text=json_response)

    provider = GeminiProvider(api_key="fake_key", client=mock_gemini_client)
    tool_call = await provider.select_tool("What is the meaning of life?", [EchoTool()])
    assert tool_call is None


def test_gemini_missing_api_key_raises(monkeypatch):
    """Verifies that missing GEMINI_API_KEY raises a clear RuntimeError, not a fallback."""
    from core.config import Settings
    empty_settings = Settings(gemini_api_key=None)
    object.__setattr__(empty_settings, "gemini_api_key", None)
    monkeypatch.setattr("core.agent.llm.gemini.get_settings", lambda **kwargs: empty_settings)

    with pytest.raises(RuntimeError) as exc_info:
        GeminiProvider()
    assert "GEMINI_API_KEY is not configured" in str(exc_info.value)


def test_gemini_api_key_redaction():
    """Verifies that the API key is redacted in __repr__ and not exposed."""
    provider = GeminiProvider(api_key="secret_test_key_12345", model="gemini-custom", client=MagicMock())
    repr_str = repr(provider)

    assert "secret_test_key_12345" not in repr_str
    assert "***" in repr_str
    assert "gemini-custom" in repr_str


@pytest.mark.asyncio
async def test_gemini_api_failure_raises(mock_gemini_client):
    """Verifies that Gemini API failures raise a RuntimeError and do NOT silently switch to Ollama."""
    mock_gemini_client.aio.interactions.create.side_effect = ConnectionError("Connection refused by API")

    provider = GeminiProvider(api_key="fake_key", client=mock_gemini_client)
    with pytest.raises(RuntimeError) as exc_info:
        await provider.generate("Hello")

    assert "Gemini generation request failed" in str(exc_info.value)


# ============================================================================
# Factory Resolution Tests
# ============================================================================

def test_llm_factory_resolution(monkeypatch):
    """Verifies configuration-driven factory resolution for ollama, mock, and gemini."""
    # 1. Ollama
    ollama_provider = get_llm_provider("ollama")
    assert isinstance(ollama_provider, OllamaProvider)

    # 2. Mock
    mock_provider = get_llm_provider("mock")
    assert isinstance(mock_provider, MockLLMProvider)

    # 3. Gemini (with simulated key)
    monkeypatch.setenv("GEMINI_API_KEY", "fake_factory_key")
    gemini_provider = get_llm_provider("gemini")
    assert isinstance(gemini_provider, GeminiProvider)

    # 4. Invalid provider
    with pytest.raises(ValueError) as exc_info:
        get_llm_provider("unsupported_cloud_provider")
    assert "Unsupported LLM provider" in str(exc_info.value)


# ============================================================================
# Live Integration Test (Conditional)
# ============================================================================

@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY environment variable not configured",
)
async def test_live_gemini_generate():
    """Executes a real non-destructive prompt against Google Gemini if an API key is present."""
    provider = GeminiProvider()
    response = await provider.generate("Respond with the exact word 'ORBIS_TEST_OK'.")
    assert "ORBIS_TEST_OK" in response.strip()
