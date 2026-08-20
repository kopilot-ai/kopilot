"""Dynamic skill loader.

Discovers skill definitions from:
  1. Built-in YAML files shipped with the package
  2. Extra directories mounted at runtime (e.g. ConfigMap volumes)
  3. AISkill CRD resources from the cluster (future)

Each YAML is parsed into a SkillDefinition dataclass and used to build
an autonomous sub-agent via the sub-agent factory.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

import structlog
import yaml

from kopilot.config import get_settings

logger = structlog.get_logger(__name__)

BUILTIN_DIR = pathlib.Path(__file__).parent / "builtin"
EXTRA_SKILL_DIRS_ENV = "SKILL_DIRS"


@dataclass
class SkillDefinition:
    """Everything needed to instantiate a sub-agent for this skill."""

    name: str
    display_name: str
    description: str
    category: str
    system_prompt: str
    documentation: str = ""
    source: str = "builtin"


def _parse_yaml(path: pathlib.Path) -> SkillDefinition | None:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return SkillDefinition(
            name=raw["name"],
            display_name=raw.get("display_name", raw["name"]),
            description=raw.get("description", ""),
            category=raw.get("category", "general"),
            system_prompt=raw.get("system_prompt", ""),
            documentation=raw.get("documentation", ""),
            source=str(path),
        )
    except Exception:
        logger.exception("skill.parse_failed", path=str(path))
        return None


def discover_skills() -> list[SkillDefinition]:
    """Discover and load all skill definitions from builtin + extra dirs."""
    enabled = set(get_settings().enabled_skills)
    skills: list[SkillDefinition] = []

    dirs = [BUILTIN_DIR]
    import os
    separator = ";" if os.name == "nt" else ":"
    for extra in os.environ.get(EXTRA_SKILL_DIRS_ENV, "").split(separator):
        extra = extra.strip()
        if extra:
            p = pathlib.Path(extra)
            if p.is_dir():
                dirs.append(p)

    for d in dirs:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.yaml")):
            defn = _parse_yaml(f)
            if defn and defn.name in enabled:
                skills.append(defn)
                logger.info("skill.loaded", name=defn.name, source=str(f))
            elif defn:
                logger.debug("skill.skipped", name=defn.name, reason="not in enabled_skills")

    return skills
