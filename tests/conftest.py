"""Shared fixtures for the test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _set_test_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "gpt-oss:20b")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("SAFETY_DRY_RUN_DEFAULT", "true")
    monkeypatch.setenv("SAFETY_REQUIRE_APPROVAL_DESTRUCTIVE", "true")
    monkeypatch.setenv("LOG_FORMAT", "console")

    import kubedevaiops.config as cfg_mod
    cfg_mod._settings = None

    import kubedevaiops.skills.base as skill_mod
    skill_mod._registry = None

    import kubedevaiops.agent.llm as llm_mod
    llm_mod._chat_model = None

    import kubedevaiops.agent.memory as mem_mod
    mem_mod._checkpointer = None


@pytest.fixture
def mock_subprocess(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    async def _fake(*args, **kwargs):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"mocked output", b""))
        proc.kill = MagicMock()
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _fake)
