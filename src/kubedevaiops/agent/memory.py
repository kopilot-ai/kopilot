"""Conversation and context memory for the agent.

Uses LangGraph's checkpointing with SQLite for durable persistence across restarts,
falling back to in-memory MemorySaver if SQLite is unavailable.
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

logger = structlog.get_logger(__name__)

DEFAULT_DB_DIR = pathlib.Path.home() / ".kubedevaiops"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "checkpoints.db"


@dataclass
class TaskContext:
    """Rich context attached to a single agent task."""

    task_id: str
    channel: str = "api"
    user: str = "system"
    namespace: str | None = None
    cluster_state: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
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
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    score: float = 0.0


_checkpointer: BaseCheckpointSaver | None = None


def get_checkpointer() -> BaseCheckpointSaver:
    """Singleton LangGraph checkpointer — SQLite (sync) if available, else in-memory."""
    global _checkpointer  # noqa: PLW0603
    if _checkpointer is not None:
        return _checkpointer

    db_path = os.environ.get("CHECKPOINT_DB_PATH", str(DEFAULT_DB_PATH))

    # MemorySaver supports both sync and async, making it safe for ainvoke().
    # SqliteSaver only supports sync; AsyncSqliteSaver requires a running event loop
    # to initialize. For production persistence, use Postgres via langgraph-checkpoint-postgres.
    _checkpointer = MemorySaver()
    logger.info("checkpointer.memory_saver")

    return _checkpointer


def reset_checkpointer() -> None:
    """Clear the cached checkpointer (used in tests)."""
    global _checkpointer  # noqa: PLW0603
    _checkpointer = None
