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
