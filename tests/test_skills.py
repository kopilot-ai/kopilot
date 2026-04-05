"""Tests for skill loading and registry."""

from kubedevaiops.skills.loader import discover_skills, BUILTIN_DIR


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
