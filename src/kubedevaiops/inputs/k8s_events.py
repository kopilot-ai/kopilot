"""Kubernetes event watcher — triggers sub-agent investigations on anomalies.

Watches Warning events across all namespaces. When a specific resource+reason
combination exceeds the threshold within the time window, triggers an automated
investigation through the supervisor agent.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from kubedevaiops.outputs.audit import log_event

logger = structlog.get_logger(__name__)

EVENT_THRESHOLD = 3
WINDOW_SECONDS = 300
MAX_CONCURRENT_INVESTIGATIONS = 3
BACKOFF_BASE = 5
BACKOFF_MAX = 60


class K8sEventWatcher:
    """Watches K8s Warning events and auto-triggers agent investigation."""

    def __init__(self, threshold: int = EVENT_THRESHOLD, window: int = WINDOW_SECONDS):
        self._counts: dict[str, list[float]] = defaultdict(list)
        self._running = False
        self._threshold = threshold
        self._window = window
        self._active_investigations = 0
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_config)
        self._task = asyncio.create_task(self._watch_loop())
        logger.info("k8s_events.watcher.started", threshold=self._threshold, window=self._window)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
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

    async def _watch_loop(self) -> None:
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
                    await self._process(event)
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("k8s_events.watch_error", backoff=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)

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
            if self._active_investigations < MAX_CONCURRENT_INVESTIGATIONS:
                asyncio.create_task(self._investigate(key, obj))
            else:
                logger.warning("k8s_events.investigation_throttled", key=key)
            self._counts[key].clear()

    async def _investigate(self, key: str, event_obj) -> None:
        from kubedevaiops.agent.supervisor import run_task
        from kubedevaiops.agent.memory import TaskContext

        self._active_investigations += 1
        try:
            involved = getattr(event_obj, "involved_object", None)
            ns = getattr(involved, "namespace", None) or "default" if involved else "default"
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
        finally:
            self._active_investigations -= 1
