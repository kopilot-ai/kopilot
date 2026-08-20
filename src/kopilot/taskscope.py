"""Per-task execution scope shared between the supervisor and the executor.

Uses contextvars so that tool invocations running inside a task's async
context can report back (risk levels seen, rate-limit bucket) without
threading state through LangChain tool signatures.

The values stored are *mutable holders*: child asyncio tasks inherit a copy
of the context, so setting a ContextVar inside a tool would not be visible to
the supervisor. Mutating the shared holder object is.
"""

from __future__ import annotations

from contextvars import ContextVar

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

_scope: ContextVar[dict[str, str] | None] = ContextVar("kopilot_task_scope", default=None)


def begin_task(task_id: str) -> None:
    """Enter a task scope: set the task id and reset the recorded risk."""
    _scope.set({"task_id": task_id, "max_risk": "low"})


def current_task_id() -> str:
    scope = _scope.get()
    return scope["task_id"] if scope else "global"


def record_risk(risk: str) -> None:
    """Record the risk level of an executed/attempted command."""
    scope = _scope.get()
    if scope is None:
        return
    if _RISK_ORDER.get(risk, 0) > _RISK_ORDER.get(scope["max_risk"], 0):
        scope["max_risk"] = risk


def max_recorded_risk() -> str:
    scope = _scope.get()
    return scope["max_risk"] if scope else "low"
