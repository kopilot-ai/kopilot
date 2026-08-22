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

    import kopilot.config as cfg_mod
    cfg_mod._settings = None

    import kopilot.skills.base as skill_mod
    skill_mod._registry = None

    import kopilot.agent.llm as llm_mod
    llm_mod._chat_model = None

    import kopilot.agent.memory as mem_mod
    mem_mod._checkpointer = None

    import kopilot.executor.approvals as approvals_mod
    approvals_mod._store = None

    from kopilot.executor import autonomy as autonomy_mod
    autonomy_mod.reset_engine()

    # No persistence path in the default test env, so the ledger is off unless
    # a test asks for one with the `ledger` fixture.
    monkeypatch.delenv("LEDGER_PATH", raising=False)
    from kopilot.outputs import audit as audit_mod
    audit_mod.reset_ledger()


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Point the audit ledger at a temp file and hand back its Ledger."""
    from kopilot.outputs import audit as audit_mod

    monkeypatch.setenv("LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    audit_mod.reset_ledger()
    yield audit_mod.get_ledger()
    audit_mod.reset_ledger()


@pytest.fixture
def mock_subprocess(monkeypatch):
    """Stub out subprocess execution in the executor middleware."""
    from kopilot.executor import middleware

    async def _fake_run_once(cmd, timeout):
        return 0, "mocked output"

    monkeypatch.setattr(middleware, "_run_once", _fake_run_once)


@pytest.fixture
def autonomy_staging():
    """Autopilot grant for the staging namespace (max MEDIUM)."""
    from kopilot.executor.autonomy import AutopilotGrant, get_engine, reset_engine

    get_engine().set_grant(
        AutopilotGrant(name="staging-autopilot", namespaces=["staging"])
    )
    yield
    reset_engine()


@pytest.fixture
def autonomy_observe():
    """Observe mode via an emergency-brake policy."""
    from kopilot.executor.autonomy import get_engine, reset_engine

    get_engine().set_brake("test-brake")
    yield
    reset_engine()
