"""Tests for configuration."""


from kubedevaiops.config import GeminiSettings, LLMProvider, get_settings


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

    from kubedevaiops.config import GeminiSettings
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
    cfg = get_settings()
    assert cfg.observability.metrics_enabled is True
    assert cfg.observability.metrics_port == 9090
