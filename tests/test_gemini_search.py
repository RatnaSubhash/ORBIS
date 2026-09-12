"""
Unit and Integration Tests for Gemini Search Provider (ORBIS Phase 7).
Validates Google Search tool interactions, step citation extraction into SearchResult models,
factory resolution, and guarantees against silent mock fallbacks.
"""

import os
from unittest.mock import AsyncMock, MagicMock
import pytest

from core.tools.gemini_search import GeminiSearchProvider
from core.tools.registry import create_default_tool_registry
from core.tools.search_provider import (
    BaseSearchProvider,
    ConfigurableSearchProvider,
    MockSearchProvider,
    SearchResult,
    get_search_provider,
)
from core.tools.web_research_tool import WebResearchTool


class MockCitation:
    def __init__(self, url: str, title: str, start_index: int = 0, end_index: int = 20, type: str = "url_citation"):
        self.url = url
        self.title = title
        self.start_index = start_index
        self.end_index = end_index
        self.type = type


class MockTextContent:
    def __init__(self, text: str, annotations: list = None):
        self.type = "text"
        self.text = text
        self.annotations = annotations or []


class MockStep:
    def __init__(self, step_type: str = "model_output", content: list = None):
        self.type = step_type
        self.content = content or []


class MockSearchInteraction:
    def __init__(self, steps: list = None):
        self.steps = steps or []


@pytest.fixture
def mock_gemini_search_client():
    client = MagicMock()
    client.aio = MagicMock()
    client.aio.interactions = MagicMock()
    client.aio.interactions.create = AsyncMock()
    return client


# ============================================================================
# Unit Tests (Offline / Mocked)
# ============================================================================

@pytest.mark.asyncio
async def test_gemini_search_citations_extraction(mock_gemini_search_client):
    """
    Verifies that GeminiSearchProvider extracts URLCitation annotations from interaction
    steps into ORBIS SearchResult models with preserved titles, URLs, snippets, and domains.
    """
    text = "Python 3.12 is the latest stable release of the language."
    citation1 = MockCitation(
        url="https://www.python.org/downloads/",
        title="Python Releases",
        start_index=0,
        end_index=25,
    )
    citation2 = MockCitation(
        url="https://docs.python.org/3/",
        title="Python Documentation",
        start_index=26,
        end_index=56,
    )

    text_item = MockTextContent(text=text, annotations=[citation1, citation2])
    output_step = MockStep(step_type="model_output", content=[text_item])

    mock_gemini_search_client.aio.interactions.create.return_value = MockSearchInteraction(
        steps=[output_step]
    )

    provider = GeminiSearchProvider(
        api_key="fake_key",
        model="gemini-test-search",
        client=mock_gemini_search_client,
    )

    results = await provider.search("latest python", max_results=5)

    assert len(results) == 2
    res1, res2 = results[0], results[1]

    # Verify Result 1
    assert isinstance(res1, SearchResult)
    assert res1.url == "https://www.python.org/downloads/"
    assert res1.title == "Python Releases"
    assert res1.source_domain == "www.python.org"
    assert res1.snippet == "Python 3.12 is the latest"
    assert res1.metadata["provider"] == "gemini_google_search"
    assert res1.metadata["start_index"] == 0
    assert res1.metadata["end_index"] == 25

    # Verify Result 2
    assert res2.url == "https://docs.python.org/3/"
    assert res2.title == "Python Documentation"
    assert res2.source_domain == "docs.python.org"
    assert res2.snippet == "stable release of the language"

    # Verify Google Search tool was requested
    call_kwargs = mock_gemini_search_client.aio.interactions.create.await_args.kwargs
    assert call_kwargs["tools"] == [{"type": "google_search"}]
    assert "Search the web for: latest python" in call_kwargs["input"]


@pytest.mark.asyncio
async def test_gemini_search_deduplication(mock_gemini_search_client):
    """Verifies that duplicate URLs in citation annotations are deduplicated."""
    text = "Citation text."
    citation1 = MockCitation(url="https://example.org/page", title="Page 1", start_index=0, end_index=5)
    citation2 = MockCitation(url="https://example.org/page", title="Page Duplicate", start_index=6, end_index=10)

    text_item = MockTextContent(text=text, annotations=[citation1, citation2])
    step = MockStep(content=[text_item])
    mock_gemini_search_client.aio.interactions.create.return_value = MockSearchInteraction(steps=[step])

    provider = GeminiSearchProvider(api_key="fake_key", client=mock_gemini_search_client)
    results = await provider.search("test query")

    assert len(results) == 1
    assert results[0].url == "https://example.org/page"


def test_gemini_search_missing_key_raises(monkeypatch):
    """
    CRITICAL: Verifies that missing GEMINI_API_KEY with GeminiSearchProvider
    raises RuntimeError and does NOT silently fall back to MockSearchProvider.
    """
    from core.config import Settings
    empty_settings = Settings(gemini_api_key=None)
    object.__setattr__(empty_settings, "gemini_api_key", None)
    monkeypatch.setattr("core.tools.gemini_search.get_settings", lambda **kwargs: empty_settings)

    with pytest.raises(RuntimeError) as exc_info:
        GeminiSearchProvider()
    assert "GEMINI_API_KEY is not configured" in str(exc_info.value)


@pytest.mark.asyncio
async def test_gemini_search_api_failure_raises(mock_gemini_search_client):
    """
    CRITICAL: Verifies that API failures raise RuntimeError rather than
    silently returning synthetic mock data.
    """
    mock_gemini_search_client.aio.interactions.create.side_effect = RuntimeError("API Quota Exceeded")

    provider = GeminiSearchProvider(api_key="fake_key", client=mock_gemini_search_client)
    with pytest.raises(RuntimeError) as exc_info:
        await provider.search("Python news")

    assert "Gemini Search request failed" in str(exc_info.value)


# ============================================================================
# Search Provider Factory Tests
# ============================================================================

def test_search_provider_factory_resolution(monkeypatch):
    """Verifies configuration-driven search provider resolution."""
    # 1. Mock
    mock_p = get_search_provider("mock")
    assert isinstance(mock_p, MockSearchProvider)

    # 2. Configurable
    conf_p = get_search_provider("configurable")
    assert isinstance(conf_p, ConfigurableSearchProvider)

    # 3. Gemini (with simulated key)
    monkeypatch.setenv("GEMINI_API_KEY", "fake_search_key")
    gem_p = get_search_provider("gemini")
    assert isinstance(gem_p, GeminiSearchProvider)

    # 4. Unsupported
    with pytest.raises(ValueError) as exc_info:
        get_search_provider("unsupported_search_engine")
    assert "Unsupported search provider" in str(exc_info.value)


def test_registry_integration_with_search_provider(monkeypatch):
    """Verifies that create_default_tool_registry() respects search provider configuration."""
    # With SEARCH_PROVIDER=mock
    monkeypatch.setenv("SEARCH_PROVIDER", "mock")
    reg_mock = create_default_tool_registry()
    tool_mock = reg_mock.get("web_research")
    assert isinstance(tool_mock, WebResearchTool)
    assert isinstance(tool_mock.search_provider, MockSearchProvider)

    # With SEARCH_PROVIDER=gemini
    monkeypatch.setenv("GEMINI_API_KEY", "fake_key")
    monkeypatch.setenv("SEARCH_PROVIDER", "gemini")
    from core.config import get_settings
    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None

    reg_gem = create_default_tool_registry()
    tool_gem = reg_gem.get("web_research")
    assert isinstance(tool_gem, WebResearchTool)
    assert isinstance(tool_gem.search_provider, GeminiSearchProvider)


# ============================================================================
# Live Integration Test (Conditional)
# ============================================================================

@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY environment variable not configured",
)
async def test_live_gemini_search():
    """Executes a live web search via Google Gemini Interactions if an API key is available."""
    provider = GeminiSearchProvider()
    results = await provider.search("Latest Python version release", max_results=3)
    assert isinstance(results, list)
    for r in results:
        assert isinstance(r, SearchResult)
        assert r.url.startswith("http")
        assert len(r.title) > 0
