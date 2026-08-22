"""FastAPI REST gateway with authentication, metrics, and observability."""

from __future__ import annotations

import hashlib
import hmac
import secrets as pysecrets
import threading
import uuid
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from kopilot import __version__
from kopilot.agent.memory import TaskContext
from kopilot.agent.supervisor import run_task
from kopilot.config import get_settings
from kopilot.executor.approvals import ApprovalStatus, get_approval_store
from kopilot.executor.middleware import get_execution_stats
from kopilot.interop import (
    get_agent_manifest,
    get_portable_skill_manifest,
    list_portable_skill_manifests,
)
from kopilot.outputs.audit import log_event
from kopilot.skills.base import get_registry

logger = structlog.get_logger(__name__)

_task_history: list[dict[str, Any]] = []
_concurrency_lock = threading.Lock()
_concurrent_tasks = 0

MAX_HISTORY = 1000


class TaskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    namespace: str | None = None
    user: str = "api"
    reflect: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskResponse(BaseModel):
    task_id: str
    answer: str
    risk_level: str = "low"
    elapsed_ms: int = 0
    attempts: int = 1


class HealthResponse(BaseModel):
    status: str
    version: str
    skills_loaded: int
    llm_provider: str


class WebhookPayload(BaseModel):
    source: str
    payload: dict[str, Any] = Field(default_factory=dict)


def _require_auth(request: Request) -> None:
    """Bearer-token guard. Open when API_AUTH_TOKEN is unset (dev mode)."""
    token = get_settings().api.auth_token
    if not token:
        return
    header = request.headers.get("authorization", "")
    provided = header[7:] if header.startswith("Bearer ") else ""
    if not provided or not pysecrets.compare_digest(provided, token):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


def _principal(request: Request) -> str:
    """The authenticated identity behind a request.

    Today that is the bearer credential, recorded as a fingerprint so the
    ledger names the credential without ever storing it.  When auth is
    disabled the request carries no identity and says so.  A client-supplied
    name (``x-kopilot-operator``) never reaches this function: it is display
    only, and anyone can send any value.
    """
    token = get_settings().api.auth_token
    if not token:
        return "unauthenticated"
    header = request.headers.get("authorization", "")
    provided = header[7:] if header.startswith("Bearer ") else ""
    return f"token:{hashlib.sha256(provided.encode()).hexdigest()[:12]}"


def _acquire_task_slot() -> bool:
    global _concurrent_tasks  # noqa: PLW0603
    limit = get_settings().safety.max_concurrent_tasks
    with _concurrency_lock:
        if _concurrent_tasks >= limit:
            return False
        _concurrent_tasks += 1
        return True


def _release_task_slot() -> None:
    global _concurrent_tasks  # noqa: PLW0603
    with _concurrency_lock:
        _concurrent_tasks = max(0, _concurrent_tasks - 1)


def _record_history(prompt: str, result: dict[str, Any]) -> None:
    _task_history.append({
        "task_id": result["task_id"],
        "prompt": prompt[:200],
        "risk_level": result.get("risk_level", "low"),
        "elapsed_ms": result.get("elapsed_ms", 0),
        "attempts": result.get("attempts", 1),
    })
    if len(_task_history) > MAX_HISTORY:
        _task_history[:] = _task_history[-MAX_HISTORY // 2:]


async def _execute(prompt: str, ctx: TaskContext, reflect: bool = False) -> TaskResponse:
    if not _acquire_task_slot():
        limit = get_settings().safety.max_concurrent_tasks
        raise HTTPException(
            status_code=429, detail=f"Max concurrent tasks ({limit}) reached"
        )
    try:
        result = await run_task(prompt, context=ctx, reflect=reflect)
    except Exception:
        logger.exception("task.failed", task_id=ctx.task_id)
        raise HTTPException(
            status_code=500,
            detail=f"Task {ctx.task_id} failed; see server logs for details.",
        ) from None
    finally:
        _release_task_slot()

    _record_history(prompt, result)
    return TaskResponse(**{k: v for k, v in result.items() if k in TaskResponse.model_fields})


def create_app(with_event_watcher: bool = False) -> FastAPI:
    cfg = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not cfg.api.auth_token:
            logger.warning(
                "api.auth_disabled",
                hint="Set API_AUTH_TOKEN to require bearer authentication.",
            )
        watcher = None
        if with_event_watcher:
            try:
                from kopilot.inputs.k8s_events import K8sEventWatcher

                watcher = K8sEventWatcher()
                await watcher.start()
            except Exception:
                logger.warning("k8s_event_watcher.skipped")
                watcher = None
        yield
        if watcher is not None:
            await watcher.stop()

    app = FastAPI(
        title="Kopilot",
        description="Approval-gated AI Kubernetes operations agent",
        version=__version__,
        lifespan=lifespan,
    )
    if cfg.api.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.api.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )

    # ── Public, read-only surface ───────────────────────────────────────────

    @app.get("/health", response_model=HealthResponse)
    async def health():
        reg = get_registry()
        return HealthResponse(
            status="healthy",
            version=__version__,
            skills_loaded=len(reg.list_names()),
            llm_provider=cfg.llm.provider.value,
        )

    @app.get("/readyz")
    async def readyz():
        return {"ready": len(get_registry().list_names()) > 0}

    @app.get("/skills")
    async def list_skills():
        return get_registry().list_details()

    @app.get("/skills/portable")
    async def list_portable_skills():
        return list_portable_skill_manifests()

    @app.get("/skills/portable/{name}")
    async def portable_skill(name: str):
        manifest = get_portable_skill_manifest(name)
        if manifest is None:
            raise HTTPException(status_code=404, detail=f"Unknown skill '{name}'")
        return manifest

    @app.get("/interop")
    async def interop_manifest():
        return get_agent_manifest()

    @app.get("/.well-known/agent-manifest.json")
    async def well_known_agent_manifest():
        return get_agent_manifest()

    # ── Authenticated surface ───────────────────────────────────────────────

    @app.get("/metrics", response_class=PlainTextResponse, dependencies=[Depends(_require_auth)])
    async def metrics():
        """Prometheus-compatible metrics endpoint."""
        from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest

        registry = CollectorRegistry()
        stats = get_execution_stats()

        g = Gauge("kopilot_concurrent_tasks", "Current concurrent tasks", registry=registry)
        g.set(_concurrent_tasks)

        for key, val in stats.items():
            c = Counter(f"kopilot_executor_{key}_total", f"Executor {key}", registry=registry)
            c.inc(val)

        skills = Gauge("kopilot_skills_loaded", "Loaded skills", registry=registry)
        skills.set(len(get_registry().list_names()))

        tasks_total = Gauge("kopilot_tasks_total", "Total tasks processed", registry=registry)
        tasks_total.set(len(_task_history))

        pending = Gauge(
            "kopilot_approvals_pending", "Pending approval requests", registry=registry
        )
        pending.set(len(get_approval_store().list(ApprovalStatus.PENDING)))

        return generate_latest(registry).decode()

    @app.get("/tasks/history", dependencies=[Depends(_require_auth)])
    async def task_history(limit: int = 20):
        """Return recent task results for observability."""
        return _task_history[-max(1, min(limit, MAX_HISTORY)):]

    @app.post("/tasks", response_model=TaskResponse, dependencies=[Depends(_require_auth)])
    async def submit_task(req: TaskRequest):
        task_id = str(uuid.uuid4())
        ctx = TaskContext(
            task_id=task_id,
            channel="api",
            user=req.user,
            namespace=req.namespace,
            metadata=req.metadata,
        )
        log_event("task.submitted", task_id=task_id, prompt=req.prompt[:200])
        return await _execute(req.prompt, ctx, reflect=req.reflect)

    # ── Approvals ───────────────────────────────────────────────────────────

    @app.get("/autonomy", dependencies=[Depends(_require_auth)])
    async def autonomy_state():
        """Effective autonomy dial: level, brakes, and autopilot grants."""
        from kopilot.executor.autonomy import get_engine

        return get_engine().snapshot()

    @app.get("/approvals", dependencies=[Depends(_require_auth)])
    async def list_approvals(status: str | None = None):
        store = get_approval_store()
        parsed: ApprovalStatus | None = None
        if status is not None:
            try:
                parsed = ApprovalStatus(status)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"Unknown status '{status}'") from exc
        return [r.to_dict() for r in store.list(parsed)]

    @app.post("/approvals/{approval_id}/approve", dependencies=[Depends(_require_auth)])
    async def approve(approval_id: str, request: Request, execute: bool = True):
        """Approve a gated command.

        What you sign is what runs: by default the exact reviewed command is
        executed immediately and its output returned, so no LLM gets a chance
        to rephrase it. Pass ``?execute=false`` to leave a standing single-use
        approval for the agent to consume instead.

        The approver of record is the authenticated principal. Safety and the
        autonomy brake are re-evaluated at execution time, so an approval can
        be spent on a refusal; ``executed`` says which happened.
        """
        store = get_approval_store()
        req = store.approve(
            approval_id,
            decided_by=_principal(request),
            operator_display=request.headers.get("x-kopilot-operator", ""),
        )
        if req is None:
            raise HTTPException(status_code=404, detail="No pending approval with that id")
        if not execute:
            return {**req.to_dict(), "executed": False}

        from kopilot.executor.middleware import execute_approved

        consumed = store.consume_if_approved(req.command)
        outcome = await execute_approved(consumed or req)
        current = store.get(req.id) or consumed or req
        return {**current.to_dict(), "executed": outcome.executed, "output": outcome.output}

    @app.post("/approvals/{approval_id}/deny", dependencies=[Depends(_require_auth)])
    async def deny(approval_id: str, request: Request):
        req = get_approval_store().deny(
            approval_id,
            decided_by=_principal(request),
            operator_display=request.headers.get("x-kopilot-operator", ""),
        )
        if req is None:
            raise HTTPException(status_code=404, detail="No pending approval with that id")
        return req.to_dict()

    # ── Webhooks (HMAC-verified) ────────────────────────────────────────────

    @app.post("/webhook", response_model=TaskResponse)
    async def webhook(request: Request):
        secret = cfg.api.webhook_secret
        if not secret:
            raise HTTPException(
                status_code=403,
                detail="Webhooks are disabled: API_WEBHOOK_SECRET is not configured.",
            )
        body = await request.body()
        signature = request.headers.get("x-kopilot-signature", "")
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not pysecrets.compare_digest(signature, expected):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

        try:
            payload = WebhookPayload.model_validate_json(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Malformed webhook payload") from exc

        prompt = payload.payload.get("prompt") or payload.payload.get("description", "")
        if not prompt:
            raise HTTPException(status_code=400, detail="No prompt in payload")

        task_id = str(uuid.uuid4())
        ctx = TaskContext(task_id=task_id, channel=payload.source, metadata=payload.payload)
        log_event("webhook.received", source=payload.source, task_id=task_id)
        return await _execute(prompt, ctx)

    return app
