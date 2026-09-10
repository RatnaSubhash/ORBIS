"""
Search Provider Abstraction for ORBIS Phase 7 Web Research.
Defines BaseSearchProvider, SearchResult, MockSearchProvider (offline deterministic),
and ConfigurableSearchProvider (extensible provider with graceful mock fallback).
"""

from abc import ABC, abstractmethod
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from pydantic import BaseModel, Field

from core.config import get_settings

logger = logging.getLogger("orbis.tools.search")


class SearchResult(BaseModel):
    """Structured representation of a single search engine result."""
    title: str = Field(..., min_length=1, description="Webpage or article title")
    url: str = Field(..., min_length=1, description="Target webpage URL")
    snippet: str = Field(default="", description="Summary snippet provided by the search engine")
    source_domain: str = Field(default="", description="Derived domain name of the result")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional provider metadata")

    def model_post_init(self, __context: Any) -> None:
        if not self.source_domain and self.url:
            parsed = urlparse(self.url)
            self.source_domain = parsed.netloc.lower()


class BaseSearchProvider(ABC):
    """Abstract contract for web search providers."""

    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        """
        Executes a web search for the given query.
        Returns a bounded list of SearchResult models.
        """
        pass


class MockSearchProvider(BaseSearchProvider):
    """
    Deterministic, offline search provider for testing and offline environments.
    Guarantees reproducible results without network requests or external credentials.
    """

    def __init__(self, predefined_results: Optional[Dict[str, List[SearchResult]]] = None) -> None:
        self._predefined = predefined_results or {}

    def set_mock_results(self, query: str, results: List[SearchResult]) -> None:
        """Configures specific mock results for a given query string."""
        self._predefined[query.lower().strip()] = results

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        """Returns deterministic search results based on query keywords or custom fixtures."""
        clean_q = query.strip()
        lower_q = clean_q.lower()

        # Check explicit fixture
        if lower_q in self._predefined:
            return self._predefined[lower_q][:max_results]

        # Deterministic generation for common topic keywords
        results: List[SearchResult] = []

        if "python" in lower_q:
            results.append(
                SearchResult(
                    title="Welcome to Python.org",
                    url="https://www.python.org",
                    snippet="The official home of the Python Programming Language, documentation, and releases.",
                )
            )
            results.append(
                SearchResult(
                    title="Python Documentation",
                    url="https://docs.python.org/3/",
                    snippet="Official Python 3 documentation, tutorials, and standard library references.",
                )
            )

        if "orbis" in lower_q or "continuity" in lower_q:
            results.append(
                SearchResult(
                    title="ORBIS — Workspace Continuity & Desktop Agent Architecture",
                    url="https://github.com/orbis/desktop-agent",
                    snippet="An intelligent context-aware desktop agent for workspace continuity and computer assistance.",
                )
            )
            results.append(
                SearchResult(
                    title="Context DNA and Workspace Session Segmentation Research",
                    url="https://research.orbis.local/continuity-spec",
                    snippet="Deterministic state tracking, sessionization, and knowledge graph models for continuity.",
                )
            )

        if "pytest" in lower_q or "test" in lower_q:
            results.append(
                SearchResult(
                    title="pytest: helps you write better programs",
                    url="https://docs.pytest.org/en/stable/",
                    snippet="The pytest framework makes it easy to write small, readable tests, and can scale to support complex functional testing.",
                )
            )

        # Default synthetic results if no specific keyword matched
        if not results:
            results.append(
                SearchResult(
                    title=f"Search Results for: {clean_q}",
                    url=f"https://search.example.org/search?q={clean_q.replace(' ', '+')}",
                    snippet=f"Overview and documentation regarding '{clean_q}' and related technical topics.",
                )
            )
            results.append(
                SearchResult(
                    title=f"Technical Guide: Understanding {clean_q}",
                    url=f"https://docs.example.org/guides/{clean_q.lower().replace(' ', '-')}",
                    snippet=f"In-depth reference manual and practical instructions for working with {clean_q}.",
                )
            )

        return results[:max_results]


class ConfigurableSearchProvider(BaseSearchProvider):
    """
    Production-ready search provider supporting external search endpoints via HTTP.
    Gracefully falls back to MockSearchProvider when no API key or endpoint is configured.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        timeout: float = 5.0,
        fallback_mock: Optional[MockSearchProvider] = None,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.search_api_key
        self.endpoint = endpoint or settings.search_endpoint
        self.timeout = timeout or settings.web_request_timeout
        self.mock_provider = fallback_mock or MockSearchProvider()

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        """
        Queries an external search endpoint if configured; otherwise uses mock provider.
        Never blocks or crashes on missing credentials.
        """
        if not self.endpoint or not self.api_key:
            logger.debug("No external search credentials configured. Using deterministic mock search.")
            return await self.mock_provider.search(query, max_results=max_results)

        # Real HTTP search query when credentials are provided
        import httpx

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "ORBIS-Agent/1.0 (+https://github.com/orbis/desktop-agent)",
            "Accept": "application/json",
        }
        params = {"q": query, "count": max_results}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(self.endpoint, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()

                results: List[SearchResult] = []
                # Map standard JSON search API response
                items = data.get("results") or data.get("items") or []
                for item in items[:max_results]:
                    results.append(
                        SearchResult(
                            title=item.get("title", "Untitled"),
                            url=item.get("url", ""),
                            snippet=item.get("snippet", item.get("description", "")),
                            metadata={"provider": "external_api"},
                        )
                    )
                return results

        except Exception as e:
            logger.warning("External search API request failed: %s. Falling back to mock results.", e)
            return await self.mock_provider.search(query, max_results=max_results)
