"""Kopf handlers for Kopilot CRDs.

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

from kopilot.outputs.audit import log_event

logger = structlog.get_logger(__name__)

GROUP = "kopilot-ai.github.io"
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
    from kopilot.agent.memory import TaskContext
    from kopilot.agent.supervisor import run_task

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


def _apply_aiskill(spec, name, patch, status) -> None:
    """Reconcile an AISkill spec into the process-local skill registry.

    Effective in ``kopilot serve``, where the operator thread shares the
    process with the API and supervisor. A standalone ``kopilot operator``
    updates status only; the serve replica loads the skill.
    """
    from kopilot.skills.base import get_registry
    from kopilot.skills.loader import SkillDefinition

    enabled = spec.get("enabled", True)
    existing = (status or {}).get("conditions")

    if not enabled:
        get_registry().unregister(name)
        patch.status["phase"] = "Disabled"
        _set_condition(
            patch, "Ready", "False", "Disabled",
            f"Skill '{name}' is disabled",
            existing_conditions=existing,
        )
        log_event("operator.aiskill.disabled", name=name)
        return

    system_prompt = (spec.get("systemPrompt") or "").strip()
    if not system_prompt:
        patch.status["phase"] = "Invalid"
        _set_condition(
            patch, "Ready", "False", "Invalid",
            "spec.systemPrompt is required to load a skill",
            existing_conditions=existing,
        )
        log_event("operator.aiskill.invalid", name=name)
        return

    defn = SkillDefinition(
        name=name,
        display_name=spec.get("displayName", name),
        description=spec.get("description", ""),
        category=spec.get("category", "custom"),
        system_prompt=system_prompt,
        documentation=spec.get("documentation", ""),
        source=f"crd:{name}",
    )
    try:
        get_registry().register(defn)
    except Exception as exc:
        logger.exception("operator.aiskill.load_failed", skill=name)
        patch.status["phase"] = "Failed"
        _set_condition(
            patch, "Ready", "False", "LoadFailed",
            f"Skill '{name}' failed to load: {exc}",
            existing_conditions=existing,
        )
        log_event("operator.aiskill.load_failed", name=name, error=str(exc))
        return

    patch.status["phase"] = "Loaded"
    patch.status["loaded"] = True
    _set_condition(
        patch, "Ready", "True", "Loaded",
        f"Skill '{name}' loaded into the registry",
        existing_conditions=existing,
    )
    log_event("operator.aiskill.loaded", name=name, enabled=True)


@kopf.on.create(GROUP, VERSION, "aiskills")
async def on_aiskill_create(spec, name, patch, status, **_):
    """Register a skill from a CRD resource."""
    logger.info("operator.aiskill.created", skill=name)
    _apply_aiskill(spec, name, patch, status)


@kopf.on.update(GROUP, VERSION, "aiskills", field="spec")
async def on_aiskill_update(spec, name, patch, status, **_):
    """Handle skill spec updates (content changes and enable/disable)."""
    logger.info("operator.aiskill.updated", skill=name)
    _apply_aiskill(spec, name, patch, status)


@kopf.on.delete(GROUP, VERSION, "aiskills")
async def on_aiskill_delete(name, **_):
    """Remove a deleted CRD skill from the registry."""
    from kopilot.skills.base import get_registry

    get_registry().unregister(name)
    log_event("operator.aiskill.deleted", name=name)


# ── AIPolicy ────────────────────────────────────────────────────────────────


def _apply_aipolicy(spec, name, patch, status) -> None:
    """Reconcile an AIPolicy into the autonomy engine.

    ``autonomyLevel: 0`` engages a cluster-wide emergency brake, ``2`` grants
    namespace-scoped autopilot, ``1`` (or absent) is explicit copilot and
    clears any prior grant or brake from this policy.
    """
    from kopilot.executor.autonomy import AutopilotGrant, get_engine

    engine = get_engine()
    level = int(spec.get("autonomyLevel", 1))
    namespaces = list(spec.get("namespaces") or [])
    existing = (status or {}).get("conditions")

    # Reconcile from a clean slate for this policy name.
    engine.remove_grant(name)
    engine.clear_brake(name)

    if level == 0:
        engine.set_brake(name)
        patch.status["phase"] = "Active"
        _set_condition(
            patch, "Ready", "True", "BrakeEngaged",
            f"Policy '{name}' holds the emergency brake: all mutations are refused",
            existing_conditions=existing,
        )
        log_event("operator.aipolicy.brake", name=name)
        return

    if level >= 2:
        if not namespaces:
            patch.status["phase"] = "Invalid"
            _set_condition(
                patch, "Ready", "False", "Invalid",
                "autonomyLevel 2 requires spec.namespaces to scope the autopilot",
                existing_conditions=existing,
            )
            log_event("operator.aipolicy.invalid", name=name)
            return
        engine.set_grant(AutopilotGrant(name=name, namespaces=namespaces))
        patch.status["phase"] = "Active"
        _set_condition(
            patch, "Ready", "True", "AutopilotGranted",
            f"Policy '{name}' grants autopilot in: {', '.join(namespaces)}",
            existing_conditions=existing,
        )
        log_event("operator.aipolicy.autopilot", name=name, namespaces=namespaces)
        return

    patch.status["phase"] = "Active"
    _set_condition(
        patch, "Ready", "True", "Copilot",
        f"Policy '{name}' keeps approval-gated copilot behavior",
        existing_conditions=existing,
    )
    log_event("operator.aipolicy.loaded", name=name)


@kopf.on.create(GROUP, VERSION, "aipolicies")
async def on_aipolicy_create(spec, name, patch, status, **_):
    """Register an AI policy."""
    logger.info("operator.aipolicy.created", policy=name)
    _apply_aipolicy(spec, name, patch, status)


@kopf.on.update(GROUP, VERSION, "aipolicies", field="spec")
async def on_aipolicy_update(spec, name, patch, status, **_):
    """Handle policy updates."""
    logger.info("operator.aipolicy.updated", policy=name)
    _apply_aipolicy(spec, name, patch, status)


@kopf.on.delete(GROUP, VERSION, "aipolicies")
async def on_aipolicy_delete(name, **_):
    """Release any grant or brake this policy held."""
    from kopilot.executor.autonomy import get_engine

    engine = get_engine()
    engine.remove_grant(name)
    engine.clear_brake(name)
    log_event("operator.aipolicy.deleted", name=name)
