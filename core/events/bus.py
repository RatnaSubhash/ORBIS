"""
In-Process Asynchronous Event Bus for ORBIS Phase 12.
Provides non-blocking, decoupled pub-sub event streaming for AgentEvent telemetry.
Guarantees that subscriber execution never delays, blocks, or impacts task execution.
"""

import asyncio
from collections import deque
import inspect
import logging
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Set, Tuple, Union
from uuid import uuid4

from core.events.models import AgentEvent

logger = logging.getLogger("orbis.events.bus")

SubscriberCallback = Callable[[AgentEvent], Union[None, Awaitable[None]]]
FilterFn = Callable[[AgentEvent], bool]


class EventBus:
    """
    Lightweight, in-process, async-safe pub/sub event bus.
    Enforces strict decoupling between agent task execution and telemetry subscribers:
    - publish() is completely non-blocking: dispatches via event-loop tasks.
    - Subscriber exceptions are contained and logged; they never fail tasks.
    - Bounded in-memory event retention prevents memory leaks.
    - Safe lifecycle management ensures no orphaned background tasks.
    """

    DEFAULT_MAX_GLOBAL_EVENTS: int = 1000
    DEFAULT_MAX_TASK_EVENTS: int = 100

    def __init__(
        self,
        max_global_events: int = DEFAULT_MAX_GLOBAL_EVENTS,
        max_task_events: int = DEFAULT_MAX_TASK_EVENTS,
    ) -> None:
        self.max_global_events = max_global_events
        self.max_task_events = max_task_events

        # Subscribers: subscription_id -> (callback, filter_fn)
        self._subscribers: Dict[str, Tuple[SubscriberCallback, Optional[FilterFn]]] = {}

        # Active background delivery tasks
        self._pending_tasks: Set[asyncio.Task] = set()

        # Bounded event history
        self._global_history: Deque[AgentEvent] = deque(maxlen=self.max_global_events)
        self._task_history: Dict[str, Deque[AgentEvent]] = {}
        self._task_order: Deque[str] = deque(maxlen=200)  # track active tasks for eviction

        self._is_closed: bool = False

    @property
    def is_closed(self) -> bool:
        """Returns True if the event bus has been closed."""
        return self._is_closed

    @property
    def subscriber_count(self) -> int:
        """Returns the number of currently active subscribers."""
        return len(self._subscribers)

    def subscribe(
        self,
        callback: SubscriberCallback,
        filter_fn: Optional[FilterFn] = None,
        replay_task_id: Optional[str] = None,
    ) -> str:
        """
        Registers an event listener callback.
        Optionally filters events via filter_fn.
        If replay_task_id is specified, immediately dispatches historical events for that task.
        Returns a unique subscription_id.
        """
        if self._is_closed:
            raise RuntimeError("Cannot subscribe to a closed EventBus.")

        sub_id = f"sub_{uuid4().hex[:8]}"
        self._subscribers[sub_id] = (callback, filter_fn)

        # Replay historical events if requested
        if replay_task_id and replay_task_id in self._task_history:
            events_to_replay = list(self._task_history[replay_task_id])
            self._schedule_replay(sub_id, callback, filter_fn, events_to_replay)

        return sub_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """
        Removes a subscription by ID.
        Returns True if found and removed, False otherwise.
        """
        if subscription_id in self._subscribers:
            del self._subscribers[subscription_id]
            return True
        return False

    def _record_history(self, event: AgentEvent) -> None:
        """Stores event in global and per-task bounded history queues."""
        self._global_history.append(event)

        task_id = event.task_id
        if task_id:
            if task_id not in self._task_history:
                # Evict oldest task history if over limit
                if len(self._task_history) >= 200 and self._task_order:
                    oldest = self._task_order.popleft()
                    self._task_history.pop(oldest, None)
                self._task_history[task_id] = deque(maxlen=self.max_task_events)
                self._task_order.append(task_id)

            self._task_history[task_id].append(event)

    def _schedule_replay(
        self,
        sub_id: str,
        callback: SubscriberCallback,
        filter_fn: Optional[FilterFn],
        events: List[AgentEvent],
    ) -> None:
        """Schedules delivery of historical events to a late-joining subscriber."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _run_replay() -> None:
            for ev in events:
                if filter_fn and not filter_fn(ev):
                    continue
                await self._deliver_to_subscriber(sub_id, callback, ev)

        task = loop.create_task(_run_replay())
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _deliver_to_subscriber(
        self,
        sub_id: str,
        callback: SubscriberCallback,
        event: AgentEvent,
    ) -> None:
        """
        Invokes an individual subscriber callback with complete exception isolation.
        Handles both synchronous and asynchronous callbacks safely.
        """
        try:
            res = callback(event)
            if inspect.isawaitable(res):
                await res
        except Exception as e:
            logger.warning("EventBus subscriber %s raised an exception: %s", sub_id, e)

    def publish_nowait(self, event: AgentEvent) -> None:
        """
        Synchronous, non-blocking publication method.
        Records event in history and schedules delivery to subscribers via asyncio.create_task.
        Does NOT block or wait for subscribers.
        """
        if self._is_closed:
            logger.warning("EventBus is closed; dropping event %s (%s).", event.event_id, event.event_type)
            return

        self._record_history(event)

        if not self._subscribers:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # If no running event loop, we cannot schedule async delivery tasks
            return

        for sub_id, (cb, filter_fn) in list(self._subscribers.items()):
            if filter_fn and not filter_fn(event):
                continue

            task = loop.create_task(self._deliver_to_subscriber(sub_id, cb, event))
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)

    async def publish(self, event: AgentEvent) -> None:
        """
        Asynchronous publication interface.
        CRITICAL ARCHITECTURAL GUARANTEE:
        This method schedules delivery on the event loop and returns IMMEDIATELY.
        It does NOT wait or gather subscriber callbacks, ensuring agent task execution
        is never slowed down or blocked by slow subscribers.
        """
        self.publish_nowait(event)

    def get_task_events(self, task_id: str) -> List[AgentEvent]:
        """Returns all recorded events for a task in chronological order."""
        if task_id in self._task_history:
            return list(self._task_history[task_id])
        return []

    def get_history(self, limit: Optional[int] = None) -> List[AgentEvent]:
        """Returns global event history up to limit."""
        events = list(self._global_history)
        if limit is not None:
            return events[-limit:]
        return events

    async def close(self, timeout: float = 2.0) -> None:
        """
        Gracefully shuts down the event bus.
        Drops active subscribers, cancels in-flight tasks, and prevents new events.
        """
        self._is_closed = True
        self._subscribers.clear()

        if self._pending_tasks:
            tasks = list(self._pending_tasks)
            for t in tasks:
                if not t.done():
                    t.cancel()

            try:
                await asyncio.wait(tasks, timeout=timeout)
            except Exception:
                pass
            self._pending_tasks.clear()
