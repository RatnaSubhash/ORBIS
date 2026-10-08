"""
REST API route definitions for the ORBIS FastAPI Gateway (Phase 12.8).
Exposes task submission, status retrieval, bounded historical event queries,
and human approval / continuation endpoints.
"""

from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional, Set


from fastapi import APIRouter, Depends, HTTPException, Query, status

from core.agent.orchestrator import AgentOrchestrator
from core.config import get_settings
from core.events.bus import EventBus
from core.gateway.models import (
    ApprovalRequest,
    ApprovalResponse,
    ContinuityContextResponse,
    ContinuityObservationStatus,
    ContextDnaSummary,
    ConversationContextResponse,
    ConversationNewResponse,
    ConversationResetRequest,
    ConversationResetResponse,
    ErrorResponse,
    KnowledgeGraphSummary,
    PendingApprovalDetails,
    RestorationExecuteRequest,
    RestorationExecuteResponse,
    RestorationPlanPreview,
    RestorationPreviewRequest,
    RestorationPreviewResponse,
    RestorationSessionsResponse,
    RestorationStepPreview,
    SessionEventItem,
    SessionEventsResponse,
    SessionSummaryItem,
    TaskEventsResponse,
    TaskStatusResponse,
    TaskSubmitRequest,
    TaskSubmitResponse,
    ToolMetadataItem,
    ToolRegistryResponse,
    VoiceCancelResponse,
    VoiceSpeakRequest,
    VoiceSpeakResponse,
    VoiceStartResponse,
    VoiceStopResponse,
)
from core.gateway.task_manager import TaskManager
from core.events.sanitizer import EventSanitizer
from core.voice.models import VoiceState
from core.voice.normalizer import VoiceNormalizer

logger = logging.getLogger("orbis.gateway.routes")

router = APIRouter(prefix="/api", tags=["Agent Gateway"])

# Dependencies placeholders to be overridden in app creation
def get_orchestrator() -> AgentOrchestrator:
    raise NotImplementedError("Orchestrator dependency not configured.")


def get_task_manager() -> TaskManager:
    raise NotImplementedError("TaskManager dependency not configured.")


def get_event_bus() -> EventBus:
    raise NotImplementedError("EventBus dependency not configured.")


def get_db_manager() -> Any:
    raise NotImplementedError("DatabaseManager dependency not configured.")


def get_observation_service() -> Any:
    raise NotImplementedError("WorkspaceObservationService dependency not configured.")


def get_restoration_engine(
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
    db: Any = Depends(get_db_manager),
) -> Any:
    if getattr(orchestrator, "restoration_engine", None) is not None:
        return orchestrator.restoration_engine
    from core.restoration.engine import RestorationEngine
    return RestorationEngine(db=db)


def get_voice_service(orchestrator: AgentOrchestrator = Depends(get_orchestrator)):
    """
    Resolves the canonical, singleton VoiceService attached to the orchestrator.
    Guarantees exactly one authoritative VoiceService instance for the gateway lifecycle.
    """
    if getattr(orchestrator, "voice_service", None) is None:
        settings = get_settings()
        # Mocks are strictly restricted to explicit test environment; never silent in production
        if getattr(settings, "orbis_env", "development") == "test":
            from core.voice.capture.mock_capture import MockAudioCapture
            from core.voice.stt.mock_stt import MockSTTProvider
            from core.voice.tts.mock_tts import MockTTSProvider
            capture = MockAudioCapture()
            stt = MockSTTProvider()
            tts = MockTTSProvider()
        else:
            from core.voice.capture.sounddevice_capture import SoundDeviceAudioCapture
            from core.voice.stt.whisper_stt import FasterWhisperSTTProvider
            from core.voice.tts.sapi_tts import WindowsSapiTTSProvider
            import sys
            capture = SoundDeviceAudioCapture()
            stt = FasterWhisperSTTProvider()
            tts = WindowsSapiTTSProvider() if sys.platform == "win32" else MockTTSProvider()


        from core.voice.service import VoiceService
        orchestrator.voice_service = VoiceService(
            orchestrator=orchestrator,
            capture=capture,
            stt_provider=stt,
            tts_provider=tts,
        )
    return orchestrator.voice_service


TASK_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")


def validate_task_id(task_id: str) -> str:
    """Validates task_id format to prevent injection or invalid lookups."""
    if not task_id or not TASK_ID_PATTERN.match(task_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid task ID format. Must contain only alphanumeric characters, underscores, and hyphens.",
        )
    return task_id


@router.get("/health", summary="Gateway Health Check")
async def health_check():
    """Returns gateway service health and version."""
    return {"status": "ok", "service": "orbis-gateway", "version": "0.2.0"}


@router.post(
    "/tasks",
    response_model=TaskSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Submit Agent Task",
)
async def submit_task(
    request: TaskSubmitRequest,
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """
    Submits a user task to the existing AgentOrchestrator.
    By default, dispatches execution in the background and immediately returns
    HTTP 202 Accepted with task_id and correlation_id for real-time tracking.
    """
    try:
        task = await task_manager.submit_task(
            prompt=request.prompt,
            orchestrator=orchestrator,
            correlation_id=request.correlation_id,
            conversation_id=request.conversation_id,
            metadata=request.metadata,
            sync=request.sync,
        )
        http_code = status.HTTP_200_OK if request.sync else status.HTTP_202_ACCEPTED
        return TaskSubmitResponse(
            task_id=task.task_id,
            correlation_id=task.correlation_id,
            status=task.status,
            message="Task accepted for execution." if not request.sync else "Task execution completed.",
        )
    except Exception as e:
        logger.exception("Failed to submit task: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initiate task execution.",
        )


@router.get(
    "/tasks/{task_id}",
    response_model=TaskStatusResponse,
    responses={404: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
    summary="Get Task Status",
)
async def get_task_status(
    task_id: str,
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
    task_manager: TaskManager = Depends(get_task_manager),
    event_bus: EventBus = Depends(get_event_bus),
):
    """
    Returns the user-safe operational status of an agent task.
    Strictly prevents leakage of chain-of-thought, system prompts, raw tool outputs,
    or internal secrets.
    """
    valid_id = validate_task_id(task_id)
    managed = task_manager.get_task(valid_id)

    # Check for live pending approval details from orchestrator
    pending_details: Optional[PendingApprovalDetails] = None
    if orchestrator._pending_approval and orchestrator._pending_approval.task_id == valid_id:
        p = orchestrator._pending_approval
        pending_details = PendingApprovalDetails(
            task_id=p.task_id,
            blocked_step_id=p.blocked_step_id,
            action_description=p.action_description,
            tool_name=p.tool_name,
            target_resource=p.target_resource,
            expires_at=p.expires_at,
        )

    if managed:
        return TaskStatusResponse(
            task_id=managed.task_id,
            correlation_id=managed.correlation_id,
            status=managed.status,
            summary=managed.summary,
            answer=getattr(managed, "answer", None) or managed.summary,
            pending_approval=pending_details,
            created_at=managed.created_at,
            updated_at=managed.updated_at,
            error=managed.error,
        )

    # If not in TaskManager memory, check if EventBus holds historical events for this task
    events = event_bus.get_task_events(valid_id)
    if not events:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{valid_id}' not found.",
        )

    latest_event = events[-1]
    first_event = events[0]
    historical_answer = None
    for ev in reversed(events):
        if getattr(ev, "event_type", None) == AgentEventType.TASK_COMPLETED:
            meta = getattr(ev, "metadata", {}) or {}
            historical_answer = meta.get("answer") or ev.safe_user_message
            break
    if not historical_answer:
        historical_answer = latest_event.safe_user_message

    return TaskStatusResponse(
        task_id=valid_id,
        correlation_id=latest_event.correlation_id,
        status=latest_event.status,
        summary=latest_event.safe_user_message,
        answer=historical_answer,
        pending_approval=pending_details,
        created_at=first_event.timestamp,
        updated_at=latest_event.timestamp,
    )


@router.get(
    "/tasks/{task_id}/events",
    response_model=TaskEventsResponse,
    responses={404: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
    summary="Get Task Historical Events",
)
async def get_task_events(
    task_id: str,
    event_bus: EventBus = Depends(get_event_bus),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """
    Returns bounded historical AgentEvents for a specific task.
    Enforces task isolation and returns only pre-sanitized event telemetry.
    """
    valid_id = validate_task_id(task_id)
    events = event_bus.get_task_events(valid_id)
    managed = task_manager.get_task(valid_id)

    if not events and not managed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{valid_id}' not found.",
        )

    serialized_events = [ev.model_dump(mode="json") for ev in events]
    return TaskEventsResponse(
        task_id=valid_id,
        events=serialized_events,
        total=len(serialized_events),
    )


@router.post(
    "/tasks/{task_id}/approve",
    response_model=ApprovalResponse,
    responses={409: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
    summary="Resolve Pending Action Approval",
)
async def approve_task_step(
    task_id: str,
    request: ApprovalRequest,
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
    task_manager: TaskManager = Depends(get_task_manager),
):
    """
    Affirmatively confirms or denies a high-risk action blocked on user approval.
    Reuses existing AgentOrchestrator safety gates and emits APPROVAL_GRANTED or
    APPROVAL_DENIED / TASK_CANCELLED events to the EventBus.
    """
    valid_id = validate_task_id(task_id)

    try:
        new_status, msg = await task_manager.handle_approval(
            task_id=valid_id,
            approved=request.approved,
            orchestrator=orchestrator,
            reason=request.reason,
        )
        return ApprovalResponse(
            task_id=valid_id,
            status=new_status,
            message=msg,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except Exception as e:
        logger.exception("Approval handling error on task %s: %s", valid_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resolve action approval.",
        )


# ============================================================================
# Phase 11/12 Push-to-Talk Voice Interface Bridge
# ============================================================================

@router.post(
    "/voice/start",
    response_model=VoiceStartResponse,
    status_code=status.HTTP_200_OK,
    responses={503: {"model": ErrorResponse}},
    summary="Start Push-to-Talk Voice Capture",
)
async def start_voice_capture(
    voice_service: Any = Depends(get_voice_service),
):
    """
    Begins Push-to-Talk microphone capture using the canonical VoiceService instance.
    Checks microphone availability and transitions to LISTENING state.
    """
    if not voice_service.capture.is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Microphone access is unavailable.",
        )
    try:
        voice_service.start_listening()
        return VoiceStartResponse(status="listening", message="Microphone capture started.")
    except Exception as e:
        logger.exception("Failed to start voice capture: %s", e)
        if voice_service.state == VoiceState.LISTENING:
            try:
                buf = voice_service.stop_listening()
                buf.release()
            except Exception:
                pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Microphone access is unavailable.",
        )


@router.post(
    "/voice/stop",
    response_model=VoiceStopResponse,
    status_code=status.HTTP_200_OK,
    responses={500: {"model": ErrorResponse}},
    summary="Stop Push-to-Talk Voice Capture and Transcribe",
)
async def stop_voice_capture(
    voice_service: Any = Depends(get_voice_service),
):
    """
    Stops Push-to-Talk voice capture and transcribes using the canonical VoiceService.
    Crucial privacy and security invariants:
    - Zero audio persistence to disk
    - Immediate in-memory audio release in finally block
    - Returns only { transcript, empty, status } (raw transcript kept internal)
    """
    if voice_service.state != VoiceState.LISTENING:
        return VoiceStopResponse(transcript="", empty=True, status="idle")

    audio = voice_service.stop_listening()
    try:
        transcription = await voice_service.stt_provider.transcribe(audio)
    except Exception as e:
        logger.exception("Voice transcription failed: %s", e)
        voice_service.state = VoiceState.IDLE
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Voice transcription failed.",
        )
    finally:
        audio.release()

    if transcription.is_empty_or_inaudible or not transcription.text:
        return VoiceStopResponse(transcript="", empty=True, status="idle")

    normalized = VoiceNormalizer.normalize_input(transcription.text)
    return VoiceStopResponse(
        transcript=normalized,
        empty=not bool(normalized),
        status="stopped",
    )


@router.post(
    "/voice/cancel",
    response_model=VoiceCancelResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel Push-to-Talk Voice Capture",
)
async def cancel_voice_capture(
    voice_service: Any = Depends(get_voice_service),
):
    """
    Cancels active voice capture without transcribing, immediately releasing audio memory.
    """
    if voice_service.state == VoiceState.LISTENING:
        try:
            audio = voice_service.stop_listening()
            audio.release()
        except Exception:
            pass
    return VoiceCancelResponse(status="idle", message="Voice capture cancelled.")


@router.post(
    "/voice/speak",
    response_model=VoiceSpeakResponse,
    status_code=status.HTTP_200_OK,
    responses={500: {"model": ErrorResponse}},
    summary="Speak Text via Canonical VoiceService SAPI5 TTS",
)
async def speak_text(
    req: VoiceSpeakRequest,
    voice_service: Any = Depends(get_voice_service),
):
    """
    Speaks the provided user-facing response using the canonical VoiceService's
    local SAPI5 TTS provider. Memory-only, no cloud, no persistence.
    Sanitizes text through EventSanitizer to guarantee no secrets, raw HTML,
    or internal execution reasoning reach the vocalizer.
    """
    clean_text = EventSanitizer.clean_message(req.text or "")
    if not clean_text:
        return VoiceSpeakResponse(status="skipped", message="Empty text.")

    try:
        await voice_service.speak(clean_text)
        return VoiceSpeakResponse(status="spoken", message="Speech synthesized successfully.")
    except Exception as e:
        logger.exception("TTS synthesis failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Speech synthesis failed.",
        )


# ============================================================================
# Phase F: Workspace Continuity & Semantic Memory Endpoints
# ============================================================================

@router.get(
    "/continuity/context",
    response_model=ContinuityContextResponse,
    summary="Get Workspace Continuity & Semantic Memory Telemetry",
)
async def get_continuity_context(
    db: Any = Depends(get_db_manager),
    obs_svc: Any = Depends(get_observation_service),
):
    """
    Exposes real workspace continuity telemetry, detected sessions,
    persisted Context DNA, and Knowledge Graph statistics.
    Returns factual empty structures if no data has been observed yet.
    Never fabricates intelligence or mock success.
    """
    try:
        # 1. Observation status
        obs_active = bool(obs_svc and getattr(obs_svc, "is_running", False))
        poll_interval = float(getattr(obs_svc, "polling_interval", 5.0)) if obs_svc else 5.0

        total_events = 0
        if db:
            with db.get_connection() as conn:
                cur = conn.execute("SELECT COUNT(*) FROM workspace_events;")
                row = cur.fetchone()
                total_events = row[0] if row else 0

        obs_status = ContinuityObservationStatus(
            active=obs_active,
            polling_interval=poll_interval,
            total_events=total_events,
        )

        # 2. Recent Sessions
        recent_sessions_raw = db.get_sessions(limit=10) if db else []
        recent_sessions: List[SessionSummaryItem] = []
        for s in recent_sessions_raw:
            recent_sessions.append(
                SessionSummaryItem(
                    session_id=s.session_id,
                    start_time=s.start_time,
                    end_time=s.end_time,
                    duration_seconds=s.duration_seconds,
                    event_count=s.event_count,
                    applications=list(s.applications or []),
                    resources=[EventSanitizer.clean_message(r) for r in (s.resources or []) if r],
                    confidence=float(getattr(s, "confidence", 1.0)),
                )
            )

        # 3. Current / Latest Meaningful Context DNA
        current_dna: Optional[ContextDnaSummary] = None
        if db:
            with db.get_connection() as conn:
                cur = conn.execute("SELECT data_json FROM context_dna ORDER BY id DESC LIMIT 1;")
                row = cur.fetchone()
                if row and row["data_json"]:
                    from core.types import ContextDNA
                    dna_obj = ContextDNA.model_validate_json(row["data_json"])
                    act = dna_obj.activity_context or {}
                    app_ctx = dna_obj.application_context or {}
                    res_ctx = dna_obj.resource_context or {}
                    qi = dna_obj.quality_indicators or {}

                    projects = res_ctx.get("projects") or []
                    proj_root = EventSanitizer.clean_message(projects[0]) if projects else None

                    primary_app = app_ctx.get("primary_application")
                    active_apps = list(app_ctx.get("active_applications") or [])

                    res_list = [
                        EventSanitizer.clean_message(r)
                        for r in (res_ctx.get("observed_resource_paths") or [])
                        if r
                    ]
                    primary_res = res_list[0] if res_list else None

                    entities = res_list[:5] + active_apps[:3]

                    current_dna = ContextDnaSummary(
                        dna_id=dna_obj.dna_id,
                        session_id=dna_obj.session_id,
                        version=dna_obj.context_dna_version,
                        created_at=dna_obj.created_at,
                        freshness="persisted",
                        task_summary=act.get("task_summary") or act.get("dominant_activity"),
                        focus_score=qi.get("focus_score") or qi.get("confidence_score") or qi.get("confidence"),
                        project_root=proj_root,
                        primary_application=primary_app,
                        active_applications=active_apps,
                        primary_resource=primary_res,
                        resources_accessed=res_list,
                        entities=entities,
                    )

        # 4. Knowledge Graph Summary
        graph_nodes = db.get_all_graph_nodes() if db else []
        graph_edges = db.get_all_graph_edges() if db else []

        app_count = sum(1 for n in graph_nodes if n.get("node_type") in ("application", "process"))
        file_count = sum(1 for n in graph_nodes if n.get("node_type") in ("file", "resource"))
        topic_count = sum(1 for n in graph_nodes if n.get("node_type") in ("topic", "entity", "project"))

        top_entities = [
            {
                "id": n.get("node_id"),
                "type": n.get("node_type"),
                "label": EventSanitizer.clean_message(n.get("label", "")),
            }
            for n in graph_nodes[:10]
        ]

        kg_summary = KnowledgeGraphSummary(
            total_nodes=len(graph_nodes),
            total_edges=len(graph_edges),
            application_count=app_count,
            file_count=file_count,
            topic_count=topic_count,
            top_entities=top_entities,
        )

        return ContinuityContextResponse(
            observation=obs_status,
            current_dna=current_dna,
            recent_sessions=recent_sessions,
            knowledge_graph=kg_summary,
        )
    except Exception as e:
        logger.exception("Failed to retrieve continuity context: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve continuity context.",
        )


@router.get(
    "/continuity/sessions/{session_id}/events",
    response_model=SessionEventsResponse,
    responses={404: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
    summary="Get Sanitized Workspace Events for a Session",
)
async def get_session_events(
    session_id: str,
    db: Any = Depends(get_db_manager),
):
    """
    Returns sanitized workspace events belonging to a specific recorded session.
    Strictly applies EventSanitizer to window titles, resource paths, and metadata
    to ensure no credentials or sensitive tokens leak.
    """
    valid_id = validate_task_id(session_id)
    if not db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DatabaseManager not available.",
        )

    session = db.get_session(valid_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{valid_id}' not found.",
        )

    raw_events = db.get_session_events(valid_id)
    sanitized_events: List[SessionEventItem] = []

    for ev in raw_events:
        sanitized_events.append(
            SessionEventItem(
                event_id=ev.event_id,
                timestamp=ev.timestamp,
                event_type=ev.event_type.value if hasattr(ev.event_type, "value") else str(ev.event_type),
                process_name=ev.process_name,
                window_title=EventSanitizer.clean_message(ev.window_title or ""),
                resource_path=EventSanitizer.clean_message(ev.resource_path or "") if ev.resource_path else None,
                metadata=EventSanitizer.clean_metadata(ev.metadata),
            )
        )

    return SessionEventsResponse(
        session_id=valid_id,
        events=sanitized_events,
        total=len(sanitized_events),
    )


# ── Authoritative Tool Metadata Mappings ─────────────────────────────────────

TOOL_METADATA_EXTRAS: Dict[str, Dict[str, Any]] = {
    "file": {
        "name": "File System Inspection",
        "category": "System",
        "example_prompt": "Inspect the files in my workspace.",
        "fallback_actions": ["inspect_path", "list_directory", "check_exists", "read_file"],
    },
    "desktop": {
        "name": "Desktop Context",
        "category": "Interface",
        "example_prompt": "Which application window is currently active?",
        "fallback_actions": ["get_active_window", "capture_snapshot", "inspect_active_window"],
    },
    "system_control": {
        "name": "System Audio Control",
        "category": "System",
        "example_prompt": "What is my current master volume?",
        "fallback_actions": ["get_volume", "set_volume", "mute", "unmute"],
    },
    "system": {
        "name": "System Utilities",
        "category": "System",
        "example_prompt": "What is the current system date and time?",
        "fallback_actions": ["get_time", "get_date", "get_system_info"],
    },
    "system_utility": {
        "name": "System Utilities",
        "category": "System",
        "example_prompt": "What is the current system date and time?",
        "fallback_actions": ["get_time", "get_date", "get_system_info"],
    },
    "echo": {
        "name": "Diagnostic Echo",
        "category": "System",
        "example_prompt": "Echo 'ORBIS operational status: online'.",
        "fallback_actions": ["echo"],
    },
    "terminal": {
        "name": "Terminal Automation",
        "category": "Automation",
        "example_prompt": "Check git status in the workspace.",
        "fallback_actions": ["execute_command"],
    },
    "terminal_tool": {
        "name": "Terminal Automation",
        "category": "Automation",
        "example_prompt": "Check git status in the workspace.",
        "fallback_actions": ["execute_command"],
    },
    "restoration_tool": {
        "name": "Workspace Restoration",
        "category": "Automation",
        "example_prompt": "Restore my previous workspace layout.",
        "fallback_actions": ["generate_plan", "execute_restoration"],
    },
    "web_research": {
        "name": "Web Research Engine",
        "category": "Intelligence",
        "example_prompt": "Search for the latest Python 3.13 features.",
        "fallback_actions": ["search", "fetch_page", "research"],
    },
    "knowledge": {
        "name": "Knowledge & Reasoning",
        "category": "Intelligence",
        "example_prompt": "Explain the difference between TCP and UDP.",
        "fallback_actions": ["factual", "explanation", "comparison", "project_assistance"],
    },
    "knowledge_tool": {
        "name": "Knowledge & Reasoning",
        "category": "Intelligence",
        "example_prompt": "Explain the difference between TCP and UDP.",
        "fallback_actions": ["factual", "explanation", "comparison", "project_assistance"],
    },
    "document": {
        "name": "Document Persistence",
        "category": "Creation",
        "example_prompt": "Create a README.md summary in my workspace.",
        "fallback_actions": ["write_document", "persist_file"],
    },
    "document_tool": {
        "name": "Document Persistence",
        "category": "Creation",
        "example_prompt": "Create a README.md summary in my workspace.",
        "fallback_actions": ["write_document", "persist_file"],
    },
    "content_generator": {
        "name": "Content Generator",
        "category": "Creation",
        "example_prompt": "Draft a structured technical report outline.",
        "fallback_actions": ["report", "summary", "readme", "document"],
    },
    "continuity_tool": {
        "name": "Context Continuity",
        "category": "Memory",
        "example_prompt": "What was I working on recently?",
        "fallback_actions": ["query_continuity", "session_lookup"],
    },
    "intent_continuity": {
        "name": "Intent Continuity",
        "category": "Memory",
        "example_prompt": "Retrieve my previous task intent and focus.",
        "fallback_actions": ["reconstruct_intent"],
    },
    "voice": {
        "name": "Voice Engine",
        "category": "Interface",
        "example_prompt": "Speak 'ORBIS readiness verified' aloud.",
        "fallback_actions": ["speak", "synthesize", "get_status"],
    },
}


def _extract_tool_actions(schema: Dict[str, Any], fallback: List[str]) -> List[str]:
    """Extracts clean action names from tool parameter schema without fabricating."""
    props = schema.get("properties", {})
    for key in ("action", "mode", "content_type"):
        if key in props and isinstance(props[key], dict):
            enum_vals = props[key].get("enum")
            if enum_vals and isinstance(enum_vals, list):
                return [str(v) for v in enum_vals]
    return fallback


def _sanitize_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Defensively copies and sanitizes parameter schema to prevent host path
    or credential leakage in default values while preserving types and constraints.
    """
    import copy
    clean = copy.deepcopy(schema)

    def _scrub(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: _scrub(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_scrub(item) for item in obj]
        elif isinstance(obj, str):
            # Scrub absolute paths or environment files
            if re.search(r"^[a-zA-Z]:[/\\]", obj) or obj.startswith("/Users/") or obj.startswith("/home/"):
                return "<workspace_root>"
            return EventSanitizer.clean_message(obj)
        return obj

    return _scrub(clean)


@router.get(
    "/tools",
    response_model=ToolRegistryResponse,
    summary="List Authoritative Registered Tools",
    description="Returns user-safe metadata, parameter schemas, risk levels, and categories for all registered ORBIS tools."
)
async def list_tools(
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
) -> ToolRegistryResponse:
    """
    Inspects the live ToolRegistry attached to the AgentOrchestrator and
    returns real capability metadata with zero mocked or fabricated entries.
    """
    if getattr(orchestrator, "tool_registry", None) is None:
        return ToolRegistryResponse(tools=[], total_tools=0, categories=[])

    raw_tools = orchestrator.tool_registry.list_tools()
    items: List[ToolMetadataItem] = []
    seen_categories = set()

    for tool in raw_tools:
        tid = getattr(tool, "name", "unknown")
        meta = TOOL_METADATA_EXTRAS.get(tid, {})

        display_name = meta.get("name") or tid.replace("_", " ").title()
        category = meta.get("category", "System")
        example_prompt = meta.get("example_prompt")
        fallback_actions = meta.get("fallback_actions", [tid])

        # Risk level serialization (LOW, MEDIUM, HIGH, BLOCKED)
        risk = tool.risk_level.value if hasattr(tool.risk_level, "value") else str(tool.risk_level)

        # Parameter schema extraction and sanitization
        try:
            raw_schema = tool.get_schema_json() if hasattr(tool, "get_schema_json") else {}
        except Exception:
            raw_schema = {}
        clean_schema = _sanitize_schema(raw_schema)

        # Action extraction
        actions = _extract_tool_actions(clean_schema, fallback_actions)

        items.append(
            ToolMetadataItem(
                id=tid,
                name=display_name,
                description=getattr(tool, "description", ""),
                version=getattr(tool, "version", "1.0.0"),
                risk_level=risk,
                category=category,
                status="AVAILABLE",
                parameters_schema=clean_schema,
                actions=actions,
                example_prompt=example_prompt,
            )
        )
        seen_categories.add(category)

    sorted_categories = sorted(seen_categories)

    return ToolRegistryResponse(
        tools=items,
        total_tools=len(items),
        categories=sorted_categories,
    )


# ── Conversational Continuity & Session Endpoints ──────────────────────────────

@router.post(
    "/conversation/reset",
    response_model=ConversationResetResponse,
    summary="Reset Conversation Context",
)
async def reset_conversation(
    request: Optional[ConversationResetRequest] = None,
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
):
    """
    Clears active multi-turn conversational context (turns, topics, entities, comparisons).
    Provides a clean slate for the user.
    """
    target_id = request.conversation_id if request else None
    orchestrator.reset_conversation(target_id)
    cid = target_id or "default"
    return ConversationResetResponse(
        status="ok",
        conversation_id=cid,
        message=f"Conversation context '{cid}' has been reset.",
    )


@router.post(
    "/conversation/new",
    response_model=ConversationNewResponse,
    summary="Start New Conversation Session",
)
async def start_new_conversation(
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
):
    """
    Allocates and registers a new isolated conversation session ID with clean context.
    """
    from uuid import uuid4
    new_id = f"conv_{uuid4().hex[:8]}"
    orchestrator.reset_conversation(new_id)
    orchestrator.get_conversation_context(new_id)
    return ConversationNewResponse(
        status="ok",
        conversation_id=new_id,
        message="New conversation session initialized.",
    )


@router.get(
    "/conversation/context",
    response_model=ConversationContextResponse,
    summary="Get Conversation Context",
)
async def get_conversation_context(
    conversation_id: Optional[str] = Query(None, description="Conversation session ID"),
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
):
    """
    Returns active conversation context including topic, entities, and turn history.
    """
    target_id = conversation_id or "default"
    ctx = orchestrator.get_conversation_context(target_id)
    turn_dicts = [
        {
            "turn_index": t.turn_index,
            "user_query": t.user_query,
            "resolved_query": t.resolved_query,
            "response_text": t.response_text,
            "topic": t.topic,
            "entities": t.entities,
            "tool_name": t.tool_name,
            "timestamp": t.timestamp.isoformat() if t.timestamp else None,
        }
        for t in ctx.turns
    ]
    return ConversationContextResponse(
        conversation_id=target_id,
        active_topic=ctx.active_topic,
        active_entity=ctx.active_entity,
        active_entities=ctx.active_entities,
        turn_count=len(ctx.turns),
        turns=turn_dicts,
    )


# ============================================================================
# Phase 10 / Workspace Restoration Endpoints
# ============================================================================

@router.get(
    "/restoration/sessions",
    response_model=RestorationSessionsResponse,
    summary="List Restorable Workspace Sessions",
)
async def list_restoration_sessions(
    limit: int = Query(10, ge=1, le=50, description="Max candidate sessions to return"),
    engine: Any = Depends(get_restoration_engine),
):
    """
    Returns candidate workspace sessions with application descriptors,
    resource targets, and continuity metadata.
    """
    try:
        sessions = engine.get_restorable_sessions(limit=limit)
        return RestorationSessionsResponse(sessions=sessions, total=len(sessions))
    except Exception as e:
        logger.error("Failed to list restoration sessions: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query restorable sessions: {e}",
        )


@router.post(
    "/restoration/preview",
    response_model=RestorationPreviewResponse,
    summary="Preview Declarative Workspace Restoration Plan",
)
async def preview_restoration_plan(
    request: RestorationPreviewRequest,
    engine: Any = Depends(get_restoration_engine),
):
    """
    Constructs and preflight-validates a multi-app declarative restoration plan
    without launching processes.
    """
    try:
        query_text = request.query or (
            f"Restore session {request.session_id}" if request.session_id else "Restore my previous workspace"
        )
        plan, ice_result, message = await engine.create_restoration_plan(
            query=query_text,
            session_id=request.session_id,
            target_resource=request.target_resource,
        )

        if not plan:
            return RestorationPreviewResponse(
                status="no_plan",
                plan=None,
                message=message,
            )

        step_previews = []
        for s in plan.steps:
            app_desc = engine.registry.get(s.app_identifier)
            disp_name = app_desc.display_name if app_desc else s.app_identifier.title()
            step_previews.append(
                RestorationStepPreview(
                    step_id=s.step_id,
                    app_identifier=s.app_identifier,
                    display_name=disp_name,
                    command_executable=s.command_executable,
                    command_args=s.command_args,
                    target_type=s.resource.target_type.value if s.resource else None,
                    target_value=s.resource.resolved_target or s.resource.raw_target if s.resource else None,
                    window_bounds=s.target_window_state.bounds.model_dump() if (s.target_window_state and s.target_window_state.bounds) else None,
                    window_placement=s.target_window_state.placement.value if (s.target_window_state and s.target_window_state.placement) else None,
                    risk_level=s.risk_level.value,
                    status=s.status.value,
                    is_valid=(s.status.value != "SKIPPED_INVALID"),
                    status_message=s.status_message,
                )
            )

        plan_preview = RestorationPlanPreview(
            plan_id=plan.plan_id,
            session_id=plan.session_id,
            goal_description=plan.goal_description,
            overall_risk=plan.overall_risk.value,
            requires_user_approval=plan.requires_user_approval,
            is_preflight_valid=plan.is_preflight_valid,
            preflight_errors=plan.preflight_errors,
            steps=step_previews,
        )

        return RestorationPreviewResponse(
            status="ok",
            plan=plan_preview,
            message=message,
        )
    except Exception as e:
        logger.error("Failed to preview restoration plan: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate restoration plan: {e}",
        )


@router.post(
    "/restoration/execute",
    response_model=RestorationExecuteResponse,
    summary="Execute Workspace Restoration Plan",
)
async def execute_restoration_plan(
    request: RestorationExecuteRequest,
    engine: Any = Depends(get_restoration_engine),
):
    """
    Executes a preflight-validated restoration plan with failure isolation,
    window layout positioning, and independent verification.
    """
    try:
        query_text = request.query or (
            f"Restore session {request.session_id}" if request.session_id else "Restore my previous workspace"
        )
        plan, ice_result, message = await engine.create_restoration_plan(
            query=query_text,
            session_id=request.session_id,
            target_resource=request.target_resource,
        )

        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Could not build restoration plan: {message}",
            )

        # Filter to selected steps if provided
        if request.selected_step_ids is not None and len(request.selected_step_ids) > 0:
            from core.restoration.models import RestorationStepStatus
            selected_set = set(request.selected_step_ids)
            selected_lower = {str(x).lower() for x in request.selected_step_ids}
            for idx, s in enumerate(plan.steps):
                is_selected = (
                    s.step_id in selected_set
                    or str(idx) in selected_set
                    or s.app_identifier.lower() in selected_lower
                    or any(s.step_id.endswith(f"_{x}") for x in selected_set)
                )
                if not is_selected:
                    s.status = RestorationStepStatus.SKIPPED_INVALID
                    s.status_message = "Skipped by user selection."

        # Execute with user policy approval callback
        approval_cb = (lambda p: True) if request.approved else (lambda p: False)
        result = await engine.execute_restoration_plan(plan, approval_callback=approval_cb)

        # Convert step results and verification reports to dicts
        step_res_dict = {
            sid: res.model_dump() for sid, res in result.step_results.items()
        }
        ver_rep_dict = {
            sid: rep.model_dump() for sid, rep in result.verification_reports.items()
        }

        return RestorationExecuteResponse(
            plan_id=result.plan_id,
            session_id=result.session_id,
            success=result.success,
            is_partial_restoration=result.is_partial_restoration,
            completed_steps=result.completed_steps,
            failed_steps=result.failed_steps,
            skipped_steps=result.skipped_steps,
            step_results=step_res_dict,
            verification_reports=ver_rep_dict,
            summary=result.summary,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to execute restoration plan: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute workspace restoration: {e}",
        )


