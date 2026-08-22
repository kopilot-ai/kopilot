"""Tests for configuration."""

import pytest
from pydantic import ValidationError

from kopilot.config import AutonomySettings, GeminiSettings, LLMProvider, get_settings


def test_defaults():
    cfg = get_settings()
    assert cfg.llm.provider == LLMProvider.OLLAMA
    assert cfg.llm.model == "gpt-oss:20b"


def test_safety_protected_namespaces():
    assert "kube-system" in get_settings().safety.protected_namespaces


def test_enabled_skills():
    assert "security" in get_settings().enabled_skills


def test_gemini_provider_enum():
    assert LLMProvider.GEMINI.value == "gemini"


def test_gemini_settings_defaults():
    settings = GeminiSettings()
    assert settings.model == "gemini-2.5-flash"
    assert settings.api_key == ""


def test_gemini_settings_from_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-pro")

    from kopilot.config import GeminiSettings
    gs = GeminiSettings()
    assert gs.api_key == "test-key-123"
    assert gs.model == "gemini-pro"


def test_all_providers_defined():
    providers = {p.value for p in LLMProvider}
    assert providers == {"ollama", "openai", "azure_openai", "anthropic", "gemini"}


def test_safety_max_concurrent_tasks():
    cfg = get_settings()
    assert cfg.safety.max_concurrent_tasks == 5


def test_observability_defaults():
    get_settings()


def test_autonomy_defaults_to_copilot():
    assert get_settings().autonomy.level == 1


@pytest.mark.parametrize("level", ["-1", "-99", "3", "10"])
def test_autonomy_level_bounded_to_the_dial(monkeypatch, level):
    """Out-of-range levels are rejected; -1 used to silently disable observe."""
    monkeypatch.setenv("AUTONOMY_LEVEL", level)
    with pytest.raises(ValidationError):
        AutonomySettings()


@pytest.mark.parametrize("level", ["0", "1", "2"])
def test_autonomy_level_accepts_the_dial(monkeypatch, level):
    monkeypatch.setenv("AUTONOMY_LEVEL", level)
    assert AutonomySettings().level == int(level)


def test_event_watcher_is_off_by_default():
    assert get_settings().watchers.k8s_events_enabled is False
