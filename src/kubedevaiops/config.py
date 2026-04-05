"""Centralised configuration using pydantic-settings.

Reads from environment variables (or a .env file) with sensible defaults
so the agent works out-of-the-box with a local Ollama instance.
"""

from __future__ import annotations

from enum import Enum
from typing import ClassVar

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(str, Enum):
    OLLAMA = "ollama"
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_")

    provider: LLMProvider = LLMProvider.OLLAMA
    model: str = "gpt-oss:20b"
    temperature: float = 0.1
    max_tokens: int = 4096
    request_timeout: int = 120


class OllamaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OLLAMA_")

    base_url: str = "http://localhost:11434"


class OpenAISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPENAI_")

    api_key: str = ""
    model: str = "gpt-4o"


class AzureOpenAISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AZURE_OPENAI_")

    endpoint: str = ""
    api_key: str = ""
    deployment: str = "gpt-4o"
    api_version: str = "2024-08-01-preview"


class GeminiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEMINI_")

    api_key: str = ""
    model: str = "gemini-2.5-flash"


class K8sSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="K8S_")

    kubeconfig: str = Field(default="", alias="KUBECONFIG")
    namespace: str = "kubedevaiops"


class APISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="API_")

    host: str = "0.0.0.0"
    port: int = 8080
    cors_origins: list[str] = ["*"]


class SlackSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SLACK_")

    bot_token: str = ""
    app_token: str = ""
    signing_secret: str = ""
    enabled: bool = False


class SafetySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SAFETY_")

    dry_run_default: bool = False
    require_approval_destructive: bool = True
    max_concurrent_tasks: int = 5
    protected_namespaces: list[str] = [
        "kube-system",
        "kube-public",
        "kube-node-lease",
    ]


class ObservabilitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="")

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

    llm: ClassVar[LLMSettings] = LLMSettings()
    ollama: ClassVar[OllamaSettings] = OllamaSettings()
    openai: ClassVar[OpenAISettings] = OpenAISettings()
    azure_openai: ClassVar[AzureOpenAISettings] = AzureOpenAISettings()
    gemini: ClassVar[GeminiSettings] = GeminiSettings()
    k8s: ClassVar[K8sSettings] = K8sSettings()
    api: ClassVar[APISettings] = APISettings()
    slack: ClassVar[SlackSettings] = SlackSettings()
    safety: ClassVar[SafetySettings] = SafetySettings()
    observability: ClassVar[ObservabilitySettings] = ObservabilitySettings()

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
