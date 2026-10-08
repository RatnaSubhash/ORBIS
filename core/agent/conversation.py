"""
Conversational Continuity & Intent Intelligence Layer for ORBIS.
Provides bounded multi-turn conversation context, modular reference resolution,
first-class comparison intent modeling, topic switching and returning,
active entity tracking, and safe ambiguity detection.

Preserves the principle that conversational context is untrusted input and must never
bypass capability classification, planning, validation, risk assessment, or human-in-the-loop approval.
"""

from datetime import datetime, timezone
from enum import Enum
import logging
import re
from typing import Any, Dict, List, Literal, Optional, Tuple
from uuid import uuid4
from pydantic import BaseModel, Field

logger = logging.getLogger("orbis.agent.conversation")


class IntentType(str, Enum):
    """Classification of user conversational intent."""
    GENERAL = "GENERAL"
    EXPLANATION = "EXPLANATION"
    COMPARISON = "COMPARISON"
    FOLLOW_UP_ASPECT = "FOLLOW_UP_ASPECT"
    FOLLOW_UP_EXAMPLE = "FOLLOW_UP_EXAMPLE"
    FOLLOW_UP_SIMPLIFICATION = "FOLLOW_UP_SIMPLIFICATION"
    RESEARCH = "RESEARCH"
    DOCUMENT_CREATION = "DOCUMENT_CREATION"
    SYSTEM_CONTROL = "SYSTEM_CONTROL"
    PROJECT_ASSISTANCE = "PROJECT_ASSISTANCE"
    WORKSPACE_CONTINUITY = "WORKSPACE_CONTINUITY"
    WORKSPACE_RESTORATION = "WORKSPACE_RESTORATION"
    CLARIFICATION = "CLARIFICATION"


class ComparisonIntent(BaseModel):
    """Structured, validated representation of a conceptual comparison request."""
    concept_a: str = Field(..., description="First entity/concept to compare.")
    concept_b: str = Field(..., description="Second entity/concept to compare.")
    dimension: Optional[str] = Field(None, description="Tradeoff dimension or aspect (e.g. 'speed', 'reliability').")
    source: Literal["current_request", "conversation_context"] = Field(
        default="current_request",
        description="Source from which comparison entities were resolved.",
    )


class ConversationTurn(BaseModel):
    """Factual record of an individual conversational turn."""
    turn_id: str = Field(default_factory=lambda: uuid4().hex)
    turn_index: int
    user_query: str
    resolved_query: str
    response_text: str
    topic: Optional[str] = None
    entities: List[str] = Field(default_factory=list)
    comparison: Optional[ComparisonIntent] = None
    intent_type: Optional[str] = None
    tool_name: Optional[str] = None
    tool_result_summary: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResolutionResult(BaseModel):
    """Outcome of resolving anaphoric references or topic continuity in a user query."""
    resolved_query: str
    intent_type: Optional[IntentType] = None
    comparison: Optional[ComparisonIntent] = None
    active_topic: Optional[str] = None
    active_entity: Optional[str] = None
    active_entities: List[str] = Field(default_factory=list)
    follow_up_aspect: Optional[str] = None
    is_ambiguous: bool = False
    clarification_message: Optional[str] = None
    was_modified: bool = False
    topic_switched: bool = False
    reference_mapping: Dict[str, str] = Field(default_factory=dict)


# Common technical/comparative evaluation dimensions
EVALUATION_ASPECTS = (
    "reliability", "performance", "speed", "latency", "throughput", "security",
    "scalability", "complexity", "cost", "memory", "usage", "overhead",
    "architecture", "design", "fault tolerance", "maintenance", "ease of use",
    "deployment", "efficiency", "concurrency", "threading", "licensing",
    "use cases", "tradeoffs", "differences",
)


def extract_comparison_pair(text: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Extracts (concept_a, concept_b, dimension) from a comparative query.
    Works generically across arbitrary domains without topic whitelisting.
    """
    if not text:
        return None, None, None

    cleaned = text.strip().rstrip(".?!")

    # 1. "Between A and B, which one is [dimension]"
    m = re.search(
        r"\bbetween\s+([a-zA-Z0-9_\-.\s]+?)\s+and\s+([a-zA-Z0-9_\-.\s]+?)[,;]?\s+which\s+(?:one\s+)?(?:is|are)\s+([a-zA-Z0-9_\-.\s]+)",
        cleaned,
        re.IGNORECASE,
    )
    if m:
        ca = _clean_concept(m.group(1))
        cb = _clean_concept(m.group(2))
        dim = m.group(3).strip()
        if ca and cb:
            return ca, cb, dim

    # 2. "How does A differ from B" / "How do A and B differ"
    m = re.search(
        r"\bhow\s+(?:does|do)\s+([a-zA-Z0-9_\-.\s]+?)\s+differ\s+(?:from|with)\s+([a-zA-Z0-9_\-.\s]+)",
        cleaned,
        re.IGNORECASE,
    )
    if m:
        ca = _clean_concept(m.group(1))
        cb = _clean_concept(m.group(2))
        if ca and cb:
            return ca, cb, None

    # 3. "How are A and B different"
    m = re.search(
        r"\bhow\s+are\s+([a-zA-Z0-9_\-.\s]+?)\s+and\s+([a-zA-Z0-9_\-.\s]+?)\s+different\b",
        cleaned,
        re.IGNORECASE,
    )
    if m:
        ca = _clean_concept(m.group(1))
        cb = _clean_concept(m.group(2))
        if ca and cb:
            return ca, cb, None

    # 4. "Difference/differences between A and B"
    m = re.search(
        r"\b(?:what\s+is\s+the\s+)?(?:difference|differences)\s+between\s+([a-zA-Z0-9_\-.\s]+?)\s+and\s+([a-zA-Z0-9_\-.\s]+)",
        cleaned,
        re.IGNORECASE,
    )
    if m:
        ca = _clean_concept(m.group(1))
        cb = _clean_concept(m.group(2))
        if ca and cb:
            return ca, cb, None

    # 5. "Put A and B side by side"
    m = re.search(
        r"\bput\s+([a-zA-Z0-9_\-.\s]+?)\s+and\s+([a-zA-Z0-9_\-.\s]+?)\s+side\s+by\s+side\b",
        cleaned,
        re.IGNORECASE,
    )
    if m:
        ca = _clean_concept(m.group(1))
        cb = _clean_concept(m.group(2))
        if ca and cb:
            return ca, cb, None

    # 6. "Compare A and/with/to B [optional dimension]"
    m = re.search(
        r"\bcompare\s+([a-zA-Z0-9_\-.\s]+?)\s+(?:and|with|to)\s+([a-zA-Z0-9_\-.\s]+?)(?:\s+(?:in\s+terms\s+of|regarding|for)\s+([a-zA-Z0-9_\-.\s]+))?$",
        cleaned,
        re.IGNORECASE,
    )
    if m:
        ca = _clean_concept(m.group(1))
        cb = _clean_concept(m.group(2))
        dim = m.group(3).strip() if m.group(3) else None
        if ca and cb and ca.lower() not in ("it", "that", "this", "them"):
            return ca, cb, dim

    # 7. "A versus B" / "A vs B"
    m = re.search(
        r"\b([a-zA-Z0-9_\-.\s]+?)\s+(?:versus|vs\.?)\s+([a-zA-Z0-9_\-.\s]+)",
        cleaned,
        re.IGNORECASE,
    )
    if m:
        ca = _clean_concept(m.group(1))
        cb = _clean_concept(m.group(2))
        if ca and cb:
            return ca, cb, None

    return None, None, None


def _clean_concept(text: str) -> str:
    """Strips leading articles and punctuation from a concept candidate."""
    s = text.strip()
    s = re.sub(r"^(?:the|a|an)\s+", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"[,;:.?!]+$", "", s).strip()
    return s


def detect_topic_reset(query: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Detects explicit topic breaks, reset commands, or topic switching directives.
    e.g. "Okay, forget networking for a second. Explain mutexes."
         "Forget that. Tell me about DNS."
         "Never mind that. What is Docker?"
    Returns: (is_reset, extracted_new_topic, clean_subquery)
    """
    if not query:
        return False, None, None

    cleaned = query.strip()

    # Pattern with follow-up subquery after punctuation delimiter
    multi_clause = re.match(
        r"^(?:okay,?\s*)?(?:forget\s+(?:about\s+)?(?:that|it|all\s+that|[\w\-]+(?:\s+[\w\-]+)*?)(?:\s+for\s+a\s+(?:second|moment|bit))?|never\s+mind(?:\s+that)?|moving\s+on(?:\s+from\s+[\w\-]+)?|(?:let's\s+)?switch\s+to(?:\s+[\w\-]+)*?)\s*[,.;:]\s*(.+)$",
        cleaned,
        re.IGNORECASE,
    )
    if multi_clause:
        subquery = multi_clause.group(1).strip()
        new_topic = None
        switch_m = re.search(r"\b(?:let's\s+)?switch\s+to\s+([a-zA-Z0-9_\-]+)\b", cleaned[: multi_clause.start(1)], re.IGNORECASE)
        if switch_m:
            new_topic = _clean_concept(switch_m.group(1))
        if not new_topic:
            topic_m = re.search(
                r"^(?:tell\s+me\s+about|explain|what\s+is|what\s+are|how\s+about|describe)\s+(?:the\s+)?([a-zA-Z0-9_\-\s]+?)[?.!]?$",
                subquery,
                re.IGNORECASE,
            )
            if topic_m:
                new_topic = _clean_concept(topic_m.group(1))
        return True, new_topic, subquery

    # Standalone reset command without follow-up subquery
    standalone = re.match(
        r"^(?:okay,?\s*)?(?:forget\s+(?:about\s+)?(?:that|it|all\s+that|[\w\-]+(?:\s+[\w\-]+)*?)(?:\s+for\s+a\s+(?:second|moment|bit))?|never\s+mind(?:\s+that)?|moving\s+on(?:\s+from\s+[\w\-]+)?)[.?!]?$",
        cleaned,
        re.IGNORECASE,
    )
    if standalone:
        return True, None, None

    return False, None, None


def is_aspect_query(query: str) -> Tuple[bool, Optional[str]]:
    """
    Detects if the query is an evaluation/dimension question following an active topic/comparison.
    e.g. "What about reliability?", "How about performance?", "What about speed?"
    Only matches true technical evaluation dimensions, preserving entity proposals like "What about HSRP?".
    """
    if not query:
        return False, None

    m = re.search(r"^(?:what|how)\s+about\s+(?:the\s+)?([a-zA-Z0-9_\-\s]+?)[?.!]?$", query.strip(), re.IGNORECASE)
    if m:
        candidate = m.group(1).strip().lower()
        for asp in EVALUATION_ASPECTS:
            if re.search(rf"\b{re.escape(asp)}\b", candidate):
                return True, candidate

    return False, None


def is_which_one_query(query: str) -> Tuple[bool, Optional[str]]:
    """
    Detects if query is a comparative choice follow-up.
    e.g. "Which one is faster?", "Which one is more useful in an operating system?"
    """
    if not query:
        return False, None

    m = re.search(
        r"^(?:and\s+)?which\s+(?:one\s+)?(?:of\s+them\s+)?(?:is|are|would\s+be)\s+([a-zA-Z0-9_\-.\s]+?)[?.!]?$",
        query.strip(),
        re.IGNORECASE,
    )
    if m:
        dimension = m.group(1).strip()
        return True, dimension

    if re.search(r"\bwhich\s+(?:one\s+)?is\s+(?:faster|better|easier|more|less|preferred|recommended)\b", query, re.IGNORECASE):
        m2 = re.search(r"\bwhich\s+(?:one\s+)?is\s+([a-zA-Z0-9_\-.\s]+?)[?.!]?$", query.strip(), re.IGNORECASE)
        dim = m2.group(1).strip() if m2 else "better"
        return True, dim

    return False, None


def is_example_or_simplification_query(query: str) -> Tuple[bool, Optional[str]]:
    """
    Detects requests for examples or simpler explanations of the active topic.
    """
    if not query:
        return False, None

    lower = query.strip().lower()
    if re.search(r"\b(?:give\s+(?:me\s+)?(?:a\s+)?(?:simple\s+)?real-?world\s+example|give\s+(?:me\s+)?an\s+example|show\s+an\s+example)\b", lower):
        return True, "example"

    if re.search(r"\b(?:explain\s+(?:that|it)\s+(?:in\s+simple\s+(?:words|terms)|simply)|explain\s+that\s+again|make\s+it\s+simpler|in\s+simple\s+words)\b", lower):
        return True, "simplification"

    return False, None


def is_cross_capability_continuation(query: str) -> Tuple[bool, Optional[str]]:
    """
    Detects transitions to research or document generation referencing context.
    e.g. "Find me a good resource about this." -> 'research'
         "Save the important points to my project folder." -> 'document'
    Excludes self-contained commands with explicit file names or direct content.
    """
    if not query:
        return False, None

    lower = query.strip().lower()

    # Research intent referencing active context
    if (
        re.search(r"\b(?:find\s+(?:me\s+)?(?:a\s+)?good\s+resource\s+about\s+(?:this|that|it))\b", lower)
        or re.search(r"\b(?:find\s+(?:some\s+)?good\s+references(?:\s+for\s+(?:this|that|it))?)\b", lower)
        or re.search(r"\b(?:look\s+up\s+references\s+(?:for|about)\s+(?:this|that|it))\b", lower)
    ):
        return True, "research"

    # Document generation intent referencing active context
    has_explicit_filename = bool(re.search(r"\b(?:called|named)\s+[\w\-.]+|\b[\w\-.]+\.(?:md|txt|py|json|yaml|html)\b", lower))
    if not has_explicit_filename:
        if (
            re.search(r"\b(?:save|put|write|create)\s+(?:the\s+)?(?:important\s+points|key\s+points|notes|summary)\b", lower)
            or re.search(r"\b(?:save|put)\s+(?:this|that|it)\s+(?:to|in)\s+(?:my\s+)?(?:project\s+folder|workspace|document)\b", lower)
        ):
            return True, "document"

    return False, None


class ConversationContext(BaseModel):
    """
    Bounded ring buffer and state tracker for multi-turn conversational continuity.
    Retains recent relevant turns, active topic, active goal, important entities,
    active comparison state, and recently created artifacts.
    """
    max_turns: int = Field(default=10, ge=2, le=50)
    turns: List[ConversationTurn] = Field(default_factory=list)
    active_topic: Optional[str] = None
    active_goal: Optional[str] = None
    active_entity: Optional[str] = None
    active_entities: List[str] = Field(default_factory=list)
    comparison_state: Optional[ComparisonIntent] = None
    entities: Dict[str, str] = Field(default_factory=dict)
    topic_history: List[str] = Field(default_factory=list)
    last_artifact: Optional[Dict[str, Any]] = None
    last_continuity_projects: List[str] = Field(default_factory=list)
    last_continuity_applications: List[str] = Field(default_factory=list)
    last_continuity_resources: List[str] = Field(default_factory=list)
    last_continuity_session_id: Optional[str] = None

    def clear(self) -> None:
        """Resets the conversational context completely."""
        self.turns.clear()
        self.active_topic = None
        self.active_goal = None
        self.active_entity = None
        self.active_entities.clear()
        self.comparison_state = None
        self.entities.clear()
        self.topic_history.clear()
        self.last_artifact = None
        self.last_continuity_projects.clear()
        self.last_continuity_applications.clear()
        self.last_continuity_resources.clear()
        self.last_continuity_session_id = None

    def record_turn(
        self,
        user_query: str,
        resolved_query: str,
        response_text: str,
        tool_name: Optional[str] = None,
        tool_result_summary: Optional[str] = None,
        artifact: Optional[Dict[str, Any]] = None,
        comparison: Optional[ComparisonIntent] = None,
        intent_type: Optional[str] = None,
        continuity_data: Optional[Dict[str, Any]] = None,
    ) -> ConversationTurn:
        """
        Records a completed conversational turn into the bounded history and
        updates active topics, goals, comparison state, and entities.
        """
        turn_index = len(self.turns) + 1

        extracted_entities = self._extract_key_entities(resolved_query)

        # Ingest structured continuity results if available
        if continuity_data and isinstance(continuity_data, dict):
            if "projects" in continuity_data and isinstance(continuity_data["projects"], list) and continuity_data["projects"]:
                self.last_continuity_projects = [p for p in continuity_data["projects"] if p]
                for p in self.last_continuity_projects:
                    self.entities[p.lower()] = p
                if len(self.last_continuity_projects) == 1:
                    self.active_entity = self.last_continuity_projects[0]
                    self.entities["it"] = self.last_continuity_projects[0]
                    self.entities["that"] = self.last_continuity_projects[0]
            if "active_applications" in continuity_data and isinstance(continuity_data["active_applications"], list):
                self.last_continuity_applications = [a for a in continuity_data["active_applications"] if a]
            if "primary_resources" in continuity_data and isinstance(continuity_data["primary_resources"], list):
                self.last_continuity_resources = [r for r in continuity_data["primary_resources"] if r]
            if "session_id" in continuity_data:
                self.last_continuity_session_id = continuity_data["session_id"]
        elif tool_name == "continuity_tool" and response_text:
            # Heuristic extraction of project name from grounded response text if structured data omitted
            m_proj = re.search(r"\b(?:project|working\s+on)\s+['\"]?([a-zA-Z0-9_\-]+)['\"]?", response_text, re.IGNORECASE)
            if m_proj:
                cand_proj = m_proj.group(1).strip()
                if cand_proj.lower() not in ("a", "an", "the", "your", "my", "this", "that"):
                    self.last_continuity_projects = [cand_proj]
                    self.entities[cand_proj.lower()] = cand_proj
                    self.active_entity = cand_proj
                    self.entities["it"] = cand_proj

        if comparison:
            self.comparison_state = comparison
            self.active_topic = f"{comparison.concept_a} vs {comparison.concept_b}"
            self.active_entities = [comparison.concept_a, comparison.concept_b]
            self.entities["it"] = comparison.concept_a
            self.entities["that"] = comparison.concept_a
            self.entities["first"] = comparison.concept_a
            self.entities["second"] = comparison.concept_b
        elif self.comparison_state is None and extracted_entities:
            self.active_entity = extracted_entities[0]
            self.active_entities = extracted_entities
            for ent in extracted_entities:
                self.entities[ent.lower()] = ent
                self.entities["it"] = ent
                self.entities["that"] = ent

        # Infer topic from query only if not in an active comparison
        if self.comparison_state is None:
            inferred_topic = self._infer_topic(resolved_query)
            if inferred_topic:
                self._update_topic(inferred_topic)
                if not self.active_entity:
                    self.active_entity = inferred_topic
                    self.active_entities = [inferred_topic]
                    self.entities["it"] = inferred_topic
                    self.entities["that"] = inferred_topic

        goal = self._extract_goal(resolved_query)
        if goal:
            self.active_goal = goal

        if artifact:
            self.last_artifact = artifact
            if "file_name" in artifact:
                self.entities["file"] = artifact["file_name"]
                self.entities["document"] = artifact["file_name"]
                self.entities["artifact"] = artifact["file_name"]

        turn = ConversationTurn(
            turn_index=turn_index,
            user_query=user_query,
            resolved_query=resolved_query,
            response_text=response_text,
            topic=self.active_topic,
            entities=extracted_entities,
            comparison=comparison or self.comparison_state,
            intent_type=intent_type,
            tool_name=tool_name,
            tool_result_summary=tool_result_summary,
        )

        self.turns.append(turn)

        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns:]

        return turn

    def resolve_query(self, raw_query: str) -> ResolutionResult:
        """
        Deterministically resolves pronouns, anaphoric references, comparisons,
        and follow-up questions in raw_query against active conversation state.
        If a reference cannot be resolved confidently, marks as ambiguous with a
        clarification request rather than guessing.
        """
        if not raw_query or not raw_query.strip():
            return ResolutionResult(
                resolved_query=raw_query,
                is_ambiguous=True,
                clarification_message="I didn't hear a query. Could you please repeat your request?",
            )

        query = raw_query.strip()
        lower = query.lower()

        # 1. Topic Reset / Explicit Topic Switch
        is_reset, reset_topic, clean_subquery = detect_topic_reset(query)
        if is_reset:
            self.comparison_state = None
            self.active_entity = None
            self.active_entities.clear()
            self.entities.clear()
            self.active_topic = reset_topic
            if reset_topic:
                self.active_entity = reset_topic
                self.active_entities = [reset_topic]
                self.entities["it"] = reset_topic
                self.entities["that"] = reset_topic
                self.entities["this"] = reset_topic
                self.topic_history.append(reset_topic)

            sub = clean_subquery or (f"Tell me about {reset_topic}." if reset_topic else query)
            sub_res = self.resolve_query(sub)
            return ResolutionResult(
                resolved_query=sub_res.resolved_query,
                intent_type=sub_res.intent_type or IntentType.EXPLANATION,
                comparison=sub_res.comparison,
                active_topic=self.active_topic,
                active_entity=self.active_entity,
                active_entities=self.active_entities,
                was_modified=True,
                topic_switched=True,
                reference_mapping={"topic_reset": reset_topic or "cleared"},
            )

        # 2. Topic Return ("Coming back to TCP...", "Back to TCP...")
        return_match = re.search(
            r"\b(?:coming\s+back\s+to|returning\s+to|back\s+to|let's\s+return\s+to|going\s+back\s+to)\s+([a-zA-Z0-9_\-\s]+?)(?:[,.]|$|\bwhy\b|\bhow\b|\bwhat\b|\bexplain\b|\bcan\b)",
            query,
            re.IGNORECASE,
        )
        if return_match:
            target_topic = return_match.group(1).strip()
            matched_history_topic = None
            for prior in reversed(self.topic_history):
                if target_topic.lower() in prior.lower() or prior.lower() in target_topic.lower():
                    matched_history_topic = prior
                    break

            effective_topic = matched_history_topic or target_topic
            self._update_topic(effective_topic)
            self.active_entity = effective_topic
            self.active_entities = [effective_topic]
            self.entities["it"] = effective_topic
            self.entities["that"] = effective_topic

            resolved = query
            resolved = re.sub(r"\bwhy\s+is\s+it\b", f"why is {effective_topic}", resolved, flags=re.IGNORECASE)
            resolved = re.sub(r"\bhow\s+does\s+it\b", f"how does {effective_topic}", resolved, flags=re.IGNORECASE)
            resolved = re.sub(r"\bexplain\s+it\b", f"explain {effective_topic}", resolved, flags=re.IGNORECASE)
            return ResolutionResult(
                resolved_query=resolved,
                intent_type=IntentType.EXPLANATION,
                active_topic=self.active_topic,
                active_entity=self.active_entity,
                active_entities=self.active_entities,
                was_modified=(resolved != query),
                reference_mapping={"topic_return": effective_topic},
            )

        # 3. Restoration Intent Check (e.g. "Continue where I left off", "Restore my previous workspace")
        from core.agent.planner.classifier import classify_capability, CapabilityClass
        cap = classify_capability(query)
        if cap == CapabilityClass.WORKSPACE_RESTORATION:
            if re.search(r"\bopen\s+(?:the|my|that|this)?\s*project(?:\s+i\s+was\s+working\s+on)?\b", lower) and not re.search(r"\bopen\s+project\s+([a-zA-Z0-9_\-\.]+)\b", lower):
                if self.last_continuity_projects:
                    if len(self.last_continuity_projects) == 1:
                        target_proj = self.last_continuity_projects[0]
                        resolved = f"Open project {target_proj}."
                        return ResolutionResult(
                            resolved_query=resolved,
                            intent_type=IntentType.WORKSPACE_RESTORATION,
                            active_topic=target_proj,
                            active_entity=target_proj,
                            active_entities=self.last_continuity_projects,
                            was_modified=True,
                            reference_mapping={"project": target_proj},
                        )
                    else:
                        proj_list = ", ".join(self.last_continuity_projects)
                        return ResolutionResult(
                            resolved_query=query,
                            is_ambiguous=True,
                            clarification_message=f"You were working on multiple projects ({proj_list}). Which one would you like to open?",
                        )
                elif self.active_entity:
                    target_proj = self.active_entity
                    resolved = f"Open project {target_proj}."
                    return ResolutionResult(
                        resolved_query=resolved,
                        intent_type=IntentType.WORKSPACE_RESTORATION,
                        active_topic=target_proj,
                        active_entity=target_proj,
                        active_entities=[target_proj],
                        was_modified=True,
                        reference_mapping={"project": target_proj},
                    )

            return ResolutionResult(
                resolved_query=query,
                intent_type=IntentType.WORKSPACE_RESTORATION,
                active_topic=self.active_topic,
                active_entity=self.active_entity,
                active_entities=self.active_entities,
                was_modified=False,
            )

        # 3.1 Workspace Continuity / Intent Continuity
        from core.continuity.service import ContinuityService
        if ContinuityService.is_intent_query(query) or ContinuityService.is_continuity_query(query):
            return ResolutionResult(
                resolved_query=query,
                intent_type=IntentType.WORKSPACE_CONTINUITY,
                active_topic=self.active_topic,
                active_entity=self.active_entity,
                active_entities=self.active_entities,
                was_modified=False,
            )

        # 3.2 Contextual "Open it" / "Open that" Resolution with Ambiguity Gate
        is_open_it = bool(re.search(r"^(?:can\s+you\s+|please\s+)?open\s+(?:it|that|this)[?.!]?$", lower))
        if is_open_it:
            if self.last_continuity_projects:
                if len(self.last_continuity_projects) == 1:
                    proj = self.last_continuity_projects[0]
                    resolved = f"Open project {proj}."
                    return ResolutionResult(
                        resolved_query=resolved,
                        intent_type=IntentType.WORKSPACE_RESTORATION,
                        active_topic=proj,
                        active_entity=proj,
                        active_entities=self.last_continuity_projects,
                        was_modified=True,
                        reference_mapping={"it": proj},
                    )
                else:
                    proj_list = ", ".join(self.last_continuity_projects)
                    return ResolutionResult(
                        resolved_query=query,
                        is_ambiguous=True,
                        clarification_message=f"You were working on multiple projects ({proj_list}). Which one would you like to open?",
                    )
            elif self.last_continuity_resources:
                if len(self.last_continuity_resources) == 1:
                    res = self.last_continuity_resources[0]
                    resolved = f"Open {res}."
                    return ResolutionResult(
                        resolved_query=resolved,
                        intent_type=IntentType.WORKSPACE_RESTORATION,
                        active_topic=res,
                        active_entity=res,
                        active_entities=self.last_continuity_resources,
                        was_modified=True,
                        reference_mapping={"it": res},
                    )
                else:
                    res_list = ", ".join(self.last_continuity_resources)
                    return ResolutionResult(
                        resolved_query=query,
                        is_ambiguous=True,
                        clarification_message=f"You were working on multiple files ({res_list}). Which one would you like to open?",
                    )
            elif self.last_artifact and (self.last_artifact.get("file_name") or self.last_artifact.get("file_path")):
                fname = self.last_artifact.get("file_name") or self.last_artifact.get("file_path")
                resolved = f"Open {fname}."
                return ResolutionResult(
                    resolved_query=resolved,
                    intent_type=IntentType.GENERAL,
                    active_topic=self.active_topic,
                    active_entity=fname,
                    active_entities=[fname],
                    was_modified=True,
                    reference_mapping={"it": fname},
                )
            elif self.active_entity:
                resolved = f"Open project {self.active_entity}."
                return ResolutionResult(
                    resolved_query=resolved,
                    intent_type=IntentType.WORKSPACE_RESTORATION,
                    active_topic=self.active_topic,
                    active_entity=self.active_entity,
                    active_entities=self.active_entities,
                    was_modified=True,
                    reference_mapping={"it": self.active_entity},
                )
            else:
                return ResolutionResult(
                    resolved_query=query,
                    is_ambiguous=True,
                    clarification_message="What would you like me to open? Could you please specify the project or file?",
                )

        # 3.3 Deterministic Ordinal Reference Resolution (e.g. "Open the first one", "Open the second project")
        ordinal_open_match = re.search(
            r"\bopen\s+(?:the\s+)?(first|second|third|fourth|1st|2nd|3rd|4th)(?:\s+(?:one|project|app|application|workspace|file))?[?.!]?$",
            lower,
        )
        if ordinal_open_match:
            ord_str = ordinal_open_match.group(1).lower()
            ord_map = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3}
            target_idx = ord_map.get(ord_str, 0)
            candidates = self.last_continuity_projects or self.last_continuity_resources
            if candidates:
                if target_idx < len(candidates):
                    target_item = candidates[target_idx]
                    resolved = f"Open project {target_item}."
                    return ResolutionResult(
                        resolved_query=resolved,
                        intent_type=IntentType.WORKSPACE_RESTORATION,
                        active_topic=target_item,
                        active_entity=target_item,
                        active_entities=candidates,
                        was_modified=True,
                        reference_mapping={"ordinal": target_item},
                    )
                else:
                    cand_list = ", ".join(candidates)
                    return ResolutionResult(
                        resolved_query=query,
                        is_ambiguous=True,
                        clarification_message=f"There are only {len(candidates)} candidates available ({cand_list}). Which one would you like to open?",
                    )

        # 4. Artifact Reference Resolution
        artifact_ref_match = re.search(
            r"\b(?:the\s+file\s+you\s+(?:just\s+)?(?:created|made|wrote)|the\s+document\s+we\s+made|the\s+created\s+file)\b",
            lower,
        )
        if artifact_ref_match and self.last_artifact:
            file_name = self.last_artifact.get("file_name") or self.last_artifact.get("file_path")
            if file_name:
                resolved = query[: artifact_ref_match.start()] + file_name + query[artifact_ref_match.end():]
                return ResolutionResult(
                    resolved_query=resolved,
                    intent_type=IntentType.GENERAL,
                    active_topic=self.active_topic,
                    active_entity=file_name,
                    active_entities=[file_name],
                    was_modified=True,
                    reference_mapping={"artifact": file_name},
                )

        # 4. Comparison Follow-Up ("Which one is faster?", "Which is more useful in OS?")
        is_which, which_dim = is_which_one_query(query)
        if is_which:
            if self.comparison_state:
                ca = self.comparison_state.concept_a
                cb = self.comparison_state.concept_b
                dim_str = which_dim or "faster or better"
                resolved = f"Between {ca} and {cb}, which one is {dim_str}?"
                comp = ComparisonIntent(concept_a=ca, concept_b=cb, dimension=dim_str, source="conversation_context")
                return ResolutionResult(
                    resolved_query=resolved,
                    intent_type=IntentType.COMPARISON,
                    comparison=comp,
                    active_topic=f"{ca} vs {cb}",
                    active_entity=ca,
                    active_entities=[ca, cb],
                    was_modified=True,
                    reference_mapping={"which_one": f"{ca} vs {cb}"},
                )
            elif len(self.active_entities) >= 2:
                ca, cb = self.active_entities[:2]
                dim_str = which_dim or "faster or better"
                resolved = f"Between {ca} and {cb}, which one is {dim_str}?"
                comp = ComparisonIntent(concept_a=ca, concept_b=cb, dimension=dim_str, source="conversation_context")
                self.comparison_state = comp
                return ResolutionResult(
                    resolved_query=resolved,
                    intent_type=IntentType.COMPARISON,
                    comparison=comp,
                    active_topic=f"{ca} vs {cb}",
                    active_entity=ca,
                    active_entities=[ca, cb],
                    was_modified=True,
                    reference_mapping={"which_one": f"{ca} vs {cb}"},
                )
            else:
                return ResolutionResult(
                    resolved_query=query,
                    is_ambiguous=True,
                    clarification_message="Which concepts would you like me to compare?",
                )

        # 5. Aspect / Dimension Follow-Up ("What about reliability?")
        is_aspect, aspect_name = is_aspect_query(query)
        if is_aspect and aspect_name:
            if self.comparison_state:
                ca = self.comparison_state.concept_a
                cb = self.comparison_state.concept_b
                resolved = f"How do {ca} and {cb} compare in terms of {aspect_name}?"
                comp = ComparisonIntent(concept_a=ca, concept_b=cb, dimension=aspect_name, source="conversation_context")
                return ResolutionResult(
                    resolved_query=resolved,
                    intent_type=IntentType.FOLLOW_UP_ASPECT,
                    comparison=comp,
                    follow_up_aspect=aspect_name,
                    active_topic=f"{ca} vs {cb}",
                    active_entity=ca,
                    active_entities=[ca, cb],
                    was_modified=True,
                    reference_mapping={"aspect_follow_up": f"{ca} vs {cb}"},
                )
            elif self.active_topic:
                resolved = f"What about the {aspect_name} of {self.active_topic}?"
                return ResolutionResult(
                    resolved_query=resolved,
                    intent_type=IntentType.FOLLOW_UP_ASPECT,
                    follow_up_aspect=aspect_name,
                    active_topic=self.active_topic,
                    active_entity=self.active_entity,
                    active_entities=[self.active_entity] if self.active_entity else [],
                    was_modified=True,
                    reference_mapping={"aspect_follow_up": self.active_topic},
                )

        # 6. Example & Simplification Follow-Ups
        is_ex_sim, ex_sim_type = is_example_or_simplification_query(query)
        if is_ex_sim:
            target = f"{self.comparison_state.concept_a} and {self.comparison_state.concept_b}" if self.comparison_state else (self.active_topic or self.active_entity)
            if target:
                if ex_sim_type == "example":
                    resolved = f"Give a simple real-world example of {target}."
                    intent_t = IntentType.FOLLOW_UP_EXAMPLE
                else:
                    resolved = f"Explain {target} in simple terms."
                    intent_t = IntentType.FOLLOW_UP_SIMPLIFICATION
                return ResolutionResult(
                    resolved_query=resolved,
                    intent_type=intent_t,
                    active_topic=self.active_topic,
                    active_entity=self.active_entity,
                    active_entities=[self.active_entity] if self.active_entity else [],
                    was_modified=True,
                    reference_mapping={"follow_up": target},
                )

        # 7. Cross-Capability Continuation ("Find me a good resource about this", "Save important points to project folder")
        is_cross, cross_type = is_cross_capability_continuation(query)
        if is_cross:
            target = f"{self.comparison_state.concept_a} and {self.comparison_state.concept_b}" if self.comparison_state else (self.active_topic or self.active_entity or "the topic")
            if cross_type == "research":
                resolved = f"Find online resources and research about {target}."
                return ResolutionResult(
                    resolved_query=resolved,
                    intent_type=IntentType.RESEARCH,
                    active_topic=self.active_topic,
                    active_entity=self.active_entity,
                    active_entities=[self.active_entity] if self.active_entity else [],
                    was_modified=True,
                    reference_mapping={"research_target": target},
                )
            elif cross_type == "document":
                folder_str = " in project folder" if "project folder" in lower or "project" in lower else ""
                resolved = f"Create a document summarizing the important points of {target}{folder_str}."
                return ResolutionResult(
                    resolved_query=resolved,
                    intent_type=IntentType.DOCUMENT_CREATION,
                    active_topic=self.active_topic,
                    active_entity=self.active_entity,
                    active_entities=[self.active_entity] if self.active_entity else [],
                    was_modified=True,
                    reference_mapping={"document_target": target},
                )

        # 8. Comparison Resolution (Explicit or Anaphoric)
        c_a, c_b, c_dim = extract_comparison_pair(query)
        if c_a and c_b:
            comp = ComparisonIntent(concept_a=c_a, concept_b=c_b, dimension=c_dim, source="current_request")
            self.comparison_state = comp
            self.active_topic = f"{c_a} vs {c_b}"
            self.active_entity = c_a
            self.active_entities = [c_a, c_b]
            self.entities["it"] = c_a
            self.entities["that"] = c_a
            self.entities["first"] = c_a
            self.entities["second"] = c_b
            self._update_topic(f"{c_a} vs {c_b}")
            dim_clause = f" Focus on {c_dim}." if c_dim else ""
            resolved = f"Compare {c_a} and {c_b}.{dim_clause}" if c_dim else query
            return ResolutionResult(
                resolved_query=resolved,
                intent_type=IntentType.COMPARISON,
                comparison=comp,
                active_topic=self.active_topic,
                active_entity=c_a,
                active_entities=[c_a, c_b],
                was_modified=(resolved != query),
                reference_mapping={"concept_a": c_a, "concept_b": c_b},
            )

        anaphoric_comp_match = re.search(
            r"\b(?:compare|contrast|differentiate)\s+(?:it|that|this)\s+(?:with|to|and)\s+([a-zA-Z0-9_\-.\s]+?)(?:[,.]|$|\bin\b|\bfor\b|\bregarding\b)",
            query,
            re.IGNORECASE,
        ) or re.search(
            r"\b(?:difference|differences)\s+between\s+(?:it|that|this)\s+and\s+([a-zA-Z0-9_\-.\s]+?)(?:[,.]|$|\bin\b|\bfor\b|\bregarding\b)",
            query,
            re.IGNORECASE,
        ) or re.search(
            r"\b(?:how\s+does\s+(?:it|that|this)\s+differ\s+from|how\s+is\s+(?:it|that|this)\s+different\s+from)\s+([a-zA-Z0-9_\-.\s]+?)(?:[,.]|$)",
            query,
            re.IGNORECASE,
        )
        if anaphoric_comp_match:
            c_b_raw = _clean_concept(anaphoric_comp_match.group(1))
            prior_entity = self.active_entity or self.active_topic
            if prior_entity:
                comp = ComparisonIntent(concept_a=prior_entity, concept_b=c_b_raw, source="conversation_context")
                self.comparison_state = comp
                self.active_topic = f"{prior_entity} vs {c_b_raw}"
                self.active_entities = [prior_entity, c_b_raw]
                self._update_topic(f"{prior_entity} vs {c_b_raw}")
                resolved = f"Compare {prior_entity} with {c_b_raw}."
                return ResolutionResult(
                    resolved_query=resolved,
                    intent_type=IntentType.COMPARISON,
                    comparison=comp,
                    active_topic=self.active_topic,
                    active_entity=prior_entity,
                    active_entities=[prior_entity, c_b_raw],
                    was_modified=True,
                    reference_mapping={"it_or_that": prior_entity, "concept_b": c_b_raw},
                )
            else:
                return ResolutionResult(
                    resolved_query=query,
                    is_ambiguous=True,
                    clarification_message=f"What would you like me to compare {c_b_raw} with?",
                )

        if re.search(r"^(?:can\s+you\s+)?(?:please\s+)?(?:compare|contrast)(?:\s+(?:it|them|that|these))?[?.!]?$", lower):
            if self.active_topic:
                return ResolutionResult(
                    resolved_query=query,
                    is_ambiguous=True,
                    clarification_message=f"What would you like me to compare {self.active_topic} with?",
                )
            return ResolutionResult(
                resolved_query=query,
                is_ambiguous=True,
                clarification_message="What concepts would you like me to compare?",
            )

        # 9. Generic Reference & Pronoun Resolution
        if re.search(r"\bboth\b", lower) and self.comparison_state:
            both_str = f"{self.comparison_state.concept_a} and {self.comparison_state.concept_b}"
            resolved = re.sub(r"\bboth\b", both_str, query, flags=re.IGNORECASE)
            return ResolutionResult(
                resolved_query=resolved,
                intent_type=IntentType.EXPLANATION,
                active_topic=self.active_topic,
                active_entity=self.active_entity,
                active_entities=self.active_entities,
                was_modified=True,
                reference_mapping={"both": both_str},
            )

        if re.search(r"\bthe\s+first\s+one\b", lower) and self.comparison_state:
            resolved = re.sub(r"\bthe\s+first\s+one\b", self.comparison_state.concept_a, query, flags=re.IGNORECASE)
            return ResolutionResult(
                resolved_query=resolved,
                intent_type=IntentType.EXPLANATION,
                active_topic=self.active_topic,
                active_entity=self.comparison_state.concept_a,
                active_entities=self.active_entities,
                was_modified=True,
                reference_mapping={"the_first_one": self.comparison_state.concept_a},
            )

        if re.search(r"\b(?:the\s+second\s+one|the\s+other\s+one|the\s+latter)\b", lower) and self.comparison_state:
            resolved = re.sub(r"\b(?:the\s+second\s+one|the\s+other\s+one|the\s+latter)\b", self.comparison_state.concept_b, query, flags=re.IGNORECASE)
            return ResolutionResult(
                resolved_query=resolved,
                intent_type=IntentType.EXPLANATION,
                active_topic=self.active_topic,
                active_entity=self.comparison_state.concept_b,
                active_entities=self.active_entities,
                was_modified=True,
                reference_mapping={"the_second_one": self.comparison_state.concept_b},
            )

        has_that = bool(
            re.search(r"\b(?:let's\s+)?(?:use|take|choose|select|pick|go\s+with)\s+that\b", lower)
            or re.search(r"^(?:okay,?\s*)?(?:let's\s+)?(?:use|take|choose|select|pick|go\s+with)\s+that[?.!]?$", lower)
            or re.search(r"^(?:what|how)\s+about\s+that[?.!]?$", lower)
            or re.search(r"^(?:explain|describe|tell\s+me\s+about)\s+that[?.!]?$", lower)
            or re.search(r"\babout\s+that[?.!]?$", lower)
        )

        its_match = re.search(r"\bits\s+([a-zA-Z0-9_\-]+)\b", lower)
        has_its = False
        if its_match:
            prefix = lower[: its_match.start()].strip()
            last_clause = re.split(r"[.!?;\n]", prefix)[-1].strip()
            clean_prefix = re.sub(
                r"\b(?:explain|tell|me|about|what|is|how|does|can|you|please|why|show|give|the|a|an|and|or|so|okay|then|now|let's|lets|use|take|choose|select|pick|that|this)\b",
                "",
                last_clause,
            ).strip()
            if not clean_prefix or len(clean_prefix) < 2:
                has_its = True

        is_intra_command = bool(
            re.search(
                r"\b(?:find|search(?:\s+for)?|locate|read|check|inspect|open|view|fetch|get|create|write|make)\s+(?:the\s+)?([A-Za-z0-9_.\-/\s]+?)\s+and\s+(?:summarize|explain|show|describe|display|print|read|analyze)\s+(?:it|them)\b",
                lower,
            )
        )
        is_self_contained_task = bool(
            re.search(r"\b(?:create|write|make|generate|build)\s+(?:a|an|the)\s+(?:[a-zA-Z0-9_\-.]+\s+)?(?:file|document|script|readme|report)\b", lower)
        )
        is_idiom = bool(re.search(r"\b(?:what\s+time\s+is\s+it|is\s+it\s+possible|make\s+it\s+so|worth\s+it)\b", lower))

        has_it = False
        if not is_intra_command and not is_self_contained_task and not is_idiom:
            if re.search(r"^(?:can\s+you\s+|please\s+)?(?:explain|describe|summarize|clarify|elaborate\s+on)\s+it[?.!]?$", lower):
                has_it = True
            elif re.search(r"^(?:tell\s+me\s+(?:more\s+)?about\s+it|what\s+about\s+it)[?.!]?$", lower):
                has_it = True
            elif re.search(r"\b(?:why\s+is\s+it|how\s+does\s+it|why\s+does\s+it|where\s+is\s+it)\b", lower):
                has_it = True
            elif re.search(r"\babout\s+it[?.!]?$", lower):
                has_it = True

        is_anaphoric = has_that or has_its or has_it
        is_workspace_op = any(
            w in lower for w in ("workspace", "desktop", "process", "terminal", "echo", "active application")
        )

        if is_anaphoric and not is_workspace_op:
            if not self.active_entity and not self.active_topic:
                ref_word = "that" if has_that else ("its" if has_its else "it")
                return ResolutionResult(
                    resolved_query=query,
                    is_ambiguous=True,
                    clarification_message=f"What does '{ref_word}' refer to here? Could you please specify the topic or entity?",
                )

            entity_to_use = self.active_entity or self.active_topic
            resolved = query
            ref_map = {}

            if has_that:
                resolved = re.sub(
                    r"\b(?:let's\s+use|use|take|choose|select)\s+that\b",
                    f"use {entity_to_use}",
                    resolved,
                    flags=re.IGNORECASE,
                )
                resolved = re.sub(
                    r"\b(?:explain|describe|tell\s+me\s+about)\s+that\b",
                    f"explain {entity_to_use}",
                    resolved,
                    flags=re.IGNORECASE,
                )
                ref_map["that"] = entity_to_use

            if has_its:
                resolved = re.sub(
                    r"\bits\s+([a-zA-Z0-9_\-]+)\b",
                    rf"{entity_to_use} \1",
                    resolved,
                    flags=re.IGNORECASE,
                )
                ref_map["its"] = entity_to_use

            if has_it:
                resolved = re.sub(
                    r"\b(?:explain|about|for|understand|summarize)\s+it\b",
                    lambda m: m.group(0)[:-2] + entity_to_use,
                    resolved,
                    flags=re.IGNORECASE,
                )
                resolved = re.sub(
                    r"\bwhy\s+is\s+it\b",
                    f"why is {entity_to_use}",
                    resolved,
                    flags=re.IGNORECASE,
                )
                resolved = re.sub(
                    r"\bhow\s+does\s+it\b",
                    f"how does {entity_to_use}",
                    resolved,
                    flags=re.IGNORECASE,
                )
                ref_map["it"] = entity_to_use

            return ResolutionResult(
                resolved_query=resolved,
                intent_type=IntentType.EXPLANATION,
                active_topic=self.active_topic,
                active_entity=self.active_entity,
                active_entities=self.active_entities,
                was_modified=(resolved != query),
                reference_mapping=ref_map,
            )

        # 10. Non-anaphoric query: check for independent topic proposal or substantive topic
        proposal_match = re.search(r"^(?:what\s+about|how\s+about)\s+([a-zA-Z0-9_\-\s]+?)[?.!]?$", query, re.IGNORECASE)
        if proposal_match:
            proposed = _clean_concept(proposal_match.group(1))
            self.active_entity = proposed
            self.active_entities = [proposed]
            self.entities["it"] = proposed
            self.entities["that"] = proposed
            self._update_topic(proposed)
            return ResolutionResult(
                resolved_query=query,
                intent_type=IntentType.EXPLANATION,
                active_topic=self.active_topic,
                active_entity=self.active_entity,
                active_entities=self.active_entities,
                was_modified=False,
            )

        inferred = self._infer_topic(query)
        if inferred and inferred.lower() != (self.active_topic or "").lower():
            self._update_topic(inferred)
            self.active_entity = inferred
            self.active_entities = [inferred]
            self.entities["it"] = inferred
            self.entities["that"] = inferred

        return ResolutionResult(
            resolved_query=query,
            intent_type=IntentType.GENERAL,
            active_topic=self.active_topic,
            active_entity=self.active_entity,
            active_entities=self.active_entities,
            was_modified=False,
        )

    def get_relevant_context_summary(self, max_turns: int = 3) -> str:
        """
        Returns a concise, bounded summary of recent conversational state
        for inclusion in planner context without unbounded memory dumps.
        """
        items = []
        if self.active_topic:
            items.append(f"Active Topic: {self.active_topic}")
        if self.comparison_state:
            items.append(f"Active Comparison: {self.comparison_state.concept_a} vs {self.comparison_state.concept_b}")
        if self.active_goal:
            items.append(f"Active Goal: {self.active_goal}")
        if self.active_entity:
            items.append(f"Active Entity: {self.active_entity}")
        if self.last_artifact:
            fname = self.last_artifact.get("file_name", "unknown")
            items.append(f"Last Generated Artifact: {fname}")

        recent = self.turns[-max_turns:]
        if recent:
            recent_turns_str = "; ".join(f"Turn {t.turn_index}: '{t.user_query}' -> '{t.response_text[:40]}...'" for t in recent)
            items.append(f"Recent History: [{recent_turns_str}]")

        return "\n".join(items)

    def _update_topic(self, new_topic: str) -> None:
        """Updates the active topic and topic history, avoiding immediate duplicates."""
        clean_topic = new_topic.strip()
        self.active_topic = clean_topic
        if not self.topic_history or self.topic_history[-1].lower() != clean_topic.lower():
            self.topic_history.append(clean_topic)

    def _extract_key_entities(self, text: str) -> List[str]:
        """Heuristically extracts key named entities, acronyms, and technical terms."""
        entities = []
        acronyms = re.findall(r"\b[A-Z]{2,6}\b", text)
        for acr in acronyms:
            if acr not in entities and acr not in ("ORBIS", "README", "DAG", "LLM", "API"):
                entities.append(acr)

        title_case_terms = re.findall(r"\b(?:[A-Z][a-z]+\s+)+[A-Z][a-z]+\b", text)
        for term in title_case_terms:
            if term not in entities:
                entities.append(term)

        m = re.search(r"\b(?:explain|compare|understand|about|with)\s+([a-zA-Z0-9_\-]+)\b", text, re.IGNORECASE)
        if m:
            cand = m.group(1).strip()
            if cand.lower() not in ("it", "that", "this", "them", "both", "a", "an", "the", "me", "you"):
                if cand not in entities:
                    entities.append(cand)

        return entities

    def _infer_topic(self, text: str) -> Optional[str]:
        """Infers the primary topic of a prompt if it represents a substantive subject."""
        lower = text.lower().strip()

        m = re.search(
            r"^(?:explain|describe|tell\s+me\s+about|what\s+is|what\s+are)\s+(?:the\s+)?([a-zA-Z0-9_\-\s]+?)[?.!]?$",
            text,
            re.IGNORECASE,
        )
        if m:
            candidate = m.group(1).strip()
            if candidate and candidate.lower() not in (
                "it", "that", "this", "something", "anything",
                "your capability", "your capabilities", "capabilities", "what you do", "what all you do"
            ):
                return candidate

        proj_match = re.search(
            r"\b(?:build|create|work\s+on|help(?:\s+me)?\s+with|assist(?:\s+with)?)\s+(?:a\s+)?([a-zA-Z0-9_\-\s]+?\s+project)\b",
            text,
            re.IGNORECASE,
        )
        if proj_match:
            return proj_match.group(1).strip()

        acronyms = re.findall(r"\b[A-Z]{2,6}\b", text)
        for acr in acronyms:
            if acr not in ("ORBIS", "README", "DAG", "LLM", "API"):
                return acr

        return None

    def _extract_goal(self, text: str) -> Optional[str]:
        """Extracts declared user goals from prompt."""
        m = re.search(r"\b(?:i\s+want\s+to|i'd\s+like\s+to|goal\s+is\s+to|looking\s+to)\s+([^,.;]+)", text, re.IGNORECASE)
        if m:
            return m.group(0).strip()
        return None
