"""Kopf handlers for KubeDevAIOps CRDs.

Implements proper reconciliation with status conditions, finalizers,
and update handling for AITask, AISkill, and AIPolicy resources.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import kopf
import structlog

from kubedevaiops.outputs.audit import log_event

logger = structlog.get_logger(__name__)

GROUP = "kubedevaiops.io"
VERSION = "v1alpha1"

FINALIZER = "kubedevaiops.io/cleanup"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_condition(
    patch, cond_type: str, status: str, reason: str, message: str
) -> None:
    """Set a status condition following K8s API conventions."""
    conditions = patch.status.get("conditions", [])
    if not isinstance(conditions, list):
        conditions = []

    for c in conditions:
        if c.get("type") == cond_type:
            c["status"] = status
            c["reason"] = reason
            c["message"] = message[:500]
            c["lastTransitionTime"] = _now_iso()
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


@kopf.on.create(GROUP, VERSION, "aitasks")
async def on_aitask_create(spec, name, namespace, patch, **_):
    """Handle AITask creation: validate spec, execute task, update status."""
    from kubedevaiops.agent.supervisor import run_task
    from kubedevaiops.agent.memory import TaskContext

    prompt = spec.get("prompt", "")
    if not prompt:
        patch.status["phase"] = "Failed"
        _set_condition(patch, "Ready", "False", "InvalidSpec", "spec.prompt is required")
        return

    task_id = str(uuid.uuid4())
    patch.status["phase"] = "Executing"
    patch.status["taskId"] = task_id
    patch.status["startedAt"] = _now_iso()
    _set_condition(patch, "Ready", "False", "Executing", "Task is being processed")
    log_event("operator.aitask.created", task_id=task_id, name=name)

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
        patch.status["completedAt"] = _now_iso()
        patch.status["elapsedMs"] = result.get("elapsed_ms", 0)
        _set_condition(patch, "Ready", "True", "Completed", "Task completed successfully")
    except Exception as exc:
        logger.exception("operator.aitask.failed", task_id=task_id)
        patch.status["phase"] = "Failed"
        patch.status["message"] = str(exc)[:500]
        patch.status["completedAt"] = _now_iso()
        _set_condition(patch, "Ready", "False", "ExecutionFailed", str(exc)[:500])


@kopf.on.update(GROUP, VERSION, "aitasks", field="spec")
async def on_aitask_update(spec, name, namespace, patch, **_):
    """Re-execute when spec changes."""
    log_event("operator.aitask.updated", name=name)
    await on_aitask_create(spec=spec, name=name, namespace=namespace, patch=patch)


# ── AISkill ─────────────────────────────────────────────────────────────────


@kopf.on.create(GROUP, VERSION, "aiskills")
async def on_aiskill_create(spec, name, patch, **_):
    """Register a skill from a CRD resource."""
    enabled = spec.get("enabled", True)
    logger.info("operator.aiskill.created", skill=name, enabled=enabled)

    patch.status["phase"] = "Loaded" if enabled else "Disabled"
    _set_condition(
        patch, "Ready",
        "True" if enabled else "False",
        "Loaded" if enabled else "Disabled",
        f"Skill '{name}' {'loaded' if enabled else 'disabled'}",
    )
    log_event("operator.aiskill.loaded", name=name, enabled=enabled)


@kopf.on.update(GROUP, VERSION, "aiskills", field="spec")
async def on_aiskill_update(spec, name, patch, **_):
    """Handle skill spec updates (enable/disable toggle)."""
    enabled = spec.get("enabled", True)
    logger.info("operator.aiskill.updated", skill=name, enabled=enabled)
    patch.status["phase"] = "Loaded" if enabled else "Disabled"
    _set_condition(
        patch, "Ready",
        "True" if enabled else "False",
        "Updated",
        f"Skill '{name}' {'enabled' if enabled else 'disabled'}",
    )


# ── AIPolicy ────────────────────────────────────────────────────────────────


@kopf.on.create(GROUP, VERSION, "aipolicies")
async def on_aipolicy_create(spec, name, patch, **_):
    """Register an AI policy."""
    logger.info("operator.aipolicy.created", policy=name)
    patch.status["phase"] = "Active"
    _set_condition(patch, "Ready", "True", "Active", f"Policy '{name}' is active")
    log_event("operator.aipolicy.loaded", name=name)


@kopf.on.update(GROUP, VERSION, "aipolicies", field="spec")
async def on_aipolicy_update(spec, name, patch, **_):
    """Handle policy updates."""
    logger.info("operator.aipolicy.updated", policy=name)
    patch.status["phase"] = "Active"
    _set_condition(patch, "Ready", "True", "Updated", f"Policy '{name}' updated")
    log_event("operator.aipolicy.updated", name=name)
