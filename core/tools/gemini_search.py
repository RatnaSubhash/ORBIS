"""
Google Gemini Search Provider for ORBIS Phase 7 Web Research.
Uses the official Google Interactions API with google_search tool
and maps URL citations into standard SearchResult models without silent mock fallbacks.
"""

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from core.config import get_settings
from core.tools.search_provider import BaseSearchProvider, SearchResult

logger = logging.getLogger("orbis.tools.gemini_search")


class GeminiSearchProvider(BaseSearchProvider):
    """
    Search provider using Google Gemini's official Interactions API
    with the Google Search tool: tools=[{"type": "google_search"}].
    Extracts URL citation annotations from interaction steps into SearchResult models.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        client: Optional[Any] = None,
    ) -> None:
        settings = get_settings()
        self.model = model or settings.gemini_model

        if client is not None:
            self.client = client
            self._has_key = True
        else:
            try:
                from google import genai
            except ImportError as exc:
                raise ImportError(
                    "The 'google-genai' package is required to use GeminiSearchProvider. "
                    "Install it using 'pip install .[gemini]' or 'pip install google-genai'."
                ) from exc

            raw_key = api_key or settings.gemini_api_key
            if not raw_key:
                raise RuntimeError(
                    "GEMINI_API_KEY is not configured for GeminiSearchProvider. "
                    "Please set the GEMINI_API_KEY environment variable or configure SEARCH_PROVIDER=mock."
                )

            self._has_key = True
            self.client = genai.Client(api_key=raw_key)

    def __repr__(self) -> str:
        key_status = "***" if self._has_key else "missing"
        return f"<GeminiSearchProvider(model='{self.model}', api_key='{key_status}')>"

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        """
        Executes web search via the Gemini Interactions API using tools=[{"type": "google_search"}].
        Extracts step URL citations into SearchResult abstractions.
        Does NOT silently fall back to mock search on failures.
        """
        clean_query = query.strip()
        if not clean_query:
            return []

        try:
            interaction = await self.client.aio.interactions.create(
                model=self.model,
                input=f"Search the web for: {clean_query}",
                tools=[{"type": "google_search"}],
            )
        except Exception as exc:
            logger.error("Gemini Search Interaction failed for query '%s': %s", clean_query, exc)
            raise RuntimeError(f"Gemini Search request failed: {exc}") from exc

        results: List[SearchResult] = []
        seen_urls = set()

        steps = getattr(interaction, "steps", None) or []
        for step in steps:
            step_type = getattr(step, "type", None) or (step.get("type") if isinstance(step, dict) else None)
            if step_type == "model_output":
                content = getattr(step, "content", None) or (step.get("content") if isinstance(step, dict) else None)
                if isinstance(content, list):
                    for item in content:
                        item_type = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
                        if item_type == "text":
                            full_text = getattr(item, "text", "") or (item.get("text") if isinstance(item, dict) else "")
                            annotations = getattr(item, "annotations", None) or (item.get("annotations") if isinstance(item, dict) else None) or []
                            for ann in annotations:
                                ann_type = getattr(ann, "type", None) or (ann.get("type") if isinstance(ann, dict) else None)
                                url = getattr(ann, "url", None) or (ann.get("url") if isinstance(ann, dict) else None)
                                if not url or url in seen_urls:
                                    continue

                                title = getattr(ann, "title", None) or (ann.get("title") if isinstance(ann, dict) else None) or "Web Search Result"
                                start_idx = getattr(ann, "start_index", None) if not isinstance(ann, dict) else ann.get("start_index")
                                end_idx = getattr(ann, "end_index", None) if not isinstance(ann, dict) else ann.get("end_index")

                                snippet = ""
                                if full_text and start_idx is not None and end_idx is not None:
                                    if 0 <= start_idx < end_idx <= len(full_text):
                                        snippet = full_text[start_idx:end_idx].strip()

                                if not snippet and full_text:
                                    snippet = full_text[:200].strip()

                                domain = urlparse(url).netloc.lower()
                                metadata: Dict[str, Any] = {
                                    "provider": "gemini_google_search",
                                    "model": self.model,
                                }
                                if start_idx is not None:
                                    metadata["start_index"] = start_idx
                                if end_idx is not None:
                                    metadata["end_index"] = end_idx

                                results.append(
                                    SearchResult(
                                        title=title,
                                        url=url,
                                        snippet=snippet,
                                        source_domain=domain,
                                        metadata=metadata,
                                    )
                                )
                                seen_urls.add(url)

        return results[:max_results]
