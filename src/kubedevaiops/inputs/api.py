"""FastAPI REST gateway with metrics and observability."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from kubedevaiops.agent.supervisor import run_task
from kubedevaiops.agent.memory import TaskContext
from kubedevaiops.config import get_settings
from kubedevaiops.executor.middleware import get_execution_stats
from kubedevaiops.interop import (
    get_agent_manifest,
    get_portable_skill_manifest,
    list_portable_skill_manifests,
)
from kubedevaiops.outputs.audit import log_event
from kubedevaiops.skills.base import get_registry

logger = structlog.get_logger(__name__)

_task_history: list[dict[str, Any]] = []
_concurrent_tasks = 0


class TaskRequest(BaseModel):
    prompt: str
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


def create_app() -> FastAPI:
    cfg = get_settings()
    app = FastAPI(
        title="Kopilot",
        description="Autonomous AI-powered Kubernetes operations agent",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.api.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", response_model=HealthResponse)
    async def health():
        reg = get_registry()
        return HealthResponse(
            status="healthy",
            version="0.1.0",
            skills_loaded=len(reg.list_names()),
            llm_provider=cfg.llm.provider.value,
        )

    @app.get("/readyz")
    async def readyz():
        return {"ready": True}

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

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics():
        """Prometheus-compatible metrics endpoint."""
        from prometheus_client import (
            CollectorRegistry,
            Gauge,
            Counter,
            generate_latest,
        )

        registry = CollectorRegistry()
        stats = get_execution_stats()

        g = Gauge("kubedevaiops_concurrent_tasks", "Current concurrent tasks", registry=registry)
        g.set(_concurrent_tasks)

        for key, val in stats.items():
            c = Counter(f"kubedevaiops_executor_{key}_total", f"Executor {key}", registry=registry)
            c.inc(val)

        skills = Gauge("kubedevaiops_skills_loaded", "Loaded skills", registry=registry)
        skills.set(len(get_registry().list_names()))

        tasks_total = Gauge("kubedevaiops_tasks_total", "Total tasks processed", registry=registry)
        tasks_total.set(len(_task_history))

        return generate_latest(registry).decode()

    @app.get("/tasks/history")
    async def task_history(limit: int = 20):
        """Return recent task results for observability."""
        return _task_history[-limit:]

    @app.post("/tasks", response_model=TaskResponse)
    async def submit_task(req: TaskRequest):
        global _concurrent_tasks  # noqa: PLW0603
        safety_cfg = cfg.safety

        if _concurrent_tasks >= safety_cfg.max_concurrent_tasks:
            raise HTTPException(
                status_code=429,
                detail=f"Max concurrent tasks ({safety_cfg.max_concurrent_tasks}) reached",
            )

        task_id = str(uuid.uuid4())
        ctx = TaskContext(
            task_id=task_id,
            channel="api",
            user=req.user,
            namespace=req.namespace,
            metadata=req.metadata,
        )
        log_event("task.submitted", task_id=task_id, prompt=req.prompt[:200])

        _concurrent_tasks += 1
        try:
            result = await run_task(req.prompt, context=ctx, reflect=req.reflect)
        except Exception as exc:
            logger.exception("task.failed", task_id=task_id)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            _concurrent_tasks -= 1

        _task_history.append({
            "task_id": result["task_id"],
            "prompt": req.prompt[:200],
            "risk_level": result["risk_level"],
            "elapsed_ms": result.get("elapsed_ms", 0),
            "attempts": result.get("attempts", 1),
        })
        if len(_task_history) > 1000:
            _task_history[:] = _task_history[-500:]

        return TaskResponse(**{k: v for k, v in result.items() if k in TaskResponse.model_fields})

    @app.post("/webhook", response_model=TaskResponse)
    async def webhook(payload: WebhookPayload):
        prompt = payload.payload.get("prompt") or payload.payload.get("description", "")
        if not prompt:
            raise HTTPException(status_code=400, detail="No prompt in payload")

        task_id = str(uuid.uuid4())
        ctx = TaskContext(task_id=task_id, channel=payload.source, metadata=payload.payload)
        log_event("webhook.received", source=payload.source, task_id=task_id)

        result = await run_task(prompt, context=ctx)

        _task_history.append({
            "task_id": result["task_id"],
            "prompt": prompt[:200],
            "risk_level": result["risk_level"],
            "elapsed_ms": result.get("elapsed_ms", 0),
        })

        return TaskResponse(**{k: v for k, v in result.items() if k in TaskResponse.model_fields})

    return app
