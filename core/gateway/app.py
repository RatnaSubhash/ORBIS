"""
FastAPI Application Factory for ORBIS Phase 12.8 Gateway.
Initializes routes, WebSocket endpoints, lifespan resource cleanup,
CORS policy, and machine-readable user-safe error handling.
"""

from contextlib import asynccontextmanager
import logging
import os
from typing import AsyncGenerator, List, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core.agent.orchestrator import AgentOrchestrator
from core.events.bus import EventBus
from core.gateway import routes, websocket
from core.gateway.task_manager import TaskManager
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
) -> FastAPI:
    """
    Creates and configures an ORBIS Gateway FastAPI application.
    Injects existing AgentOrchestrator and EventBus instances without duplication.
    """
    # 1. Resolve or lazily construct shared event bus & orchestrator
    bus = event_bus or (orchestrator.event_bus if orchestrator and orchestrator.event_bus else EventBus())
    mgr = task_manager or TaskManager()

    # If no orchestrator provided, build default orchestrator lazily
    orch = orchestrator
    if orch is None:
        from core.config import get_settings
        from core.tools.registry import create_default_tool_registry
        settings = get_settings()
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

        reg = create_default_tool_registry(llm_provider=llm)
        orch = AgentOrchestrator(
            llm_provider=llm,
            tool_registry=reg,
            event_bus=bus,
        )


    # 2. Lifespan context manager for safe shutdown & resource cleanup
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        logger.info("Starting ORBIS Gateway service.")
        app.state.orchestrator = orch
        app.state.event_bus = bus
        app.state.task_manager = mgr

        yield

        logger.info("Shutting down ORBIS Gateway service. Cleaning up background tasks.")
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

    # 3. CORS Middleware configuration
    origins = resolve_cors_origins(allowed_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # 4. Dependency Overrides
    app.dependency_overrides[routes.get_orchestrator] = lambda: orch
    app.dependency_overrides[routes.get_task_manager] = lambda: mgr
    app.dependency_overrides[routes.get_event_bus] = lambda: bus
    app.dependency_overrides[websocket.get_event_bus] = lambda: bus

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
