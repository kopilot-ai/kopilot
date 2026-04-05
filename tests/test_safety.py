"""Tests for safety guardrails."""

from kubedevaiops.agent.safety import RiskLevel, assess_command


def test_block_delete_protected_ns():
    v = assess_command("kubectl delete deployment nginx -n kube-system")
    assert not v.allowed
    assert v.risk == RiskLevel.CRITICAL


def test_allow_get():
    v = assess_command("kubectl get pods -n default")
    assert v.allowed
    assert v.risk == RiskLevel.LOW


def test_destructive_needs_approval():
    v = assess_command("kubectl delete deployment nginx -n production")
    assert v.requires_approval
