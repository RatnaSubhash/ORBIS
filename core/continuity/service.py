"""
Continuity Service for ORBIS Phase 5.
Coordinates continuity query classification, retrieval across SQLite,
Knowledge Graph, and Semantic Vector Memory, and grounded response synthesis.
"""

from datetime import datetime
import re
from typing import Optional, Union

from core.continuity.grounding import GroundedResponseBuilder
from core.continuity.models import (
    ContinuityQuery,
    ContinuityQueryType,
    ContinuityResponse,
    ProvenanceSource,
)
from core.continuity.retriever import ContinuityRetriever
from core.memory.db import DatabaseManager
from core.memory.graph.graph import KnowledgeGraph
from core.memory.vector.memory import SemanticMemory


class ContinuityService:
    """
    Main orchestration service for ORBIS Phase 5 continuity requests.
    Decoupled from future general-purpose agent planners.
    """

    def __init__(
        self,
        db: DatabaseManager,
        graph: Optional[KnowledgeGraph] = None,
        vector_memory: Optional[SemanticMemory] = None,
        retriever: Optional[ContinuityRetriever] = None,
        response_builder: Optional[GroundedResponseBuilder] = None,
    ) -> None:
        self.db = db
        self.graph = graph
        self.vector_memory = vector_memory
        self.retriever = retriever or ContinuityRetriever(
            db=db, graph=graph, vector_memory=vector_memory
        )
        self.response_builder = response_builder or GroundedResponseBuilder()

    CONTINUITY_GRAMMAR_PATTERN = re.compile(
        r"(?:"
        r"\b(?:what|which)\s+(?:apps?|applications?|files?|windows?|projects?|programs?|tools?|codebases?|repos?|repositories?|software)?\s*(?:was\s+i|did\s+i|i\s+was|i\s+did|i\s+worked|i\s+opened|i\s+touched|have\s+i)\s*(?:doing|working(?:\s+on|\s+with)?|using|use|running|looking\s+at|on|editing)?\b"
        r"|\bwhat\s+(?:am\s+i|i\s+am)\s+working\s+on\b"
        r"|\b(?:doing|working\s+on|working\s+with|worked\s+on)\s+(?:earlier|before|previously|last|yesterday|today)\b"
        r"|\b(?:where\s+(?:did\s+i|i)\s+leave\s+off|continue\s+where\s+i\s+left\s+off)\b"
        r"|\b(?:last|previous|recent)\s+(?:session|workspace|context)\b"
        r"|\bshow\s+recent\b"
        r"|\b(?:what|which)\s+(?:files?|apps?|applications?|projects?|codebases?|programs?|tools?|software)\s+(?:did\s+i|was\s+i|i\s+did|i\s+was)\s+(?:open|touch|using|use|working\s+with|on)\b"
        r"|\b(?:tell\s+me|show\s+me)\s+(?:what|which)\s+(?:project|files?|apps?|software|tools?)\b"
        r"|\bwhat\s+changed\s+(?:today|yesterday)?\b"
        r"|\bwhich\s+project\s+(?:was\s+active|is\s+active)\b"
        r"|\bwhich\s+files?\s+(?:did\s+i\s+use|were\s+involved)\b"
        r"|\bfiles?\s+involved\s+in\s+(?:yesterday'?s?|today'?s?)\s+work\b"
        r"|\bwhat\s+was\s+i\s+working\s+on\s+yesterday\b"
        r")",
        re.IGNORECASE,
    )

    INTENT_GRAMMAR_PATTERN = re.compile(
        r"(?:"
        r"\b(?:what\s+(?:was\s+i|i\s+was)\s+trying\s+to\s+(?:do|accomplish)|trying\s+to\s+accomplish)\b"
        r"|\b(?:what\s+(?:should|can)\s+i\s+(?:probably\s+)?do\s+next|what\s+to\s+do\s+next)\b"
        r"|\b(?:next\s+(?:action|step)|suggest\s+next|candidate\s+next|intent\s+continuity)\b"
        r"|\b(?:what\s+remains|unfinished\s+work|unfinished\s+tasks?|what\s+task\s+was\s+unfinished)\b"
        r")",
        re.IGNORECASE,
    )

    @classmethod
    def is_continuity_query(cls, query: str) -> bool:
        """
        Determines whether a user query is asking for workspace continuity context.
        Supports both direct questions ("What was I doing earlier?") and
        indirect / embedded questions ("Can you tell me what I was doing earlier?").
        Uses structural grammatical pattern matching without relying on ungrounded LLM guessing.
        """
        if not query or not query.strip():
            return False

        q = query.strip().lower()
        if cls.CONTINUITY_GRAMMAR_PATTERN.search(q):
            return True

        patterns = [
            "what was i working on",
            "what was i doing",
            "what did i work on",
            "what am i working on",
            "what was i doing earlier",
            "last session",
            "previous session",
            "recent workspace",
            "recent context",
            "show recent",
            "where did i leave off",
            "where i left off",
            "continue where i left off",
            "workspace context",
            "what apps was i using",
            "what applications was i using",
            "what app was i using",
            "what apps were open",
            "which apps were open",
            "what applications were open",
            "what apps were running",
            "which files was i working with",
            "what files was i working with",
            "what files was i working on",
            "what files did i open",
            "which files did i touch",
            "which project was i working on",
            "what project was i working on",
            "what project was i on",
            "which project was i on",
            "which project was active",
            "what project was active",
            "which files did i use",
            "what files did i use",
            "what changed today",
            "what changed yesterday",
            "what was i working on yesterday",
            "yesterday's session",
            "yesterday's work",
            "files involved in yesterday's work",
            "files involved",
        ]
        return any(p in q for p in patterns)

    @classmethod
    def is_intent_query(cls, query: str) -> bool:
        """
        Determines whether a user query is asking for forward-looking intent,
        unfinished work, or next actions (Phase 9 ICE).
        Supports both direct and indirect embedded question forms.
        """
        if not query or not query.strip():
            return False

        q = query.strip().lower()
        if cls.INTENT_GRAMMAR_PATTERN.search(q):
            return True

        patterns = [
            "trying to accomplish",
            "what was i trying to do",
            "what was i trying to accomplish",
            "unfinished",
            "what remains",
            "what should i do next",
            "what should i probably do next",
            "what to do next",
            "next action",
            "next step",
            "suggest next",
            "candidate next",
            "intent continuity",
            "what task was unfinished",
            "what task is unfinished",
            "unfinished work",
            "unfinished tasks",
        ]
        return any(p in q for p in patterns)

    @classmethod
    def classify_query(cls, query_text: str) -> ContinuityQuery:
        """
        Dimension-based classification of continuity requests.
        Categorizes intent into APP, PROJECT, FILE, or GENERAL continuity
        using structural dimensional analysis rather than a brittle exact-phrase dictionary.
        Extracts temporal boundaries (e.g. yesterday, today).
        """
        clean_text = query_text.strip() if query_text else ""
        q_lower = clean_text.lower()

        # Dimension matching
        is_project_dimension = bool(
            re.search(r"\b(project|projects|repo|repos|repository|repositories|codebase|workspace)\b", q_lower)
        )
        is_file_dimension = bool(
            re.search(r"\b(file|files|document|documents|script|scripts|filename|filenames|buffer|buffers)\b", q_lower)
        )
        is_app_dimension = bool(
            re.search(r"\b(app|apps|application|applications|program|programs|software|tool|tools)\b", q_lower)
        )

        # Temporal boundary extraction
        start_time = None
        end_time = None
        now = datetime.now()
        local_tz = datetime.now().astimezone().tzinfo or datetime.now(timezone.utc).tzinfo

        if re.search(r"\byesterday\b", q_lower):
            from datetime import timedelta
            yesterday = now - timedelta(days=1)
            start_time = datetime(yesterday.year, yesterday.month, yesterday.day, 0, 0, 0, tzinfo=local_tz)
            end_time = datetime(yesterday.year, yesterday.month, yesterday.day, 23, 59, 59, 999999, tzinfo=local_tz)
        elif re.search(r"\btoday\b", q_lower):
            start_time = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=local_tz)
            end_time = now.astimezone(local_tz)

        if "what changed" in q_lower or "changes" in q_lower:
            q_type = ContinuityQueryType.WHAT_CHANGED_TODAY
            max_res = 5
        elif "last session" in q_lower or "previous session" in q_lower:
            q_type = ContinuityQueryType.LAST_SESSION
            max_res = 1
        elif "recent context" in q_lower or "recent workspace" in q_lower or "show recent" in q_lower:
            q_type = ContinuityQueryType.RECENT_CONTEXT
            max_res = 3
        elif (is_project_dimension and not is_file_dimension and not is_app_dimension) or "project was active" in q_lower:
            q_type = ContinuityQueryType.PROJECT_QUERY
            max_res = 1
        elif (is_file_dimension and not is_project_dimension and not is_app_dimension) or "files did i use" in q_lower or "files were involved" in q_lower:
            q_type = ContinuityQueryType.FILE_QUERY
            max_res = 1
        elif is_app_dimension and not is_project_dimension and not is_file_dimension:
            q_type = ContinuityQueryType.APP_QUERY
            max_res = 1
        elif any(k in q_lower for k in ("working on", "doing", "did i work", "am i working", "leave off", "left off")):
            q_type = ContinuityQueryType.WHAT_WAS_I_WORKING_ON
            max_res = 1
        elif is_project_dimension:
            q_type = ContinuityQueryType.PROJECT_QUERY
            max_res = 1
        elif is_file_dimension:
            q_type = ContinuityQueryType.FILE_QUERY
            max_res = 1
        elif is_app_dimension:
            q_type = ContinuityQueryType.APP_QUERY
            max_res = 1
        else:
            q_type = ContinuityQueryType.GENERAL_CONTINUITY
            max_res = 3

        return ContinuityQuery(
            query_text=clean_text,
            query_type=q_type,
            start_time=start_time,
            end_time=end_time,
            max_results=max_res,
        )

    def execute_query(
        self,
        query: Union[str, ContinuityQuery],
        cutoff_time: Optional[datetime] = None,
    ) -> ContinuityResponse:
        """
        Executes an end-to-end continuity query turn.
        Retrieves evidence and synthesizes a grounded response.
        """
        if isinstance(query, str):
            if not query or not query.strip():
                return ContinuityResponse(
                    query=query or "",
                    has_sufficient_context=False,
                    grounded_summary="Empty continuity query provided. Context is unavailable.",
                    active_applications=[],
                    primary_resources=[],
                    last_activity_timestamp=None,
                    confidence_score=0.0,
                    evidence=[],
                    provenance=ProvenanceSource(),
                )
            structured_query = self.classify_query(query)
            if cutoff_time is not None:
                structured_query.cutoff_time = cutoff_time
        else:
            structured_query = query
            if cutoff_time is not None and structured_query.cutoff_time is None:
                structured_query.cutoff_time = cutoff_time
            if not structured_query.query_text or not structured_query.query_text.strip():
                return ContinuityResponse(
                    query="",
                    has_sufficient_context=False,
                    grounded_summary="Empty continuity query provided. Context is unavailable.",
                    active_applications=[],
                    primary_resources=[],
                    last_activity_timestamp=None,
                    confidence_score=0.0,
                    evidence=[],
                    provenance=ProvenanceSource(),
                )

        evidence_list = self.retriever.retrieve(structured_query)
        return self.response_builder.build_response(
            query=structured_query.query_text,
            evidence_list=evidence_list,
            query_type=structured_query.query_type,
        )
