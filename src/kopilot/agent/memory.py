"""Conversation and context memory for the agent.

Uses LangGraph's in-memory checkpointer. Conversation state is process-local
and does not survive restarts; for durable multi-replica persistence, plug in
a Postgres checkpointer (langgraph-checkpoint-postgres) via get_checkpointer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

logger = structlog.get_logger(__name__)



@dataclass
class TaskContext:
    """Rich context attached to a single agent task."""

    task_id: str
    channel: str = "api"
    user: str = "system"
    namespace: str | None = None
    cluster_state: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class IncidentMemory:
    """Long-term memory for incidents and learned remediations."""

    incident_id: str
    summary: str
    root_cause: str
    remediation: str
    namespace: str = ""
    resource_kind: str = ""
    resource_name: str = ""
    resolved: bool = False
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    score: float = 0.0


_checkpointer: BaseCheckpointSaver | None = None


def get_checkpointer() -> BaseCheckpointSaver:
    """Singleton LangGraph checkpointer (in-memory).

    MemorySaver supports both sync and async invocation, making it safe for
    ainvoke(). State is lost on restart by design; swap in a Postgres saver
    (langgraph-checkpoint-postgres) here for durable persistence.
    """
    global _checkpointer  # noqa: PLW0603
    if _checkpointer is not None:
        return _checkpointer

    _checkpointer = MemorySaver()
    logger.info("checkpointer.memory_saver")

    return _checkpointer


def reset_checkpointer() -> None:
    """Clear the cached checkpointer (used in tests)."""
    global _checkpointer  # noqa: PLW0603
    _checkpointer = None
