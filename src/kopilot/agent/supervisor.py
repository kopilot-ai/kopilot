"""Supervisor agent with LangChain v1+ middleware and reflection support.

Implements the LangChain *subagents* multi-agent pattern:
  1. User sends a natural-language request.
  2. Supervisor classifies intent and delegates to one or more skill
     sub-agents (each a fully autonomous ReAct agent).
  3. Sub-agent results are synthesised into a final response.
  4. A reflection step optionally evaluates the quality of the response
     and triggers re-planning if needed.

The supervisor itself has access to the executor tools as a fallback.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage

from kopilot.agent.llm import get_chat_model
from kopilot.agent.memory import TaskContext, get_checkpointer
from kopilot.executor.middleware import EXECUTOR_TOOLS
from kopilot.outputs.audit import log_event
from kopilot.skills.base import get_registry
from kopilot.taskscope import begin_task, max_recorded_risk

logger = structlog.get_logger(__name__)

SUPERVISOR_PROMPT = """\
You are **Kopilot**, an autonomous AI Kubernetes operations engineer.

You coordinate specialised sub-agents to handle complex cluster operations.
Each sub-agent is a domain expert with its own knowledge and the ability
to execute any kubectl, helm, or shell command autonomously.

## Available Sub-Agents
{skill_descriptions}

## How to delegate
- Call the sub-agent tool whose domain best matches the user request.
- You may call MULTIPLE sub-agents if the task spans domains.
- Pass a CLEAR, DETAILED instruction as the sub-agent's input.

## Fallback
If no sub-agent fits, you have direct access to kubectl / helm / shell tools.
Use them for simple queries or when the task doesn't warrant a specialist.

## Safety
- Destructive operations (delete, drain, cordon) are checked automatically.
- Operations on kube-system and other protected namespaces are blocked.
- Explain your reasoning before taking any action.
- Always prefer read-only commands first (get, describe, logs).
- For destructive operations, show dry-run output before applying.

## Response format
- Be concise but thorough.
- Structure your response with clear sections for findings, actions taken, and recommendations.
- If you encountered errors, explain what went wrong and what alternatives exist.
"""

REFLECTION_PROMPT = """\
You are a quality reviewer for a Kubernetes AIOps agent. Evaluate the agent's response
to determine if it adequately addressed the user's request.

Score the response 1-10 on these criteria:
- COMPLETENESS: Did it address all parts of the request?
- ACCURACY: Are the commands and explanations correct?
- SAFETY: Were appropriate safety checks performed?
- ACTIONABILITY: Can the user act on the recommendations?

If the overall score is below 6, respond with "NEEDS_IMPROVEMENT:" followed by
specific feedback on what to fix. Otherwise respond with "SATISFACTORY" followed
by a brief summary.

User request: {prompt}
Agent response: {response}
"""


def _make_subagent_tools():
    """Wrap each loaded sub-agent as a LangChain tool the supervisor can call."""
    from langchain_core.tools import StructuredTool

    registry = get_registry()
    tools = []

    for name in registry.list_names():
        defn = registry.get_definition(name)
        agent = registry.get_agent(name)
        if not agent or not defn:
            continue

        async def _invoke(instruction: str, _agent=agent, _name=name) -> str:
            log_event("supervisor.delegate", skill=_name, instruction=instruction[:200])
            try:
                result = await _agent.ainvoke(
                    {"messages": [HumanMessage(content=instruction)]},
                    config={"recursion_limit": 80},
                )
            except Exception as exc:
                logger.warning("subagent.failed", name=_name, error=str(exc)[:200])
                return f"Sub-agent '{_name}' encountered an error: {str(exc)[:500]}"

            msgs = result.get("messages", [])
            for msg in reversed(msgs):
                if isinstance(msg, AIMessage) and msg.content:
                    return _content_to_text(msg.content)
            return "(sub-agent returned no response)"

        tools.append(
            StructuredTool.from_function(
                coroutine=_invoke,
                name=f"delegate_to_{name}",
                description=f"Delegate to the {defn.display_name} sub-agent. {defn.description}",
            )
        )

    return tools


_compiled_supervisor = None


def get_supervisor():
    """Build and cache the compiled supervisor graph."""
    global _compiled_supervisor  # noqa: PLW0603
    if _compiled_supervisor is not None:
        return _compiled_supervisor

    registry = get_registry()
    skill_desc = registry.agent_descriptions_for_prompt()
    prompt = SUPERVISOR_PROMPT.format(skill_descriptions=skill_desc)

    subagent_tools = _make_subagent_tools()
    all_tools = subagent_tools + list(EXECUTOR_TOOLS)

    llm = get_chat_model()

    _compiled_supervisor = create_agent(
        model=llm,
        tools=all_tools,
        system_prompt=prompt,
        name="supervisor",
        checkpointer=get_checkpointer(),
    )

    logger.info(
        "supervisor.ready",
        skills=len(subagent_tools),
        direct_tools=len(EXECUTOR_TOOLS),
    )
    return _compiled_supervisor


def reset_supervisor() -> None:
    """Clear cached supervisor (for tests or config reload)."""
    global _compiled_supervisor  # noqa: PLW0603
    _compiled_supervisor = None


def _content_to_text(content: Any) -> str:
    """Normalise LangChain message content (str or block list) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in (None, "text"):
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


async def _reflect(prompt: str, response: str) -> dict[str, Any]:
    """Run a lightweight reflection on the agent's response."""
    llm = get_chat_model()
    reflection_input = REFLECTION_PROMPT.format(
        prompt=prompt[:1000], response=response[:3000]
    )
    try:
        result = await llm.ainvoke([HumanMessage(content=reflection_input)])
        content = _content_to_text(result.content) if hasattr(result, "content") else str(result)
        needs_improvement = content.strip().startswith("NEEDS_IMPROVEMENT:")
        return {
            "score": "needs_improvement" if needs_improvement else "satisfactory",
            "feedback": content,
        }
    except Exception as exc:
        logger.debug("reflection.failed", error=str(exc)[:200])
        return {"score": "skipped", "feedback": "Reflection unavailable"}


async def run_task(
    prompt: str,
    context: TaskContext | None = None,
    reflect: bool = False,
    max_retries: int = 1,
) -> dict[str, Any]:
    """Submit a natural-language task. Returns task_id, answer, risk_level, timing."""
    supervisor = get_supervisor()
    task_id = context.task_id if context else str(uuid.uuid4())

    config = {
        "configurable": {"thread_id": task_id},
        "recursion_limit": 100,
    }

    log_event("task.start", task_id=task_id, prompt=prompt[:200])
    begin_task(task_id)
    start = time.monotonic()

    answer = ""
    reflection = None
    attempts = 0

    for attempt in range(max_retries + 1):
        attempts = attempt + 1
        try:
            result = await supervisor.ainvoke(
                {"messages": [HumanMessage(content=prompt)]},
                config=config,
            )
        except Exception as exc:
            err_str = str(exc)
            if "error parsing tool call" in err_str.lower():
                logger.warning("task.tool_parse_error", task_id=task_id, err=err_str[:300])
                answer = (
                    "The agent attempted to execute a complex command but the LLM "
                    "produced a malformed tool call. This typically happens with "
                    "complex shell escaping. Please rephrase or break the request "
                    "into smaller steps."
                )
                break

            if attempt < max_retries:
                logger.warning("task.retry", task_id=task_id, attempt=attempt, err=err_str[:200])
                continue
            raise

        msgs = result.get("messages", [])
        for msg in reversed(msgs):
            if isinstance(msg, AIMessage) and msg.content:
                answer = _content_to_text(msg.content)
                break

        if reflect and answer:
            reflection = await _reflect(prompt, answer)
            if reflection["score"] == "needs_improvement" and attempt < max_retries:
                prompt = (
                    f"{prompt}\n\n[REFLECTION FEEDBACK - improve your response]:\n"
                    f"{reflection['feedback']}"
                )
                logger.info("task.reflection_retry", task_id=task_id, attempt=attempt)
                continue

        break

    elapsed_ms = int((time.monotonic() - start) * 1000)
    log_event("task.done", task_id=task_id, answer_len=len(answer), elapsed_ms=elapsed_ms)

    result_dict: dict[str, Any] = {
        "task_id": task_id,
        "answer": answer,
        # Highest risk level the safety layer saw for any command this task
        # attempted (recorded via taskscope, whether executed or gated).
        "risk_level": max_recorded_risk(),
        "elapsed_ms": elapsed_ms,
        "attempts": attempts,
    }
    if reflection:
        result_dict["reflection"] = reflection

    return result_dict
