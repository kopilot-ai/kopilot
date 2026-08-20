"""Sub-agent factory.

Builds a fully autonomous LangGraph agent from a skill definition.
Each sub-agent gets:
  - A domain-specialised system prompt
  - Inline documentation from the skill definition
  - The generic executor tools (kubectl, helm, shell, read_resource)
  - Its own memory scope
"""

from __future__ import annotations

import structlog
from langchain.agents import create_agent

from kopilot.agent.llm import get_chat_model
from kopilot.executor.middleware import EXECUTOR_TOOLS

logger = structlog.get_logger(__name__)

SUBAGENT_WRAPPER = """\
{system_prompt}

## Reference Documentation
{documentation}

## Execution Rules
- Use the provided tools to interact with the Kubernetes cluster.
- You can run ANY kubectl, helm, or shell command — construct them yourself.
- Always start with read-only commands (get, describe, logs) before modifying.
- For destructive actions, prefer --dry-run first, then apply if correct.
- Explain your reasoning briefly before each action.
- When done, summarise your findings and actions clearly.
"""


def build_subagent(
    name: str,
    system_prompt: str,
    documentation: str = "",
    extra_tools: list | None = None,
):
    """Create a compiled LangGraph ReAct agent for a skill domain.

    Returns a callable agent that accepts {"messages": [...]} and returns
    the agent's final state.
    """
    tools = list(EXECUTOR_TOOLS)
    if extra_tools:
        tools.extend(extra_tools)

    full_prompt = SUBAGENT_WRAPPER.format(
        system_prompt=system_prompt,
        documentation=documentation or "(No additional documentation loaded.)",
    )

    llm = get_chat_model()

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=full_prompt,
        name=name,
    )

    logger.info("subagent.built", name=name, tools=len(tools))
    return agent
