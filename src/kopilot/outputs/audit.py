"""Structured audit trail and the hash-chained ledger.

Two outputs from one call site.  :func:`log_event` writes the structlog line
every existing log pipeline already reads.  :func:`record_event` appends an
RFC-0001 v0 envelope to an append-only JSONL ledger stored next to the
approvals database, where every line carries the SHA-256 of the line before
it.  History cannot be edited, reordered, or truncated without breaking the
chain, and :func:`verify_chain` walks it to prove that.

The ledger is the record of authority, so it holds the whole command: a
SHA-256 over the exact bytes that ran plus a redacted display form, never a
truncation.  The digest proves what ran; the display form is safe to read.

Path resolution: ``LEDGER_PATH`` when set, otherwise ``ledger.jsonl`` beside
``APPROVALS_DB_PATH``.  With neither configured the process is memory-only by
design, and the ledger degrades to the structlog line alone after one warning.

One writer per file, matching the single-replica scope of the approval store.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog

from kopilot import __version__
from kopilot.agent.safety import _ALL_NS_PAT, _NS_PAT, normalize_command
from kopilot.taskscope import current_task_id

audit_logger = structlog.get_logger("kopilot.audit")
logger = structlog.get_logger(__name__)

LEDGER_SCHEMA = "kopilot-ai/ledger-event/v0"
LEDGER_FILENAME = "ledger.jsonl"
GENESIS_HASH = "0" * 64
REDACTED = "[redacted]"

# Authority strings (RFC 0001: human:<id> | policy:<ref> | contract:<ref> | none)
AUTHORITY_NONE = "none"
SAFETY_AUTHORITY = "policy:kopilot-safety"
_AUTHORITY_SCHEMES = ("human", "policy", "contract", "none")

# Crockford base32, so an event id sorts by time as a plain string (ULID).
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# Tail window used to find the chain tip without reading the whole file.
_TAIL_WINDOW = 65_536


class Plane(StrEnum):
    GATE = "gate"
    GOVERN = "govern"
    METER = "meter"


class Decision(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"
    AUTOPILOTED = "autopiloted"
    BRAKED = "braked"
    GATE_PASS = "gate_pass"
    GATE_FAIL = "gate_fail"
    OBSERVED = "observed"


def log_event(event_type: str, **kwargs: Any) -> None:
    """Write an audit entry with ISO-8601 timestamp."""
    audit_logger.info(
        event_type,
        timestamp=datetime.now(UTC).isoformat(),
        **kwargs,
    )


# ── Redaction and digests ───────────────────────────────────────────────────

# `--from-literal=key=value` (and the `--from-env-file` sibling): the value is
# a secret whatever the key is called.
_LITERAL_PAT = re.compile(r"(--from-(?:literal|env-file)(?:=|\s+)[^\s=]*=)(\S+)")
# A flag whose *name* says secret: --token=…, --password …, --api-key=…
_SECRET_FLAG_PAT = re.compile(
    r"(--[a-z0-9-]*(?:password|passwd|token|secret|api-?key|credential)[a-z0-9-]*[=\s])(\S+)",
    re.IGNORECASE,
)
# A key=value or key: value pair whose key says secret.
_SECRET_KV_PAT = re.compile(
    r"([A-Za-z0-9_.-]*(?:password|passwd|token|secret|api_?key|credential|private_?key)"
    r"[A-Za-z0-9_.-]*\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)",
    re.IGNORECASE,
)
_BEARER_PAT = re.compile(r"((?:authorization:\s*)?bearer\s+)(\S+)", re.IGNORECASE)
_AUTH_HEADER_PAT = re.compile(rf"(authorization:\s*)(?!{re.escape(REDACTED)})(\S+)", re.IGNORECASE)
_JWT_PAT = re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9._-]{8,}")
# Long, mixed, opaque: a key or a token rather than a resource name.
_OPAQUE_PAT = re.compile(
    r"\b(?=[A-Za-z0-9+/_-]*[A-Za-z])(?=[A-Za-z0-9+/_-]*[0-9])[A-Za-z0-9+/_-]{28,}={0,2}"
)


def redact_command(command: str) -> str:
    """Display form of a command: secret values removed, nothing truncated."""
    text = _LITERAL_PAT.sub(rf"\g<1>{REDACTED}", command)
    text = _SECRET_FLAG_PAT.sub(rf"\g<1>{REDACTED}", text)
    text = _SECRET_KV_PAT.sub(rf"\g<1>{REDACTED}", text)
    text = _BEARER_PAT.sub(rf"\g<1>{REDACTED}", text)
    text = _AUTH_HEADER_PAT.sub(rf"\g<1>{REDACTED}", text)
    text = _JWT_PAT.sub(REDACTED, text)
    return _OPAQUE_PAT.sub(REDACTED, text)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def command_digest(command: str) -> str:
    """Digest of the complete command, unredacted and untruncated."""
    return f"sha256:{sha256_hex(command)}"


def authority_for(identity: str) -> str:
    """Render a decider identity as an RFC-0001 authority string."""
    identity = (identity or "").strip()
    if not identity:
        return AUTHORITY_NONE
    if identity.split(":", 1)[0].lower() in _AUTHORITY_SCHEMES:
        return identity
    return f"human:{identity}"


def advisory_display(claimed: str) -> dict[str, Any] | None:
    """Wrap a self-asserted operator name so no reader mistakes it for identity."""
    if not claimed:
        return None
    return {"claimed": claimed, "source": "x-kopilot-operator", "advisory": True}


def subject_ref(command: str) -> str:
    """Plane-appropriate subject path for a command: the namespaces it names."""
    normalized = normalize_command(command)
    if _ALL_NS_PAT.search(normalized):
        return "ns/*"
    names = list(dict.fromkeys(m.group(1).strip("\"'") for m in _NS_PAT.finditer(normalized)))
    return ("ns/" + ",".join(names)) if names else "cluster"


# ── The chain ───────────────────────────────────────────────────────────────


def new_event_id() -> str:
    """A ULID: 48 bits of millisecond time, 80 bits of randomness, sortable."""
    value = (int(datetime.now(UTC).timestamp() * 1000) << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def _canonical(entry: dict[str, Any]) -> str:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def entry_hash(entry: dict[str, Any]) -> str:
    """SHA-256 over the canonical entry, excluding the hash field itself."""
    return sha256_hex(_canonical({k: v for k, v in entry.items() if k != "hash"}))


@dataclass
class ChainVerification:
    """Result of walking a ledger file end to end."""

    ok: bool
    entries: int
    broken_at: int | None = None
    reason: str = ""


def verify_chain(path: str | Path) -> ChainVerification:
    """Walk the ledger and check every link: order, contents, and hashes."""
    ledger_file = Path(path)
    if not ledger_file.exists():
        return ChainVerification(ok=True, entries=0, reason="ledger file does not exist yet")

    expected = GENESIS_HASH
    count = 0
    with ledger_file.open(encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                return ChainVerification(False, count, lineno, "line is not valid JSON")
            if entry.get("prev_hash") != expected:
                return ChainVerification(
                    False, count, lineno, "prev_hash does not match the preceding entry"
                )
            if entry_hash(entry) != entry.get("hash"):
                return ChainVerification(
                    False, count, lineno, "entry hash does not match the entry contents"
                )
            expected = entry["hash"]
            count += 1
    return ChainVerification(ok=True, entries=count)


class Ledger:
    """Append-only JSONL file with a SHA-256 chain over every entry."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._tip = self._read_tip()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def tip(self) -> str:
        return self._tip

    def _read_tip(self) -> str:
        """Hash of the last entry, so a restart continues the same chain."""
        last = self._last_line()
        if last is None:
            return GENESIS_HASH
        try:
            return str(json.loads(last)["hash"])
        except (ValueError, KeyError):
            logger.error("ledger.tip_unreadable", path=str(self._path))
            return GENESIS_HASH

    def _last_line(self) -> str | None:
        if not self._path.exists() or self._path.stat().st_size == 0:
            return None
        size = self._path.stat().st_size
        with self._path.open("rb") as handle:
            window = min(size, _TAIL_WINDOW)
            handle.seek(size - window)
            chunk = handle.read(window)
            # A single entry longer than the window: fall back to a full read.
            if b"\n" not in chunk[:-1] and window < size:
                handle.seek(0)
                chunk = handle.read()
        lines = [ln for ln in chunk.decode("utf-8", errors="replace").splitlines() if ln.strip()]
        return lines[-1] if lines else None

    def append(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Link an envelope onto the chain and flush it to disk."""
        with self._lock:
            entry = dict(envelope)
            entry["prev_hash"] = self._tip
            entry["hash"] = entry_hash(entry)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(_canonical(entry) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._tip = entry["hash"]
            return entry

    def entries(self) -> list[dict[str, Any]]:
        """Every entry, oldest first."""
        if not self._path.exists():
            return []
        with self._path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def verify(self) -> ChainVerification:
        return verify_chain(self._path)


_ledger: Ledger | None = None
_ledger_lock = threading.Lock()
_ledger_warned = False


def ledger_path() -> str:
    """Where the ledger lives: LEDGER_PATH, else beside the approvals DB."""
    explicit = os.environ.get("LEDGER_PATH", "").strip()
    if explicit:
        return explicit

    from kopilot.config import get_settings

    db_path = get_settings().approvals.db_path.strip()
    if not db_path:
        return ""
    try:
        return str(Path(db_path).with_name(LEDGER_FILENAME))
    except ValueError:
        return ""


def get_ledger() -> Ledger | None:
    """The process ledger, or None when no persistence path is configured."""
    global _ledger, _ledger_warned  # noqa: PLW0603
    with _ledger_lock:
        if _ledger is not None:
            return _ledger
        path = ledger_path()
        if not path:
            if not _ledger_warned:
                logger.warning(
                    "ledger.disabled",
                    hint="Set LEDGER_PATH or APPROVALS_DB_PATH to keep an audit ledger.",
                )
                _ledger_warned = True
            return None
        try:
            _ledger = Ledger(path)
        except OSError as exc:
            logger.error("ledger.open_failed", path=path, error=str(exc))
            return None
        logger.info("ledger.opened", path=str(_ledger.path), tip=_ledger.tip)
        return _ledger


def reset_ledger() -> None:
    """Drop the cached ledger handle (used in tests and on config reload)."""
    global _ledger, _ledger_warned  # noqa: PLW0603
    with _ledger_lock:
        _ledger = None
        _ledger_warned = False


# ── Emission ────────────────────────────────────────────────────────────────


def record_event(
    action: str,
    decision_type: Decision | str,
    authority: str,
    *,
    plane: Plane | str = Plane.GOVERN,
    agent_id: str = "kopilot",
    subject_kind: str = "k8s",
    ref: str = "cluster",
    policy_ref: str | None = None,
    evidence: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any] | None:
    """Append one RFC-0001 v0 envelope; returns the linked entry, or None.

    ``context`` carries the kopilot-specific detail (approval id, stage,
    outcome) that the shared envelope has no field for.
    """
    envelope: dict[str, Any] = {
        "schema": LEDGER_SCHEMA,
        "event_id": new_event_id(),
        "time": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "plane": str(plane),
        "agent": {
            "id": agent_id,
            "framework": f"kopilot@{__version__}",
            "run_id": run_id or current_task_id(),
        },
        "subject": {"kind": subject_kind, "ref": ref},
        "action": action,
        "decision": {"type": str(decision_type), "authority": authority},
    }
    if policy_ref:
        envelope["decision"]["policy_ref"] = policy_ref
    if evidence:
        envelope["evidence"] = evidence
    if context:
        envelope["context"] = {k: v for k, v in context.items() if v is not None}

    ledger = get_ledger()
    if ledger is None:
        return None
    try:
        return ledger.append(envelope)
    except OSError as exc:
        logger.error("ledger.append_failed", path=str(ledger.path), error=str(exc))
        return None


def record_command_event(
    command: str,
    tool: str,
    decision_type: Decision | str,
    authority: str,
    *,
    policy_ref: str | None = None,
    evidence: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Ledger an event about a command: hashed in full, displayed redacted."""
    full_evidence = {"digest": command_digest(command)}
    full_evidence.update(evidence or {})
    return record_event(
        redact_command(command),
        decision_type,
        authority,
        agent_id=f"kopilot/executor/{tool}",
        ref=subject_ref(command),
        policy_ref=policy_ref,
        evidence=full_evidence,
        context=context,
    )
