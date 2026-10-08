"""
Abstract Base Content Generator and Grounding Models for ORBIS Phase 8.
Defines contracts for producing structured, grounded content while keeping
generation strictly separated from filesystem persistence.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class ContentGrounding(BaseModel):
    """
    Tracks provenance, references, and certainty for generated content.
    Prevents hallucinated authority by explicitly noting the data sources used.
    """
    user_topic: str = Field(..., description="The user's original topic or prompt.")
    sources_used: List[str] = Field(
        default_factory=list,
        description="List of URLs, file paths, or document references used as context.",
    )
    has_context: bool = Field(
        default=False,
        description="Whether external context or research data was supplied.",
    )
    is_untrusted_data_used: bool = Field(
        default=False,
        description="Whether external/untrusted web data was incorporated.",
    )
    is_generated: bool = Field(
        default=True,
        description="Marks that text was model-synthesized rather than an exact quote.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Additional provenance or safety remarks.",
    )


class GeneratedContent(BaseModel):
    """Structured representation of generated content before persistence."""
    title: str = Field(..., description="Heading or title for the generated document.")
    content: str = Field(..., description="The generated Markdown or plain text body.")
    format: Literal["markdown", "text", "docx", "pptx", "pdf", "xlsx"] = Field(
        default="markdown",
        description="Document format ('markdown', 'text', 'docx', 'pptx', 'pdf', 'xlsx').",
    )
    topic: str = Field(..., description="Primary topic of the document.")
    char_count: int = Field(..., ge=0, description="Total characters in content.")
    line_count: int = Field(..., ge=0, description="Total lines in content.")
    grounding: ContentGrounding = Field(
        ...,
        description="Grounding metadata distinguishing user intent, sources, and generated assertions.",
    )
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="UTC timestamp of generation.",
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serializes to a dictionary suitable for ToolResult output."""
        return self.model_dump()


class BaseContentGenerator(ABC):
    """
    Abstract interface for content generators.
    Generates structured content purely in memory without filesystem interaction.
    """

    @abstractmethod
    async def generate_content(
        self,
        topic: str,
        content_type: Literal["report", "summary", "readme", "document"] = "report",
        format: Literal["markdown", "text"] = "markdown",
        context_data: Optional[str] = None,
        source_references: Optional[List[str]] = None,
        max_length_chars: int = 10000,
    ) -> GeneratedContent:
        """
        Synthesizes structured content for a given topic and optional context.
        """
        pass
