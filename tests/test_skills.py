"""Tests for skill loading and registry."""

from kopilot.skills.loader import BUILTIN_DIR, discover_skills


def test_builtin_yaml_files_exist():
    yamls = list(BUILTIN_DIR.glob("*.yaml"))
    assert len(yamls) >= 6


def test_discover_skills_loads_enabled():
    skills = discover_skills()
    names = {s.name for s in skills}
    assert "security" in names
    assert "administration" in names
    assert "troubleshooting" in names


def test_skill_definitions_have_prompts():
    for skill in discover_skills():
        assert len(skill.system_prompt) > 50, f"{skill.name} has no meaningful prompt"
        assert skill.category


# ── Dynamic registration (AISkill CRDs) ──────────────────────────────────────


def test_registry_register_and_unregister(monkeypatch):
    from unittest.mock import MagicMock

    from kopilot.skills import base as skills_base
    from kopilot.skills.base import SkillRegistry
    from kopilot.skills.loader import SkillDefinition

    agent = MagicMock()
    monkeypatch.setattr(skills_base, "build_subagent", lambda **_: agent)

    reg = SkillRegistry()
    defn = SkillDefinition(
        name="crd_skill",
        display_name="CRD Skill",
        description="from a CRD",
        category="custom",
        system_prompt="You are a test skill.",
        source="crd:crd_skill",
    )
    reg.register(defn)
    assert "crd_skill" in reg.list_names()
    assert reg.get_agent("crd_skill") is agent
    assert reg.get_definition("crd_skill").source == "crd:crd_skill"

    reg.unregister("crd_skill")
    assert "crd_skill" not in reg.list_names()
    assert reg.get_agent("crd_skill") is None
    # unregistering twice is a no-op
    reg.unregister("crd_skill")
