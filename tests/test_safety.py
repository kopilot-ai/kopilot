"""Tests for safety guardrails, including adversarial bypass attempts."""

import pytest

from kubedevaiops.agent.safety import RiskLevel, assess_command, is_destructive, is_mutating


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


# ── Protected-namespace bypass attempts ─────────────────────────────────────


@pytest.mark.parametrize("cmd", [
    # equals-sign and glued flag syntax
    "kubectl delete pods --all --namespace=kube-system",
    "kubectl delete pods --all -n=kube-system",
    "kubectl --namespace=kube-system delete pods --all",
    # the namespace as the resource itself
    "kubectl delete namespace kube-system",
    "kubectl delete ns kube-public",
    # extra whitespace
    "kubectl   delete   pod x   -n   kube-system",
    # chained after an innocuous command
    "kubectl get pods -n default && kubectl delete pods -n kube-system --all",
])
def test_protected_namespace_variants_blocked(cmd):
    v = assess_command(cmd)
    assert not v.allowed, f"should be blocked: {cmd}"
    assert not v.requires_approval or v.risk == RiskLevel.CRITICAL


# ── Destructive verbs beyond `delete` ───────────────────────────────────────


@pytest.mark.parametrize("cmd", [
    "kubectl patch deployment web -p '{\"spec\":{\"replicas\":0}}'",
    "kubectl replace --force -f deploy.yaml",
    "kubectl scale deployment web --replicas=0",
    "kubectl drain node-1 --ignore-daemonsets",
    "kubectl taint nodes node-1 key=value:NoSchedule",
    "kubectl apply -f manifests/ --prune --all",
    "helm uninstall my-release",
    "helm rollback my-release 1",
    "helm delete my-release",
])
def test_destructive_verbs_gated(cmd):
    v = assess_command(cmd)
    assert v.requires_approval or not v.allowed, f"should be gated: {cmd}"


# ── Obfuscation attempts ────────────────────────────────────────────────────


@pytest.mark.parametrize("cmd", [
    "kubectl delete ns $(echo kube-system)",
    "kubectl get pods | xargs kubectl delete pod",
    "eval kubectl delete pod x",
    "kubectl delete pod `hostname`",
])
def test_obfuscated_commands_gated(cmd):
    v = assess_command(cmd)
    assert v.requires_approval or not v.allowed, f"should be gated: {cmd}"


# ── Non-destructive mutations and reads ─────────────────────────────────────


def test_mutating_but_not_destructive_is_medium():
    v = assess_command("kubectl apply -f deployment.yaml")
    assert v.allowed
    assert v.risk == RiskLevel.MEDIUM


def test_reads_stay_low_risk():
    for cmd in [
        "kubectl get pods -A",
        "kubectl describe node worker-1",
        "kubectl logs my-pod -n staging",
        "helm list -A",
    ]:
        v = assess_command(cmd)
        assert v.allowed and v.risk == RiskLevel.LOW, cmd


def test_classifier_helpers():
    assert is_destructive("kubectl delete pod x")
    assert not is_destructive("kubectl get pod x")
    assert is_mutating("kubectl apply -f x.yaml")
    assert not is_mutating("kubectl get pods")


# ── Opaque-payload verbs (kubectl exec / cp / attach) ───────────────────────


@pytest.mark.parametrize("cmd", [
    "kubectl exec -n kube-system etcd-master -- etcdctl del / --prefix",
    "kubectl cp /etc/passwd kube-system/pod:/tmp/x",
    "kubectl attach -n kube-public somepod",
])
def test_opaque_payload_into_protected_namespace_blocked(cmd):
    v = assess_command(cmd)
    assert not v.allowed and v.risk == RiskLevel.CRITICAL, cmd


@pytest.mark.parametrize("cmd", [
    "kubectl exec -n staging api-pod -- rm -rf /data",
    "kubectl cp ./payload staging/api-pod:/tmp/payload",
])
def test_opaque_payload_requires_approval(cmd):
    v = assess_command(cmd)
    assert v.requires_approval and not v.allowed, cmd
