"""Safety guardrails for autonomous operations.

Every action the agent wants to take passes through these checks before
execution.  Destructive operations on protected namespaces are blocked or
require explicit approval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

import structlog

from kubedevaiops.config import get_settings

logger = structlog.get_logger(__name__)

DESTRUCTIVE_VERBS = {"delete", "drain", "cordon", "taint", "replace", "patch"}
HIGH_RISK_RESOURCES = {"node", "namespace", "persistentvolume", "clusterrole", "clusterrolebinding"}

_DELETE_PAT = re.compile(
    r"kubectl\s+delete|helm\s+uninstall|kubectl\s+drain|kubectl\s+cordon",
    re.IGNORECASE,
)
_NS_PAT = re.compile(r"(?:-n|--namespace)\s+(\S+)", re.IGNORECASE)


class RiskLevel(str, Enum):
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


def assess_command(command: str) -> SafetyVerdict:
    """Evaluate a raw shell / kubectl command and return a safety verdict."""
    cfg = get_settings().safety

    ns_match = _NS_PAT.search(command)
    target_ns = ns_match.group(1) if ns_match else None

    if target_ns in cfg.protected_namespaces and _DELETE_PAT.search(command):
        return SafetyVerdict(
            allowed=False,
            risk=RiskLevel.CRITICAL,
            reason=f"Destructive operation on protected namespace '{target_ns}' is blocked.",
        )

    if _DELETE_PAT.search(command):
        return SafetyVerdict(
            allowed=not cfg.require_approval_destructive,
            risk=RiskLevel.HIGH,
            reason="Destructive command detected; approval may be required.",
            requires_approval=cfg.require_approval_destructive,
        )

    return SafetyVerdict(allowed=True, risk=RiskLevel.LOW, reason="OK")


def assess_action(verb: str, resource: str, namespace: str | None = None) -> SafetyVerdict:
    """Structured assessment: verb + resource + optional namespace."""
    cfg = get_settings().safety
    verb_lower = verb.lower()
    res_lower = resource.lower()

    if namespace in cfg.protected_namespaces and verb_lower in DESTRUCTIVE_VERBS:
        return SafetyVerdict(
            allowed=False,
            risk=RiskLevel.CRITICAL,
            reason=f"Cannot {verb} {resource} in protected namespace '{namespace}'.",
        )

    if verb_lower in DESTRUCTIVE_VERBS and res_lower in HIGH_RISK_RESOURCES:
        return SafetyVerdict(
            allowed=False,
            risk=RiskLevel.CRITICAL,
            reason=f"Destructive operation on cluster-scoped resource '{resource}' blocked.",
            requires_approval=True,
        )

    if verb_lower in DESTRUCTIVE_VERBS:
        return SafetyVerdict(
            allowed=not cfg.require_approval_destructive,
            risk=RiskLevel.HIGH,
            reason=f"Destructive verb '{verb}' requires approval.",
            requires_approval=cfg.require_approval_destructive,
        )

    if cfg.dry_run_default:
        return SafetyVerdict(
            allowed=True,
            risk=RiskLevel.MEDIUM,
            reason="Allowed in dry-run mode.",
        )

    return SafetyVerdict(allowed=True, risk=RiskLevel.LOW, reason="OK")
