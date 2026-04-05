"""Tests for executor middleware — rate limiting, retries, safety."""

from __future__ import annotations

import pytest

from kubedevaiops.executor.middleware import (
    ToolRateLimiter,
    _pre_flight,
    run_kubectl,
    run_helm,
    run_shell,
    read_resource,
    get_execution_stats,
)
from kubedevaiops.agent.safety import RiskLevel


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


@pytest.mark.asyncio
async def test_read_resource_file(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello world")
    result = await read_resource.ainvoke({"path_or_url": str(f)})
    assert "hello world" in result


@pytest.mark.asyncio
async def test_read_resource_missing():
    result = await read_resource.ainvoke({"path_or_url": "/nonexistent/path.txt"})
    assert "Could not read" in result


def test_execution_stats():
    stats = get_execution_stats()
    assert isinstance(stats, dict)
