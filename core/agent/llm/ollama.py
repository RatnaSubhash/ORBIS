"""
Ollama Provider implementation for ORBIS.
Communicates with a local Ollama daemon via asynchronous HTTP.
"""

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from core.agent.llm.base import BaseLLMProvider
from core.config import get_settings
from core.tools.base import BaseTool
from core.types import ToolCall

logger = logging.getLogger("orbis.llm.ollama")


class OllamaProvider(BaseLLMProvider):
    """Local LLM provider interfacing with an Ollama service."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.ollama_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.timeout = timeout or settings.ollama_timeout

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        format: Optional[str] = None,
    ) -> str:
        """Sends a completion request to Ollama /api/generate."""
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }
        if system_prompt:
            payload["system"] = system_prompt
        if format:
            payload["format"] = format

        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            response = await client.post("/api/generate", json=payload)
            response.raise_for_status()
            data = response.json()
            return data.get("response", "").strip()

    async def select_tool(
        self, user_request: str, tools: List[BaseTool]
    ) -> Optional[ToolCall]:
        """
        Presents available tool schemas to the model and requests a structured
        JSON tool invocation matching the user's intent.
        """
        if not tools:
            return None

        tools_catalog = []
        for t in tools:
            tools_catalog.append(
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.get_schema_json(),
                }
            )

        system_prompt = (
            "You are the tool selection module of ORBIS, an autonomous desktop agent.\n"
            "Given a user request and available tools, select the most appropriate tool and parameters.\n"
            "You MUST respond ONLY with valid JSON conforming to this exact structure:\n"
            '{"tool": "<tool_name_or_null>", "parameters": {<key_value_pairs>}}\n'
            "If no tool is suitable or necessary, respond with:\n"
            '{"tool": null, "parameters": {}}\n'
            "Do NOT output markdown code blocks, conversational filler, or chain-of-thought."
        )

        user_prompt = (
            f"Available Tools:\n{json.dumps(tools_catalog, indent=2)}\n\n"
            f"User Request: {user_request}\n\n"
            "JSON Response:"
        )

        try:
            raw_response = await self.generate(user_prompt, system_prompt=system_prompt)
            # Clean possible markdown wrapping
            cleaned = raw_response.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            parsed = json.loads(cleaned)
            tool_name = parsed.get("tool")
            if not tool_name or tool_name == "null":
                return None

            parameters = parsed.get("parameters", {})
            return ToolCall(tool_name=tool_name, parameters=parameters)

        except (json.JSONDecodeError, httpx.HTTPError, KeyError) as e:
            logger.warning("Failed to parse tool call from Ollama response: %s", e)
            return None
