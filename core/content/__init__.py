"""
ORBIS Phase 8 Content Generation Package.
Provides clean abstractions for structured document synthesis, grounding, and provenance.
"""

from core.content.base import BaseContentGenerator, ContentGrounding, GeneratedContent
from core.content.llm import LLMContentGenerator
from core.content.mock import MockContentGenerator

__all__ = [
    "BaseContentGenerator",
    "ContentGrounding",
    "GeneratedContent",
    "LLMContentGenerator",
    "MockContentGenerator",
]
