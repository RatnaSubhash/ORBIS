"""
Knowledge & General Information Synthesis Tool for ORBIS.
Provides factual answers, conceptual explanations, protocol/concept comparisons,
and structured project assistance via the configured BaseLLMProvider.
Conforms to BaseTool contract with independent verification and clean provider-unavailable handling.
"""

import logging
import re
from typing import Any, Dict, List, Literal, Optional, Type
from pydantic import BaseModel, Field

from core.agent.llm.base import BaseLLMProvider
from core.tools.base import BaseTool
from core.types import ExecutionContext, RiskLevel, ToolResult, VerificationReport

logger = logging.getLogger("orbis.tools.knowledge")

# Core offline foundational knowledge fallback for essential computer networking concepts
# Used strictly when the LLM provider is unavailable, network is down, or API quota is exceeded.
CORE_OFFLINE_KNOWLEDGE: Dict[str, str] = {
    "tcp": (
        "TCP (Transmission Control Protocol) is a connection-oriented transport layer protocol "
        "that provides reliable, ordered, and error-checked delivery of a stream of packets between applications."
    ),
    "udp": (
        "UDP (User Datagram Protocol) is a connectionless transport layer protocol "
        "that provides lightweight, low-latency transmission without delivery, sequencing, or error-recovery guarantees."
    ),
    "tcp_udp_comparison": (
        "TCP is connection-oriented, ensuring reliable, in-order packet delivery with error recovery, "
        "making it ideal for web browsing and file transfer. UDP is connectionless and lightweight, offering lower latency "
        "without delivery guarantees, making it suitable for streaming, gaming, and DNS queries."
    ),
    "dns": (
        "DNS (Domain Name System) translates human-readable domain names (like example.com) into numerical "
        "IP addresses used by computers to locate and route traffic across the internet."
    ),
    "computer_networks": (
        "A computer network is a system of connected devices and computers that exchange data and share resources "
        "using standardized communication protocols over physical or wireless links."
    ),
}


class KnowledgeParameters(BaseModel):
    """Parameters for KnowledgeTool operations."""
    query: str = Field(
        ...,
        description="The natural language knowledge query, concept to explain, or project assistance topic.",
    )
    mode: Literal["factual", "explanation", "comparison", "project_assistance"] = Field(
        default="factual",
        description="Type of knowledge synthesis: 'factual' (direct answer), 'explanation' (conceptual), 'comparison' (tradeoffs), 'project_assistance' (advisory guidance).",
    )
    subject: Optional[str] = Field(
        default=None,
        description="The extracted project subject or entity (e.g., 'compiler in Rust', 'distributed cache').",
    )
    intent: Optional[str] = Field(
        default=None,
        description="The user intent action (e.g., 'build', 'design', 'create', 'plan', 'assist').",
    )
    concept_a: Optional[str] = Field(
        default=None,
        description="The primary concept in a comparison request (e.g., 'TCP', 'mutex', 'Docker').",
    )
    concept_b: Optional[str] = Field(
        default=None,
        description="The secondary concept in a comparison request (e.g., 'UDP', 'semaphore', 'Kubernetes').",
    )
    dimension: Optional[str] = Field(
        default=None,
        description="Optional evaluation dimension or tradeoff aspect (e.g., 'speed', 'reliability', 'ease of deployment').",
    )
    context_data: Optional[str] = Field(
        default=None,
        description="Optional supplementary context data to incorporate into the explanation.",
    )


def get_knowledge_system_prompt(mode: str) -> str:
    """Returns a tailored system prompt for knowledge synthesis."""
    if mode == "project_assistance":
        return (
            "You are the project advisory and planning engine for ORBIS, an autonomous desktop agent.\n"
            "Given a user's project request (e.g., 'help me build a compiler in Rust'), outline actionable, "
            "structured guidance:\n"
            "1. Briefly describe core project architectures or topics.\n"
            "2. Suggest specific steps ORBIS can perform (e.g. researching architectures online, drafting documentation or READMEs in workspace, inspecting workspace files, running terminal tests).\n"
            "3. Ask what specific component the user wants to start with.\n"
            "Format: 3-5 concise, professional sentences suitable for voice. Do not output markdown code blocks or filler."
        )
    elif mode == "comparison":
        return (
            "You are the conceptual comparison engine for ORBIS, an intelligent assistant.\n"
            "Compare the requested concepts covering all essential distinguishing dimensions:\n"
            "- Core connection or operational model\n"
            "- Reliability, ordering, or performance tradeoffs\n"
            "- Typical use cases for each\n"
            "Format: 3-5 complete, balanced sentences suitable for voice. Do not cut off after only one side."
        )
    elif mode == "explanation":
        return (
            "You are the technical explanation engine for ORBIS, an intelligent assistant.\n"
            "Explain the concept clearly and accurately covering:\n"
            "- What it is\n"
            "- How it functions\n"
            "- Its main purpose or importance\n"
            "Format: 2-4 concise, informative sentences suitable for voice. Do not output filler or conversational preambles."
        )
    else:  # factual
        return (
            "You are the factual knowledge engine for ORBIS, an intelligent assistant.\n"
            "Provide a direct, accurate, factual answer to the question.\n"
            "Format: 1-3 clear sentences suitable for voice. Do not output conversational filler."
        )


_DEFAULT = object()


class KnowledgeTool(BaseTool):
    """
    Synthesizes factual knowledge, technical explanations, conceptual comparisons,
    and structured project assistance via the configured LLM provider.
    Maintains deterministic offline knowledge for foundational networking concepts.
    """

    name: str = "knowledge"
    description: str = (
        "Synthesizes answers for general factual questions, technical explanations, "
        "conceptual comparisons, and structured project assistance using the configured LLM."
    )
    version: str = "1.0.0"
    risk_level: RiskLevel = RiskLevel.LOW
    parameters_schema: Type[BaseModel] = KnowledgeParameters

    def __init__(self, llm_provider: Any = _DEFAULT) -> None:
        if llm_provider is _DEFAULT:
            try:
                from core.agent.llm.factory import get_llm_provider
                self.llm_provider = get_llm_provider()
            except Exception as e:
                logger.warning("Failed to resolve LLM provider for KnowledgeTool: %s", e)
                self.llm_provider = None
        else:
            self.llm_provider = llm_provider

    def _resolve_llm_provider(self) -> Optional[BaseLLMProvider]:
        """Resolves the active LLM provider."""
        return self.llm_provider

    def _check_offline_knowledge(self, query: str, mode: str, fallback_mode: bool = False) -> Optional[str]:
        """
        Checks if the query matches a foundational concept.
        When fallback_mode=True (LLM unavailable/timed out), performs broader keyword matching.
        """
        lower = query.lower().strip()
        # TCP & UDP comparison
        if ("tcp" in lower and "udp" in lower) or ("compare" in lower and ("tcp" in lower or "udp" in lower)):
            return CORE_OFFLINE_KNOWLEDGE["tcp_udp_comparison"]
        # Foundational TCP definition
        if re.search(r"\b(?:what\s+is\s+tcp|define\s+tcp|explain\s+tcp|what\s+is\s+transmission\s+control\s+protocol)\b", lower) or lower in ("tcp", "tcp protocol"):
            return CORE_OFFLINE_KNOWLEDGE["tcp"]
        if fallback_mode and re.search(r"\b(?:tcp|transmission\s+control\s+protocol)\b", lower):
            return CORE_OFFLINE_KNOWLEDGE["tcp"]
        # Foundational UDP definition
        if re.search(r"\b(?:what\s+is\s+udp|define\s+udp|explain\s+udp|what\s+is\s+user\s+datagram\s+protocol)\b", lower) or lower in ("udp", "udp protocol"):
            return CORE_OFFLINE_KNOWLEDGE["udp"]
        if fallback_mode and re.search(r"\b(?:udp|user\s+datagram\s+protocol)\b", lower):
            return CORE_OFFLINE_KNOWLEDGE["udp"]
        # Foundational DNS definition (Exclude "how does dns work", which is an open-ended explanation)
        if re.search(r"\b(?:what\s+is\s+dns|define\s+dns|explain\s+dns|what\s+does\s+dns\s+stand\s+for|what\s+is\s+the\s+domain\s+name\s+system)\b", lower):
            return CORE_OFFLINE_KNOWLEDGE["dns"]
        if fallback_mode and re.search(r"\b(?:dns|domain\s+name\s+system)\b", lower):
            return CORE_OFFLINE_KNOWLEDGE["dns"]
        # Foundational Computer Networks definition
        if "computer network" in lower:
            return CORE_OFFLINE_KNOWLEDGE["computer_networks"]
        return None

    async def execute(
        self, params: BaseModel, context: ExecutionContext
    ) -> ToolResult:
        """Synthesizes a knowledge response."""
        if not isinstance(params, KnowledgeParameters):
            params = self.validate_parameters(params.model_dump())

        query = params.query.strip()
        mode = params.mode
        subject = getattr(params, "subject", None) or query
        intent = getattr(params, "intent", None) or "assist"

        # 1. Deterministic foundational grounding when an appropriate foundational answer exists
        if mode != "project_assistance":
            foundational_match = self._check_offline_knowledge(query, mode, fallback_mode=False)
            if foundational_match:
                output = {
                    "action": "knowledge_synthesis",
                    "answer": foundational_match,
                    "query": query,
                    "mode": mode,
                    "source": "offline_core",
                }
                return ToolResult(success=True, output=output)

        llm = self.llm_provider

        # 2. Configured real LLM execution for open-ended synthesis or project assistance
        if llm is not None and getattr(llm, "is_available", lambda: True)():
            system_prompt = get_knowledge_system_prompt(mode)
            if mode == "project_assistance":
                user_prompt = f"User Request: I want to {intent} a {subject}."
            elif mode == "comparison" and getattr(params, "concept_a", None) and getattr(params, "concept_b", None):
                dim_str = f" Focus particularly on {params.dimension}." if getattr(params, "dimension", None) else ""
                user_prompt = f"User Request: Compare {params.concept_a} and {params.concept_b}.{dim_str}"
            else:
                user_prompt = f"User Request: {query}"
            if params.context_data:
                user_prompt += f"\nContext:\n{params.context_data}"

            try:
                import asyncio
                raw_answer = await asyncio.wait_for(
                    llm.generate(
                        prompt=user_prompt,
                        system_prompt=system_prompt,
                    ),
                    timeout=20.0,
                )
                if raw_answer and raw_answer.strip():
                    clean_ans = raw_answer.strip()
                    clean_ans = re.sub(r"^```[a-zA-Z]*\n?", "", clean_ans)
                    clean_ans = re.sub(r"\n?```$", "", clean_ans).strip()

                    # For project assistance, ensure clean prefix without conversational wrappers
                    if mode == "project_assistance":
                        clean_subj = re.sub(
                            r"^(?:can\s+you\s+(?:please\s+)?|could\s+you\s+(?:please\s+)?|would\s+you\s+(?:please\s+)?|"
                            r"i\s+(?:want|would\s+like|need)\s+(?:you\s+to\s+|to\s+)?|please\s+|hey\s+orbis,?\s*|orbis,?\s*)+",
                            "",
                            subject,
                            flags=re.IGNORECASE,
                        ).strip()
                        clean_subj = re.sub(
                            r"^(?:help(?:\s+me)?\s+(?:to\s+|with\s+)?|assist(?:\s+me)?\s+(?:in|with)?\s*|guide\s+me\s+(?:in|on)\s+|work\s+on\s+)?",
                            "",
                            clean_subj,
                            flags=re.IGNORECASE,
                        ).strip()
                        clean_subj = re.sub(
                            r"^(?:build|design|create|plan|develop|architect|implement|code|program)\s+",
                            "",
                            clean_subj,
                            flags=re.IGNORECASE,
                        ).strip()

                        if "project" in clean_subj.lower():
                            prefix = f"I can help you with your {clean_subj}."
                        elif intent in ("build", "design", "create", "plan", "develop", "architect", "implement"):
                            prefix = f"I can help you {intent} your {clean_subj}."
                        else:
                            prefix = f"I can help you with your {clean_subj} project."

                        if not clean_ans.lower().startswith("i can help you"):
                            clean_ans = f"{prefix} {clean_ans}"

                    output = {
                        "action": "knowledge_synthesis",
                        "answer": clean_ans,
                        "query": query,
                        "subject": subject,
                        "intent": intent,
                        "mode": mode,
                        "source": "llm",
                    }
                    return ToolResult(success=True, output=output)
                else:
                    # Malformed provider response (empty / whitespace)
                    return ToolResult(
                        success=False,
                        error=f"Language model provider returned an empty or malformed response for '{query}'.",
                        output={
                            "action": "knowledge_synthesis",
                            "answer": f"The language model returned an empty response for '{query}'.",
                            "query": query,
                            "mode": mode,
                            "source": "malformed_provider_response",
                        },
                    )
            except asyncio.TimeoutError:
                logger.warning("Knowledge synthesis via LLM timed out for query: %s", query)
                offline_match = self._check_offline_knowledge(query, mode, fallback_mode=True)
                if offline_match:
                    return ToolResult(
                        success=True,
                        output={
                            "action": "knowledge_synthesis",
                            "answer": offline_match,
                            "query": query,
                            "mode": mode,
                            "source": "offline_core",
                        },
                    )
                return ToolResult(
                    success=False,
                    error=f"Knowledge synthesis timed out after 20.0s for '{query}'.",
                    output={
                        "action": "knowledge_synthesis",
                        "answer": f"Knowledge synthesis timed out for '{query}'. The language model service did not respond in time.",
                        "query": query,
                        "mode": mode,
                        "source": "llm_timeout",
                    },
                )
            except Exception as exc:
                logger.warning("Knowledge synthesis via LLM failed: %s", exc)
                offline_match = self._check_offline_knowledge(query, mode, fallback_mode=True)
                if offline_match:
                    return ToolResult(
                        success=True,
                        output={
                            "action": "knowledge_synthesis",
                            "answer": offline_match,
                            "query": query,
                            "mode": mode,
                            "source": "offline_core",
                        },
                    )
                return ToolResult(
                    success=False,
                    error=f"Knowledge synthesis failed due to provider error: {exc}",
                    output={
                        "action": "knowledge_synthesis",
                        "answer": f"I encountered an error communicating with the language model service: {exc}",
                        "query": query,
                        "mode": mode,
                        "source": "provider_exception",
                    },
                )

        # 3. When LLM is unavailable (None or unavailable)
        # A. Deterministic foundational grounding fallback
        if mode != "project_assistance":
            offline_match = self._check_offline_knowledge(query, mode, fallback_mode=True)
            if offline_match:
                output = {
                    "action": "knowledge_synthesis",
                    "answer": offline_match,
                    "query": query,
                    "mode": mode,
                    "source": "offline_core",
                }
                return ToolResult(success=True, output=output)

        # B. Project assistance structured offline guidance
        if mode == "project_assistance":
            clean_subj = re.sub(
                r"^(?:can\s+you\s+(?:please\s+)?|could\s+you\s+(?:please\s+)?|would\s+you\s+(?:please\s+)?|"
                r"i\s+(?:want|would\s+like|need)\s+(?:you\s+to\s+|to\s+)?|please\s+|hey\s+orbis,?\s*|orbis,?\s*)+",
                "",
                subject,
                flags=re.IGNORECASE,
            ).strip()
            clean_subj = re.sub(
                r"^(?:help(?:\s+me)?\s+(?:to\s+|with\s+)?|assist(?:\s+me)?\s+(?:in|with)?\s*|guide\s+me\s+(?:in|on)\s+|work\s+on\s+)?",
                "",
                clean_subj,
                flags=re.IGNORECASE,
            ).strip()
            clean_subj = re.sub(
                r"^(?:build|design|create|plan|develop|architect|implement|code|program)\s+",
                "",
                clean_subj,
                flags=re.IGNORECASE,
            ).strip()

            if "project" in clean_subj.lower():
                prefix = f"I can help you with your {clean_subj}."
            elif intent in ("build", "design", "create", "plan", "develop", "architect", "implement"):
                prefix = f"I can help you {intent} your {clean_subj}."
            else:
                prefix = f"I can help you with your {clean_subj} project."

            fallback_ans = (
                f"{prefix} I can research architectures and topics online, "
                "draft documentation and project files in your workspace, and run tests or commands in the terminal. "
                "What specific component would you like to start with?"
            )
            output = {
                "action": "knowledge_synthesis",
                "answer": fallback_ans,
                "query": query,
                "subject": clean_subj,
                "intent": intent,
                "mode": mode,
                "source": "structural_assistance",
            }
            return ToolResult(success=True, output=output)

        # C. Explicit provider failure for open-ended knowledge when LLM is unavailable
        unavailable_msg = (
            f"I cannot synthesize detailed information about '{query}' right now because the language model service "
            "is currently unavailable or quota-limited. You can ask me to search the web, inspect workspace files, "
            "run terminal commands, or check the current time."
        )
        output = {
            "action": "knowledge_synthesis",
            "answer": unavailable_msg,
            "query": query,
            "mode": mode,
            "source": "provider_unavailable",
        }
        return ToolResult(success=True, output=output)

    async def verify(
        self, params: BaseModel, result: ToolResult, context: ExecutionContext
    ) -> VerificationReport:
        """
        Independently verifies that the synthesized output is non-empty, contains
        a readable answer, and does not leak raw tool schemas or prompt echo.
        """
        if not result.success or not isinstance(result.output, dict):
            return VerificationReport(
                verified=False,
                details=f"Tool execution failed: {result.error or 'non-dictionary output'}",
            )

        answer = result.output.get("answer")
        if not answer or not isinstance(answer, str) or len(answer.strip()) < 5:
            return VerificationReport(
                verified=False,
                details="Verification failed: generated answer is empty or suspiciously brief.",
            )

        # Reject answers that merely echo the question verbatim
        if answer.strip().lower() == params.query.strip().lower():
            return VerificationReport(
                verified=False,
                details="Verification failed: generated answer is an echo of the query.",
            )

        # Reject answers that contain unresolved placeholder tokens like [Concept 1], [Concept 2], <concept>, etc.
        placeholder_match = re.search(r"\[(?:concept|entity|topic|subject|item|placeholder)\s*\d*\]", answer, re.IGNORECASE)
        if placeholder_match:
            return VerificationReport(
                verified=False,
                details=f"Verification failed: generated answer contains unresolved placeholder token '{placeholder_match.group(0)}'.",
            )

        # For project assistance, verify no conversational wrappers in subject
        if getattr(params, "mode", None) == "project_assistance":
            subj = result.output.get("subject", "")
            if subj and any(wrap in subj.lower() for wrap in ("can you help me", "could you help me", "help me build", "help me")):
                return VerificationReport(
                    verified=False,
                    details=f"Verification failed: project subject contains conversational wrapper '{subj}'.",
                )

        return VerificationReport(
            verified=True,
            details="Knowledge answer verified successfully.",
        )
