"""Tests for Kopf operator handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class MockPatch:
    """Simulates kopf's patch object for status updates."""

    def __init__(self):
        self.status = {}


@pytest.mark.asyncio
async def test_aitask_create_missing_prompt():
    from kubedevaiops.operator.handlers import on_aitask_create

    patch_obj = MockPatch()
    await on_aitask_create(spec={}, name="test", namespace="default", patch=patch_obj)
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
        )

    assert patch_obj.status["phase"] == "Completed"
    assert "All good." in patch_obj.status["result"]
    assert "conditions" in patch_obj.status


@pytest.mark.asyncio
async def test_aitask_create_failure():
    from kubedevaiops.operator.handlers import on_aitask_create

    mock_run = AsyncMock(side_effect=RuntimeError("LLM timeout"))

    patch_obj = MockPatch()
    with patch("kubedevaiops.agent.supervisor.run_task", mock_run):
        await on_aitask_create(
            spec={"prompt": "do something"},
            name="fail-task",
            namespace="default",
            patch=patch_obj,
        )

    assert patch_obj.status["phase"] == "Failed"
    assert "LLM timeout" in patch_obj.status["message"]


@pytest.mark.asyncio
async def test_aiskill_create():
    from kubedevaiops.operator.handlers import on_aiskill_create

    patch_obj = MockPatch()
    await on_aiskill_create(
        spec={"enabled": True, "name": "test-skill"},
        name="test-skill",
        patch=patch_obj,
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
    )
    assert patch_obj.status["phase"] == "Active"
