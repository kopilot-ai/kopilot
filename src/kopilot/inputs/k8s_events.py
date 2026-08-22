"""Kubernetes event watcher — triggers sub-agent investigations on anomalies.

Watches Warning events across all namespaces. When a specific resource+reason
combination exceeds the threshold within the time window, triggers an automated
investigation through the supervisor agent.

Off by default (``WATCHERS_K8S_EVENTS_ENABLED``, chart value
``watchers.k8sEvents.enabled``). Event reasons and messages are attacker-
controllable strings that this watcher feeds straight into an LLM prompt:
anyone who can create a Pod can create an event. Until that input is treated
as untrusted end to end, the watcher stays opt-in.

The kubernetes client's watch stream is synchronous, so it runs in a worker
thread and feeds an asyncio queue; the async side consumes events without
blocking the event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections import defaultdict

import structlog

from kopilot.outputs.audit import log_event

logger = structlog.get_logger(__name__)

EVENT_THRESHOLD = 3
WINDOW_SECONDS = 300
MAX_CONCURRENT_INVESTIGATIONS = 3
BACKOFF_BASE = 5
BACKOFF_MAX = 60
QUEUE_MAX = 1000


class K8sEventWatcher:
    """Watches K8s Warning events and auto-triggers agent investigation."""

    def __init__(self, threshold: int = EVENT_THRESHOLD, window: int = WINDOW_SECONDS):
        self._counts: dict[str, list[float]] = defaultdict(list)
        self._running = False
        self._threshold = threshold
        self._window = window
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_INVESTIGATIONS)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._consumer_task: asyncio.Task | None = None
        self._watch_thread: threading.Thread | None = None
        # Strong references to in-flight investigation tasks (asyncio only
        # keeps weak references, so unreferenced tasks can be GC'd mid-run).
        self._investigations: set[asyncio.Task] = set()

    async def start(self) -> None:
        from kopilot.config import get_settings

        if not get_settings().watchers.k8s_events_enabled:
            logger.info(
                "k8s_events.watcher.disabled",
                hint="Set WATCHERS_K8S_EVENTS_ENABLED=true to enable it.",
            )
            return
        self._running = True
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._load_config)
        self._watch_thread = threading.Thread(
            target=self._watch_blocking, args=(loop,), daemon=True, name="k8s-event-watch"
        )
        self._watch_thread.start()
        self._consumer_task = asyncio.create_task(self._consume_loop())
        logger.info("k8s_events.watcher.started", threshold=self._threshold, window=self._window)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._consumer_task
        for task in list(self._investigations):
            task.cancel()
        logger.info("k8s_events.watcher.stopped")

    @staticmethod
    def _load_config() -> None:
        from kubernetes import config

        try:
            config.load_incluster_config()
        except Exception:
            try:
                config.load_kube_config()
            except Exception:
                logger.warning("k8s_events.no_config")

    def _watch_blocking(self, loop: asyncio.AbstractEventLoop) -> None:
        """Runs in a worker thread: stream events into the asyncio queue."""
        from kubernetes import client, watch

        backoff = BACKOFF_BASE
        while self._running:
            try:
                v1 = client.CoreV1Api()
                w = watch.Watch()
                backoff = BACKOFF_BASE
                for event in w.stream(v1.list_event_for_all_namespaces, timeout_seconds=60):
                    if not self._running:
                        return
                    try:
                        loop.call_soon_threadsafe(self._enqueue, event)
                    except RuntimeError:
                        # Event loop closed; shut the thread down.
                        return
            except Exception:
                logger.exception("k8s_events.watch_error", backoff=backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)

    def _enqueue(self, event: dict) -> None:
        """Runs on the event loop thread; drops events when the queue is full."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("k8s_events.queue_full")

    async def _consume_loop(self) -> None:
        while self._running:
            event = await self._queue.get()
            try:
                await self._process(event)
            except Exception:
                logger.exception("k8s_events.process_error")

    async def _process(self, event: dict) -> None:
        obj = event.get("object")
        if obj is None:
            return

        event_type = getattr(obj, "type", None)
        if event_type != "Warning":
            return

        involved = getattr(obj, "involved_object", None)
        if not involved:
            return

        ns = getattr(involved, "namespace", None) or "default"
        name = getattr(involved, "name", "unknown")
        reason = getattr(obj, "reason", "Unknown")

        key = f"{ns}/{name}:{reason}"
        now = time.time()
        self._counts[key] = [t for t in self._counts[key] if now - t < self._window]
        self._counts[key].append(now)

        if len(self._counts[key]) >= self._threshold:
            logger.warning("k8s_events.threshold", key=key, count=len(self._counts[key]))
            log_event("k8s_events.auto_trigger", key=key)
            if self._semaphore.locked():
                logger.warning("k8s_events.investigation_throttled", key=key)
            else:
                task = asyncio.create_task(self._investigate(key, obj))
                self._investigations.add(task)
                task.add_done_callback(self._investigations.discard)
            self._counts[key].clear()

    async def _investigate(self, key: str, event_obj) -> None:
        from kopilot.agent.memory import TaskContext
        from kopilot.agent.supervisor import run_task

        async with self._semaphore:
            try:
                involved = getattr(event_obj, "involved_object", None)
                ns = (getattr(involved, "namespace", None) or "default") if involved else "default"
                name = getattr(involved, "name", "unknown") if involved else "unknown"
                reason = getattr(event_obj, "reason", "Unknown")
                message = getattr(event_obj, "message", "No message")

                prompt = (
                    f"Automatic investigation: repeated Warning event on {ns}/{name}.\n"
                    f"Reason: {reason}\nMessage: {message}\n\n"
                    f"Diagnose the root cause and recommend remediation."
                )

                ctx = TaskContext(task_id=f"auto-{key}", channel="k8s-event", namespace=ns)
                await run_task(prompt, context=ctx)
            except Exception:
                logger.exception("k8s_events.auto_failed", key=key)
