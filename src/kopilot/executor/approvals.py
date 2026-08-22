"""Human-in-the-loop approval workflow for destructive commands.

When the safety layer flags a command as requiring approval, the executor
registers a pending :class:`ApprovalRequest` instead of running it.  A human
reviews the queue (``GET /approvals``) and approves or denies each request.
An approved command is executed the next time the agent retries it, within
the approval TTL, after which the approval is consumed.

The store is process-local and scoped to a single replica.  With a
``db_path`` (``APPROVALS_DB_PATH``) every transition is journaled to SQLite
and reloaded on startup, so approvals survive a restart; without one the
store is memory-only.

SQLite is the *working queue*, not the history: settled requests are dropped
from it once they are past the retention window so a long-lived process does
not grow one row per command.  Every transition, and the full record of any
request about to be dropped, is written first to the hash-chained ledger,
which is append-only and which the cleanup never touches.

``decided_by`` is the authenticated principal.  ``operator_display`` is a
self-asserted name a client may claim for readability; it is advisory and
must never be read as identity.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import structlog

from kopilot.agent.safety import normalize_command as _normalize
from kopilot.outputs.audit import (
    AUTHORITY_NONE,
    Decision,
    advisory_display,
    authority_for,
    log_event,
    record_command_event,
    redact_command,
)

logger = structlog.get_logger(__name__)

APPROVAL_TTL_SECONDS = 600.0
MAX_PENDING = 200


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    CONSUMED = "consumed"
    EXPIRED = "expired"


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
    operator_display: str = ""

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
            # The authenticated principal that decided this request.
            "decided_by": self.decided_by,
            # A name the client claimed for itself: display only, never identity.
            "operator_display": self.operator_display,
            "operator_display_advisory": True,
        }

    def ledger_context(self, stage: str, **extra) -> dict:
        """Context block for a ledger envelope about this request."""
        return {
            "stage": stage,
            "approval_id": self.id,
            "risk": self.risk,
            "tool": self.tool,
            "operator_display": advisory_display(self.operator_display),
            **extra,
        }


class ApprovalStore:
    """Thread-safe approval queue; optionally journaled to SQLite."""

    def __init__(self, ttl: float = APPROVAL_TTL_SECONDS, db_path: str | None = None):
        self._ttl = ttl
        self._requests: dict[str, ApprovalRequest] = {}
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        if db_path:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(db_path, check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY,
                    command TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    decided_at REAL,
                    decided_by TEXT NOT NULL DEFAULT '',
                    operator_display TEXT NOT NULL DEFAULT ''
                )"""
            )
            columns = {row[1] for row in self._db.execute("PRAGMA table_info(approvals)")}
            if "operator_display" not in columns:
                self._db.execute(
                    "ALTER TABLE approvals ADD COLUMN operator_display TEXT NOT NULL DEFAULT ''"
                )
            self._db.commit()
            with self._lock:
                self._load_locked()
                self._expire_locked()

    def _load_locked(self) -> None:
        assert self._db is not None
        rows = self._db.execute(
            "SELECT id, command, tool, reason, risk, status, created_at, decided_at,"
            " decided_by, operator_display FROM approvals"
        ).fetchall()
        for row in rows:
            req = ApprovalRequest(
                command=row[1],
                tool=row[2],
                reason=row[3],
                risk=row[4],
                id=row[0],
                status=ApprovalStatus(row[5]),
                created_at=row[6],
                decided_at=row[7],
                decided_by=row[8],
                operator_display=row[9],
            )
            self._requests[req.id] = req
        if rows:
            logger.info("approvals.reloaded", count=len(rows))

    def _persist_locked(self, req: ApprovalRequest) -> None:
        if self._db is None:
            return
        self._db.execute(
            "INSERT INTO approvals (id, command, tool, reason, risk, status, created_at,"
            " decided_at, decided_by, operator_display) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET status=excluded.status,"
            " decided_at=excluded.decided_at, decided_by=excluded.decided_by,"
            " operator_display=excluded.operator_display",
            (
                req.id,
                req.command,
                req.tool,
                req.reason,
                req.risk,
                req.status.value,
                req.created_at,
                req.decided_at,
                req.decided_by,
                req.operator_display,
            ),
        )
        self._db.commit()

    def _delete_persisted_locked(self, approval_id: str) -> None:
        if self._db is None:
            return
        self._db.execute("DELETE FROM approvals WHERE id = ?", (approval_id,))
        self._db.commit()

    def _expire_locked(self) -> None:
        now = time.time()
        # Settled requests are kept briefly for operator visibility, then
        # dropped from the working queue so a long-lived process does not grow
        # one entry per command. The record is written to the ledger first;
        # only the queue forgets, the history never does.
        for key in [
            k
            for k, r in self._requests.items()
            if r.status is not ApprovalStatus.PENDING
            and now - (r.decided_at or r.created_at) > self._ttl * 4
        ]:
            retired = self._requests.pop(key)
            record_command_event(
                retired.command,
                retired.tool,
                Decision.OBSERVED,
                authority_for(retired.decided_by),
                context=retired.ledger_context("approval_retired", record=retired.to_dict()),
            )
            self._delete_persisted_locked(key)
        for req in self._requests.values():
            expired_pending = (
                req.status is ApprovalStatus.PENDING and now - req.created_at > self._ttl
            )
            expired_approval = (
                req.status is ApprovalStatus.APPROVED
                and req.decided_at is not None
                and now - req.decided_at > self._ttl
            )
            if expired_pending or expired_approval:
                req.status = ApprovalStatus.EXPIRED
                self._persist_locked(req)
                record_command_event(
                    req.command,
                    req.tool,
                    Decision.OBSERVED,
                    AUTHORITY_NONE,
                    context=req.ledger_context("approval_expired"),
                )

    def request(self, command: str, tool: str, reason: str, risk: str) -> ApprovalRequest:
        """Register (or return the existing) pending request for a command."""
        normalized = _normalize(command)
        with self._lock:
            self._expire_locked()
            for req in self._requests.values():
                if req.status is ApprovalStatus.PENDING and _normalize(req.command) == normalized:
                    return req

            pending = [r for r in self._requests.values() if r.status is ApprovalStatus.PENDING]
            if len(pending) >= MAX_PENDING:
                oldest = min(pending, key=lambda r: r.created_at)
                oldest.status = ApprovalStatus.EXPIRED
                self._persist_locked(oldest)

            req = ApprovalRequest(command=command, tool=tool, reason=reason, risk=risk)
            self._requests[req.id] = req
            self._persist_locked(req)
            log_event(
                "approval.requested",
                approval_id=req.id,
                command=redact_command(command)[:200],
                risk=risk,
            )
            record_command_event(
                command,
                tool,
                Decision.OBSERVED,
                AUTHORITY_NONE,
                context=req.ledger_context("approval_requested", reason=reason),
            )
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
        self,
        approval_id: str,
        status: ApprovalStatus,
        decided_by: str,
        operator_display: str = "",
    ) -> ApprovalRequest | None:
        with self._lock:
            self._expire_locked()
            req = self._requests.get(approval_id)
            if req is None or req.status is not ApprovalStatus.PENDING:
                return None
            req.status = status
            req.decided_at = time.time()
            req.decided_by = decided_by
            req.operator_display = operator_display
            self._persist_locked(req)
            return req

    def approve(
        self, approval_id: str, decided_by: str = "api", operator_display: str = ""
    ) -> ApprovalRequest | None:
        """Approve a request. ``decided_by`` is the authenticated principal."""
        req = self._decide(approval_id, ApprovalStatus.APPROVED, decided_by, operator_display)
        if req:
            log_event("approval.approved", approval_id=approval_id, by=decided_by)
            record_command_event(
                req.command,
                req.tool,
                Decision.APPROVED,
                authority_for(decided_by),
                context=req.ledger_context("approval_granted", reason=req.reason),
            )
        return req

    def deny(
        self, approval_id: str, decided_by: str = "api", operator_display: str = ""
    ) -> ApprovalRequest | None:
        """Deny a request. ``decided_by`` is the authenticated principal."""
        req = self._decide(approval_id, ApprovalStatus.DENIED, decided_by, operator_display)
        if req:
            log_event("approval.denied", approval_id=approval_id, by=decided_by)
            record_command_event(
                req.command,
                req.tool,
                Decision.DENIED,
                authority_for(decided_by),
                context=req.ledger_context("approval_denied", reason=req.reason),
            )
        return req

    def record_auto(
        self, command: str, tool: str, reason: str, risk: str, policy: str
    ) -> ApprovalRequest:
        """Record a policy-approved execution in the same audit queue."""
        now = time.time()
        req = ApprovalRequest(
            command=command,
            tool=tool,
            reason=reason,
            risk=risk,
            status=ApprovalStatus.CONSUMED,
            decided_at=now,
            decided_by=f"policy:{policy}",
        )
        with self._lock:
            self._requests[req.id] = req
            self._persist_locked(req)
        log_event(
            "approval.auto_approved",
            approval_id=req.id,
            command=redact_command(command)[:200],
            risk=risk,
            policy=policy,
        )
        record_command_event(
            command,
            tool,
            Decision.AUTOPILOTED,
            f"policy:{policy}",
            policy_ref=policy,
            context=req.ledger_context("autopilot_granted", reason=reason),
        )
        return req

    def consume_if_approved(self, command: str) -> ApprovalRequest | None:
        """If an unexpired approval exists for this command, consume and return it."""
        normalized = _normalize(command)
        with self._lock:
            self._expire_locked()
            for req in self._requests.values():
                if req.status is ApprovalStatus.APPROVED and _normalize(req.command) == normalized:
                    req.status = ApprovalStatus.CONSUMED
                    self._persist_locked(req)
                    log_event(
                        "approval.consumed",
                        approval_id=req.id,
                        command=redact_command(command)[:200],
                        by=req.decided_by,
                    )
                    record_command_event(
                        req.command,
                        req.tool,
                        Decision.APPROVED,
                        authority_for(req.decided_by),
                        context=req.ledger_context("approval_consumed"),
                    )
                    return req
        return None


_store: ApprovalStore | None = None


def get_approval_store() -> ApprovalStore:
    global _store  # noqa: PLW0603
    if _store is None:
        from kopilot.config import get_settings

        _store = ApprovalStore(db_path=get_settings().approvals.db_path or None)
    return _store


def reset_approval_store() -> None:
    """Clear the store (used in tests)."""
    global _store  # noqa: PLW0603
    _store = None
