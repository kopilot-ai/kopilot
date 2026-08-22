"""Generic execution middleware.

Provides a small set of *generic* tools that any sub-agent can use to interact
with a Kubernetes cluster. The LLM decides what commands to run; the middleware
enforces safety, rate-limiting, approval gating, and audit logging.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import threading
import time
from collections import defaultdict
from dataclasses import dataclass

import structlog
from langchain_core.tools import tool

from kopilot.agent.safety import RiskLevel, SafetyVerdict, assess_command, is_mutating
from kopilot.config import get_settings
from kopilot.executor.approvals import get_approval_store
from kopilot.outputs.audit import (
    SAFETY_AUTHORITY,
    Decision,
    authority_for,
    log_event,
    record_command_event,
    redact_command,
    sha256_hex,
)
from kopilot.taskscope import current_task_id, record_risk

logger = structlog.get_logger(__name__)

MAX_OUTPUT = 12_000
# Hard cap on bytes read from a subprocess before it is killed. Prevents a
# chatty `kubectl logs` from buffering gigabytes in memory.
MAX_CAPTURE_BYTES = 2_000_000
DEFAULT_TIMEOUT = 90

# A shell-string denylist is inherently leaky and is only the last of several
# layers (RBAC first, then the safety assessor). These patterns cover the
# unambiguously catastrophic forms, terminated on any shell boundary character
# so a trailing quote, semicolon, or paren cannot slip past the anchor.
_ROOT_END = r"(?:[\s'\";&|)]|$|\*)"
_BLOCKED_PATTERNS = re.compile(
    # rm with recursive and/or force flags aimed at /
    rf"\brm\s+(?:-[a-zA-Z-]+\s+)*-[a-zA-Z-]*[rRf][a-zA-Z-]*\s+(?:-[a-zA-Z-]+\s+)*/{_ROOT_END}|"
    rf"\brm\s+(?:[^|;&]*\s)?--(?:recursive|force)\b[^|;&]*\s/{_ROOT_END}|"
    # --no-preserve-root is never legitimate here, whatever the target
    r"--no-preserve-root\b|"
    r":\(\)\s*\{.*\};\s*:|"
    r"mkfs\.|"
    r"\bdd\s+[^|;&]*of=/dev/|"
    r"\bshutdown\b|\breboot\b|\bhalt\b|\binit\s+0\b|"
    rf"\bchmod\s+(?:-[a-zA-Z]+\s+)*0*000\s+/{_ROOT_END}|"
    rf"\bchown\s+(?:-[a-zA-Z]+\s+)*\S+\s+/{_ROOT_END}|"
    r"\bfind\s+/\s+[^|;&]*-delete\b|"
    r">\s*/dev/[sh]d[a-z]\b",
    re.IGNORECASE,
)

_NAME_PAT = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")
_URL_PAT = re.compile(r"^https?://[^\s\"'<>\\]+$")


class ToolRateLimiter:
    """Sliding-window rate limiter for tool calls per thread/task."""

    def __init__(self, max_calls: int = 50, window_seconds: float = 300.0):
        self._max_calls = max_calls
        self._window = window_seconds
        self._calls: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, thread_id: str = "global") -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = [t for t in self._calls[thread_id] if now - t < self._window]
            if len(bucket) >= self._max_calls:
                self._calls[thread_id] = bucket
                return False
            bucket.append(now)
            self._calls[thread_id] = bucket
            # Evict stale buckets so long-running processes don't leak memory.
            for key in [k for k, v in self._calls.items() if not v and k != thread_id]:
                del self._calls[key]
            return True

    def reset(self, thread_id: str = "global") -> None:
        with self._lock:
            self._calls.pop(thread_id, None)


_rate_limiter = ToolRateLimiter(max_calls=50, window_seconds=300.0)

_execution_stats: dict[str, int] = defaultdict(int)


def get_execution_stats() -> dict[str, int]:
    return dict(_execution_stats)


class CommandTimeoutError(Exception):
    pass


async def _read_capped(stream: asyncio.StreamReader | None, cap: int) -> tuple[bytes, bool]:
    """Read a stream up to ``cap`` bytes. Returns (data, truncated)."""
    if stream is None:
        return b"", False
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return b"".join(chunks), False
        total += len(chunk)
        if total > cap:
            chunks.append(chunk[: cap - (total - len(chunk))])
            return b"".join(chunks), True
        chunks.append(chunk)


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()


async def _run_once(cmd: str | list[str], timeout: int) -> tuple[int | None, str]:
    """Run a command (shell string or argv list) with timeout and output cap."""
    if isinstance(cmd, str):
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

    read_task = asyncio.gather(
        _read_capped(proc.stdout, MAX_CAPTURE_BYTES),
        _read_capped(proc.stderr, MAX_CAPTURE_BYTES),
    )
    try:
        (out, out_trunc), (err, err_trunc) = await asyncio.wait_for(read_task, timeout=timeout)
    except TimeoutError:
        read_task.cancel()
        _kill_process_group(proc)
        await proc.wait()
        raise
    truncated = out_trunc or err_trunc
    if truncated:
        _kill_process_group(proc)
    await proc.wait()

    combined = (out.decode(errors="replace") + err.decode(errors="replace")).strip()
    if truncated:
        combined += "\n...(output limit reached; process terminated)"
    if len(combined) > MAX_OUTPUT:
        combined = combined[:MAX_OUTPUT] + "\n...(truncated)"
    return proc.returncode, combined


async def _exec(cmd: str | list[str], timeout: int = DEFAULT_TIMEOUT) -> str:
    """Execute a command with timeout and output caps.

    Read-only commands are retried once on timeout; mutating commands are
    never retried automatically (a timed-out `kubectl apply` may have taken
    effect on the server).
    """
    display = cmd if isinstance(cmd, str) else " ".join(cmd)
    retryable = isinstance(cmd, list) or not is_mutating(cmd)
    attempts = 2 if retryable else 1

    for attempt in range(attempts):
        _execution_stats["total_commands"] += 1
        try:
            returncode, combined = await _run_once(cmd, timeout)
        except TimeoutError:
            _execution_stats["timeouts"] += 1
            if attempt < attempts - 1:
                await asyncio.sleep(2)
                continue
            raise CommandTimeoutError(
                f"Command timed out after {timeout}s: {display[:100]}"
            ) from None

        if returncode != 0:
            _execution_stats["errors"] += 1
        else:
            _execution_stats["successes"] += 1
        return combined

    raise CommandTimeoutError(f"Command timed out after {timeout}s: {display[:100]}")


def _denylist_verdict() -> SafetyVerdict:
    return SafetyVerdict(
        allowed=False,
        risk=RiskLevel.CRITICAL,
        reason="Command matches a blocked destructive pattern.",
    )


def _current_verdict(command: str) -> SafetyVerdict:
    """Safety assessment as of now, without spending a rate-limit slot."""
    if _BLOCKED_PATTERNS.search(command):
        return _denylist_verdict()
    return assess_command(command)


def _pre_flight(command: str) -> SafetyVerdict:
    """Run safety checks before executing any command."""
    if _BLOCKED_PATTERNS.search(command):
        _execution_stats["blocked"] += 1
        return _denylist_verdict()

    if not _rate_limiter.check(current_task_id()):
        _execution_stats["rate_limited"] += 1
        return SafetyVerdict(
            allowed=False,
            risk=RiskLevel.MEDIUM,
            reason="Tool call rate limit exceeded. Wait before retrying.",
        )

    verdict = assess_command(command)
    record_risk(verdict.risk.value)
    return verdict


def _brake_state() -> tuple[str, list[str]]:
    """The policy holding the brake, as an authority string plus every brake."""
    from kopilot.executor.autonomy import get_engine

    brakes = list(get_engine().snapshot().get("brakes") or [])
    return (f"policy:{brakes[0]}" if brakes else "policy:autonomy-level-0"), brakes


def _record_brake(
    command: str, tool_name: str, stage: str, risk: str, approval_id: str = ""
) -> None:
    authority, brakes = _brake_state()
    record_command_event(
        command,
        tool_name,
        Decision.BRAKED,
        authority,
        policy_ref=brakes[0] if brakes else None,
        context={
            "stage": stage,
            "risk": risk,
            "tool": tool_name,
            "brakes": brakes,
            "approval_id": approval_id or None,
        },
    )


async def _run_and_record(
    command: str,
    tool_name: str,
    authority: str,
    *,
    stage: str = "execution_result",
    policy_ref: str | None = None,
    approval_id: str = "",
) -> str:
    """Execute a command and ledger its result under the deciding authority."""
    context: dict = {
        "stage": stage,
        "tool": tool_name,
        "approval_id": approval_id or None,
    }
    try:
        output = await _exec(command)
    except CommandTimeoutError as e:
        record_command_event(
            command,
            tool_name,
            Decision.OBSERVED,
            authority,
            policy_ref=policy_ref,
            context={**context, "outcome": "timeout", "error": str(e)},
        )
        return f"ERROR: {e}"

    record_command_event(
        command,
        tool_name,
        Decision.OBSERVED,
        authority,
        policy_ref=policy_ref,
        context={
            **context,
            "outcome": "completed",
            "output_sha256": sha256_hex(output),
            "output_bytes": len(output),
        },
    )
    return output


async def _guarded_run(command: str, tool_name: str) -> str:
    """Common safety pipeline + execution for the kubectl/helm/shell tools."""
    from kopilot.executor.autonomy import AutonomyDecision, get_engine

    verdict = _pre_flight(command)

    decision = get_engine().decide(command, tool_name, verdict.risk, verdict.requires_approval)
    if decision is AutonomyDecision.REFUSE:
        log_event("executor.observe_refused", command=redact_command(command)[:200], tool=tool_name)
        _record_brake(command, tool_name, "observe_refused", verdict.risk.value)
        return (
            "OBSERVE MODE (autonomy level 0): mutating commands are refused. "
            "Report what you would have done instead."
        )
    if decision is AutonomyDecision.AUTO_APPROVE:
        store = get_approval_store()
        policy = get_engine().granting_policy(command, verdict.risk) or "unknown"
        record = store.record_auto(
            command=command,
            tool=tool_name,
            reason=verdict.reason,
            risk=verdict.risk.value,
            policy=policy,
        )
        log_event(
            f"executor.{tool_name}.auto_approved",
            command=redact_command(command)[:200],
            approval_id=record.id,
            policy=policy,
        )
        return await _run_and_record(
            command,
            tool_name,
            f"policy:{policy}",
            stage="autopilot_execution_result",
            policy_ref=policy,
            approval_id=record.id,
        )

    if verdict.requires_approval:
        store = get_approval_store()
        approved = store.consume_if_approved(command)
        if approved is not None:
            log_event(
                f"executor.{tool_name}.approved",
                command=redact_command(command)[:200],
                approval_id=approved.id,
            )
            return await _run_and_record(
                command,
                tool_name,
                authority_for(approved.decided_by),
                stage="approved_execution_result",
                approval_id=approved.id,
            )

        req = store.request(
            command=command, tool=tool_name, reason=verdict.reason, risk=verdict.risk.value
        )
        log_event(
            "executor.approval_required",
            command=redact_command(command)[:200],
            approval_id=req.id,
        )
        return (
            f"APPROVAL REQUIRED ({verdict.risk.value}): {verdict.reason} "
            f"A pending approval request was created with id '{req.id}'. "
            f"A human operator must approve it (POST /approvals/{req.id}/approve) "
            f"before this exact command can be executed. Do not attempt to work "
            f"around this gate; report the approval id to the user instead."
        )

    if not verdict.allowed:
        log_event("executor.blocked", command=redact_command(command)[:200], reason=verdict.reason)
        record_command_event(
            command,
            tool_name,
            Decision.DENIED,
            SAFETY_AUTHORITY,
            context={
                "stage": "safety_blocked",
                "risk": verdict.risk.value,
                "tool": tool_name,
                "reason": verdict.reason,
            },
        )
        return f"BLOCKED ({verdict.risk.value}): {verdict.reason}"

    log_event(f"executor.{tool_name}", command=redact_command(command)[:200])
    return await _run_and_record(command, tool_name, SAFETY_AUTHORITY)


@dataclass
class ApprovedExecution:
    """Outcome of running a command that was approved earlier."""

    executed: bool
    output: str


async def execute_approved(req) -> ApprovedExecution:
    """Run the exact command a human approved, re-checked at execution time.

    An approval is a decision about the past; the brake and the safety
    assessment are facts about now.  Both are re-evaluated here, so an
    approval issued before an emergency brake engaged refuses instead of
    running, and the refusal is written to the ledger like any other decision.
    """
    from kopilot.executor.autonomy import AutonomyDecision, get_engine

    verdict = _current_verdict(req.command)
    decision = get_engine().decide(req.command, req.tool, verdict.risk, verdict.requires_approval)

    if decision is AutonomyDecision.REFUSE:
        _execution_stats["brake_refused"] += 1
        log_event(
            "executor.approved_refused_by_brake",
            command=redact_command(req.command)[:200],
            approval_id=req.id,
        )
        _record_brake(
            req.command, req.tool, "approved_execution_refused", verdict.risk.value, req.id
        )
        _, brakes = _brake_state()
        held_by = ", ".join(brakes) or "autonomy level 0"
        return ApprovedExecution(
            executed=False,
            output=(
                f"REFUSED: the emergency brake ({held_by}) engaged after this approval "
                f"was granted. The command was not run; clear the brake and request "
                f"approval again."
            ),
        )

    blocked_now = not verdict.allowed and (
        not verdict.requires_approval or verdict.risk is RiskLevel.CRITICAL
    )
    if blocked_now:
        _execution_stats["blocked"] += 1
        log_event(
            "executor.approved_blocked",
            command=redact_command(req.command)[:200],
            approval_id=req.id,
            reason=verdict.reason,
        )
        record_command_event(
            req.command,
            req.tool,
            Decision.DENIED,
            SAFETY_AUTHORITY,
            context={
                "stage": "approved_execution_blocked",
                "risk": verdict.risk.value,
                "tool": req.tool,
                "approval_id": req.id,
                "reason": verdict.reason,
            },
        )
        return ApprovedExecution(
            executed=False,
            output=(
                f"BLOCKED ({verdict.risk.value}): {verdict.reason} The safety assessment "
                f"changed after this approval was granted; the command was not run."
            ),
        )

    log_event(
        "executor.approved_via_api",
        command=redact_command(req.command)[:200],
        approval_id=req.id,
        by=req.decided_by,
    )
    output = await _run_and_record(
        req.command,
        req.tool,
        authority_for(req.decided_by),
        stage="approved_execution_result",
        approval_id=req.id,
    )
    return ApprovedExecution(executed=True, output=output)


@tool
async def run_kubectl(command: str) -> str:
    """Run any kubectl command against the cluster.

    Pass the FULL command string including 'kubectl', e.g.:
      run_kubectl("kubectl get pods -n production -o wide")
      run_kubectl("kubectl describe node worker-1")
      run_kubectl("kubectl apply -f - <<EOF\\napiVersion: v1\\n...")

    Destructive operations on protected namespaces are blocked; other
    destructive operations require human approval before they run.
    """
    if not command.strip().startswith("kubectl"):
        command = f"kubectl {command}"
    return await _guarded_run(command, "kubectl")


@tool
async def run_helm(command: str) -> str:
    """Run any Helm command.

    Pass the FULL command string including 'helm', e.g.:
      run_helm("helm list -A")
      run_helm("helm install my-release bitnami/nginx -n web --create-namespace")
      run_helm("helm upgrade my-release ./chart --set replicas=3 --dry-run")

    Uninstall and rollback operations require human approval.
    """
    if not command.strip().startswith("helm"):
        command = f"helm {command}"
    return await _guarded_run(command, "helm")


@tool
async def run_shell(command: str) -> str:
    """Run a general shell command for diagnostics or scripting.

    Use this for commands that are NOT kubectl or helm, e.g.:
      run_shell("curl -s http://my-service:8080/health")
      run_shell("cat /etc/resolv.conf")
      run_shell("dig kubernetes.default.svc.cluster.local")

    Dangerous commands (rm -rf /, dd, mkfs) are blocked.
    """
    return await _guarded_run(command, "shell")


@tool
async def read_resource(path_or_url: str) -> str:
    """Read a documentation file, URL, or Kubernetes ConfigMap.

    Examples:
      read_resource("/etc/kopilot/docs/security-playbook.md")
      read_resource("configmap:my-config:default")  # reads a ConfigMap

    For ConfigMaps, use the format: configmap:<name>:<namespace>
    File reads are restricted to the configured documentation directories.
    """
    if not _rate_limiter.check(current_task_id()):
        _execution_stats["rate_limited"] += 1
        return "BLOCKED (medium): Tool call rate limit exceeded. Wait before retrying."

    if path_or_url.startswith("configmap:"):
        parts = path_or_url.split(":")
        name = parts[1] if len(parts) > 1 else ""
        ns = parts[2] if len(parts) > 2 and parts[2] else "default"
        if not _NAME_PAT.match(name) or not _NAME_PAT.match(ns):
            return f"BLOCKED: invalid ConfigMap reference '{path_or_url[:120]}'."
        log_event("executor.read_configmap", name=name, namespace=ns)
        try:
            return await _exec(["kubectl", "get", "configmap", name, "-n", ns, "-o", "yaml"])
        except CommandTimeoutError as e:
            return f"ERROR: {e}"

    if path_or_url.startswith(("http://", "https://")):
        if not _URL_PAT.match(path_or_url):
            return f"BLOCKED: malformed URL '{path_or_url[:120]}'."
        log_event("executor.read_url", url=path_or_url[:200])
        try:
            return await _exec(["curl", "-sL", "--max-time", "15", "--", path_or_url])
        except CommandTimeoutError as e:
            return f"ERROR: {e}"

    import pathlib

    allowed_roots = [
        pathlib.Path(root).resolve() for root in get_settings().safety.read_paths if root.strip()
    ]
    try:
        p = pathlib.Path(path_or_url).resolve()
        if not any(p.is_relative_to(root) for root in allowed_roots):
            allowed = ", ".join(str(r) for r in allowed_roots) or "(no configured directories)"
            return f"BLOCKED: file reads are restricted to {allowed}."
        if p.exists() and p.is_file():
            log_event("executor.read_file", path=str(p))
            return p.read_text(encoding="utf-8", errors="replace")[:MAX_OUTPUT]
    except OSError:
        pass

    return f"Could not read resource: {path_or_url}"


EXECUTOR_TOOLS = [run_kubectl, run_helm, run_shell, read_resource]
