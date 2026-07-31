"""Centralised configuration using pydantic-settings.

Reads from environment variables (or a .env file) with sensible defaults
so the agent works out-of-the-box with a local Ollama instance.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(StrEnum):
    OLLAMA = "ollama"
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_", extra="ignore")

    provider: LLMProvider = LLMProvider.OLLAMA
    model: str = "gpt-oss:20b"
    temperature: float = 0.1
    max_tokens: int = 4096
    request_timeout: int = 120


class OllamaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OLLAMA_", extra="ignore")

    base_url: str = "http://localhost:11434"


class OpenAISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPENAI_", extra="ignore")

    api_key: str = ""
    model: str = "gpt-4o"


class AzureOpenAISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AZURE_OPENAI_", extra="ignore")

    endpoint: str = ""
    api_key: str = ""
    deployment: str = "gpt-4o"
    api_version: str = "2024-08-01-preview"


class AnthropicSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ANTHROPIC_", extra="ignore")

    api_key: str = ""
    model: str = "claude-opus-5"


class GeminiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEMINI_", extra="ignore")

    api_key: str = ""
    model: str = "gemini-2.5-flash"


class K8sSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="K8S_", extra="ignore")

    kubeconfig: str = Field(default="", alias="KUBECONFIG")
    namespace: str = "kubedevaiops"


class APISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="API_", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8080
    # Empty list disables CORS entirely (same-origin only).  Set an explicit
    # allowlist for browser clients; "*" is intentionally not the default.
    cors_origins: list[str] = []
    # Bearer token required on task-submitting and history endpoints when set.
    # Leave empty only for local development; the server logs a warning.
    auth_token: str = ""
    # Shared secret for HMAC-SHA256 webhook signature verification
    # (X-Kopilot-Signature header). Webhooks are rejected when unset.
    webhook_secret: str = ""


class SlackSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SLACK_", extra="ignore")

    bot_token: str = ""
    app_token: str = ""
    signing_secret: str = ""
    enabled: bool = False
    # Slack user IDs allowed to run tasks. Empty list = allow all workspace
    # members (not recommended outside sandboxes).
    allowed_users: list[str] = []


class SafetySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SAFETY_", extra="ignore")

    dry_run_default: bool = False
    require_approval_destructive: bool = True
    max_concurrent_tasks: int = 5
    protected_namespaces: list[str] = [
        "kube-system",
        "kube-public",
        "kube-node-lease",
    ]
    # Directories read_resource() may read files from. Empty = file reads
    # disabled (ConfigMap and URL reads are unaffected).
    read_paths: list[str] = ["/etc/kubedevaiops"]


class ObservabilitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    log_level: str = "INFO"
    log_format: str = "json"
    metrics_enabled: bool = True
    metrics_port: int = 9090


class Settings(BaseSettings):
    """Root settings object - aggregates all sub-settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm: LLMSettings = Field(default_factory=LLMSettings)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    azure_openai: AzureOpenAISettings = Field(default_factory=AzureOpenAISettings)
    anthropic: AnthropicSettings = Field(default_factory=AnthropicSettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    k8s: K8sSettings = Field(default_factory=K8sSettings)
    api: APISettings = Field(default_factory=APISettings)
    slack: SlackSettings = Field(default_factory=SlackSettings)
    safety: SafetySettings = Field(default_factory=SafetySettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)

    enabled_skills: list[str] = [
        "security",
        "administration",
        "networking",
        "monitoring",
        "troubleshooting",
        "cost_optimization",
    ]


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Clear the cached settings (used in tests)."""
    global _settings  # noqa: PLW0603
    _settings = None
