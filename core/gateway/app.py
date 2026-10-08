"""
FastAPI Application Factory for ORBIS Phase 12.8 Gateway.
Initializes routes, WebSocket endpoints, lifespan resource cleanup,
CORS policy, and machine-readable user-safe error handling.
"""

from contextlib import asynccontextmanager
import logging
import os
import sys
from typing import AsyncGenerator, List, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core.agent.orchestrator import AgentOrchestrator
from core.config import get_settings
from core.events.bus import EventBus
from core.gateway import routes, websocket
from core.gateway.task_manager import TaskManager
from core.memory.db import DatabaseManager
from core.observation.service import WorkspaceObservationService
from core.types import WorkspaceEvent
from core.voice.models import VoiceState

logger = logging.getLogger("orbis.gateway.app")

DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "app://.",
]


def resolve_cors_origins(configured_origins: Optional[List[str]] = None) -> List[str]:
    """Resolves CORS origins from configuration or environment; forbids unrestricted '*'."""
    if configured_origins:
        return [o.strip() for o in configured_origins if o.strip()]

    env_origins = os.environ.get("ORBIS_CORS_ORIGINS")
    if env_origins:
        return [o.strip() for o in env_origins.split(",") if o.strip()]

    return list(DEFAULT_CORS_ORIGINS)


def create_app(
    orchestrator: Optional[AgentOrchestrator] = None,
    event_bus: Optional[EventBus] = None,
    task_manager: Optional[TaskManager] = None,
    allowed_origins: Optional[List[str]] = None,
    db_manager: Optional[DatabaseManager] = None,
    observation_service: Optional[WorkspaceObservationService] = None,
) -> FastAPI:
    """
    Creates and configures an ORBIS Gateway FastAPI application.
    Injects existing AgentOrchestrator, EventBus, DatabaseManager, and WorkspaceObservationService.
    """
    settings = get_settings()

    # 1. Resolve canonical persistent DatabaseManager
    db = db_manager
    if db is None:
        db = DatabaseManager(db_path=settings.db_path)
        db.initialize()

    # 2. Resolve or lazily construct shared event bus & orchestrator
    bus = event_bus or (orchestrator.event_bus if orchestrator and orchestrator.event_bus else EventBus())
    mgr = task_manager or TaskManager()

    # 3. Resolve or construct WorkspaceObservationService & real-time session builder
    obs_svc = observation_service
    session_builder: Optional[Any] = None
    synthesizer: Optional[Any] = None

    if obs_svc is None and settings.observation_enabled:
        if getattr(settings, "orbis_env", "development") == "test":
            from core.observation.mock import MockWorkspaceObserver
            observer = MockWorkspaceObserver()
        elif sys.platform == "win32":
            from core.observation.windows import WindowsWorkspaceObserver
            observer = WindowsWorkspaceObserver()
        else:
            from core.observation.mock import MockWorkspaceObserver
            observer = MockWorkspaceObserver()

        obs_svc = WorkspaceObservationService(
            observer=observer,
            db_manager=db,
            polling_interval=settings.observation_interval,
        )

    if obs_svc is not None:
        from core.continuity.sessionizer import IncrementalSessionBuilder
        from core.continuity.dna import ContextDNASynthesizer

        session_builder = IncrementalSessionBuilder(
            session_gap_seconds=settings.session_gap_minutes * 60.0
        )
        synthesizer = ContextDNASynthesizer()

        async def _on_workspace_event_sessionizer(event: WorkspaceEvent) -> None:
            try:
                finalized = session_builder.add_event(event)
                if finalized:
                    db.record_session(finalized)
                    dna = synthesizer.synthesize(finalized)
                    db.record_context_dna(dna)
            except Exception as exc:
                logger.warning("Sessionizer event listener encountered error: %s", exc)

        obs_svc.add_event_listener(_on_workspace_event_sessionizer)

    # 4. If no orchestrator provided, build default orchestrator lazily
    orch = orchestrator
    if orch is None:
        from core.tools.registry import create_default_tool_registry
        if getattr(settings, "orbis_env", "development") == "test":
            from core.agent.llm.mock import MockLLMProvider
            llm = MockLLMProvider()
        else:
            from core.agent.llm.factory import get_llm_provider
            try:
                llm = get_llm_provider()
            except Exception as exc:
                logger.error("Failed to initialize configured LLM provider (%s): %s. Setting llm=None.", getattr(settings, "llm_provider", "unknown"), exc)
                llm = None

        reg = create_default_tool_registry(llm_provider=llm, db=db)
        orch = AgentOrchestrator(
            llm_provider=llm,
            tool_registry=reg,
            db_manager=db,
            event_bus=bus,
        )

    # 5. Lifespan context manager for safe shutdown & resource cleanup
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        logger.info("Starting ORBIS Gateway service.")
        app.state.orchestrator = orch
        app.state.event_bus = bus
        app.state.task_manager = mgr
        app.state.db_manager = db
        app.state.observation_service = obs_svc
        app.state.session_builder = session_builder

        if obs_svc is not None:
            obs_svc.start()

        yield

        logger.info("Shutting down ORBIS Gateway service. Cleaning up background tasks.")
        if obs_svc is not None and obs_svc.is_running:
            await obs_svc.stop()
            if session_builder and synthesizer:
                try:
                    trailing = session_builder.finalize()
                    if trailing:
                        db.record_session(trailing)
                        dna = synthesizer.synthesize(trailing)
                        db.record_context_dna(dna)
                except Exception as exc:
                    logger.warning("Error finalizing trailing session on shutdown: %s", exc)

        if getattr(orch, "voice_service", None) and getattr(orch.voice_service, "state", None) == VoiceState.LISTENING:
            try:
                audio = orch.voice_service.stop_listening()
                audio.release()
            except Exception:
                pass
        await mgr.close(timeout=2.0)
        await bus.close(timeout=2.0)

        logger.info("ORBIS Gateway service shutdown complete.")

    app = FastAPI(
        title="ORBIS Agent Gateway",
        description="Local IPC and real-time event streaming gateway for the ORBIS autonomous desktop agent.",
        version="0.2.0",
        lifespan=lifespan,
    )
    app.state.orchestrator = orch
    app.state.event_bus = bus
    app.state.task_manager = mgr
    app.state.db_manager = db
    app.state.observation_service = obs_svc
    app.state.session_builder = session_builder

    # 6. CORS Middleware configuration
    origins = resolve_cors_origins(allowed_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # 7. Dependency Overrides
    app.dependency_overrides[routes.get_orchestrator] = lambda: orch
    app.dependency_overrides[routes.get_task_manager] = lambda: mgr
    app.dependency_overrides[routes.get_event_bus] = lambda: bus
    app.dependency_overrides[websocket.get_event_bus] = lambda: bus
    app.dependency_overrides[routes.get_db_manager] = lambda: db
    app.dependency_overrides[routes.get_observation_service] = lambda: obs_svc

    # 5. Include Routers
    app.include_router(routes.router)
    app.include_router(websocket.router)

    # 6. Global User-Safe Error Handlers
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": exc.detail,
                "code": f"HTTP_{exc.status_code}",
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "Invalid request parameters.",
                "code": "VALIDATION_ERROR",
                "details": [
                    {"loc": [str(x) for x in err.get("loc", [])], "msg": err.get("msg", "")}
                    for err in exc.errors()
                ],
            },
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled gateway exception on %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "An unexpected server error occurred.",
                "code": "INTERNAL_SERVER_ERROR",
            },
        )

    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("core.gateway.app:create_app", factory=True, host="127.0.0.1", port=8000, reload=False)

