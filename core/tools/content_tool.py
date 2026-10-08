"""
Content Generator Tool for ORBIS Phase 8.
Wraps BaseContentGenerator as a registered BaseTool for multi-step DAG planning.
Operates strictly in memory; does NOT write to the filesystem.
"""

from typing import Any, Dict, List, Literal, Optional, Type
from pydantic import BaseModel, Field

from core.config import get_settings
from core.content.base import BaseContentGenerator, GeneratedContent
from core.content.mock import MockContentGenerator
from core.tools.base import BaseTool
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport


class ContentGeneratorParameters(BaseModel):
    """Parameters schema for content generation."""
    topic: str = Field(
        ...,
        min_length=1,
        description="The subject, question, or purpose of the document to generate.",
    )
    content_type: Literal["report", "summary", "readme", "document"] = Field(
        default="report",
        description="The structure of the generated document.",
    )
    format: Literal["markdown", "text", "docx", "pptx", "pdf", "xlsx"] = Field(
        default="markdown",
        description="The document markup format ('markdown', 'text', 'docx', 'pptx', 'pdf', 'xlsx').",
    )
    context_data: Optional[str] = Field(
        default=None,
        description="Optional reference context or raw data (e.g. from web research or files).",
    )
    source_references: Optional[List[str]] = Field(
        default=None,
        description="Optional list of source URLs or file paths for attribution.",
    )
    max_chars: int = Field(
        default=10000,
        ge=100,
        le=50000,
        description="Maximum character limit for generated content.",
    )


class ContentGeneratorTool(BaseTool):
    """
    In-memory content synthesis tool.
    Produces structured, formatted text without touching the filesystem.
    """

    name: str = "content_generator"
    description: str = (
        "Synthesizes structured, grounded document content in memory based on a topic and optional reference context."
    )
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    parameters_schema: Type[BaseModel] = ContentGeneratorParameters

    def __init__(self, generator: Optional[BaseContentGenerator] = None) -> None:
        self.generator = generator or MockContentGenerator()

    async def execute(self, params: BaseModel, context: ExecutionContext) -> ToolResult:
        """Executes in-memory content synthesis."""
        if not isinstance(params, ContentGeneratorParameters):
            params = self.validate_parameters(params.model_dump())

        try:
            generated: GeneratedContent = await self.generator.generate_content(
                topic=params.topic,
                content_type=params.content_type,
                format=params.format,
                context_data=params.context_data,
                source_references=params.source_references,
                max_length_chars=params.max_chars,
            )

            return ToolResult(
                success=True,
                output=generated.to_dict(),
                metadata={
                    "topic": params.topic,
                    "format": params.format,
                    "content_type": params.content_type,
                    "char_count": generated.char_count,
                    "has_context": generated.grounding.has_context,
                },
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Content generation failed: {str(e)}",
                metadata={"topic": params.topic},
            )

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        """Verifies that generated content conforms to requested format and non-empty bounds."""
        if not result.success:
            return VerificationReport(
                verified=False,
                details="Content generation step reported failure.",
                discrepancies=[result.error or "Unknown failure"],
            )

        output = result.output
        if not isinstance(output, dict):
            return VerificationReport(
                verified=False,
                details="Content generation output must be a structured dictionary.",
                discrepancies=["Output is not a dictionary."],
            )

        discrepancies = []
        content = output.get("content", "")
        if not content or not content.strip():
            discrepancies.append("Generated content is empty.")

        expected_format = getattr(params, "format", "markdown")
        if output.get("format") != expected_format:
            discrepancies.append(
                f"Generated format '{output.get('format')}' does not match expected '{expected_format}'."
            )

        if "grounding" not in output:
            discrepancies.append("Missing required grounding metadata.")

        if discrepancies:
            return VerificationReport(
                verified=False,
                details="Content generation output failed verification checks.",
                discrepancies=discrepancies,
            )

        return VerificationReport(
            verified=True,
            details=f"Content generated successfully ({len(content)} characters, format: {expected_format}).",
        )
