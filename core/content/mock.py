"""
Deterministic Mock Content Generator for ORBIS Phase 8.
Enables reliable, offline, repeatable unit testing and demonstrations without
requiring external LLM services, GPUs, or network connectivity.
"""

from datetime import datetime, timezone
import re
from typing import Dict, List, Literal, Optional

from core.content.base import BaseContentGenerator, ContentGrounding, GeneratedContent


class MockContentGenerator(BaseContentGenerator):
    """
    Deterministic template-based content generator for tests and offline workflows.
    """

    def __init__(
        self,
        default_template: Optional[str] = None,
        should_fail: bool = False,
        failure_message: str = "Simulated content generation failure.",
    ) -> None:
        self.default_template = default_template
        self.should_fail = should_fail
        self.failure_message = failure_message
        self.call_history: List[Dict[str, Any]] = []

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
        Produces deterministic, structured document content based on inputs.
        """
        self.call_history.append(
            {
                "topic": topic,
                "content_type": content_type,
                "format": format,
                "context_data_length": len(context_data) if context_data else 0,
                "source_references": source_references or [],
            }
        )

        if self.should_fail:
            raise RuntimeError(self.failure_message)

        clean_topic = topic.strip() or "Untitled Document"
        title = self._format_title(clean_topic, content_type)

        if self.default_template:
            body = self.default_template.format(
                topic=clean_topic,
                title=title,
                context=context_data or "",
            )
        else:
            body = self._build_deterministic_content(
                clean_topic, title, content_type, format, context_data, source_references
            )

        # Enforce character bound
        if len(body) > max_length_chars:
            body = body[:max_length_chars].rstrip() + "\n\n[Content truncated at maximum length bound]"

        lines = body.splitlines()
        sources = list(source_references or [])

        # Detect untrusted web marker in context
        is_untrusted = False
        if context_data and ("Untrusted data" in context_data or "is_untrusted_external_data" in context_data):
            is_untrusted = True

        grounding = ContentGrounding(
            user_topic=clean_topic,
            sources_used=sources,
            has_context=bool(context_data and context_data.strip()),
            is_untrusted_data_used=is_untrusted,
            is_generated=True,
            notes=f"Generated via MockContentGenerator ({content_type}/{format}).",
        )

        return GeneratedContent(
            title=title,
            content=body,
            format=format,
            topic=clean_topic,
            char_count=len(body),
            line_count=len(lines),
            grounding=grounding,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    def _format_title(self, topic: str, content_type: str) -> str:
        words = topic.split()
        capitalized = " ".join(w.capitalize() for w in words[:6])
        if content_type == "report":
            return f"{capitalized}: Research & Analysis Report"
        elif content_type == "summary":
            return f"Executive Summary: {capitalized}"
        elif content_type == "readme":
            return f"Project README — {capitalized}"
        return f"{capitalized} Document"

    def _build_deterministic_content(
        self,
        topic: str,
        title: str,
        content_type: str,
        format: str,
        context_data: Optional[str],
        source_references: Optional[List[str]],
    ) -> str:
        sources = source_references or []
        has_ctx = bool(context_data and context_data.strip())

        if format == "markdown":
            sections = [f"# {title}\n"]

            if content_type == "readme":
                sections.append("## Overview\n")
                sections.append(f"This document describes **{topic}** within the ORBIS workspace.\n")
                if has_ctx:
                    sections.append("## Specifications & Details\n")
                    clean_ctx = context_data.strip() if context_data else ""
                    # Sanitize any raw markdown headers in context
                    clean_ctx = re.sub(r"^#+\s*", "", clean_ctx, flags=re.MULTILINE)
                    sections.append(f"{clean_ctx[:1500]}\n")
                sections.append("## Usage & Getting Started\n")
                sections.append("Refer to the project guidelines and architectural documentation for execution instructions.\n")

            elif content_type == "summary":
                sections.append("## Summary Overview\n")
                sections.append(f"Key findings and synthesized highlights regarding **{topic}**.\n")
                if has_ctx:
                    sections.append("## Analyzed Data\n")
                    sections.append(f"{context_data.strip()[:1500]}\n")

            else:  # report or document
                sections.append("## 1. Executive Summary\n")
                sections.append(f"This report presents structured findings and analysis on **{topic}**.\n")

                sections.append("## 2. Key Findings\n")
                if has_ctx:
                    sections.append(f"{context_data.strip()[:2000]}\n")
                else:
                    sections.append(f"- Investigation into {topic} confirms operational viability.\n")
                    sections.append("- System boundaries and architectural parameters are defined according to specification.\n")
                    sections.append("- Execution occurs under strict deterministic safety boundaries.\n")

                sections.append("## 3. Conclusion & Next Steps\n")
                sections.append(f"The synthesis for {topic} is complete and verified against local workspace parameters.\n")

            if sources:
                sections.append("## References & Sources\n")
                for s in sources:
                    sections.append(f"- [{s}]({s})")
                sections.append("")

            return "\n".join(sections).strip() + "\n"

        else:  # plain text
            lines = [
                "=" * 70,
                title.upper(),
                "=" * 70,
                "",
                f"Topic: {topic}",
                f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
                "",
                "1. OVERVIEW",
                "-" * 40,
                f"Structured documentation regarding {topic}.",
                "",
                "2. DETAILS & CONTEXT",
                "-" * 40,
            ]
            if has_ctx:
                lines.append(context_data.strip()[:2000])
            else:
                lines.append(f"Comprehensive record for {topic} generated within ORBIS workspace.")
            lines.append("")

            if sources:
                lines.append("3. REFERENCES")
                lines.append("-" * 40)
                for s in sources:
                    lines.append(f"  * {s}")
                lines.append("")

            lines.extend([
                "=" * 70,
                "END OF DOCUMENT",
                "=" * 70,
                "",
            ])
            return "\n".join(lines)
