"""Tests for the generic executor middleware."""

import pytest

from kubedevaiops.executor.middleware import run_helm, run_kubectl, run_shell


@pytest.mark.asyncio
async def test_kubectl_runs(mock_subprocess):
    result = await run_kubectl.ainvoke({"command": "kubectl get pods"})
    assert "mocked output" in result


@pytest.mark.asyncio
async def test_kubectl_blocked_in_kube_system(mock_subprocess):
    result = await run_kubectl.ainvoke(
        {"command": "kubectl delete deployment coredns -n kube-system"}
    )
    assert "BLOCKED" in result


@pytest.mark.asyncio
async def test_helm_runs(mock_subprocess):
    result = await run_helm.ainvoke({"command": "helm list -A"})
    assert "mocked output" in result


@pytest.mark.asyncio
async def test_shell_runs(mock_subprocess):
    result = await run_shell.ainvoke({"command": "echo hello"})
    assert "mocked output" in result
