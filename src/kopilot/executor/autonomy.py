"""Autonomy dial: decide how much a command may do without a human.

Three levels, enforced at the executor:

- **0 — observe**: every mutating command is refused outright — the refusal
  keys off ``is_mutating``, not off whether the safety layer happened to ask
  for approval, so a command that slips past approval gating still stops here.
  Applying an AIPolicy with ``autonomyLevel: 0`` acts as a cluster-wide
  emergency brake.
- **1 — copilot** (default): every mutating command waits for a human
  approval; reads run freely.
- **2 — autopilot**: namespace-scoped grants auto-approve approval-gated
  commands, but only when every namespace the command names is inside the
  grant, the tool is kubectl or helm, and the command names its namespaces
  explicitly (no ``-A``, no implicit default). CRITICAL commands, shell
  commands, and opaque payloads (``kubectl exec/cp/attach``) are never
  auto-approved, and protected namespaces stay refused upstream regardless
  of any grant.

Every autonomous execution is recorded in the same approval queue humans use,
as a consumed request decided by ``policy:<name>``: one audit trail.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum, auto

import structlog

from kopilot.agent.safety import (
    _ALL_NS_PAT,
    _NS_PAT,
    _OPAQUE_PAYLOAD_PAT,
    RiskLevel,
    is_mutating,
    normalize_command,
)

logger = structlog.get_logger(__name__)

# Tools eligible for autopilot; shell payloads are never auto-approved.
_AUTOPILOT_TOOLS = {"kubectl", "helm"}


class AutonomyDecision(Enum):
    ALLOW = auto()          # run without ceremony (reads at any level)
    GATE = auto()           # create a pending approval (copilot)
    AUTO_APPROVE = auto()   # execute now, audited as policy-approved
    REFUSE = auto()         # observe mode: mutation refused outright


@dataclass
class AutopilotGrant:
    """A namespace-scoped permission to act without a human."""

    name: str
    namespaces: list[str] = field(default_factory=list)


def _explicit_namespaces(command: str) -> list[str] | None:
    """Namespaces a command names explicitly; None when ambiguous.

    Ambiguous means: an all-namespaces flag, or no namespace flag at all.
    Autopilot only acts where it was scoped, so ambiguity falls back to GATE.
    """
    normalized = normalize_command(command)
    if _ALL_NS_PAT.search(normalized):
        return None
    found = [m.group(1).strip("\"'") for m in _NS_PAT.finditer(normalized)]
    return found or None


class AutonomyEngine:
    """Holds the effective autonomy state and decides per command."""

    def __init__(self, base_level: int = 1):
        self._base_level = base_level
        self._grants: dict[str, AutopilotGrant] = {}
        self._brakes: set[str] = set()
        self._lock = threading.Lock()

    # ── state management (env at boot, AIPolicy CRDs at runtime) ──────────

    def set_grant(self, grant: AutopilotGrant) -> None:
        with self._lock:
            self._grants[grant.name] = grant
        logger.info(
            "autonomy.grant_set", policy=grant.name, namespaces=grant.namespaces
        )

    def remove_grant(self, name: str) -> None:
        with self._lock:
            self._grants.pop(name, None)
        logger.info("autonomy.grant_removed", policy=name)

    def set_brake(self, name: str) -> None:
        with self._lock:
            self._brakes.add(name)
        logger.warning("autonomy.brake_engaged", policy=name)

    def clear_brake(self, name: str) -> None:
        with self._lock:
            self._brakes.discard(name)
        logger.info("autonomy.brake_released", policy=name)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "base_level": self._base_level,
                "observe": self._observe_locked(),
                "brakes": sorted(self._brakes),
                "grants": [
                    {"name": g.name, "namespaces": g.namespaces}
                    for g in self._grants.values()
                ],
            }

    def _observe_locked(self) -> bool:
        return self._base_level == 0 or bool(self._brakes)

    # ── decisions ──────────────────────────────────────────────────────────

    def granting_policy(self, command: str, risk: RiskLevel) -> str | None:
        """Name of the grant that covers this command, if any."""
        if risk is RiskLevel.CRITICAL:
            return None
        if _OPAQUE_PAYLOAD_PAT.search(normalize_command(command)):
            return None
        namespaces = _explicit_namespaces(command)
        if namespaces is None:
            return None
        with self._lock:
            for grant in self._grants.values():
                if all(ns in grant.namespaces for ns in namespaces):
                    return grant.name
        return None

    def decide(
        self,
        command: str,
        tool_name: str,
        risk: RiskLevel,
        needs_approval: bool,
    ) -> AutonomyDecision:
        with self._lock:
            observe = self._observe_locked()
        # Observe mode refuses on *mutation*, not on approval-required. The two
        # used to be the same set; keeping them separate means a gap in the
        # approval rules can never quietly re-open the emergency brake.
        if observe and (needs_approval or is_mutating(command)):
            return AutonomyDecision.REFUSE
        if not needs_approval:
            return AutonomyDecision.ALLOW
        if tool_name in _AUTOPILOT_TOOLS and self.granting_policy(command, risk):
            return AutonomyDecision.AUTO_APPROVE
        return AutonomyDecision.GATE


def build_engine_from_settings() -> AutonomyEngine:
    from kopilot.config import get_settings

    cfg = get_settings().autonomy
    # Level 2 keeps a copilot base and adds a grant on top; anything at or
    # below 0 is observe. Settings validation pins the range to 0-2, and the
    # clamp here keeps a hand-built AutonomySettings from disabling observe.
    engine = AutonomyEngine(base_level=0 if cfg.level <= 0 else 1)
    if cfg.level >= 2 and cfg.autopilot_namespaces:
        engine.set_grant(
            AutopilotGrant(
                name="env:autonomy",
                namespaces=list(cfg.autopilot_namespaces),
            )
        )
    return engine


_engine: AutonomyEngine | None = None
_engine_lock = threading.Lock()


def get_engine() -> AutonomyEngine:
    global _engine  # noqa: PLW0603
    with _engine_lock:
        if _engine is None:
            _engine = build_engine_from_settings()
        return _engine


def reset_engine() -> None:
    global _engine  # noqa: PLW0603
    with _engine_lock:
        _engine = None
