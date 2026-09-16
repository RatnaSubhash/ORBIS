"""
WebSocket endpoint and real-time event streaming for the ORBIS FastAPI Gateway (Phase 12.8).
Provides task-isolated live event delivery, bounded queues, backpressure handling for slow clients,
historical event replay, and leak-free subscription lifecycle.
"""

import asyncio
from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel

from core.events.bus import EventBus
from core.events.models import AgentEvent

logger = logging.getLogger("orbis.gateway.websocket")

# Valid task identifier pattern (letters, numbers, hyphens, underscores)
TASK_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")

router = APIRouter(tags=["WebSocket"])


def get_event_bus() -> EventBus:
    """Dependency provider placeholder for EventBus; overridden at app creation."""
    raise NotImplementedError("EventBus dependency not configured.")


class WebSocketConnectionConfig(BaseModel):
    """Configuration for WebSocket backpressure and queues."""
    max_queue_size: int = 100
    overflow_policy: str = "close"  # "close" (disconnect lagging client) or "drop" (evict oldest event)


async def handle_task_websocket(
    websocket: WebSocket,
    task_id: str,
    event_bus: EventBus,
    replay: bool = True,
    config: Optional[WebSocketConnectionConfig] = None,
) -> None:
    """
    Handles a WebSocket connection subscribed to a specific task's AgentEvents.
    Guarantees:
    1. Task isolation: only events for task_id are delivered.
    2. Historical replay: bounded past events for this task are delivered first.
    3. Backpressure safety: bounded asyncio.Queue protects server memory against slow clients.
    4. Clean lifecycle: unregisters EventBus subscription immediately on client disconnect.
    """
    cfg = config or WebSocketConnectionConfig()

    # 1. Validate task_id syntax
    if not task_id or not TASK_ID_PATTERN.match(task_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid task ID format.")
        return

    # 2. Accept connection
    await websocket.accept()

    # 3. Setup bounded queue for this connection
    event_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=cfg.max_queue_size)
    close_sentinel_received = False

    # 4. Subscriber callback with backpressure handling
    def _on_event(ev: AgentEvent) -> None:
        nonlocal close_sentinel_received
        try:
            event_queue.put_nowait(ev)
        except asyncio.QueueFull:
            logger.warning("Slow client detected on task %s (queue full). Policy: %s", task_id, cfg.overflow_policy)
            if cfg.overflow_policy == "close":
                if not close_sentinel_received:
                    close_sentinel_received = True
                    # Evict one item if necessary to insert sentinel
                    try:
                        event_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        event_queue.put_nowait(None)  # None is close sentinel
                    except asyncio.QueueFull:
                        pass
            elif cfg.overflow_policy == "drop":
                # Evict oldest event to make room for newest
                try:
                    event_queue.get_nowait()
                    event_queue.put_nowait(ev)
                except Exception:
                    pass

    # 5. Subscribe to EventBus with task-specific filter
    # Task isolation invariant: filter_fn guarantees no cross-task event leakage
    sub_id = event_bus.subscribe(
        callback=_on_event,
        filter_fn=lambda ev: ev.task_id == task_id,
    )

    replayed_ids: set[str] = set()

    try:
        # 6. Replay historical events if requested
        if replay:
            history = event_bus.get_task_events(task_id)
            for historical_event in history:
                replayed_ids.add(historical_event.event_id)
                # Historical events are already sanitized AgentEvent models
                await websocket.send_json(historical_event.model_dump(mode="json"))

        # 7. Delivery & Reader Tasks
        async def _sender_loop() -> None:
            nonlocal replayed_ids
            while True:
                item = await event_queue.get()
                if item is None:
                    # Slow client overflow disconnect sentinel
                    logger.warning("Terminating connection for task %s due to slow-client overflow.", task_id)
                    await websocket.close(
                        code=status.WS_1008_POLICY_VIOLATION,
                        reason="Slow client disconnected to prevent memory accumulation.",
                    )
                    break

                if replayed_ids and hasattr(item, "event_id") and item.event_id in replayed_ids:
                    # Deduplicate: event was already delivered during historical replay
                    replayed_ids.discard(item.event_id)
                    continue

                await websocket.send_json(item.model_dump(mode="json"))

        async def _receiver_loop() -> None:
            """Detects client-initiated close/disconnect cleanly."""
            try:
                while True:
                    msg = await websocket.receive()
                    if msg.get("type") == "websocket.disconnect":
                        break
            except Exception:
                pass

        sender_task = asyncio.create_task(_sender_loop())
        receiver_task = asyncio.create_task(_receiver_loop())

        # Wait until either sender or receiver finishes (e.g. disconnect or overflow)
        done, pending = await asyncio.wait(
            [sender_task, receiver_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for p in pending:
            p.cancel()
            try:
                await p
            except (asyncio.CancelledError, Exception):
                pass

    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected normally from task %s.", task_id)
    except Exception as e:
        logger.warning("WebSocket connection error on task %s: %s", task_id, e)
    finally:
        # 8. Clean unsubscription invariant
        event_bus.unsubscribe(sub_id)
        logger.debug("Unsubscribed WebSocket %s for task %s.", sub_id, task_id)


@router.websocket("/ws/tasks/{task_id}")
async def task_websocket_endpoint(
    websocket: WebSocket,
    task_id: str,
    replay: bool = Query(default=True, description="Whether to replay historical events for this task."),
    event_bus: EventBus = Depends(get_event_bus),
) -> None:
    """Real-time WebSocket endpoint streaming sanitized AgentEvents for a specific task."""
    await handle_task_websocket(
        websocket=websocket,
        task_id=task_id,
        event_bus=event_bus,
        replay=replay,
    )
