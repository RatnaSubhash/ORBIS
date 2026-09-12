"""
Unit Tests for TavilySearchProvider (ORBIS Phase 7 Web Research).
Validates HTTP payload construction, response parsing, error mapping (401, 403, 429, 5xx),
timeout/network handling, secret protection, and provider factory resolution.
All unit tests use mock HTTP transports and NEVER consume real credits.
"""

import json
import pytest
import httpx

from core.tools.search_provider import (
    BaseSearchProvider,
    ConfigurableSearchProvider,
    MockSearchProvider,
    SearchResult,
    get_search_provider,
)
from core.tools.tavily_search import TavilySearchProvider
from core.tools.web_research_tool import WebResearchParameters, WebResearchTool
from core.types import ExecutionContext


def make_mock_client(handler) -> httpx.AsyncClient:
    """Helper to create an AsyncClient with a custom MockTransport."""
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


# ============================================================================
# 1. Successful Response Parsing Tests
# ============================================================================

@pytest.mark.asyncio
async def test_tavily_search_success():
    """Verifies successful Tavily JSON response mapping into SearchResult models."""
    sample_response = {
        "query": "python latest version",
        "results": [
            {
                "title": "Python Official Downloads",
                "url": "https://www.python.org/downloads/",
                "content": "Python 3.12 is the latest stable release.",
                "score": 0.98,
            },
            {
                "title": "Python 3 Documentation",
                "url": "https://docs.python.org/3/",
                "content": "Comprehensive official documentation for Python 3.",
                "score": 0.85,
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["query"] == "python latest version"
        assert body["api_key"] == "fake_test_key"
        assert body["max_results"] == 5
        return httpx.Response(200, json=sample_response)

    client = make_mock_client(handler)
    provider = TavilySearchProvider(api_key="fake_test_key", http_client=client)

    results = await provider.search("python latest version", max_results=5)

    assert len(results) == 2
    res1, res2 = results[0], results[1]

    assert isinstance(res1, SearchResult)
    assert res1.title == "Python Official Downloads"
    assert res1.url == "https://www.python.org/downloads/"
    assert res1.snippet == "Python 3.12 is the latest stable release."
    assert res1.source_domain == "www.python.org"
    assert res1.metadata["provider"] == "tavily"
    assert res1.metadata["score"] == 0.98

    assert res2.url == "https://docs.python.org/3/"
    assert res2.source_domain == "docs.python.org"


@pytest.mark.asyncio
async def test_tavily_search_empty_results():
    """Verifies that an empty results list from Tavily returns an empty list without error."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"query": "rare keyword", "results": []})

    client = make_mock_client(handler)
    provider = TavilySearchProvider(api_key="fake_test_key", http_client=client)

    results = await provider.search("rare keyword")
    assert results == []


@pytest.mark.asyncio
async def test_tavily_search_empty_query():
    """Verifies that empty/whitespace queries return immediately without making HTTP calls."""
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"results": []})

    client = make_mock_client(handler)
    provider = TavilySearchProvider(api_key="fake_test_key", http_client=client)

    results = await provider.search("   ")
    assert results == []
    assert not called


# ============================================================================
# 2. Error Handling & No Silent Fallback Tests
# ============================================================================

@pytest.mark.asyncio
async def test_tavily_auth_failure_401():
    """Verifies that HTTP 401 raises RuntimeError and does NOT fall back to mock."""
    secret_key = "secret_tvly_token_9999"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Unauthorized key"})

    client = make_mock_client(handler)
    provider = TavilySearchProvider(api_key=secret_key, http_client=client)

    with pytest.raises(RuntimeError) as exc_info:
        await provider.search("test")

    err_msg = str(exc_info.value)
    assert "Tavily authentication failed (HTTP 401)" in err_msg
    # Ensure key is NOT in error message
    assert secret_key not in err_msg


@pytest.mark.asyncio
async def test_tavily_auth_failure_403():
    """Verifies that HTTP 403 raises RuntimeError and does NOT fall back to mock."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "Forbidden"})

    client = make_mock_client(handler)
    provider = TavilySearchProvider(api_key="fake_key", http_client=client)

    with pytest.raises(RuntimeError) as exc_info:
        await provider.search("test")

    assert "Tavily authentication failed (HTTP 403)" in str(exc_info.value)


@pytest.mark.asyncio
async def test_tavily_quota_failure_429():
    """Verifies that HTTP 429 raises RuntimeError and does NOT fall back to mock."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "Quota limit reached"})

    client = make_mock_client(handler)
    provider = TavilySearchProvider(api_key="fake_key", http_client=client)

    with pytest.raises(RuntimeError) as exc_info:
        await provider.search("test")

    assert "quota or rate limit exceeded (HTTP 429)" in str(exc_info.value)


@pytest.mark.asyncio
async def test_tavily_server_error_500():
    """Verifies that HTTP 5xx raises RuntimeError and does NOT fall back to mock."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="Bad Gateway")

    client = make_mock_client(handler)
    provider = TavilySearchProvider(api_key="fake_key", http_client=client)

    with pytest.raises(RuntimeError) as exc_info:
        await provider.search("test")

    assert "Tavily search service error (HTTP 502)" in str(exc_info.value)


@pytest.mark.asyncio
async def test_tavily_timeout_failure():
    """Verifies that network timeout raises RuntimeError and does NOT fall back to mock."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Read timed out")

    client = make_mock_client(handler)
    provider = TavilySearchProvider(api_key="fake_key", timeout=2.0, http_client=client)

    with pytest.raises(RuntimeError) as exc_info:
        await provider.search("test")

    assert "timed out after 2.0s" in str(exc_info.value)


@pytest.mark.asyncio
async def test_tavily_network_error():
    """Verifies that connection error raises RuntimeError and does NOT fall back to mock."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.NetworkError("DNS resolution failed")

    client = make_mock_client(handler)
    provider = TavilySearchProvider(api_key="fake_key", http_client=client)

    with pytest.raises(RuntimeError) as exc_info:
        await provider.search("test")

    assert "Tavily search network connection error" in str(exc_info.value)


@pytest.mark.asyncio
async def test_tavily_malformed_json():
    """Verifies that invalid/malformed JSON raises a clean RuntimeError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>502 Bad Gateway</body></html>")

    client = make_mock_client(handler)
    provider = TavilySearchProvider(api_key="fake_key", http_client=client)

    with pytest.raises(RuntimeError) as exc_info:
        await provider.search("test")

    assert "malformed JSON" in str(exc_info.value)


@pytest.mark.asyncio
async def test_tavily_missing_results_key():
    """Verifies that valid JSON missing the 'results' list raises a clean RuntimeError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"query": "test", "other_field": 123})

    client = make_mock_client(handler)
    provider = TavilySearchProvider(api_key="fake_key", http_client=client)

    with pytest.raises(RuntimeError) as exc_info:
        await provider.search("test")

    assert "missing 'results' list" in str(exc_info.value)


# ============================================================================
# 3. Security & Safety Invariants
# ============================================================================

def test_tavily_missing_api_key_raises(monkeypatch):
    """Verifies that TavilySearchProvider raises if TAVILY_API_KEY is not configured."""
    from core.config import Settings
    empty_settings = Settings(tavily_api_key=None)
    object.__setattr__(empty_settings, "tavily_api_key", None)
    monkeypatch.setattr("core.tools.tavily_search.get_settings", lambda **kwargs: empty_settings)

    with pytest.raises(RuntimeError) as exc_info:
        TavilySearchProvider()

    assert "TAVILY_API_KEY is not configured" in str(exc_info.value)


def test_tavily_api_key_redacted_in_repr():
    """Verifies that API key is masked as '***' in __repr__."""
    provider = TavilySearchProvider(api_key="super_secret_tavily_key_xyz")
    repr_str = repr(provider)

    assert "super_secret_tavily_key_xyz" not in repr_str
    assert "***" in repr_str


@pytest.mark.asyncio
async def test_tavily_unsafe_url_filtering():
    """Verifies that non-http/https schemes and localhost links are safely ignored."""
    sample_response = {
        "results": [
            {"title": "File Link", "url": "file:///etc/passwd", "content": "secret"},
            {"title": "Local Link", "url": "http://localhost:8080/admin", "content": "admin"},
            {"title": "Loopback Link", "url": "http://127.0.0.1/status", "content": "status"},
            {"title": "Safe Link", "url": "https://python.org", "content": "safe content"},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=sample_response)

    client = make_mock_client(handler)
    provider = TavilySearchProvider(api_key="fake_key", http_client=client)

    results = await provider.search("test")

    assert len(results) == 1
    assert results[0].url == "https://python.org"


# ============================================================================
# 4. Provider Factory & WebResearchTool Integration
# ============================================================================

def test_search_provider_factory_tavily(monkeypatch):
    """Verifies that get_search_provider('tavily') returns TavilySearchProvider."""
    monkeypatch.setenv("TAVILY_API_KEY", "fake_factory_tavily_key")
    provider = get_search_provider("tavily")

    assert isinstance(provider, TavilySearchProvider)
    assert not isinstance(provider, MockSearchProvider)


def test_search_provider_factory_mock_remains_deterministic():
    """Verifies that MockSearchProvider remains unaffected and deterministic."""
    provider = get_search_provider("mock")
    assert isinstance(provider, MockSearchProvider)
    assert not isinstance(provider, TavilySearchProvider)


@pytest.mark.asyncio
async def test_web_research_tool_uses_tavily_provider():
    """Verifies that WebResearchTool seamlessly uses TavilySearchProvider."""
    sample_response = {
        "results": [
            {
                "title": "Python Docs",
                "url": "https://docs.python.org/3/",
                "content": "Official Python 3 Documentation",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=sample_response)

    client = make_mock_client(handler)
    tavily_provider = TavilySearchProvider(api_key="fake_key", http_client=client)

    # Inject mock HTML fetcher for the secondary page fetch step in WebResearchTool
    def mock_fetcher(url: str):
        return 200, "Python Docs", "<html><body><p>Clean Python documentation text.</p></body></html>"

    tool = WebResearchTool(search_provider=tavily_provider, mock_fetcher=mock_fetcher)
    ctx = ExecutionContext()

    res = await tool.execute(
        WebResearchParameters(action="research", query="Python latest"), ctx
    )

    assert res.success is True
    assert "Python documentation text" in res.output["summary"]
    assert res.output["is_untrusted_external_data"] is True
