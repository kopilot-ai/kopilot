"""Generic execution middleware.

Provides a small set of *generic* tools that any sub-agent can use to interact
with a Kubernetes cluster. The LLM decides what commands to run; the middleware
enforces safety, rate-limiting, and audit logging.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from typing import Any

import structlog
from langchain_core.tools import tool
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from kubedevaiops.agent.safety import RiskLevel, SafetyVerdict, assess_command
from kubedevaiops.outputs.audit import log_event

logger = structlog.get_logger(__name__)

MAX_OUTPUT = 12_000
DEFAULT_TIMEOUT = 90

_BLOCKED_PATTERNS = re.compile(
    r"rm\s+-rf\s+/|"
    r":(){ :|:& };:|"
    r"mkfs\.|"
    r"dd\s+if=.*/dev/",
    re.IGNORECASE,
)


class ToolRateLimiter:
    """Token-bucket rate limiter for tool calls per thread/task."""

    def __init__(self, max_calls: int = 50, window_seconds: float = 300.0):
        self._max_calls = max_calls
        self._window = window_seconds
        self._calls: dict[str, list[float]] = defaultdict(list)

    def check(self, thread_id: str = "global") -> bool:
        now = time.monotonic()
        self._calls[thread_id] = [
            t for t in self._calls[thread_id] if now - t < self._window
        ]
        if len(self._calls[thread_id]) >= self._max_calls:
            return False
        self._calls[thread_id].append(now)
        return True

    def reset(self, thread_id: str = "global") -> None:
        self._calls.pop(thread_id, None)


_rate_limiter = ToolRateLimiter(max_calls=50, window_seconds=300.0)

_execution_stats: dict[str, int] = defaultdict(int)


def get_execution_stats() -> dict[str, int]:
    return dict(_execution_stats)


class CommandTimeoutError(Exception):
    pass


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(CommandTimeoutError),
    reraise=True,
)
async def _exec(cmd: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Execute a shell command with timeout, retry on transient failures."""
    _execution_stats["total_commands"] += 1
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        _execution_stats["timeouts"] += 1
        raise CommandTimeoutError(f"Command timed out after {timeout}s: {cmd[:100]}")

    out = (stdout or b"").decode(errors="replace")
    err = (stderr or b"").decode(errors="replace")
    combined = (out + err).strip()

    if proc.returncode != 0:
        _execution_stats["errors"] += 1
    else:
        _execution_stats["successes"] += 1

    if len(combined) > MAX_OUTPUT:
        combined = combined[:MAX_OUTPUT] + "\n...(truncated)"
    return combined


def _pre_flight(command: str) -> SafetyVerdict:
    """Run safety checks before executing any command."""
    if _BLOCKED_PATTERNS.search(command):
        _execution_stats["blocked"] += 1
        return SafetyVerdict(
            allowed=False,
            risk=RiskLevel.CRITICAL,
            reason="Command matches a blocked destructive pattern.",
        )

    if not _rate_limiter.check():
        _execution_stats["rate_limited"] += 1
        return SafetyVerdict(
            allowed=False,
            risk=RiskLevel.MEDIUM,
            reason="Tool call rate limit exceeded. Wait before retrying.",
        )

    return assess_command(command)


@tool
async def run_kubectl(command: str) -> str:
    """Run any kubectl command against the cluster.

    Pass the FULL command string including 'kubectl', e.g.:
      run_kubectl("kubectl get pods -n production -o wide")
      run_kubectl("kubectl describe node worker-1")
      run_kubectl("kubectl apply -f - <<EOF\\napiVersion: v1\\n...")

    Destructive operations on protected namespaces will be blocked.
    """
    if not command.strip().startswith("kubectl"):
        command = f"kubectl {command}"

    verdict = _pre_flight(command)
    if not verdict.allowed:
        log_event("executor.blocked", command=command[:200], reason=verdict.reason)
        return f"BLOCKED ({verdict.risk.value}): {verdict.reason}"
    if verdict.requires_approval:
        log_event("executor.approval_required", command=command[:200])
        return f"APPROVAL REQUIRED ({verdict.risk.value}): {verdict.reason}"

    log_event("executor.kubectl", command=command[:200])
    try:
        return await _exec(command)
    except CommandTimeoutError as e:
        return f"ERROR: {e}"


@tool
async def run_helm(command: str) -> str:
    """Run any Helm command.

    Pass the FULL command string including 'helm', e.g.:
      run_helm("helm list -A")
      run_helm("helm install my-release bitnami/nginx -n web --create-namespace")
      run_helm("helm upgrade my-release ./chart --set replicas=3 --dry-run")

    Uninstall operations are subject to safety checks.
    """
    if not command.strip().startswith("helm"):
        command = f"helm {command}"

    verdict = _pre_flight(command)
    if not verdict.allowed:
        log_event("executor.blocked", command=command[:200], reason=verdict.reason)
        return f"BLOCKED ({verdict.risk.value}): {verdict.reason}"
    if verdict.requires_approval:
        log_event("executor.approval_required", command=command[:200])
        return f"APPROVAL REQUIRED ({verdict.risk.value}): {verdict.reason}"

    log_event("executor.helm", command=command[:200])
    try:
        return await _exec(command)
    except CommandTimeoutError as e:
        return f"ERROR: {e}"


@tool
async def run_shell(command: str) -> str:
    """Run a general shell command for diagnostics or scripting.

    Use this for commands that are NOT kubectl or helm, e.g.:
      run_shell("curl -s http://my-service:8080/health")
      run_shell("cat /etc/resolv.conf")
      run_shell("dig kubernetes.default.svc.cluster.local")

    Dangerous commands (rm -rf /, dd, mkfs) are blocked.
    """
    verdict = _pre_flight(command)
    if not verdict.allowed:
        log_event("executor.blocked", command=command[:200], reason=verdict.reason)
        return f"BLOCKED ({verdict.risk.value}): {verdict.reason}"

    log_event("executor.shell", command=command[:200])
    try:
        return await _exec(command)
    except CommandTimeoutError as e:
        return f"ERROR: {e}"


@tool
async def read_resource(path_or_url: str) -> str:
    """Read a documentation file, URL, or Kubernetes resource manifest.

    Examples:
      read_resource("/etc/kubedevaiops/docs/security-playbook.md")
      read_resource("configmap:my-config:default")  # reads a ConfigMap

    For ConfigMaps, use the format: configmap:<name>:<namespace>
    """
    if path_or_url.startswith("configmap:"):
        parts = path_or_url.split(":")
        name = parts[1] if len(parts) > 1 else ""
        ns = parts[2] if len(parts) > 2 else "default"
        try:
            return await _exec(f"kubectl get configmap {name} -n {ns} -o yaml")
        except CommandTimeoutError as e:
            return f"ERROR: {e}"

    if path_or_url.startswith(("http://", "https://")):
        try:
            return await _exec(f"curl -sL --max-time 15 \"{path_or_url}\"")
        except CommandTimeoutError as e:
            return f"ERROR: {e}"

    try:
        import pathlib
        p = pathlib.Path(path_or_url)
        if p.exists() and p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            return text[:MAX_OUTPUT]
    except Exception:
        pass

    return f"Could not read resource: {path_or_url}"


EXECUTOR_TOOLS = [run_kubectl, run_helm, run_shell, read_resource]
