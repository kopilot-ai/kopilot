"""Safety guardrails for autonomous operations.

Every action the agent wants to take passes through these checks before
execution.  The posture is deny-by-default: a command is free to run only when
the gate can *prove* it is a read.  Everything else — every mutation, every
shell pipeline it cannot parse, every unrecognised binary — is approval-gated,
and anything touching a protected namespace is blocked outright.

Proving a read means parsing the command rather than pattern-matching it: the
shell string is split into segments on unquoted operators, each segment is
tokenised, and its leading verb is checked against an allowlist of read-only
kubectl/helm verbs and read-only shell utilities.  Command substitution,
variable indirection, redirection, and unbalanced quoting all make a command
unparseable, which makes it opaque, which makes it approval-gated.

These checks are one layer of defense in depth.  A parser-based gate is still
not a substitute for scoped RBAC on the service account the agent runs under.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import StrEnum

import structlog

from kopilot.config import get_settings

logger = structlog.get_logger(__name__)

DESTRUCTIVE_VERBS = {"delete", "drain", "cordon", "taint", "replace", "patch", "scale", "evict"}
HIGH_RISK_RESOURCES = {"node", "namespace", "persistentvolume", "clusterrole", "clusterrolebinding"}

# helm verbs that tear down or roll back a release.
_HELM_DESTRUCTIVE_VERBS = frozenset({"uninstall", "delete", "rollback"})

# ── read allowlists ─────────────────────────────────────────────────────────
#
# A verb earns a place here only when it cannot change cluster state.  Notable
# exclusions: `kubectl config` (repoints the agent at another cluster),
# `kubectl auth reconcile` (writes RBAC — `auth` is allowed only for `can-i`
# and `whoami`), and `helm template`/`helm lint` (`--post-renderer` executes an
# arbitrary binary).

_KUBECTL_READ_VERBS = frozenset(
    {
        "get",
        "describe",
        "logs",
        "top",
        "explain",
        "events",
        "diff",
        "wait",
        "api-resources",
        "api-versions",
        "cluster-info",
        "version",
        "auth",
        "rollout",
    }
)

# Verbs that read only for certain subcommands. `kubectl auth reconcile`
# writes RBAC; `kubectl rollout undo/restart/pause/resume` change workloads.
_SUBCOMMAND_READ_VERBS = {
    "auth": frozenset({"can-i", "whoami"}),
    "rollout": frozenset({"status", "history"}),
}

_HELM_READ_VERBS = frozenset(
    {
        "list",
        "status",
        "get",
        "history",
        "show",
        "search",
        "version",
        "env",
    }
)

# Shell utilities that only read.  `awk` and `sed` are deliberately absent:
# awk has system(), sed has -i.  `xargs`, `base64`, `sh`, `bash`, `eval` and
# friends are absent because they run whatever they are handed.
_SHELL_READ_COMMANDS = frozenset(
    {
        "cat",
        "head",
        "tail",
        "grep",
        "egrep",
        "fgrep",
        "sort",
        "uniq",
        "wc",
        "cut",
        "tr",
        "jq",
        "yq",
        "column",
        "nl",
        "rev",
        "echo",
        "printf",
        "date",
        "ls",
        "stat",
        "file",
        "hostname",
        "uname",
        "id",
        "whoami",
        "which",
        "env",
        "printenv",
        "ps",
        "df",
        "du",
        "free",
        "uptime",
        "dig",
        "nslookup",
        "host",
        "ping",
        "traceroute",
        "curl",
    }
)

# curl reads only while it is not asked to write a file, change method, or
# carry a body.
_CURL_WRITE_FLAG_PAT = re.compile(
    r"^(?:-X|--request|-o|--output|-O|--remote-name|-d|--data(?:-\S+)?|"
    r"-T|--upload-file|-F|--form(?:-\S+)?|-K|--config)$",
    re.IGNORECASE,
)

# Flags that consume the following token, so the verb scanner does not mistake
# a flag's value for the command verb.  An unlisted value-taking flag makes the
# scanner read the value as the verb, which fails the allowlist — fail closed.
_VALUE_FLAGS = frozenset(
    {
        "-n",
        "--namespace",
        "--context",
        "--cluster",
        "--user",
        "--kubeconfig",
        "--server",
        "-s",
        "--token",
        "--as",
        "--as-group",
        "--as-uid",
        "--request-timeout",
        "--cache-dir",
        "--certificate-authority",
        "--client-certificate",
        "--client-key",
        "--tls-server-name",
        "--username",
        "--password",
        "-v",
        "--v",
        "--log-flush-frequency",
        "--profile",
        "--profile-output",
        "--registry-config",
        "--repository-config",
        "--repository-cache",
        "--kube-context",
        "--burst-limit",
        "--qps",
    }
)

# Verbs whose payload the gate cannot inspect. They are approval-gated rather
# than blocked outright, since `kubectl exec` is a normal diagnostic tool.
_OPAQUE_PAYLOAD_PAT = re.compile(
    r"kubectl\s+(?:[^|;&]*\s)?(?:exec|cp|attach)\b",
    re.IGNORECASE,
)

# -n foo / -n=foo / -nfoo / --namespace foo / --namespace=foo
_NS_PAT = re.compile(r"(?:^|\s)(?:--namespace|-n)(?:[=\s]*)(\S+)", re.IGNORECASE)
_ALL_NS_PAT = re.compile(r"(?:^|\s)(?:-A\b|--all-namespaces\b)")

# Namespace names are DNS labels; split the command on everything a name
# cannot contain so protected namespaces match as whole words.  Without this,
# "kube-system" matches inside "my-kube-system-copy" and vice versa.
_WORD_SPLIT_PAT = re.compile(r"[^A-Za-z0-9_.-]+")

# Shell characters that end a command segment.
_SEGMENT_BREAKS = ";|&\n"
# Constructs that make a segment unparseable: command substitution, parameter
# expansion, and redirection all hide what will actually run.
_UNPARSEABLE_PAT = re.compile(r"\$\(|\$\{|`|<|>")


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


# ── parsing ─────────────────────────────────────────────────────────────────


def _split_pipeline(command: str) -> list[str] | None:
    """Split a shell string into segments on unquoted operators.

    Returns None when the string cannot be parsed with confidence: unbalanced
    quotes, a dangling escape, command substitution, parameter expansion, or
    redirection.  Callers treat None as opaque.
    """
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    escaped = False

    for ch in command:
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if ch == "\\" and quote != "'":
            buf.append(ch)
            escaped = True
            continue
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            continue
        if ch in _SEGMENT_BREAKS:
            segments.append("".join(buf))
            buf = []
            continue
        buf.append(ch)

    if quote is not None or escaped:
        return None
    segments.append("".join(buf))

    parsed = [s.strip() for s in segments if s.strip()]
    if any(_UNPARSEABLE_PAT.search(s) for s in parsed):
        return None
    return parsed


def _tokens(segment: str) -> list[str] | None:
    try:
        return shlex.split(segment)
    except ValueError:
        return None


def _binary(tokens: list[str]) -> str | None:
    """The command name, or None when it is not a plain bare name.

    A path (`./kubectl`, `/bin/sh`) or an inline assignment (`K=kubectl`) is
    not a name the gate can reason about.
    """
    if not tokens:
        return None
    head = tokens[0]
    if "/" in head or "=" in head or head.startswith("-"):
        return None
    return head


def _leading_verb(tokens: list[str]) -> tuple[str | None, list[str]]:
    """First non-flag token after the binary, plus whatever follows it."""
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-"):
            i += 2 if (tok in _VALUE_FLAGS and "=" not in tok) else 1
            continue
        return tok, tokens[i + 1 :]
    return None, []


def _segment_is_read(segment: str) -> bool:
    """True only when the segment is provably read-only."""
    tokens = _tokens(segment)
    if tokens is None:
        return False
    binary = _binary(tokens)
    if binary is None:
        return False

    if binary == "kubectl":
        verb, rest = _leading_verb(tokens)
        if verb not in _KUBECTL_READ_VERBS:
            return False
        read_subcommands = _SUBCOMMAND_READ_VERBS.get(verb)
        if read_subcommands is not None:
            return bool(rest) and rest[0] in read_subcommands
        return True

    if binary == "helm":
        verb, _ = _leading_verb(tokens)
        return verb in _HELM_READ_VERBS

    if binary == "curl":
        return not any(_CURL_WRITE_FLAG_PAT.match(t) for t in tokens[1:])

    return binary in _SHELL_READ_COMMANDS


def _segment_is_known_mutation(segment: str) -> bool:
    """True when the segment is a kubectl/helm call the gate can name.

    A recognisable mutation is still gated; the point of this check is to
    separate "I know this changes state" from "I have no idea what this does".
    """
    tokens = _tokens(segment)
    if tokens is None:
        return False
    binary = _binary(tokens)
    if binary not in {"kubectl", "helm"}:
        return False
    verb, _ = _leading_verb(tokens)
    return verb is not None and not verb.startswith("-")


# ── classifiers ─────────────────────────────────────────────────────────────


def is_read_only(command: str) -> bool:
    """True when every segment of the command is provably a read."""
    segments = _split_pipeline(_normalize(command))
    if not segments:
        return False
    return all(_segment_is_read(s) for s in segments)


def is_destructive(command: str) -> bool:
    """True when a segment names a destructive kubectl/helm verb."""
    segments = _split_pipeline(_normalize(command))
    if segments is None:
        return False
    for segment in segments:
        tokens = _tokens(segment)
        if tokens is None:
            continue
        binary = _binary(tokens)
        if binary == "kubectl":
            verb, _ = _leading_verb(tokens)
            if verb in DESTRUCTIVE_VERBS or "--prune" in tokens:
                return True
        elif binary == "helm":
            verb, _ = _leading_verb(tokens)
            if verb in _HELM_DESTRUCTIVE_VERBS:
                return True
    return False


def is_mutating(command: str) -> bool:
    """True when the command may change cluster state.

    Deny by default: anything not provably a read counts as mutating, which in
    turn means it requires approval (see :func:`assess_command`).
    """
    return not is_read_only(command)


def is_opaque(command: str) -> bool:
    """True when the gate cannot tell what the command will actually do."""
    normalized = _normalize(command)
    if _OPAQUE_PAYLOAD_PAT.search(normalized):
        return True
    segments = _split_pipeline(normalized)
    if segments is None or not segments:
        return True
    return not all(_segment_is_read(s) or _segment_is_known_mutation(s) for s in segments)


def _protected_ns_hit(command: str, protected: list[str]) -> str | None:
    """Return the protected namespace a command targets, if any.

    Matching is on whole names.  A namespace flag wins; failing that, a
    protected name appearing as a standalone word (``kubectl delete namespace
    kube-system``, ``ns/kube-system``) counts too.
    """
    protected_names = {ns for ns in protected if ns}
    if not protected_names:
        return None

    for match in _NS_PAT.finditer(command):
        ns = match.group(1).strip("\"'`;,")
        if ns in protected_names:
            return ns

    for word in _WORD_SPLIT_PAT.split(command):
        if word in protected_names:
            return word
    return None


def assess_command(command: str) -> SafetyVerdict:
    """Evaluate a raw shell / kubectl command and return a safety verdict."""
    cfg = get_settings().safety
    normalized = _normalize(command)

    destructive = is_destructive(normalized)
    opaque = is_opaque(normalized)
    mutating = destructive or opaque or is_mutating(normalized)

    # Protected namespaces are checked for every mutation, not just the
    # destructive ones: `kubectl apply -f - -n kube-system` replaces cluster
    # DNS just as finally as `kubectl delete` removes it.
    if mutating:
        target_ns = _protected_ns_hit(normalized, cfg.protected_namespaces)
        if target_ns:
            kind = "Destructive" if destructive else "Mutating"
            return SafetyVerdict(
                allowed=False,
                risk=RiskLevel.CRITICAL,
                reason=f"{kind} operation on protected namespace '{target_ns}' is blocked.",
            )

    if destructive and _ALL_NS_PAT.search(normalized):
        return SafetyVerdict(
            allowed=False,
            risk=RiskLevel.CRITICAL,
            reason="Destructive operation across all namespaces requires explicit approval.",
            requires_approval=True,
        )

    if opaque:
        return SafetyVerdict(
            allowed=not cfg.require_approval_destructive,
            risk=RiskLevel.HIGH,
            reason=(
                "Command cannot be safety-checked — shell indirection, an "
                "opaque container payload, or an unrecognised binary; "
                "approval required."
            ),
            requires_approval=cfg.require_approval_destructive,
        )

    if destructive:
        return SafetyVerdict(
            allowed=not cfg.require_approval_destructive,
            risk=RiskLevel.HIGH,
            reason="Destructive command detected; approval required.",
            requires_approval=cfg.require_approval_destructive,
        )

    if mutating:
        return SafetyVerdict(
            allowed=not cfg.require_approval_destructive,
            risk=RiskLevel.MEDIUM,
            reason="Mutating command; approval required before it changes cluster state.",
            requires_approval=cfg.require_approval_destructive,
        )

    return SafetyVerdict(allowed=True, risk=RiskLevel.LOW, reason="OK")
