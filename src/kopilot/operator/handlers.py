"""Kopf handlers for Kopilot CRDs.

Implements reconciliation with status conditions, idempotency guards, and
update handling for AITask, AISkill, and AIPolicy resources.

In-process state (the autonomy engine's grants and brakes, the skill
registry) lives in memory, so it dies with the pod. The ``@kopf.on.resume``
handlers rebuild it from the live custom resources every time the operator
starts: without them a restart silently reverts the cluster to default
autonomy and to the on-disk skills only. Resume shares the same apply
functions as create/update, and every one of them recomputes state from the
spec instead of mutating it incrementally, so replaying them is a fixed
point.

AITask is the exception: it has side effects, so resume never re-executes.
It only reconciles a task that was still running when the operator stopped.
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


def _emit_event(body, *, type: str, reason: str, message: str) -> None:
    """Post a Kubernetes event for the object, best-effort.

    ``kopf.event`` needs the operator's context vars, which only exist inside
    a running kopf loop; direct calls (tests, ``kopilot ask``) would raise.
    A missing event must never fail a reconcile, so it is swallowed.
    """
    if body is None:
        return
    try:
        kopf.event(body, type=type, reason=reason, message=message[:1000])
    except Exception:  # noqa: BLE001 — events are advisory, never load-bearing
        logger.debug("operator.event.not_posted", reason=reason)


def _spec_hash(spec) -> str:
    return hashlib.sha256(json.dumps(dict(spec), sort_keys=True, default=str).encode()).hexdigest()[
        :16
    ]


def _set_condition(
    patch,
    cond_type: str,
    status: str,
    reason: str,
    message: str,
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

    conditions.append(
        {
            "type": cond_type,
            "status": status,
            "reason": reason,
            "message": message[:500],
            "lastTransitionTime": _now_iso(),
        }
    )
    patch.status["conditions"] = conditions


# ── AITask ──────────────────────────────────────────────────────────────────


async def _run_aitask(spec, name, namespace, patch, status) -> None:
    """Execute an AITask once; idempotent across kopf retries and re-syncs."""
    from kopilot.agent.memory import TaskContext
    from kopilot.agent.supervisor import run_task

    status = status or {}
    spec_hash = _spec_hash(spec)
    prior_phase = status.get("phase")
    same_spec = status.get("specHash") == spec_hash

    # Idempotency guard: skip when this exact spec already ran to completion.
    if prior_phase in _TERMINAL_PHASES and same_spec:
        logger.info("operator.aitask.already_processed", name=name)
        return

    # A re-sync of a task that is still running (or crashed mid-run) must not
    # re-issue its command: the side effects may already have landed. Only a
    # real spec change, which moves the hash, re-executes.
    if prior_phase and prior_phase not in _TERMINAL_PHASES and same_spec:
        logger.warning("operator.aitask.resync_ignored", name=name, phase=prior_phase)
        return

    prompt = spec.get("prompt", "")
    if not prompt:
        patch.status["phase"] = "Failed"
        patch.status["specHash"] = spec_hash
        _set_condition(
            patch,
            "Ready",
            "False",
            "InvalidSpec",
            "spec.prompt is required",
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
            patch,
            "Ready",
            "True",
            "Completed",
            "Task completed successfully",
            existing_conditions=status.get("conditions"),
        )
    except Exception as exc:
        logger.exception("operator.aitask.failed", task_id=task_id)
        patch.status["phase"] = "Failed"
        patch.status["message"] = str(exc)[:500]
        patch.status["completedAt"] = _now_iso()
        _set_condition(
            patch,
            "Ready",
            "False",
            "ExecutionFailed",
            str(exc)[:500],
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


@kopf.on.resume(GROUP, VERSION, "aitasks")
async def on_aitask_resume(name, namespace, patch, status, body=None, **_):
    """Close out a task that was mid-flight when the operator stopped.

    The command may or may not have run, and the operator cannot tell which,
    so the safe default is to fail the task with the reason rather than
    re-execute it. Tasks in a terminal phase are left untouched, and a task
    created while the operator was down carries no phase at all: kopf has no
    handling record for it, so the create handler runs it as usual.
    """
    status = status or {}
    phase = status.get("phase")
    if not phase or phase in _TERMINAL_PHASES:
        return

    message = (
        f"Task was in phase '{phase}' when the operator restarted. Its "
        "command may already have run, so it is marked Failed instead of "
        "being retried. Re-apply the AITask to run it again."
    )
    patch.status["phase"] = "Failed"
    patch.status["message"] = message[:500]
    patch.status["completedAt"] = _now_iso()
    _set_condition(
        patch,
        "Ready",
        "False",
        "OperatorRestarted",
        message,
        existing_conditions=status.get("conditions"),
    )
    _emit_event(body, type="Warning", reason="OperatorRestarted", message=message)
    log_event(
        "operator.aitask.interrupted",
        name=name,
        namespace=namespace,
        phase=phase,
    )


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
            patch,
            "Ready",
            "False",
            "Disabled",
            f"Skill '{name}' is disabled",
            existing_conditions=existing,
        )
        log_event("operator.aiskill.disabled", name=name)
        return

    system_prompt = (spec.get("systemPrompt") or "").strip()
    if not system_prompt:
        patch.status["phase"] = "Invalid"
        _set_condition(
            patch,
            "Ready",
            "False",
            "Invalid",
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
            patch,
            "Ready",
            "False",
            "LoadFailed",
            f"Skill '{name}' failed to load: {exc}",
            existing_conditions=existing,
        )
        log_event("operator.aiskill.load_failed", name=name, error=str(exc))
        return

    patch.status["phase"] = "Loaded"
    patch.status["loaded"] = True
    _set_condition(
        patch,
        "Ready",
        "True",
        "Loaded",
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


@kopf.on.resume(GROUP, VERSION, "aiskills")
async def on_aiskill_resume(spec, name, patch, status, **_):
    """Reload CRD skills into a fresh process on operator start.

    Idempotent against create: ``register`` replaces by name and
    ``unregister`` is a no-op when absent, so the registry lands in the same
    state whichever handler got there first.
    """
    logger.info("operator.aiskill.resumed", skill=name)
    _apply_aiskill(spec, name, patch, status)


@kopf.on.delete(GROUP, VERSION, "aiskills")
async def on_aiskill_delete(name, **_):
    """Remove a deleted CRD skill from the registry."""
    from kopilot.skills.base import get_registry

    get_registry().unregister(name)
    log_event("operator.aiskill.deleted", name=name)


# ── AIPolicy ────────────────────────────────────────────────────────────────


def _apply_aipolicy(spec, name, patch, status, namespace=None, body=None) -> None:
    """Reconcile an AIPolicy into the autonomy engine.

    ``autonomyLevel: 0`` engages a cluster-wide emergency brake, ``2`` grants
    namespace-scoped autopilot, ``1`` (or absent) is explicit copilot and
    clears any prior grant or brake from this policy.

    AIPolicy is a namespaced resource, so an autopilot grant may not reach
    outside the namespace the policy itself lives in: whoever can create a
    policy in ``dev`` must not thereby be granted autopilot in ``prod``.
    The brake is deliberately not contained; restricting the whole cluster
    fails safe, and that is what the emergency brake is for.
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
            patch,
            "Ready",
            "True",
            "BrakeEngaged",
            f"Policy '{name}' holds the emergency brake: all mutations are refused",
            existing_conditions=existing,
        )
        log_event("operator.aipolicy.brake", name=name)
        return

    if level >= 2:
        if not namespaces:
            patch.status["phase"] = "Invalid"
            _set_condition(
                patch,
                "Ready",
                "False",
                "Invalid",
                "autonomyLevel 2 requires spec.namespaces to scope the autopilot",
                existing_conditions=existing,
            )
            log_event("operator.aipolicy.invalid", name=name)
            return

        outside = [ns for ns in namespaces if ns != namespace] if namespace else []
        if outside:
            message = (
                f"Policy '{name}' lives in namespace '{namespace}' and may only "
                f"grant autopilot there; refused for: {', '.join(sorted(outside))}"
            )
            patch.status["phase"] = "Invalid"
            _set_condition(
                patch,
                "Ready",
                "False",
                "NamespaceEscape",
                message,
                existing_conditions=existing,
            )
            _emit_event(body, type="Warning", reason="NamespaceEscape", message=message)
            log_event(
                "operator.aipolicy.namespace_escape",
                name=name,
                namespace=namespace,
                requested=namespaces,
            )
            return

        engine.set_grant(AutopilotGrant(name=name, namespaces=namespaces))
        patch.status["phase"] = "Active"
        _set_condition(
            patch,
            "Ready",
            "True",
            "AutopilotGranted",
            f"Policy '{name}' grants autopilot in: {', '.join(namespaces)}",
            existing_conditions=existing,
        )
        log_event("operator.aipolicy.autopilot", name=name, namespaces=namespaces)
        return

    patch.status["phase"] = "Active"
    _set_condition(
        patch,
        "Ready",
        "True",
        "Copilot",
        f"Policy '{name}' keeps approval-gated copilot behavior",
        existing_conditions=existing,
    )
    log_event("operator.aipolicy.loaded", name=name)


@kopf.on.create(GROUP, VERSION, "aipolicies")
async def on_aipolicy_create(spec, name, patch, status, namespace=None, body=None, **_):
    """Register an AI policy."""
    logger.info("operator.aipolicy.created", policy=name)
    _apply_aipolicy(spec, name, patch, status, namespace=namespace, body=body)


@kopf.on.update(GROUP, VERSION, "aipolicies", field="spec")
async def on_aipolicy_update(spec, name, patch, status, namespace=None, body=None, **_):
    """Handle policy updates."""
    logger.info("operator.aipolicy.updated", policy=name)
    _apply_aipolicy(spec, name, patch, status, namespace=namespace, body=body)


@kopf.on.resume(GROUP, VERSION, "aipolicies")
async def on_aipolicy_resume(spec, name, patch, status, namespace=None, body=None, **_):
    """Rebuild grants and brakes in a fresh process on operator start.

    This is the handler that keeps the emergency brake engaged across a pod
    restart. Idempotent against create: the apply drops this policy's grant
    and brake first, then re-derives both from the spec.
    """
    logger.info("operator.aipolicy.resumed", policy=name)
    _apply_aipolicy(spec, name, patch, status, namespace=namespace, body=body)


@kopf.on.delete(GROUP, VERSION, "aipolicies")
async def on_aipolicy_delete(name, **_):
    """Release any grant or brake this policy held."""
    from kopilot.executor.autonomy import get_engine

    engine = get_engine()
    engine.remove_grant(name)
    engine.clear_brake(name)
    log_event("operator.aipolicy.deleted", name=name)
