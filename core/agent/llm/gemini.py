"""
Google Gemini LLM Provider implementation for ORBIS.
Interfaces with Google's official Gemini Interactions API via the google-genai SDK.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from core.agent.llm.base import BaseLLMProvider
from core.config import get_settings
from core.tools.base import BaseTool
from core.types import ToolCall

logger = logging.getLogger("orbis.llm.gemini")


class GeminiProvider(BaseLLMProvider):
    """
    Cloud LLM provider interfacing with Google's Gemini Models via the
    official google-genai SDK and the Interactions API.
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
                    "The 'google-genai' package is required to use GeminiProvider. "
                    "Install it using 'pip install .[gemini]' or 'pip install google-genai'."
                ) from exc

            raw_key = api_key or settings.gemini_api_key
            if not raw_key:
                raise RuntimeError(
                    "GEMINI_API_KEY is not configured. Please set the GEMINI_API_KEY "
                    "environment variable or provide api_key to GeminiProvider."
                )

            self._has_key = True
            self.client = genai.Client(api_key=raw_key)

    def __repr__(self) -> str:
        key_status = "***" if self._has_key else "missing"
        return f"<GeminiProvider(model='{self.model}', api_key='{key_status}')>"

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        format: Optional[str] = None,
    ) -> str:
        """
        Sends an asynchronous generation request to Google Gemini using the Interactions API.
        Enforces non-blocking asyncio behavior.
        """
        create_kwargs: Dict[str, Any] = {
            "model": self.model,
            "input": prompt,
        }
        if system_prompt:
            create_kwargs["system_instruction"] = system_prompt
        if format == "json":
            create_kwargs["response_format"] = {"type": "text", "mime_type": "application/json"}

        try:
            interaction = await self.client.aio.interactions.create(**create_kwargs)
        except Exception as exc:
            logger.error("Gemini Interactions request failed for model '%s': %s", self.model, exc)
            raise RuntimeError(f"Gemini generation request failed: {exc}") from exc

        # Extract generated text from Interaction object
        output_text = getattr(interaction, "output_text", None)
        if output_text and isinstance(output_text, str):
            return output_text.strip()

        # Fallback inspection of steps
        steps = getattr(interaction, "steps", None) or []
        text_parts: List[str] = []
        for step in steps:
            step_type = getattr(step, "type", None) or (step.get("type") if isinstance(step, dict) else None)
            if step_type == "model_output":
                content = getattr(step, "content", None) or (step.get("content") if isinstance(step, dict) else None)
                if isinstance(content, list):
                    for item in content:
                        item_type = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
                        if item_type == "text":
                            t = getattr(item, "text", None) or (item.get("text") if isinstance(item, dict) else None)
                            if t and isinstance(t, str):
                                text_parts.append(t)

        return "".join(text_parts).strip()

    async def select_tool(
        self, user_request: str, tools: List[BaseTool]
    ) -> Optional[ToolCall]:
        """
        Presents available tool schemas to Gemini and requests a structured
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
            raw_response = await self.generate(
                user_prompt, system_prompt=system_prompt, format="json"
            )
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

        except (json.JSONDecodeError, KeyError, RuntimeError) as exc:
            logger.warning("Failed to parse tool call from Gemini response: %s", exc)
            return None
