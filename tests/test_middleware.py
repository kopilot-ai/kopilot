"""Tests for executor middleware — rate limiting, safety, approvals, read_resource."""

from __future__ import annotations

import pytest

from kubedevaiops.agent.safety import RiskLevel
from kubedevaiops.executor.approvals import get_approval_store
from kubedevaiops.executor.middleware import (
    ToolRateLimiter,
    _pre_flight,
    get_execution_stats,
    read_resource,
    run_helm,
    run_kubectl,
    run_shell,
)


class TestRateLimiter:
    def test_allows_within_limit(self):
        limiter = ToolRateLimiter(max_calls=5, window_seconds=60)
        for _ in range(5):
            assert limiter.check("thread-1")

    def test_blocks_over_limit(self):
        limiter = ToolRateLimiter(max_calls=3, window_seconds=60)
        for _ in range(3):
            limiter.check("thread-1")
        assert not limiter.check("thread-1")

    def test_separate_threads(self):
        limiter = ToolRateLimiter(max_calls=2, window_seconds=60)
        limiter.check("thread-1")
        limiter.check("thread-1")
        assert not limiter.check("thread-1")
        assert limiter.check("thread-2")

    def test_reset(self):
        limiter = ToolRateLimiter(max_calls=1, window_seconds=60)
        limiter.check("thread-1")
        assert not limiter.check("thread-1")
        limiter.reset("thread-1")
        assert limiter.check("thread-1")


class TestPreFlight:
    def test_blocks_rm_rf(self):
        verdict = _pre_flight("rm -rf /")
        assert not verdict.allowed
        assert verdict.risk == RiskLevel.CRITICAL

    @pytest.mark.parametrize("cmd", [
        "rm  -rf  /",
        "rm -fr /",
        "rm -rf --no-preserve-root /",
        "find / -name '*.log' -delete",
        "chmod -R 000 /",
    ])
    def test_blocks_rm_rf_variants(self, cmd):
        assert not _pre_flight(cmd).allowed

    def test_blocks_fork_bomb(self):
        verdict = _pre_flight(":(){ :|:& };:")
        assert not verdict.allowed

    def test_blocks_dd(self):
        verdict = _pre_flight("dd if=/dev/zero of=/dev/sda")
        assert not verdict.allowed

    def test_allows_safe_command(self):
        verdict = _pre_flight("kubectl get pods")
        assert verdict.allowed
        assert verdict.risk == RiskLevel.LOW


@pytest.mark.asyncio
async def test_kubectl_prepends_kubectl(mock_subprocess):
    result = await run_kubectl.ainvoke({"command": "get pods"})
    assert "mocked output" in result


@pytest.mark.asyncio
async def test_kubectl_keeps_existing_prefix(mock_subprocess):
    result = await run_kubectl.ainvoke({"command": "kubectl get pods"})
    assert "mocked output" in result


@pytest.mark.asyncio
async def test_helm_prepends_helm(mock_subprocess):
    result = await run_helm.ainvoke({"command": "list -A"})
    assert "mocked output" in result


@pytest.mark.asyncio
async def test_shell_blocked_dangerous(mock_subprocess):
    result = await run_shell.ainvoke({"command": "rm -rf /"})
    assert "BLOCKED" in result


# ── Approval gating at the executor ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_destructive_kubectl_requires_approval(mock_subprocess):
    result = await run_kubectl.ainvoke({"command": "kubectl delete pod x -n staging"})
    assert "APPROVAL REQUIRED" in result
    assert "mocked output" not in result
    pending = get_approval_store().list()
    assert any("delete pod x" in r.command for r in pending)


@pytest.mark.asyncio
async def test_approved_command_executes(mock_subprocess):
    cmd = "kubectl delete pod x -n staging"
    first = await run_kubectl.ainvoke({"command": cmd})
    assert "APPROVAL REQUIRED" in first

    store = get_approval_store()
    req = next(r for r in store.list() if r.command == cmd)
    store.approve(req.id, decided_by="tester")

    second = await run_kubectl.ainvoke({"command": cmd})
    assert "mocked output" in second

    # Approval is single-use: a third attempt is gated again.
    third = await run_kubectl.ainvoke({"command": cmd})
    assert "APPROVAL REQUIRED" in third


@pytest.mark.asyncio
async def test_denied_command_stays_gated(mock_subprocess):
    cmd = "helm uninstall prod-release -n production"
    first = await run_helm.ainvoke({"command": cmd})
    assert "APPROVAL REQUIRED" in first

    store = get_approval_store()
    req = next(r for r in store.list() if r.command == cmd)
    store.deny(req.id, decided_by="tester")

    second = await run_helm.ainvoke({"command": cmd})
    assert "APPROVAL REQUIRED" in second
    assert "mocked output" not in second


@pytest.mark.asyncio
async def test_protected_namespace_blocked_at_executor(mock_subprocess):
    result = await run_kubectl.ainvoke({"command": "kubectl delete pod x -n kube-system"})
    assert "BLOCKED" in result
    assert "mocked output" not in result


# ── read_resource hardening ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_resource_file_within_allowed_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFETY_READ_PATHS", f'["{tmp_path}"]')
    import kubedevaiops.config as cfg_mod
    cfg_mod._settings = None

    f = tmp_path / "test.txt"
    f.write_text("hello world")
    result = await read_resource.ainvoke({"path_or_url": str(f)})
    assert "hello world" in result


@pytest.mark.asyncio
async def test_read_resource_outside_allowed_root_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFETY_READ_PATHS", f'["{tmp_path}"]')
    import kubedevaiops.config as cfg_mod
    cfg_mod._settings = None

    result = await read_resource.ainvoke({"path_or_url": "/etc/passwd"})
    assert "BLOCKED" in result


@pytest.mark.asyncio
async def test_read_resource_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("SAFETY_READ_PATHS", f'["{tmp_path}"]')
    import kubedevaiops.config as cfg_mod
    cfg_mod._settings = None

    result = await read_resource.ainvoke({"path_or_url": str(tmp_path / "nope.txt")})
    assert "Could not read" in result


@pytest.mark.asyncio
async def test_read_resource_configmap_injection_blocked():
    result = await read_resource.ainvoke(
        {"path_or_url": "configmap:x -n d; kubectl delete ns kube-system #:d"}
    )
    assert "BLOCKED" in result


@pytest.mark.asyncio
async def test_read_resource_url_injection_blocked():
    result = await read_resource.ainvoke(
        {"path_or_url": 'http://example.com" ; rm -rf / ; "'}
    )
    assert "BLOCKED" in result


def test_execution_stats():
    stats = get_execution_stats()
    assert isinstance(stats, dict)


@pytest.mark.asyncio
async def test_risk_level_recorded_for_task(mock_subprocess):
    from kubedevaiops.taskscope import begin_task, max_recorded_risk

    begin_task("risk-test")
    await run_kubectl.ainvoke({"command": "kubectl get pods"})
    assert max_recorded_risk() == "low"
    await run_kubectl.ainvoke({"command": "kubectl delete pod x -n staging"})
    assert max_recorded_risk() == "high"


class TestBlockedPatternBypasses:
    """Root-deletion forms that must not slip past the shell denylist."""

    @pytest.mark.parametrize("cmd", [
        "rm -rf --no-preserve-root /;",
        "rm --no-preserve-root -rf /; echo x",
        "bash -c 'rm -rf --no-preserve-root /'",
        'sh -c "rm -rf --no-preserve-root /"',
        "rm -rf /;",
        "rm -rf /)",
        "rm -rf /|tee out",
        "chmod 000 /",
        "dd if=/dev/zero of=/dev/sda",
        "shutdown -h now",
    ])
    def test_root_destruction_variants_blocked(self, cmd):
        verdict = _pre_flight(cmd)
        assert not verdict.allowed, cmd
        assert not verdict.requires_approval, cmd

    @pytest.mark.parametrize("cmd", ["rm -rf ./build", "rm -rf /tmp/scratch", "rm -f notes.txt"])
    def test_ordinary_removals_still_allowed(self, cmd):
        assert _pre_flight(cmd).allowed, cmd
