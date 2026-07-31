"""Tests for memory and checkpointing."""

from __future__ import annotations

from kubedevaiops.agent.memory import (
    IncidentMemory,
    TaskContext,
    get_checkpointer,
    reset_checkpointer,
)


def test_task_context_defaults():
    ctx = TaskContext(task_id="t-1")
    assert ctx.channel == "api"
    assert ctx.user == "system"
    assert ctx.namespace is None
    assert ctx.created_at


def test_task_context_custom():
    ctx = TaskContext(
        task_id="t-2",
        channel="slack",
        user="alice",
        namespace="production",
        metadata={"priority": "high"},
    )
    assert ctx.channel == "slack"
    assert ctx.namespace == "production"


def test_incident_memory_defaults():
    mem = IncidentMemory(
        incident_id="inc-1",
        summary="Pod crash in prod",
        root_cause="OOM",
        remediation="Increase memory limit",
    )
    assert mem.resolved is False
    assert mem.score == 0.0
    assert mem.created_at


def test_checkpointer_singleton():
    reset_checkpointer()
    c1 = get_checkpointer()
    c2 = get_checkpointer()
    assert c1 is c2


def test_checkpointer_reset():
    reset_checkpointer()
    c1 = get_checkpointer()
    reset_checkpointer()
    c2 = get_checkpointer()
    assert c1 is not c2


def test_checkpointer_is_memory_saver():
    """Default checkpointer uses MemorySaver (async-compatible)."""
    from langgraph.checkpoint.memory import MemorySaver

    reset_checkpointer()
    c = get_checkpointer()
    assert isinstance(c, MemorySaver)
