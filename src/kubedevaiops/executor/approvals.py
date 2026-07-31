"""Human-in-the-loop approval workflow for destructive commands.

When the safety layer flags a command as requiring approval, the executor
registers a pending :class:`ApprovalRequest` instead of running it.  A human
reviews the queue (``GET /approvals``) and approves or denies each request.
An approved command is executed the next time the agent retries it, within
the approval TTL, after which the approval is consumed.

The store is in-memory and process-local: approvals do not survive a restart
and are scoped to a single replica.  Every transition is written to the audit
log.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

import structlog

from kubedevaiops.outputs.audit import log_event

logger = structlog.get_logger(__name__)

APPROVAL_TTL_SECONDS = 600.0
MAX_PENDING = 200


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    CONSUMED = "consumed"
    EXPIRED = "expired"


def _normalize(command: str) -> str:
    return " ".join(command.split())


@dataclass
class ApprovalRequest:
    command: str
    tool: str
    reason: str
    risk: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: float = field(default_factory=time.time)
    decided_at: float | None = None
    decided_by: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "command": self.command,
            "tool": self.tool,
            "reason": self.reason,
            "risk": self.risk,
            "status": self.status.value,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
        }


class ApprovalStore:
    """Thread-safe in-memory approval queue."""

    def __init__(self, ttl: float = APPROVAL_TTL_SECONDS):
        self._ttl = ttl
        self._requests: dict[str, ApprovalRequest] = {}
        self._lock = threading.Lock()

    def _expire_locked(self) -> None:
        now = time.time()
        for req in self._requests.values():
            if req.status is ApprovalStatus.PENDING and now - req.created_at > self._ttl or (
                req.status is ApprovalStatus.APPROVED
                and req.decided_at is not None
                and now - req.decided_at > self._ttl
            ):
                req.status = ApprovalStatus.EXPIRED

    def request(self, command: str, tool: str, reason: str, risk: str) -> ApprovalRequest:
        """Register (or return the existing) pending request for a command."""
        normalized = _normalize(command)
        with self._lock:
            self._expire_locked()
            for req in self._requests.values():
                if req.status is ApprovalStatus.PENDING and _normalize(req.command) == normalized:
                    return req

            pending = [
                r for r in self._requests.values() if r.status is ApprovalStatus.PENDING
            ]
            if len(pending) >= MAX_PENDING:
                oldest = min(pending, key=lambda r: r.created_at)
                oldest.status = ApprovalStatus.EXPIRED

            req = ApprovalRequest(command=command, tool=tool, reason=reason, risk=risk)
            self._requests[req.id] = req
            log_event("approval.requested", approval_id=req.id, command=command[:200], risk=risk)
            return req

    def get(self, approval_id: str) -> ApprovalRequest | None:
        with self._lock:
            self._expire_locked()
            return self._requests.get(approval_id)

    def list(self, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
        with self._lock:
            self._expire_locked()
            requests = sorted(self._requests.values(), key=lambda r: r.created_at, reverse=True)
            if status is not None:
                requests = [r for r in requests if r.status is status]
            return requests

    def _decide(
        self, approval_id: str, status: ApprovalStatus, decided_by: str
    ) -> ApprovalRequest | None:
        with self._lock:
            self._expire_locked()
            req = self._requests.get(approval_id)
            if req is None or req.status is not ApprovalStatus.PENDING:
                return None
            req.status = status
            req.decided_at = time.time()
            req.decided_by = decided_by
            return req

    def approve(self, approval_id: str, decided_by: str = "api") -> ApprovalRequest | None:
        req = self._decide(approval_id, ApprovalStatus.APPROVED, decided_by)
        if req:
            log_event("approval.approved", approval_id=approval_id, by=decided_by)
        return req

    def deny(self, approval_id: str, decided_by: str = "api") -> ApprovalRequest | None:
        req = self._decide(approval_id, ApprovalStatus.DENIED, decided_by)
        if req:
            log_event("approval.denied", approval_id=approval_id, by=decided_by)
        return req

    def consume_if_approved(self, command: str) -> ApprovalRequest | None:
        """If an unexpired approval exists for this command, consume and return it."""
        normalized = _normalize(command)
        with self._lock:
            self._expire_locked()
            for req in self._requests.values():
                if req.status is ApprovalStatus.APPROVED and _normalize(req.command) == normalized:
                    req.status = ApprovalStatus.CONSUMED
                    log_event(
                        "approval.consumed",
                        approval_id=req.id,
                        command=command[:200],
                        by=req.decided_by,
                    )
                    return req
        return None


_store: ApprovalStore | None = None


def get_approval_store() -> ApprovalStore:
    global _store  # noqa: PLW0603
    if _store is None:
        _store = ApprovalStore()
    return _store


def reset_approval_store() -> None:
    """Clear the store (used in tests)."""
    global _store  # noqa: PLW0603
    _store = None
