"""Tests for the supervisor agent — mocked LLM, no real model calls."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from kopilot.agent.memory import TaskContext
from kopilot.agent.supervisor import _reflect, reset_supervisor, run_task


@pytest.fixture(autouse=True)
def _clean_supervisor():
    reset_supervisor()
    yield
    reset_supervisor()


def _make_mock_supervisor(return_messages):
    """Create a mock that behaves like a compiled LangGraph agent."""
    mock = MagicMock()
    mock.ainvoke = AsyncMock(return_value={"messages": return_messages})
    return mock


@pytest.mark.asyncio
async def test_run_task_returns_expected_keys():
    mock_agent = _make_mock_supervisor([AIMessage(content="All pods healthy.")])

    with patch("kopilot.agent.supervisor.get_supervisor", return_value=mock_agent):
        result = await run_task("check health")

    assert "task_id" in result
    assert "answer" in result
    assert "risk_level" in result
    assert "elapsed_ms" in result
    assert result["answer"] == "All pods healthy."


@pytest.mark.asyncio
async def test_run_task_with_context():
    mock_agent = _make_mock_supervisor([AIMessage(content="Done.")])
    ctx = TaskContext(task_id="custom-id", channel="test", user="tester")

    with patch("kopilot.agent.supervisor.get_supervisor", return_value=mock_agent):
        result = await run_task("test prompt", context=ctx)

    assert result["task_id"] == "custom-id"


@pytest.mark.asyncio
async def test_run_task_handles_tool_parse_error():
    mock_agent = MagicMock()
    mock_agent.ainvoke = AsyncMock(side_effect=Exception("error parsing tool call blah"))

    with patch("kopilot.agent.supervisor.get_supervisor", return_value=mock_agent):
        result = await run_task("complex command")

    assert "malformed tool call" in result["answer"]
    assert result["risk_level"] == "low"


@pytest.mark.asyncio
async def test_run_task_retries_on_failure():
    call_count = 0

    async def _failing_then_succeeding(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient failure")
        return {"messages": [AIMessage(content="Recovered.")]}

    mock_agent = MagicMock()
    mock_agent.ainvoke = AsyncMock(side_effect=_failing_then_succeeding)

    with patch("kopilot.agent.supervisor.get_supervisor", return_value=mock_agent):
        result = await run_task("test retry", max_retries=1)

    assert result["answer"] == "Recovered."
    assert result["attempts"] == 2


@pytest.mark.asyncio
async def test_run_task_empty_messages():
    mock_agent = _make_mock_supervisor([])

    with patch("kopilot.agent.supervisor.get_supervisor", return_value=mock_agent):
        result = await run_task("empty test")

    assert result["answer"] == ""


@pytest.mark.asyncio
async def test_reflect_returns_satisfactory():
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content="SATISFACTORY - good response"))

    with patch("kopilot.agent.supervisor.get_chat_model", return_value=mock_llm):
        result = await _reflect("check pods", "All pods healthy.")

    assert result["score"] == "satisfactory"


@pytest.mark.asyncio
async def test_reflect_returns_needs_improvement():
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=AIMessage(content="NEEDS_IMPROVEMENT: missing details")
    )

    with patch("kopilot.agent.supervisor.get_chat_model", return_value=mock_llm):
        result = await _reflect("detailed analysis", "pods ok")

    assert result["score"] == "needs_improvement"


@pytest.mark.asyncio
async def test_reflect_handles_error():
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("LLM down"))

    with patch("kopilot.agent.supervisor.get_chat_model", return_value=mock_llm):
        result = await _reflect("test", "response")

    assert result["score"] == "skipped"
