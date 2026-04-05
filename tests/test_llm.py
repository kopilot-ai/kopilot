"""Tests for LLM provider factory."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from kubedevaiops.agent.llm import get_chat_model, reset_chat_model
from kubedevaiops.config import LLMProvider


def test_ollama_provider_returns_chat_model():
    reset_chat_model()
    model = get_chat_model()
    assert model is not None
    assert "Ollama" in type(model).__name__


def test_gemini_provider_creates_model(monkeypatch):
    """Test Gemini factory path by mocking the settings to return Gemini provider."""
    from kubedevaiops.config import LLMSettings, GeminiSettings

    mock_gemini = GeminiSettings()
    mock_gemini_with_key = MagicMock(spec=GeminiSettings)
    mock_gemini_with_key.api_key = "test-key"
    mock_gemini_with_key.model = "gemini-2.5-flash"

    mock_settings = MagicMock()
    mock_settings.llm.provider = LLMProvider.GEMINI
    mock_settings.llm.model = "gemini-2.5-flash"
    mock_settings.llm.temperature = 0.1
    mock_settings.llm.max_tokens = 4096
    mock_settings.llm.request_timeout = 120
    mock_settings.gemini = mock_gemini_with_key

    reset_chat_model()
    with patch("kubedevaiops.agent.llm.get_settings", return_value=mock_settings):
        model = get_chat_model()
    assert model is not None
    assert "Google" in type(model).__name__


def test_unsupported_provider_raises():
    """Test that an unsupported provider value raises in the match statement."""
    mock_settings = MagicMock()
    mock_settings.llm.provider = "nonexistent_provider"
    mock_settings.llm.model = "test"

    reset_chat_model()
    with patch("kubedevaiops.agent.llm.get_settings", return_value=mock_settings):
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            get_chat_model()


def test_cached_model_returns_same_instance():
    reset_chat_model()
    model1 = get_chat_model()
    model2 = get_chat_model()
    assert model1 is model2


def test_reset_clears_cache():
    reset_chat_model()
    model1 = get_chat_model()
    reset_chat_model()
    model2 = get_chat_model()
    assert model1 is not model2
