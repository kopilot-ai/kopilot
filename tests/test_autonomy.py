"""Autonomy dial: observe / copilot / autopilot decision engine."""

from __future__ import annotations

import pytest

from kubedevaiops.agent.safety import RiskLevel
from kubedevaiops.executor.autonomy import (
    AutonomyDecision,
    AutonomyEngine,
    AutopilotGrant,
)


def _engine(level=1, grants=()):
    eng = AutonomyEngine(base_level=level)
    for g in grants:
        eng.set_grant(g)
    return eng


STAGING = AutopilotGrant(name="staging-autopilot", namespaces=["staging"])


class TestObserveMode:
    def test_level0_refuses_mutations(self):
        eng = _engine(level=0)
        d = eng.decide("kubectl delete pod x -n staging", "kubectl", RiskLevel.HIGH, True)
        assert d is AutonomyDecision.REFUSE

    def test_level0_allows_reads(self):
        eng = _engine(level=0)
        d = eng.decide("kubectl get pods -n staging", "kubectl", RiskLevel.LOW, False)
        assert d is AutonomyDecision.ALLOW

    def test_brake_policy_forces_observe(self):
        eng = _engine(level=1, grants=[STAGING])
        eng.set_brake("emergency-stop")
        d = eng.decide("kubectl delete pod x -n staging", "kubectl", RiskLevel.MEDIUM, True)
        assert d is AutonomyDecision.REFUSE
        eng.clear_brake("emergency-stop")
        d = eng.decide("kubectl delete pod x -n staging", "kubectl", RiskLevel.MEDIUM, True)
        assert d is AutonomyDecision.AUTO_APPROVE


class TestCopilotDefault:
    def test_gated_without_grants(self):
        eng = _engine(level=1)
        d = eng.decide("kubectl delete pod x -n staging", "kubectl", RiskLevel.MEDIUM, True)
        assert d is AutonomyDecision.GATE

    def test_reads_allowed(self):
        eng = _engine(level=1)
        d = eng.decide("kubectl get pods -A", "kubectl", RiskLevel.LOW, False)
        assert d is AutonomyDecision.ALLOW


class TestAutopilotGrants:
    def test_grant_auto_approves_in_scope(self):
        eng = _engine(grants=[STAGING])
        d = eng.decide("kubectl delete pod x -n staging", "kubectl", RiskLevel.MEDIUM, True)
        assert d is AutonomyDecision.AUTO_APPROVE
        granted = eng.granting_policy("kubectl delete pod x -n staging", RiskLevel.MEDIUM)
        assert granted == "staging-autopilot"

    def test_namespace_outside_grant_is_gated(self):
        eng = _engine(grants=[STAGING])
        d = eng.decide("kubectl delete pod x -n production", "kubectl", RiskLevel.MEDIUM, True)
        assert d is AutonomyDecision.GATE

    def test_mixed_namespaces_one_outside_is_gated(self):
        eng = _engine(grants=[STAGING])
        cmd = "kubectl delete pod x -n staging --namespace=production"
        assert eng.decide(cmd, "kubectl", RiskLevel.MEDIUM, True) is AutonomyDecision.GATE

    def test_no_explicit_namespace_is_gated(self):
        eng = _engine(grants=[STAGING])
        d = eng.decide("kubectl delete pod x", "kubectl", RiskLevel.MEDIUM, True)
        assert d is AutonomyDecision.GATE

    def test_all_namespaces_flag_is_gated(self):
        eng = _engine(grants=[STAGING])
        cmd = "kubectl delete pods --all -A"
        assert eng.decide(cmd, "kubectl", RiskLevel.MEDIUM, True) is AutonomyDecision.GATE

    def test_high_risk_destructive_is_auto_approved_in_scope(self):
        eng = _engine(grants=[STAGING])
        d = eng.decide("kubectl drain node1 -n staging", "kubectl", RiskLevel.HIGH, True)
        assert d is AutonomyDecision.AUTO_APPROVE

    def test_critical_never_auto_approved(self):
        eng = _engine(grants=[STAGING])
        d = eng.decide(
            "kubectl delete ns staging -n staging", "kubectl", RiskLevel.CRITICAL, True
        )
        assert d is AutonomyDecision.GATE

    def test_opaque_payload_never_auto_approved(self):
        eng = _engine(grants=[STAGING])
        cmd = "kubectl exec -n staging web-0 -- rm -rf /var/data"
        assert eng.decide(cmd, "kubectl", RiskLevel.HIGH, True) is AutonomyDecision.GATE

    def test_shell_never_auto_approved(self):
        eng = _engine(grants=[STAGING])
        d = eng.decide("some-script -n staging", "shell", RiskLevel.MEDIUM, True)
        assert d is AutonomyDecision.GATE

    def test_helm_in_scope_auto_approved(self):
        eng = _engine(grants=[STAGING])
        d = eng.decide("helm rollback myapp 3 -n staging", "helm", RiskLevel.MEDIUM, True)
        assert d is AutonomyDecision.AUTO_APPROVE

    def test_remove_grant_restores_gating(self):
        eng = _engine(grants=[STAGING])
        eng.remove_grant("staging-autopilot")
        d = eng.decide("kubectl delete pod x -n staging", "kubectl", RiskLevel.MEDIUM, True)
        assert d is AutonomyDecision.GATE


class TestEngineFromSettings:
    def test_env_configured_grant(self, monkeypatch):
        monkeypatch.setenv("AUTONOMY_LEVEL", "2")
        monkeypatch.setenv("AUTONOMY_AUTOPILOT_NAMESPACES", '["staging","qa"]')
        from kubedevaiops.config import reset_settings
        reset_settings()
        from kubedevaiops.executor.autonomy import build_engine_from_settings
        eng = build_engine_from_settings()
        d = eng.decide("kubectl delete pod x -n qa", "kubectl", RiskLevel.MEDIUM, True)
        assert d is AutonomyDecision.AUTO_APPROVE
        reset_settings()

    def test_default_is_copilot(self):
        from kubedevaiops.config import reset_settings
        reset_settings()
        from kubedevaiops.executor.autonomy import build_engine_from_settings
        eng = build_engine_from_settings()
        d = eng.decide("kubectl delete pod x -n staging", "kubectl", RiskLevel.MEDIUM, True)
        assert d is AutonomyDecision.GATE


@pytest.mark.asyncio
class TestExecutorIntegration:
    async def test_auto_approved_executes_and_audits(self, mock_subprocess, autonomy_staging):
        from kubedevaiops.executor.approvals import get_approval_store
        from kubedevaiops.executor.middleware import run_kubectl

        result = await run_kubectl.ainvoke({"command": "kubectl delete pod x -n staging"})
        assert "mocked output" in result
        records = get_approval_store().list()
        auto = [r for r in records if r.decided_by.startswith("policy:")]
        assert len(auto) == 1
        assert auto[0].status.value == "consumed"

    async def test_out_of_scope_still_gated(self, mock_subprocess, autonomy_staging):
        from kubedevaiops.executor.middleware import run_kubectl

        result = await run_kubectl.ainvoke({"command": "kubectl delete pod x -n production"})
        assert "APPROVAL REQUIRED" in result

    async def test_observe_mode_refuses(self, mock_subprocess, autonomy_observe):
        from kubedevaiops.executor.middleware import run_kubectl

        result = await run_kubectl.ainvoke({"command": "kubectl delete pod x -n staging"})
        assert "OBSERVE MODE" in result
        assert "mocked output" not in result

    async def test_observe_mode_allows_reads(self, mock_subprocess, autonomy_observe):
        from kubedevaiops.executor.middleware import run_kubectl

        result = await run_kubectl.ainvoke({"command": "kubectl get pods -n staging"})
        assert "mocked output" in result
