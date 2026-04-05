"""Skill registry backed by YAML definitions and sub-agents."""

from __future__ import annotations

from typing import Any

import structlog

from kubedevaiops.agent.subagent import build_subagent
from kubedevaiops.skills.loader import SkillDefinition, discover_skills

logger = structlog.get_logger(__name__)


class SkillRegistry:
    """Holds loaded skill definitions and their compiled sub-agents."""

    def __init__(self) -> None:
        self._definitions: dict[str, SkillDefinition] = {}
        self._agents: dict[str, Any] = {}

    def load(self) -> None:
        for defn in discover_skills():
            self._definitions[defn.name] = defn
            self._agents[defn.name] = build_subagent(
                name=defn.name,
                system_prompt=defn.system_prompt,
                documentation=defn.documentation,
            )

    def get_agent(self, name: str):
        return self._agents.get(name)

    def get_definition(self, name: str) -> SkillDefinition | None:
        return self._definitions.get(name)

    def list_names(self) -> list[str]:
        return list(self._definitions)

    def list_details(self) -> list[dict[str, str]]:
        return [
            {
                "name": d.name,
                "display_name": d.display_name,
                "description": d.description,
                "category": d.category,
            }
            for d in self._definitions.values()
        ]

    def agent_descriptions_for_prompt(self) -> str:
        lines = []
        for d in self._definitions.values():
            lines.append(f"- **{d.name}**: {d.description}")
        return "\n".join(lines)


_registry: SkillRegistry | None = None


def get_registry() -> SkillRegistry:
    global _registry  # noqa: PLW0603
    if _registry is None:
        _registry = SkillRegistry()
        _registry.load()
    return _registry
