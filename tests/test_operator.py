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
    from kubedevaiops.operator.handlers import on_aitask_create

    patch_obj = MockPatch()
    await on_aitask_create(spec={}, name="test", namespace="default", patch=patch_obj, status={})
    assert patch_obj.status["phase"] == "Failed"
    assert "conditions" in patch_obj.status


@pytest.mark.asyncio
async def test_aitask_create_success():
    from kubedevaiops.operator.handlers import on_aitask_create

    mock_run = AsyncMock(return_value={
        "task_id": "t-1",
        "answer": "All good.",
        "risk_level": "low",
        "elapsed_ms": 100,
    })

    patch_obj = MockPatch()
    with patch("kubedevaiops.agent.supervisor.run_task", mock_run):
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
    from kubedevaiops.operator.handlers import on_aitask_create

    mock_run = AsyncMock(side_effect=RuntimeError("LLM timeout"))

    patch_obj = MockPatch()
    with patch("kubedevaiops.agent.supervisor.run_task", mock_run):
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
    from kubedevaiops.operator.handlers import _spec_hash, on_aitask_create

    spec = {"prompt": "check pods"}
    mock_run = AsyncMock()

    patch_obj = MockPatch()
    existing_status = {"phase": "Completed", "specHash": _spec_hash(spec)}
    with patch("kubedevaiops.agent.supervisor.run_task", mock_run):
        await on_aitask_create(
            spec=spec, name="done-task", namespace="default",
            patch=patch_obj, status=existing_status,
        )

    mock_run.assert_not_awaited()
    assert patch_obj.status == {}


@pytest.mark.asyncio
async def test_aitask_update_reruns_on_spec_change():
    from kubedevaiops.operator.handlers import _spec_hash, on_aitask_update

    mock_run = AsyncMock(return_value={
        "task_id": "t-2", "answer": "Rerun done.", "risk_level": "low", "elapsed_ms": 5,
    })

    patch_obj = MockPatch()
    old_status = {"phase": "Completed", "specHash": _spec_hash({"prompt": "old prompt"})}
    with patch("kubedevaiops.agent.supervisor.run_task", mock_run):
        await on_aitask_update(
            spec={"prompt": "new prompt"}, name="task", namespace="default",
            patch=patch_obj, status=old_status,
        )

    mock_run.assert_awaited_once()
    assert patch_obj.status["phase"] == "Completed"


@pytest.mark.asyncio
async def test_condition_transition_time_stable():
    """lastTransitionTime must not change when the condition status is unchanged."""
    from kubedevaiops.operator.handlers import _set_condition

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
    from kubedevaiops.operator.handlers import on_aiskill_create

    patch_obj = MockPatch()
    await on_aiskill_create(
        spec={"enabled": True, "name": "test-skill"},
        name="test-skill",
        patch=patch_obj,
        status={},
    )
    assert patch_obj.status["phase"] == "Loaded"


@pytest.mark.asyncio
async def test_aiskill_disabled():
    from kubedevaiops.operator.handlers import on_aiskill_create

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
    from kubedevaiops.operator.handlers import on_aipolicy_create

    patch_obj = MockPatch()
    await on_aipolicy_create(
        spec={"rules": []},
        name="test-policy",
        patch=patch_obj,
        status={},
    )
    assert patch_obj.status["phase"] == "Active"
