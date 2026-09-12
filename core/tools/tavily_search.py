"""
Tavily Search Provider for ORBIS Phase 7 Web Research.
Directly interfaces with Tavily's Search HTTP API via httpx,
enforcing secret protection, bounded payloads, and strict error handling without silent fallbacks.
"""

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from core.config import get_settings
from core.tools.search_provider import BaseSearchProvider, SearchResult

logger = logging.getLogger("orbis.tools.tavily")


class TavilySearchProvider(BaseSearchProvider):
    """
    Search provider interfacing with Tavily Search API (https://api.tavily.com/search).
    Enforces secret protection, bounded response limits, and strict status code validation.
    Never silently falls back to MockSearchProvider on errors.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        timeout: Optional[float] = None,
        max_response_bytes: Optional[int] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        settings = get_settings()
        raw_key = api_key or settings.tavily_api_key
        if not raw_key:
            raise RuntimeError(
                "TAVILY_API_KEY is not configured for TavilySearchProvider. "
                "Please set the TAVILY_API_KEY environment variable or configure SEARCH_PROVIDER=mock."
            )

        self._api_key = raw_key
        self.endpoint = endpoint or settings.tavily_endpoint
        self.timeout = timeout or settings.web_request_timeout
        self.max_response_bytes = max_response_bytes or settings.web_max_page_size_bytes
        self.http_client = http_client

    def __repr__(self) -> str:
        return f"<TavilySearchProvider(endpoint='{self.endpoint}', api_key='***')>"

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        """
        Executes an asynchronous search query against Tavily HTTP API.
        Returns a bounded list of validated SearchResult models.
        """
        clean_query = query.strip()
        if not clean_query:
            return []

        bounded_max = min(max(1, max_results), 10)

        payload = {
            "api_key": self._api_key,
            "query": clean_query,
            "max_results": bounded_max,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ORBIS-Agent/1.0 (+https://github.com/orbis/desktop-agent)",
        }

        # Use injected http_client if available; otherwise instantiate a controlled AsyncClient
        if self.http_client is not None:
            response = await self._send_request(self.http_client, payload, headers)
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await self._send_request(client, payload, headers)

        return self._parse_response(response, bounded_max)

    async def _send_request(
        self, client: httpx.AsyncClient, payload: Dict[str, Any], headers: Dict[str, str]
    ) -> httpx.Response:
        """Sends the HTTP request with strict error translation and secret masking."""
        try:
            response = await client.post(
                self.endpoint,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except httpx.TimeoutException as exc:
            logger.error("Tavily search request timed out after %ss", self.timeout)
            raise RuntimeError(f"Tavily search request timed out after {self.timeout}s.") from exc
        except httpx.NetworkError as exc:
            logger.error("Tavily search network error: %s", exc)
            raise RuntimeError("Tavily search network connection error.") from exc
        except Exception as exc:
            logger.error("Tavily search unexpected communication error: %s", exc)
            raise RuntimeError(f"Tavily search request failed: {exc}") from exc

        if response.status_code in (401, 403):
            logger.error("Tavily authentication failed (HTTP %s)", response.status_code)
            raise RuntimeError(
                f"Tavily authentication failed (HTTP {response.status_code}): Invalid or unauthorized API key."
            )

        if response.status_code == 429:
            logger.error("Tavily rate limit or credit quota exceeded (HTTP 429)")
            raise RuntimeError(
                "Tavily quota or rate limit exceeded (HTTP 429). Please check your plan or monthly credit usage."
            )

        if response.status_code >= 500:
            logger.error("Tavily server error (HTTP %s)", response.status_code)
            raise RuntimeError(
                f"Tavily search service error (HTTP {response.status_code}). Please try again later."
            )

        if response.status_code != 200:
            logger.error("Tavily request failed with unexpected HTTP %s", response.status_code)
            raise RuntimeError(
                f"Tavily search failed with HTTP status {response.status_code}."
            )

        return response

    def _parse_response(self, response: httpx.Response, bounded_max: int) -> List[SearchResult]:
        """Validates payload boundaries, JSON structure, and maps items to SearchResult."""
        content_bytes = response.content
        if len(content_bytes) > self.max_response_bytes:
            raise RuntimeError(
                f"Tavily response exceeded bounded size limit ({len(content_bytes)} > {self.max_response_bytes} bytes)."
            )

        try:
            data = response.json()
        except Exception as exc:
            raise RuntimeError("Tavily search returned malformed JSON.") from exc

        if not isinstance(data, dict):
            raise RuntimeError("Tavily search returned unexpected non-dict JSON structure.")

        raw_results = data.get("results")
        if raw_results is None or not isinstance(raw_results, list):
            raise RuntimeError("Tavily response missing 'results' list.")

        results: List[SearchResult] = []
        seen_urls = set()

        for item in raw_results:
            if not isinstance(item, dict):
                continue

            raw_url = item.get("url")
            if not raw_url or not isinstance(raw_url, str):
                continue

            cleaned_url = raw_url.strip()
            parsed = urlparse(cleaned_url)

            # Security: enforce HTTP/HTTPS only
            if parsed.scheme.lower() not in ("http", "https"):
                continue

            # Skip localhost or link-local targets
            hostname = parsed.hostname or ""
            if hostname.lower() in ("localhost", "127.0.0.1", "::1", "169.254.169.254"):
                continue

            if cleaned_url in seen_urls:
                continue

            title = (item.get("title") or "").strip() or "Web Search Result"
            snippet = (item.get("content") or "").strip()
            score = item.get("score")

            metadata: Dict[str, Any] = {
                "provider": "tavily",
            }
            if score is not None:
                metadata["score"] = score

            results.append(
                SearchResult(
                    title=title,
                    url=cleaned_url,
                    snippet=snippet,
                    source_domain=parsed.netloc.lower(),
                    metadata=metadata,
                )
            )
            seen_urls.add(cleaned_url)

        return results[:bounded_max]
