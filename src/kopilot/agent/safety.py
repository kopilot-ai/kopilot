"""Safety guardrails for autonomous operations.

Every action the agent wants to take passes through these checks before
execution.  Destructive operations on protected namespaces are blocked or
require explicit approval.

These checks are one layer of defense in depth.  They are intentionally
conservative (unparseable or obfuscated commands are treated as risky), but a
pattern-based gate can never be a substitute for scoped RBAC on the service
account the agent runs under.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

import structlog

from kopilot.config import get_settings

logger = structlog.get_logger(__name__)

DESTRUCTIVE_VERBS = {"delete", "drain", "cordon", "taint", "replace", "patch", "scale", "evict"}
HIGH_RISK_RESOURCES = {"node", "namespace", "persistentvolume", "clusterrole", "clusterrolebinding"}

# kubectl verbs that mutate cluster state (destructive or not).  `exec`, `cp`,
# `attach`, and `port-forward` are included because they carry an arbitrary
# payload into a running container: the safety layer cannot introspect what
# runs on the far side of `--`, so they are treated as state-changing.
_KUBECTL_MUTATING_VERBS = (
    "delete|drain|cordon|uncordon|taint|replace|patch|scale|edit|apply|create|"
    "label|annotate|rollout|set|expose|autoscale|evict|exec|cp|attach|port-forward"
)

# Verbs whose payload the gate cannot inspect. They are approval-gated rather
# than blocked outright, since `kubectl exec` is a normal diagnostic tool.
_OPAQUE_PAYLOAD_PAT = re.compile(
    r"kubectl\s+(?:[^|;&]*\s)?(?:exec|cp|attach)\b",
    re.IGNORECASE,
)

# Destructive command detection.  Whitespace inside a command is normalised
# before these run, so "kubectl  delete" and "kubectl delete" match alike.
_DESTRUCTIVE_PAT = re.compile(
    r"kubectl\s+(?:[^|;&]*\s)?(?:delete|drain|cordon|taint|evict)\b"
    r"|kubectl\s+(?:[^|;&]*\s)?(?:replace|patch|scale)\b"
    r"|kubectl\s+[^|;&]*--prune\b"
    r"|helm\s+(?:[^|;&]*\s)?(?:uninstall|delete|rollback)\b",
    re.IGNORECASE,
)

_MUTATING_PAT = re.compile(
    rf"kubectl\s+(?:[^|;&]*\s)?(?:{_KUBECTL_MUTATING_VERBS})\b"
    rf"|helm\s+(?:[^|;&]*\s)?(?:install|upgrade|uninstall|delete|rollback)\b",
    re.IGNORECASE,
)

# -n foo / -n=foo / --namespace foo / --namespace=foo
_NS_PAT = re.compile(r"(?:^|\s)(?:-n|--namespace)(?:[=\s]+)(\S+)", re.IGNORECASE)
_ALL_NS_PAT = re.compile(r"(?:^|\s)(?:-A\b|--all-namespaces\b)")

# Indirection that can smuggle a command past pattern matching.  Only applied
# to commands that already look kubectl/helm-adjacent.
_OBFUSCATION_PAT = re.compile(r"\$\(|`|\$\{|\beval\b|\bxargs\b\s+kubectl|\bxargs\b\s+helm")


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class SafetyVerdict:
    allowed: bool
    risk: RiskLevel
    reason: str
    requires_approval: bool = False


def normalize_command(command: str) -> str:
    """Collapse whitespace so pattern checks and approval matching agree."""
    return " ".join(command.split())


_normalize = normalize_command


def is_destructive(command: str) -> bool:
    """True when the command matches a known destructive pattern."""
    return bool(_DESTRUCTIVE_PAT.search(_normalize(command)))


def is_mutating(command: str) -> bool:
    """True when the command may change cluster state (superset of destructive)."""
    return bool(_MUTATING_PAT.search(_normalize(command)))


def _protected_ns_hit(command: str, protected: list[str]) -> str | None:
    """Return the protected namespace a destructive command targets, if any."""
    for match in _NS_PAT.finditer(command):
        ns = match.group(1).strip("\"'")
        if ns in protected:
            return ns
    # Conservative: a destructive command that merely mentions a protected
    # namespace anywhere (e.g. "kubectl delete pods -nkube-system" or resource
    # slash syntax) is still blocked.
    return next((ns for ns in protected if ns in command), None)


def assess_command(command: str) -> SafetyVerdict:
    """Evaluate a raw shell / kubectl command and return a safety verdict."""
    cfg = get_settings().safety
    normalized = _normalize(command)

    destructive = bool(_DESTRUCTIVE_PAT.search(normalized))
    opaque = bool(_OPAQUE_PAYLOAD_PAT.search(normalized))

    # A command whose payload cannot be inspected (`kubectl exec … -- …`,
    # `cp`, `attach`) can destroy from inside a pod, so it is treated exactly
    # like a destructive command for namespace protection and approval.
    if destructive or opaque:
        target_ns = _protected_ns_hit(normalized, cfg.protected_namespaces)
        if target_ns:
            return SafetyVerdict(
                allowed=False,
                risk=RiskLevel.CRITICAL,
                reason=f"Destructive operation on protected namespace '{target_ns}' is blocked.",
            )

    if opaque and not destructive:
        return SafetyVerdict(
            allowed=not cfg.require_approval_destructive,
            risk=RiskLevel.HIGH,
            reason=(
                "Command carries a payload into a container that cannot be "
                "safety-checked; approval required."
            ),
            requires_approval=cfg.require_approval_destructive,
        )

    if destructive:
        if _ALL_NS_PAT.search(normalized):
            return SafetyVerdict(
                allowed=False,
                risk=RiskLevel.CRITICAL,
                reason="Destructive operation across all namespaces requires explicit approval.",
                requires_approval=True,
            )

        return SafetyVerdict(
            allowed=not cfg.require_approval_destructive,
            risk=RiskLevel.HIGH,
            reason="Destructive command detected; approval may be required.",
            requires_approval=cfg.require_approval_destructive,
        )

    # Obfuscated invocations of kubectl/helm (eval, command substitution,
    # xargs) can hide destructive verbs from pattern matching — treat them as
    # approval-required rather than trying to decode them.
    if ("kubectl" in normalized or "helm" in normalized) and _OBFUSCATION_PAT.search(normalized):
        return SafetyVerdict(
            allowed=not cfg.require_approval_destructive,
            risk=RiskLevel.HIGH,
            reason="Indirect kubectl/helm invocation cannot be safety-checked; approval required.",
            requires_approval=cfg.require_approval_destructive,
        )

    if is_mutating(normalized):
        return SafetyVerdict(allowed=True, risk=RiskLevel.MEDIUM, reason="Mutating command.")

    return SafetyVerdict(allowed=True, risk=RiskLevel.LOW, reason="OK")

