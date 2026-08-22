"""LLM provider abstraction.

Provides a single factory that returns the configured LangChain chat model,
supporting Ollama (local), OpenAI, Azure OpenAI, Anthropic, and Gemini.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from kopilot.config import LLMProvider, get_settings

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

logger = structlog.get_logger(__name__)

_chat_model: BaseChatModel | None = None


def get_chat_model() -> BaseChatModel:
    """Return the configured LangChain chat model (cached singleton).

    Not using @lru_cache because settings can change in tests.
    """
    global _chat_model  # noqa: PLW0603
    if _chat_model is not None:
        return _chat_model

    cfg = get_settings()
    llm_cfg = cfg.llm

    match llm_cfg.provider:
        case LLMProvider.OLLAMA:
            from langchain_ollama import ChatOllama

            _chat_model = ChatOllama(
                model=llm_cfg.model,
                base_url=cfg.ollama.base_url,
                temperature=llm_cfg.temperature,
                num_predict=llm_cfg.max_tokens,
                client_kwargs={"timeout": llm_cfg.request_timeout},
            )

        case LLMProvider.OPENAI:
            from langchain_openai import ChatOpenAI
            from pydantic import SecretStr

            _chat_model = ChatOpenAI(
                model=cfg.openai.model or llm_cfg.model,
                api_key=SecretStr(cfg.openai.api_key) if cfg.openai.api_key else None,
                temperature=llm_cfg.temperature,
                max_completion_tokens=llm_cfg.max_tokens,
                timeout=llm_cfg.request_timeout,
            )

        case LLMProvider.AZURE_OPENAI:
            from langchain_openai import AzureChatOpenAI
            from pydantic import SecretStr

            _chat_model = AzureChatOpenAI(
                azure_endpoint=cfg.azure_openai.endpoint,
                api_key=SecretStr(cfg.azure_openai.api_key) if cfg.azure_openai.api_key else None,
                azure_deployment=cfg.azure_openai.deployment,
                api_version=cfg.azure_openai.api_version,
                temperature=llm_cfg.temperature,
                max_completion_tokens=llm_cfg.max_tokens,
                timeout=llm_cfg.request_timeout,
            )

        case LLMProvider.ANTHROPIC:
            from langchain_anthropic import ChatAnthropic
            from pydantic import SecretStr

            _chat_model = ChatAnthropic(
                model_name=cfg.anthropic.model or llm_cfg.model,
                api_key=SecretStr(cfg.anthropic.api_key or ""),
                max_tokens_to_sample=llm_cfg.max_tokens,
                timeout=llm_cfg.request_timeout,
                stop=None,
            )

        case LLMProvider.GEMINI:
            from langchain_google_genai import ChatGoogleGenerativeAI

            _chat_model = ChatGoogleGenerativeAI(
                model=cfg.gemini.model or llm_cfg.model,
                google_api_key=cfg.gemini.api_key,
                temperature=llm_cfg.temperature,
                max_output_tokens=llm_cfg.max_tokens,
                timeout=llm_cfg.request_timeout,
            )

        case _:
            raise ValueError(f"Unsupported LLM provider: {llm_cfg.provider}")

    logger.info("llm.initialized", provider=llm_cfg.provider.value, model=llm_cfg.model)
    return _chat_model


def reset_chat_model() -> None:
    """Clear the cached model (used in tests when settings change)."""
    global _chat_model  # noqa: PLW0603
    _chat_model = None
