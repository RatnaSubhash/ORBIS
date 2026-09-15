"""
Conversational Continuity Layer for ORBIS.
Provides bounded multi-turn conversation context, deterministic reference resolution,
topic switching and returning, active entity tracking, and safe ambiguity detection.
Preserves the principle that conversational context is untrusted input and must never
bypass planning, validation, risk assessment, or human-in-the-loop approval.
"""

from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4
from pydantic import BaseModel, Field

logger = logging.getLogger("orbis.agent.conversation")


class ConversationTurn(BaseModel):
    """Factual record of an individual conversational turn."""
    turn_id: str = Field(default_factory=lambda: uuid4().hex)
    turn_index: int
    user_query: str
    resolved_query: str
    response_text: str
    topic: Optional[str] = None
    entities: List[str] = Field(default_factory=list)
    tool_name: Optional[str] = None
    tool_result_summary: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResolutionResult(BaseModel):
    """Outcome of resolving anaphoric references or topic continuity in a user query."""
    resolved_query: str
    active_topic: Optional[str] = None
    active_entity: Optional[str] = None
    is_ambiguous: bool = False
    clarification_message: Optional[str] = None
    was_modified: bool = False
    reference_mapping: Dict[str, str] = Field(default_factory=dict)


class ConversationContext(BaseModel):
    """
    Bounded ring buffer and state tracker for multi-turn conversational continuity.
    Retains recent relevant turns, active topic, active goal, important entities,
    and recently created artifacts.
    """
    max_turns: int = Field(default=10, ge=2, le=50)
    turns: List[ConversationTurn] = Field(default_factory=list)
    active_topic: Optional[str] = None
    active_goal: Optional[str] = None
    active_entity: Optional[str] = None
    entities: Dict[str, str] = Field(default_factory=dict)
    topic_history: List[str] = Field(default_factory=list)
    last_artifact: Optional[Dict[str, Any]] = None

    def clear(self) -> None:
        """Resets the conversational context."""
        self.turns.clear()
        self.active_topic = None
        self.active_goal = None
        self.active_entity = None
        self.entities.clear()
        self.topic_history.clear()
        self.last_artifact = None

    def record_turn(
        self,
        user_query: str,
        resolved_query: str,
        response_text: str,
        tool_name: Optional[str] = None,
        tool_result_summary: Optional[str] = None,
        artifact: Optional[Dict[str, Any]] = None,
    ) -> ConversationTurn:
        """
        Records a completed conversational turn into the bounded history and
        updates active topics, goals, and entities.
        """
        turn_index = len(self.turns) + 1

        # Extract and update active entities from the query and response
        extracted_entities = self._extract_key_entities(resolved_query)

        # Infer topic from query if not already tracked
        inferred_topic = self._infer_topic(resolved_query)
        if inferred_topic:
            self._update_topic(inferred_topic)

        # Update active goal if query expresses an intent or project objective
        goal = self._extract_goal(resolved_query)
        if goal:
            self.active_goal = goal

        # Update active entity if a prominent entity was mentioned
        if extracted_entities:
            self.active_entity = extracted_entities[0]
            for ent in extracted_entities:
                key = ent.lower()
                self.entities[key] = ent
                self.entities["it"] = ent
                self.entities["that"] = ent

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
            tool_name=tool_name,
            tool_result_summary=tool_result_summary,
        )

        self.turns.append(turn)

        # Enforce bounded ring buffer
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns :]

        return turn

    def resolve_query(self, raw_query: str) -> ResolutionResult:
        """
        Deterministically resolves pronouns, anaphoric references, and topic returns
        in raw_query against active conversation state.
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

        # 1. Check for explicit Topic Return pattern
        # e.g., "Coming back to TCP, why is it reliable?", "Returning to HSRP, explain...", "Back to TCP..."
        return_match = re.search(
            r"\b(?:coming\s+back\s+to|returning\s+to|back\s+to|let's\s+return\s+to|going\s+back\s+to)\s+([a-zA-Z0-9_\-\s]+?)(?:[,.]|$|\bwhy\b|\bhow\b|\bwhat\b|\bexplain\b|\bcan\b)",
            query,
            re.IGNORECASE,
        )
        if return_match:
            target_topic = return_match.group(1).strip()
            # Match against topic history
            matched_history_topic = None
            for prior in reversed(self.topic_history):
                if target_topic.lower() in prior.lower() or prior.lower() in target_topic.lower():
                    matched_history_topic = prior
                    break

            effective_topic = matched_history_topic or target_topic
            self._update_topic(effective_topic)
            self.active_entity = effective_topic
            self.entities["it"] = effective_topic
            self.entities["that"] = effective_topic

            # Resolve references within this query
            resolved = query
            resolved = re.sub(
                r"\bwhy\s+is\s+it\b",
                f"why is {effective_topic}",
                resolved,
                flags=re.IGNORECASE,
            )
            resolved = re.sub(
                r"\bhow\s+does\s+it\b",
                f"how does {effective_topic}",
                resolved,
                flags=re.IGNORECASE,
            )
            resolved = re.sub(
                r"\bexplain\s+it\b",
                f"explain {effective_topic}",
                resolved,
                flags=re.IGNORECASE,
            )
            return ResolutionResult(
                resolved_query=resolved,
                active_topic=self.active_topic,
                active_entity=self.active_entity,
                was_modified=(resolved != query),
                reference_mapping={"topic_return": effective_topic},
            )

        # 2. Check for Reference to Last Created Artifact
        # e.g. "summarize the file you just created", "what is in the file we made?", "show me the recommendations"
        artifact_ref_match = re.search(
            r"\b(?:the\s+file\s+you\s+(?:just\s+)?(?:created|made|wrote)|the\s+document\s+we\s+made|the\s+created\s+file)\b",
            lower,
        )
        if artifact_ref_match and self.last_artifact:
            file_name = self.last_artifact.get("file_name") or self.last_artifact.get("file_path")
            if file_name:
                resolved = query[: artifact_ref_match.start()] + file_name + query[artifact_ref_match.end() :]
                return ResolutionResult(
                    resolved_query=resolved,
                    active_topic=self.active_topic,
                    active_entity=file_name,
                    was_modified=True,
                    reference_mapping={"artifact": file_name},
                )

        # 3. Check for Entity Proposal / Follow-Up Query
        # e.g. "What about HSRP?", "How about TCP?", "What about a chat application?"
        proposal_match = re.search(r"^(?:what\s+about|how\s+about)\s+([a-zA-Z0-9_\-\s]+?)[?.!]?$", query, re.IGNORECASE)
        if proposal_match:
            proposed_entity = proposal_match.group(1).strip()
            self.active_entity = proposed_entity
            self.entities["it"] = proposed_entity
            self.entities["that"] = proposed_entity
            self._update_topic(proposed_entity)
            return ResolutionResult(
                resolved_query=query,
                active_topic=self.active_topic,
                active_entity=self.active_entity,
                was_modified=False,
            )

        # 4. Check for Pronouns / Anaphoric References
        # Only treat as anaphoric if the reference is truly dangling across turns
        has_that = bool(
            re.search(r"\b(?:let's\s+)?(?:use|take|choose|select|pick|go\s+with)\s+that\b", lower)
            or re.search(r"^(?:okay,?\s*)?(?:let's\s+)?(?:use|take|choose|select|pick|go\s+with)\s+that[?.!]?$", lower)
            or re.search(r"^(?:what|how)\s+about\s+that[?.!]?$", lower)
            or re.search(r"^(?:explain|describe|tell\s+me\s+about)\s+that[?.!]?$", lower)
        )

        its_match = re.search(r"\bits\s+([a-zA-Z0-9_\-]+)\b", lower)
        has_its = False
        if its_match:
            prefix = lower[: its_match.start()].strip()
            # Check the clause immediately preceding "its"
            last_clause = re.split(r"[.!?;\n]", prefix)[-1].strip()
            clean_prefix = re.sub(
                r"\b(?:explain|tell|me|about|what|is|how|does|can|you|please|why|show|give|the|a|an|and|or|so|okay|then|now|let's|lets|use|take|choose|select|pick|that|this)\b",
                "",
                last_clause,
            ).strip()
            if not clean_prefix or len(clean_prefix) < 2:
                has_its = True

        # Check for intra-sentence commands or self-contained tasks
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

        # Check if query is an unrelated operational request
        is_workspace_op = any(
            w in lower for w in ("workspace", "desktop", "process", "terminal", "echo", "active application")
        )

        if is_anaphoric and not is_workspace_op:
            # If no active entity or topic exists, detect ambiguity rather than guessing
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

            # Handle "let's use that" / "use that"
            if has_that:
                resolved = re.sub(
                    r"\b(?:let's\s+use|use|take|choose|select)\s+that\b",
                    f"use {entity_to_use}",
                    resolved,
                    flags=re.IGNORECASE,
                )
                ref_map["that"] = entity_to_use

            # Handle "its <feature/component>" e.g. "its architecture", "its protocol", "its design"
            if has_its:
                resolved = re.sub(
                    r"\bits\s+([a-zA-Z0-9_\-]+)\b",
                    rf"{entity_to_use} \1",
                    resolved,
                    flags=re.IGNORECASE,
                )
                ref_map["its"] = entity_to_use

            # Handle "explain it", "tell me about it", "why is it ..."
            if has_it:
                if not re.search(r"\btime\s+is\s+it\b", lower):
                    resolved = re.sub(
                        r"\b(?:explain|about|for|understand|summarize)\s+it\b",
                        rf"\g<0>".replace("it", entity_to_use),
                        resolved,
                        flags=re.IGNORECASE,
                    )
                    resolved = re.sub(
                        r"\bwhy\s+is\s+it\b",
                        f"why is {entity_to_use}",
                        resolved,
                        flags=re.IGNORECASE,
                    )
                    ref_map["it"] = entity_to_use

            return ResolutionResult(
                resolved_query=resolved,
                active_topic=self.active_topic,
                active_entity=self.active_entity,
                was_modified=(resolved != query),
                reference_mapping=ref_map,
            )

        # 5. Non-anaphoric query: check for independent topic switch
        inferred = self._infer_topic(query)
        if inferred and inferred.lower() != (self.active_topic or "").lower():
            self._update_topic(inferred)
            self.active_entity = inferred
            self.entities["it"] = inferred
            self.entities["that"] = inferred

        return ResolutionResult(
            resolved_query=query,
            active_topic=self.active_topic,
            active_entity=self.active_entity,
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

        return entities

    def _infer_topic(self, text: str) -> Optional[str]:
        """Infers the primary topic of a prompt if it represents a substantive subject."""
        lower = text.lower().strip()

        m = re.search(r"^(?:explain|describe|tell\s+me\s+about|what\s+is|what\s+are)\s+(?:the\s+)?([a-zA-Z0-9_\-\s]+?)[?.!]?$", text, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            if candidate and candidate.lower() not in ("it", "that", "this", "something", "anything"):
                return candidate

        proj_match = re.search(r"\b(?:build|create|work\s+on)\s+(?:a\s+)?([a-zA-Z0-9_\-\s]+?\s+project)\b", text, re.IGNORECASE)
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
