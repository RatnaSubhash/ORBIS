"""
Passive Workspace Observation Service for ORBIS.
Periodically samples active window/process state, suppresses duplicate events,
respects privacy exclusion policies, and records structured events in SQLite.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, List, Optional

from core.config import get_settings
from core.memory.db import DatabaseManager
from core.observation.base import BaseWorkspaceObserver
from core.types import WorkspaceEvent, WorkspaceEventType

logger = logging.getLogger("orbis.observation.service")

ObservationListener = Callable[[WorkspaceEvent], Awaitable[None]]


class WorkspaceObservationService:
    """
    Background service coordinating passive workspace observation,
    event deduplication, privacy policy enforcement, and database persistence.
    """

    def __init__(
        self,
        observer: BaseWorkspaceObserver,
        db_manager: Optional[DatabaseManager] = None,
        polling_interval: Optional[float] = None,
    ) -> None:
        self.observer = observer
        self.db_manager = db_manager
        settings = get_settings()
        self.polling_interval = (
            polling_interval
            if polling_interval is not None
            else settings.observation_interval
        )
        self._is_running: bool = False
        self._poll_task: Optional[asyncio.Task] = None
        self._listeners: List[ObservationListener] = []

        # Deduplication tracking state
        self._last_process_name: Optional[str] = None
        self._last_window_title: Optional[str] = None
        self._last_pid: Optional[int] = None

    @property
    def is_running(self) -> bool:
        """Returns True if the background observation loop is active."""
        return self._is_running

    def add_event_listener(self, listener: ObservationListener) -> None:
        """Subscribes an async callback to receive workspace events."""
        self._listeners.append(listener)

    async def _emit_event(self, event: WorkspaceEvent) -> None:
        """Dispatches an event to subscribers and records to SQLite."""
        if self.db_manager:
            try:
                self.db_manager.record_workspace_event(event)
            except Exception as e:
                logger.warning("Failed to persist workspace event: %s", e)

        for listener in self._listeners:
            try:
                await listener(event)
            except Exception as e:
                logger.warning("Workspace event listener raised exception: %s", e)

    def sample_once(self) -> Optional[WorkspaceEvent]:
        """
        Synchronously samples the active workspace state once and returns an event
        if state has changed and is not excluded, updating deduplication trackers.
        Returns None if state is unchanged, excluded, or unavailable.
        """
        window = self.observer.get_active_window()
        if not window:
            return None

        # Privacy exclusion: drop event completely if process/window is excluded
        if window.is_excluded:
            # Update trackers so switching back from excluded app triggers re-activation
            self._last_process_name = None
            self._last_window_title = None
            self._last_pid = None
            return None

        # Duplicate suppression: no event if process, window title, and pid are identical
        if (
            window.process_name == self._last_process_name
            and window.title == self._last_window_title
            and window.process_id == self._last_pid
        ):
            return None

        # Determine event type
        if window.process_name != self._last_process_name or window.process_id != self._last_pid:
            event_type = WorkspaceEventType.APPLICATION_ACTIVATED
        else:
            event_type = WorkspaceEventType.WINDOW_ACTIVATED

        event = WorkspaceEvent(
            event_type=event_type,
            process_name=window.process_name,
            process_id=window.process_id,
            window_title=window.title,
            metadata={
                "hwnd": window.hwnd,
                "executable_path": window.executable_path,
            },
        )

        # Update trackers
        self._last_process_name = window.process_name
        self._last_window_title = window.title
        self._last_pid = window.process_id

        return event

    async def poll_step(self) -> Optional[WorkspaceEvent]:
        """Executes a single sampling step and dispatches the event if detected."""
        event = self.sample_once()
        if event:
            await self._emit_event(event)
        return event

    async def capture_snapshot_event(self) -> WorkspaceEvent:
        """
        Forces an explicit OBSERVATION_SNAPSHOT event representing the current state,
        bypassing duplicate suppression.
        """
        snapshot = self.observer.capture_snapshot()
        event = WorkspaceEvent(
            event_type=WorkspaceEventType.OBSERVATION_SNAPSHOT,
            process_name=snapshot.process_name if not snapshot.is_excluded else "[REDACTED]",
            process_id=snapshot.process_id if not snapshot.is_excluded else None,
            window_title=snapshot.title if not snapshot.is_excluded else "[REDACTED]",
            metadata={
                "hwnd": snapshot.hwnd if not snapshot.is_excluded else None,
                "is_excluded": snapshot.is_excluded,
            },
        )
        await self._emit_event(event)
        return event

    async def _run_loop(self) -> None:
        """Continuous background polling loop."""
        try:
            while self._is_running:
                await self.poll_step()
                await asyncio.sleep(self.polling_interval)
        except asyncio.CancelledError:
            logger.debug("Workspace observation loop cancelled.")
            raise
        except Exception as e:
            logger.error("Unexpected error in workspace observation loop: %s", e, exc_info=True)
        finally:
            self._is_running = False

    def start(self) -> None:
        """Starts the background observation task if not already running."""
        if self._is_running:
            return
        self._is_running = True
        self._poll_task = asyncio.create_task(self._run_loop())
        logger.info("Workspace observation service started (interval=%.2fs).", self.polling_interval)

    async def stop(self) -> None:
        """Stops the background observation task cleanly and cancellation-safe."""
        if not self._is_running:
            return
        self._is_running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        logger.info("Workspace observation service stopped.")
