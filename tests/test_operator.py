"""Tests for Kopf operator handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class MockPatch:
    """Simulates kopf's patch object for status updates."""

    def __init__(self):
        self.status = {}


@pytest.mark.asyncio
async def test_aitask_create_missing_prompt():
    from kopilot.operator.handlers import on_aitask_create

    patch_obj = MockPatch()
    await on_aitask_create(spec={}, name="test", namespace="default", patch=patch_obj, status={})
    assert patch_obj.status["phase"] == "Failed"
    assert "conditions" in patch_obj.status


@pytest.mark.asyncio
async def test_aitask_create_success():
    from kopilot.operator.handlers import on_aitask_create

    mock_run = AsyncMock(return_value={
        "task_id": "t-1",
        "answer": "All good.",
        "risk_level": "low",
        "elapsed_ms": 100,
    })

    patch_obj = MockPatch()
    with patch("kopilot.agent.supervisor.run_task", mock_run):
        await on_aitask_create(
            spec={"prompt": "check pods"},
            name="test-task",
            namespace="default",
            patch=patch_obj,
            status={},
        )

    assert patch_obj.status["phase"] == "Completed"
    assert "All good." in patch_obj.status["result"]
    assert "conditions" in patch_obj.status
    assert patch_obj.status["specHash"]


@pytest.mark.asyncio
async def test_aitask_create_failure_is_terminal():
    """Execution failures set Failed status and do NOT re-raise (no kopf retry)."""
    from kopilot.operator.handlers import on_aitask_create

    mock_run = AsyncMock(side_effect=RuntimeError("LLM timeout"))

    patch_obj = MockPatch()
    with patch("kopilot.agent.supervisor.run_task", mock_run):
        await on_aitask_create(
            spec={"prompt": "do something"},
            name="fail-task",
            namespace="default",
            patch=patch_obj,
            status={},
        )

    assert patch_obj.status["phase"] == "Failed"
    assert "LLM timeout" in patch_obj.status["message"]


@pytest.mark.asyncio
async def test_aitask_idempotent_on_retry():
    """A completed task with an unchanged spec must not re-execute."""
    from kopilot.operator.handlers import _spec_hash, on_aitask_create

    spec = {"prompt": "check pods"}
    mock_run = AsyncMock()

    patch_obj = MockPatch()
    existing_status = {"phase": "Completed", "specHash": _spec_hash(spec)}
    with patch("kopilot.agent.supervisor.run_task", mock_run):
        await on_aitask_create(
            spec=spec, name="done-task", namespace="default",
            patch=patch_obj, status=existing_status,
        )

    mock_run.assert_not_awaited()
    assert patch_obj.status == {}


@pytest.mark.asyncio
async def test_aitask_update_reruns_on_spec_change():
    from kopilot.operator.handlers import _spec_hash, on_aitask_update

    mock_run = AsyncMock(return_value={
        "task_id": "t-2", "answer": "Rerun done.", "risk_level": "low", "elapsed_ms": 5,
    })

    patch_obj = MockPatch()
    old_status = {"phase": "Completed", "specHash": _spec_hash({"prompt": "old prompt"})}
    with patch("kopilot.agent.supervisor.run_task", mock_run):
        await on_aitask_update(
            spec={"prompt": "new prompt"}, name="task", namespace="default",
            patch=patch_obj, status=old_status,
        )

    mock_run.assert_awaited_once()
    assert patch_obj.status["phase"] == "Completed"


@pytest.mark.asyncio
async def test_condition_transition_time_stable():
    """lastTransitionTime must not change when the condition status is unchanged."""
    from kopilot.operator.handlers import _set_condition

    patch_obj = MockPatch()
    _set_condition(patch_obj, "Ready", "True", "Completed", "done")
    first = patch_obj.status["conditions"][0]["lastTransitionTime"]

    _set_condition(
        patch_obj, "Ready", "True", "Completed", "done again",
        existing_conditions=patch_obj.status["conditions"],
    )
    assert patch_obj.status["conditions"][0]["lastTransitionTime"] == first
    assert patch_obj.status["conditions"][0]["message"] == "done again"


@pytest.mark.asyncio
async def test_aiskill_create():
    from kopilot.operator.handlers import on_aiskill_create

    patch_obj = MockPatch()
    await on_aiskill_create(
        spec={"enabled": True, "name": "test-skill"},
        name="test-skill",
        patch=patch_obj,
        status={},
    )
    # enabled but without a systemPrompt: nothing can be loaded
    assert patch_obj.status["phase"] == "Invalid"


@pytest.mark.asyncio
async def test_aiskill_disabled():
    from kopilot.operator.handlers import on_aiskill_create

    patch_obj = MockPatch()
    await on_aiskill_create(
        spec={"enabled": False},
        name="disabled-skill",
        patch=patch_obj,
        status={},
    )
    assert patch_obj.status["phase"] == "Disabled"


@pytest.mark.asyncio
async def test_aipolicy_create():
    from kopilot.operator.handlers import on_aipolicy_create

    patch_obj = MockPatch()
    await on_aipolicy_create(
        spec={"rules": []},
        name="test-policy",
        patch=patch_obj,
        status={},
    )
    assert patch_obj.status["phase"] == "Active"


# ── AISkill live registration ────────────────────────────────────────────────


def _skill_spec(**overrides):
    spec = {
        "enabled": True,
        "displayName": "FinOps Extras",
        "description": "Org-specific cost checks",
        "category": "finops",
        "systemPrompt": "You are the org FinOps reviewer.",
        "documentation": "Check quota annotations.",
    }
    spec.update(overrides)
    return spec


@pytest.mark.asyncio
async def test_aiskill_create_registers_skill():
    from unittest.mock import MagicMock

    from kopilot.operator.handlers import on_aiskill_create

    registry = MagicMock()
    patch_obj = MockPatch()
    with patch("kopilot.skills.base.get_registry", return_value=registry):
        await on_aiskill_create(
            spec=_skill_spec(), name="finops-extras", patch=patch_obj, status={}
        )

    assert patch_obj.status["phase"] == "Loaded"
    registry.register.assert_called_once()
    defn = registry.register.call_args.args[0]
    assert defn.name == "finops-extras"
    assert defn.system_prompt == "You are the org FinOps reviewer."
    assert defn.source == "crd:finops-extras"


@pytest.mark.asyncio
async def test_aiskill_create_without_prompt_is_invalid():
    from unittest.mock import MagicMock

    from kopilot.operator.handlers import on_aiskill_create

    registry = MagicMock()
    patch_obj = MockPatch()
    with patch("kopilot.skills.base.get_registry", return_value=registry):
        await on_aiskill_create(
            spec=_skill_spec(systemPrompt=""), name="broken", patch=patch_obj, status={}
        )

    assert patch_obj.status["phase"] == "Invalid"
    registry.register.assert_not_called()


@pytest.mark.asyncio
async def test_aiskill_disabled_unregisters():
    from unittest.mock import MagicMock

    from kopilot.operator.handlers import on_aiskill_create, on_aiskill_update

    registry = MagicMock()
    patch_obj = MockPatch()
    with patch("kopilot.skills.base.get_registry", return_value=registry):
        await on_aiskill_create(
            spec=_skill_spec(enabled=False), name="off-skill", patch=patch_obj, status={}
        )
        assert patch_obj.status["phase"] == "Disabled"
        registry.unregister.assert_called_with("off-skill")

        patch_obj2 = MockPatch()
        await on_aiskill_update(
            spec=_skill_spec(enabled=False), name="off-skill", patch=patch_obj2, status={}
        )
        assert patch_obj2.status["phase"] == "Disabled"


@pytest.mark.asyncio
async def test_aiskill_delete_unregisters():
    from unittest.mock import MagicMock

    from kopilot.operator.handlers import on_aiskill_delete

    registry = MagicMock()
    with patch("kopilot.skills.base.get_registry", return_value=registry):
        await on_aiskill_delete(name="finops-extras")
    registry.unregister.assert_called_with("finops-extras")


@pytest.mark.asyncio
async def test_aiskill_register_failure_sets_failed():
    from unittest.mock import MagicMock

    from kopilot.operator.handlers import on_aiskill_create

    registry = MagicMock()
    registry.register.side_effect = RuntimeError("no LLM configured")
    patch_obj = MockPatch()
    with patch("kopilot.skills.base.get_registry", return_value=registry):
        await on_aiskill_create(
            spec=_skill_spec(), name="finops-extras", patch=patch_obj, status={}
        )

    assert patch_obj.status["phase"] == "Failed"


# ── AIPolicy → autonomy engine ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aipolicy_autopilot_grant():
    from kopilot.executor.autonomy import get_engine
    from kopilot.operator.handlers import on_aipolicy_create

    patch_obj = MockPatch()
    await on_aipolicy_create(
        spec={"autonomyLevel": 2, "namespaces": ["staging", "qa"]},
        name="staging-autopilot",
        patch=patch_obj,
        status={},
    )
    assert patch_obj.status["phase"] == "Active"
    snap = get_engine().snapshot()
    assert {"name": "staging-autopilot", "namespaces": ["staging", "qa"]} in snap["grants"]


@pytest.mark.asyncio
async def test_aipolicy_brake():
    from kopilot.executor.autonomy import get_engine
    from kopilot.operator.handlers import on_aipolicy_create, on_aipolicy_delete

    patch_obj = MockPatch()
    await on_aipolicy_create(
        spec={"autonomyLevel": 0}, name="emergency-stop", patch=patch_obj, status={}
    )
    assert get_engine().snapshot()["observe"] is True
    assert patch_obj.status["phase"] == "Active"

    await on_aipolicy_delete(spec={"autonomyLevel": 0}, name="emergency-stop")
    assert get_engine().snapshot()["observe"] is False


@pytest.mark.asyncio
async def test_aipolicy_autopilot_without_namespaces_is_invalid():
    from kopilot.executor.autonomy import get_engine
    from kopilot.operator.handlers import on_aipolicy_create

    patch_obj = MockPatch()
    await on_aipolicy_create(
        spec={"autonomyLevel": 2}, name="bad-policy", patch=patch_obj, status={}
    )
    assert patch_obj.status["phase"] == "Invalid"
    assert get_engine().snapshot()["grants"] == []


@pytest.mark.asyncio
async def test_aipolicy_update_to_copilot_removes_grant():
    from kopilot.executor.autonomy import get_engine
    from kopilot.operator.handlers import on_aipolicy_create, on_aipolicy_update

    patch_obj = MockPatch()
    await on_aipolicy_create(
        spec={"autonomyLevel": 2, "namespaces": ["staging"]},
        name="p1", patch=patch_obj, status={},
    )
    assert get_engine().snapshot()["grants"]

    patch_obj2 = MockPatch()
    await on_aipolicy_update(spec={"autonomyLevel": 1}, name="p1", patch=patch_obj2, status={})
    assert get_engine().snapshot()["grants"] == []


@pytest.mark.asyncio
async def test_aipolicy_delete_removes_grant():
    from kopilot.executor.autonomy import get_engine
    from kopilot.operator.handlers import on_aipolicy_create, on_aipolicy_delete

    patch_obj = MockPatch()
    await on_aipolicy_create(
        spec={"autonomyLevel": 2, "namespaces": ["staging"]},
        name="p2", patch=patch_obj, status={},
    )
    await on_aipolicy_delete(spec={"autonomyLevel": 2, "namespaces": ["staging"]}, name="p2")
    assert get_engine().snapshot()["grants"] == []


# ── AIPolicy namespace containment ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_aipolicy_grant_confined_to_own_namespace():
    from kopilot.executor.autonomy import get_engine
    from kopilot.operator.handlers import on_aipolicy_create

    patch_obj = MockPatch()
    await on_aipolicy_create(
        spec={"autonomyLevel": 2, "namespaces": ["staging"]},
        name="ok-policy", namespace="staging", patch=patch_obj, status={},
    )
    assert patch_obj.status["phase"] == "Active"
    assert {"name": "ok-policy", "namespaces": ["staging"]} in get_engine().snapshot()["grants"]


@pytest.mark.asyncio
async def test_aipolicy_cannot_grant_outside_its_namespace():
    """A policy in dev must not hand itself autopilot over prod."""
    from kopilot.executor.autonomy import get_engine
    from kopilot.operator.handlers import on_aipolicy_create

    patch_obj = MockPatch()
    await on_aipolicy_create(
        spec={"autonomyLevel": 2, "namespaces": ["dev", "prod"]},
        name="sneaky", namespace="dev", patch=patch_obj, status={},
    )
    assert patch_obj.status["phase"] == "Invalid"
    cond = patch_obj.status["conditions"][0]
    assert cond["reason"] == "NamespaceEscape"
    assert "prod" in cond["message"]
    assert get_engine().snapshot()["grants"] == []


@pytest.mark.asyncio
async def test_aipolicy_namespace_escape_revokes_a_prior_grant():
    """Editing a valid policy into an escaping one must drop its old grant."""
    from kopilot.executor.autonomy import get_engine
    from kopilot.operator.handlers import on_aipolicy_create, on_aipolicy_update

    await on_aipolicy_create(
        spec={"autonomyLevel": 2, "namespaces": ["dev"]},
        name="drift", namespace="dev", patch=MockPatch(), status={},
    )
    assert get_engine().snapshot()["grants"]

    await on_aipolicy_update(
        spec={"autonomyLevel": 2, "namespaces": ["dev", "prod"]},
        name="drift", namespace="dev", patch=MockPatch(), status={},
    )
    assert get_engine().snapshot()["grants"] == []


# ── Resume: rebuilding in-process state after an operator restart ────────────


def test_resume_handlers_are_registered_with_kopf():
    """The bug was structural: no resume handler existed at all.

    Asserting on kopf's own registry proves the decorators wired up, not just
    that the functions are callable.
    """
    import kopf

    import kopilot.operator.handlers  # noqa: F401 — registers the decorators

    resumed = {
        h.selector.any_name
        for h in kopf.get_default_registry()._changing._handlers
        if h.reason is None and getattr(h, "initial", False)
    }
    assert {"aitasks", "aiskills", "aipolicies"} <= resumed


@pytest.mark.asyncio
async def test_aipolicy_resume_restores_autopilot_grant():
    from kopilot.executor.autonomy import get_engine, reset_engine
    from kopilot.operator.handlers import on_aipolicy_create, on_aipolicy_resume

    spec = {"autonomyLevel": 2, "namespaces": ["staging"]}
    await on_aipolicy_create(
        spec=spec, name="staging-autopilot", namespace="staging",
        patch=MockPatch(), status={},
    )
    assert get_engine().snapshot()["grants"]

    # The pod dies: in-process state goes with it, the CR stays.
    reset_engine()
    assert get_engine().snapshot()["grants"] == []

    patch_obj = MockPatch()
    await on_aipolicy_resume(
        spec=spec, name="staging-autopilot", namespace="staging",
        patch=patch_obj, status={},
    )
    assert patch_obj.status["phase"] == "Active"
    assert {"name": "staging-autopilot", "namespaces": ["staging"]} in (
        get_engine().snapshot()["grants"]
    )


@pytest.mark.asyncio
async def test_aipolicy_resume_restores_emergency_brake():
    """The brake must survive a restart, or a restart silently un-brakes."""
    from kopilot.executor.autonomy import get_engine, reset_engine
    from kopilot.operator.handlers import on_aipolicy_resume

    reset_engine()
    assert get_engine().snapshot()["observe"] is False

    patch_obj = MockPatch()
    await on_aipolicy_resume(
        spec={"autonomyLevel": 0}, name="emergency-stop", namespace="kopilot",
        patch=patch_obj, status={},
    )
    assert get_engine().snapshot()["observe"] is True
    assert patch_obj.status["phase"] == "Active"


@pytest.mark.asyncio
async def test_aipolicy_resume_is_idempotent_against_create():
    """Create then resume on the same CR leaves exactly one grant."""
    from kopilot.executor.autonomy import get_engine
    from kopilot.operator.handlers import on_aipolicy_create, on_aipolicy_resume

    spec = {"autonomyLevel": 2, "namespaces": ["qa"]}
    await on_aipolicy_create(
        spec=spec, name="qa-pilot", namespace="qa", patch=MockPatch(), status={}
    )
    await on_aipolicy_resume(
        spec=spec, name="qa-pilot", namespace="qa", patch=MockPatch(), status={}
    )
    await on_aipolicy_resume(
        spec=spec, name="qa-pilot", namespace="qa", patch=MockPatch(), status={}
    )

    assert get_engine().snapshot()["grants"] == [
        {"name": "qa-pilot", "namespaces": ["qa"]}
    ]


@pytest.mark.asyncio
async def test_aiskill_resume_reregisters_skill():
    from unittest.mock import MagicMock

    from kopilot.operator.handlers import on_aiskill_resume

    registry = MagicMock()
    patch_obj = MockPatch()
    with patch("kopilot.skills.base.get_registry", return_value=registry):
        await on_aiskill_resume(
            spec=_skill_spec(), name="finops-extras", patch=patch_obj, status={}
        )

    assert patch_obj.status["phase"] == "Loaded"
    registry.register.assert_called_once()
    assert registry.register.call_args.args[0].source == "crd:finops-extras"


@pytest.mark.asyncio
async def test_aiskill_resume_respects_disabled():
    from unittest.mock import MagicMock

    from kopilot.operator.handlers import on_aiskill_resume

    registry = MagicMock()
    patch_obj = MockPatch()
    with patch("kopilot.skills.base.get_registry", return_value=registry):
        await on_aiskill_resume(
            spec=_skill_spec(enabled=False), name="off-skill",
            patch=patch_obj, status={},
        )

    assert patch_obj.status["phase"] == "Disabled"
    registry.register.assert_not_called()
    registry.unregister.assert_called_with("off-skill")


@pytest.mark.asyncio
async def test_aitask_resume_fails_a_task_stuck_executing():
    from kopilot.operator.handlers import on_aitask_resume

    patch_obj = MockPatch()
    await on_aitask_resume(
        name="stuck", namespace="default", patch=patch_obj,
        status={"phase": "Executing", "taskId": "t-9"},
    )

    assert patch_obj.status["phase"] == "Failed"
    assert "operator restarted" in patch_obj.status["message"]
    assert patch_obj.status["completedAt"]
    cond = patch_obj.status["conditions"][0]
    assert cond["reason"] == "OperatorRestarted"
    assert cond["status"] == "False"


@pytest.mark.asyncio
async def test_aitask_resume_never_re_executes():
    """Resume must not run the command again: side effects may have landed."""
    from unittest.mock import AsyncMock as _AsyncMock

    from kopilot.operator.handlers import on_aitask_resume

    mock_run = _AsyncMock()
    with patch("kopilot.agent.supervisor.run_task", mock_run):
        await on_aitask_resume(
            name="stuck", namespace="default", patch=MockPatch(),
            status={"phase": "Executing"},
        )
    mock_run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["Completed", "Failed"])
async def test_aitask_resume_leaves_terminal_tasks_untouched(phase):
    from kopilot.operator.handlers import on_aitask_resume

    patch_obj = MockPatch()
    await on_aitask_resume(
        name="done", namespace="default", patch=patch_obj,
        status={"phase": phase, "result": "kept"},
    )
    assert patch_obj.status == {}


@pytest.mark.asyncio
async def test_aitask_resume_leaves_an_unstarted_task_to_the_create_handler():
    """No phase means nothing ran; kopf still fires create for it."""
    from kopilot.operator.handlers import on_aitask_resume

    patch_obj = MockPatch()
    await on_aitask_resume(
        name="fresh", namespace="default", patch=patch_obj, status={}
    )
    assert patch_obj.status == {}


@pytest.mark.asyncio
async def test_aitask_resync_of_in_flight_task_does_not_rerun():
    """A re-sync with an unchanged spec must not re-issue the command."""
    from kopilot.operator.handlers import _spec_hash, on_aitask_update

    spec = {"prompt": "delete the failed pods"}
    mock_run = AsyncMock()

    patch_obj = MockPatch()
    with patch("kopilot.agent.supervisor.run_task", mock_run):
        await on_aitask_update(
            spec=spec, name="in-flight", namespace="default", patch=patch_obj,
            status={"phase": "Executing", "specHash": _spec_hash(spec)},
        )

    mock_run.assert_not_awaited()
    assert patch_obj.status == {}


@pytest.mark.asyncio
async def test_aitask_spec_change_during_execution_still_reruns():
    """The in-flight guard keys on the spec hash, not on the phase alone."""
    from kopilot.operator.handlers import _spec_hash, on_aitask_update

    mock_run = AsyncMock(return_value={
        "task_id": "t-3", "answer": "done", "risk_level": "low", "elapsed_ms": 1,
    })

    patch_obj = MockPatch()
    with patch("kopilot.agent.supervisor.run_task", mock_run):
        await on_aitask_update(
            spec={"prompt": "new"}, name="in-flight", namespace="default",
            patch=patch_obj,
            status={"phase": "Executing", "specHash": _spec_hash({"prompt": "old"})},
        )

    mock_run.assert_awaited_once()
    assert patch_obj.status["phase"] == "Completed"
