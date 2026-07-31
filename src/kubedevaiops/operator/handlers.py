"""Kopf handlers for KubeDevAIOps CRDs.

Implements reconciliation with status conditions, idempotency guards, and
update handling for AITask, AISkill, and AIPolicy resources.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

import kopf
import structlog

from kubedevaiops.outputs.audit import log_event

logger = structlog.get_logger(__name__)

GROUP = "kubedevaiops.io"
VERSION = "v1alpha1"

_TERMINAL_PHASES = {"Completed", "Failed"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _spec_hash(spec) -> str:
    return hashlib.sha256(
        json.dumps(dict(spec), sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def _set_condition(
    patch, cond_type: str, status: str, reason: str, message: str,
    existing_conditions: list | None = None,
) -> None:
    """Set a status condition following K8s API conventions.

    lastTransitionTime only changes when the condition's status value
    actually transitions.
    """
    conditions = list(existing_conditions or patch.status.get("conditions") or [])
    if not isinstance(conditions, list):
        conditions = []

    for c in conditions:
        if c.get("type") == cond_type:
            if c.get("status") != status:
                c["lastTransitionTime"] = _now_iso()
            c["status"] = status
            c["reason"] = reason
            c["message"] = message[:500]
            patch.status["conditions"] = conditions
            return

    conditions.append({
        "type": cond_type,
        "status": status,
        "reason": reason,
        "message": message[:500],
        "lastTransitionTime": _now_iso(),
    })
    patch.status["conditions"] = conditions


# ── AITask ──────────────────────────────────────────────────────────────────


async def _run_aitask(spec, name, namespace, patch, status) -> None:
    """Execute an AITask once; idempotent across kopf retries and re-syncs."""
    from kubedevaiops.agent.memory import TaskContext
    from kubedevaiops.agent.supervisor import run_task

    status = status or {}
    spec_hash = _spec_hash(spec)

    # Idempotency guard: skip when this exact spec already ran to completion.
    if status.get("phase") in _TERMINAL_PHASES and status.get("specHash") == spec_hash:
        logger.info("operator.aitask.already_processed", name=name)
        return

    prompt = spec.get("prompt", "")
    if not prompt:
        patch.status["phase"] = "Failed"
        patch.status["specHash"] = spec_hash
        _set_condition(
            patch, "Ready", "False", "InvalidSpec", "spec.prompt is required",
            existing_conditions=status.get("conditions"),
        )
        return

    task_id = str(uuid.uuid4())
    patch.status["phase"] = "Executing"
    patch.status["taskId"] = task_id
    patch.status["specHash"] = spec_hash
    patch.status["startedAt"] = _now_iso()
    log_event("operator.aitask.started", task_id=task_id, name=name)

    ctx = TaskContext(
        task_id=task_id,
        channel="operator",
        namespace=namespace,
        metadata={"resource_name": name},
    )

    try:
        reflect = spec.get("reflect", False)
        result = await run_task(prompt, context=ctx, reflect=reflect)
        patch.status["phase"] = "Completed"
        patch.status["result"] = result.get("answer", "")[:4096]
        patch.status["riskLevel"] = result.get("risk_level", "low")
        patch.status["completedAt"] = _now_iso()
        patch.status["elapsedMs"] = result.get("elapsed_ms", 0)
        _set_condition(
            patch, "Ready", "True", "Completed", "Task completed successfully",
            existing_conditions=status.get("conditions"),
        )
    except Exception as exc:
        logger.exception("operator.aitask.failed", task_id=task_id)
        patch.status["phase"] = "Failed"
        patch.status["message"] = str(exc)[:500]
        patch.status["completedAt"] = _now_iso()
        _set_condition(
            patch, "Ready", "False", "ExecutionFailed", str(exc)[:500],
            existing_conditions=status.get("conditions"),
        )
        # Do not re-raise: kopf would retry and re-execute a task whose side
        # effects may already have happened. Failures are terminal and
        # surfaced in status.


@kopf.on.create(GROUP, VERSION, "aitasks")
async def on_aitask_create(spec, name, namespace, patch, status, **_):
    """Handle AITask creation: validate spec, execute task, update status."""
    await _run_aitask(spec, name, namespace, patch, status)


@kopf.on.update(GROUP, VERSION, "aitasks", field="spec")
async def on_aitask_update(spec, name, namespace, patch, status, **_):
    """Re-execute when the spec actually changes (guarded by spec hash)."""
    log_event("operator.aitask.updated", name=name)
    await _run_aitask(spec, name, namespace, patch, status)


@kopf.on.delete(GROUP, VERSION, "aitasks")
async def on_aitask_delete(name, **_):
    """Record deletion; AITasks own no external resources to clean up."""
    log_event("operator.aitask.deleted", name=name)


# ── AISkill ─────────────────────────────────────────────────────────────────


@kopf.on.create(GROUP, VERSION, "aiskills")
async def on_aiskill_create(spec, name, patch, status, **_):
    """Register a skill from a CRD resource."""
    enabled = spec.get("enabled", True)
    logger.info("operator.aiskill.created", skill=name, enabled=enabled)

    patch.status["phase"] = "Loaded" if enabled else "Disabled"
    _set_condition(
        patch, "Ready",
        "True" if enabled else "False",
        "Loaded" if enabled else "Disabled",
        f"Skill '{name}' {'loaded' if enabled else 'disabled'}",
        existing_conditions=(status or {}).get("conditions"),
    )
    log_event("operator.aiskill.loaded", name=name, enabled=enabled)


@kopf.on.update(GROUP, VERSION, "aiskills", field="spec")
async def on_aiskill_update(spec, name, patch, status, **_):
    """Handle skill spec updates (enable/disable toggle)."""
    enabled = spec.get("enabled", True)
    logger.info("operator.aiskill.updated", skill=name, enabled=enabled)
    patch.status["phase"] = "Loaded" if enabled else "Disabled"
    _set_condition(
        patch, "Ready",
        "True" if enabled else "False",
        "Updated",
        f"Skill '{name}' {'enabled' if enabled else 'disabled'}",
        existing_conditions=(status or {}).get("conditions"),
    )


# ── AIPolicy ────────────────────────────────────────────────────────────────


@kopf.on.create(GROUP, VERSION, "aipolicies")
async def on_aipolicy_create(spec, name, patch, status, **_):
    """Register an AI policy."""
    logger.info("operator.aipolicy.created", policy=name)
    patch.status["phase"] = "Active"
    _set_condition(
        patch, "Ready", "True", "Active", f"Policy '{name}' is active",
        existing_conditions=(status or {}).get("conditions"),
    )
    log_event("operator.aipolicy.loaded", name=name)


@kopf.on.update(GROUP, VERSION, "aipolicies", field="spec")
async def on_aipolicy_update(spec, name, patch, status, **_):
    """Handle policy updates."""
    logger.info("operator.aipolicy.updated", policy=name)
    patch.status["phase"] = "Active"
    _set_condition(
        patch, "Ready", "True", "Updated", f"Policy '{name}' updated",
        existing_conditions=(status or {}).get("conditions"),
    )
    log_event("operator.aipolicy.updated", name=name)
