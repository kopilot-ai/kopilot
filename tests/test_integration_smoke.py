"""Integration smoke tests — requires live Ollama and optionally Gemini + K8s cluster.

Run with: pytest tests/test_integration_smoke.py -v -s --timeout=120
Skip markers: these tests are marked @pytest.mark.integration and skipped
by default in CI. Run explicitly to validate a real setup.
"""

from __future__ import annotations

import os
import subprocess

import pytest

pytestmark = pytest.mark.integration


def _ollama_available() -> bool:
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _gemini_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "")


def _kubectl_available() -> bool:
    try:
        r = subprocess.run(["kubectl", "cluster-info"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not _ollama_available(), reason="Ollama not running")
class TestOllamaSmoke:
    """Smoke tests using a local Ollama model."""

    def test_ollama_model_loads(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setenv("LLM_MODEL", "qwen3:8b")
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")

        import kubedevaiops.config as cfg_mod
        cfg_mod._settings = None

        from kubedevaiops.agent.llm import get_chat_model, reset_chat_model
        reset_chat_model()
        model = get_chat_model()
        assert model is not None

    @pytest.mark.asyncio
    async def test_ollama_simple_invoke(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setenv("LLM_MODEL", "qwen3:8b")

        import kubedevaiops.config as cfg_mod
        cfg_mod._settings = None

        from langchain_core.messages import HumanMessage

        from kubedevaiops.agent.llm import get_chat_model, reset_chat_model

        reset_chat_model()
        model = get_chat_model()
        result = await model.ainvoke([HumanMessage(content="Say 'hello' and nothing else.")])
        assert result.content
        assert len(result.content) > 0


@pytest.mark.skipif(not _gemini_key(), reason="GEMINI_API_KEY not set")
class TestGeminiSmoke:
    """Smoke tests using Google Gemini API."""

    def test_gemini_model_loads(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        monkeypatch.setenv("GEMINI_API_KEY", _gemini_key())
        monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")

        import kubedevaiops.config as cfg_mod
        cfg_mod._settings = None

        from kubedevaiops.agent.llm import get_chat_model, reset_chat_model
        reset_chat_model()
        model = get_chat_model()
        assert "Google" in type(model).__name__

    @pytest.mark.asyncio
    async def test_gemini_simple_invoke(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        monkeypatch.setenv("GEMINI_API_KEY", _gemini_key())
        monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")

        import kubedevaiops.config as cfg_mod
        cfg_mod._settings = None

        from langchain_core.messages import HumanMessage

        from kubedevaiops.agent.llm import get_chat_model, reset_chat_model

        reset_chat_model()
        model = get_chat_model()
        result = await model.ainvoke([HumanMessage(content="Say 'hello' and nothing else.")])
        assert result.content
        assert len(result.content) > 0


@pytest.mark.skipif(not _kubectl_available(), reason="No K8s cluster")
class TestK8sSmoke:
    """Smoke tests against a live Kubernetes cluster."""

    @pytest.mark.asyncio
    async def test_kubectl_tool(self):
        from kubedevaiops.executor.middleware import run_kubectl

        result = await run_kubectl.ainvoke({"command": "kubectl get namespaces"})
        assert "default" in result
        assert "kube-system" in result

    @pytest.mark.asyncio
    async def test_kubectl_get_pods_all_ns(self):
        from kubedevaiops.executor.middleware import run_kubectl

        result = await run_kubectl.ainvoke(
            {"command": "kubectl get pods -A --no-headers"}
        )
        assert "kube-system" in result or "No resources" in result

    @pytest.mark.asyncio
    async def test_helm_tool(self):
        from kubedevaiops.executor.middleware import run_helm

        result = await run_helm.ainvoke({"command": "helm list -A"})
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_crds_installed(self):
        from kubedevaiops.executor.middleware import run_kubectl

        result = await run_kubectl.ainvoke(
            {"command": "kubectl get crd aitasks.kubedevaiops.io"}
        )
        assert "aitasks.kubedevaiops.io" in result


@pytest.mark.skipif(
    not (_ollama_available() and _kubectl_available()),
    reason="Requires Ollama + K8s",
)
class TestEndToEnd:
    """Full end-to-end test: agent processes a task against a live cluster."""

    @pytest.mark.asyncio
    async def test_ask_simple_question(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setenv("LLM_MODEL", "qwen3:8b")
        monkeypatch.setenv("SAFETY_REQUIRE_APPROVAL_DESTRUCTIVE", "true")

        import kubedevaiops.config as cfg_mod
        cfg_mod._settings = None

        from kubedevaiops.agent.llm import reset_chat_model
        from kubedevaiops.agent.memory import reset_checkpointer
        from kubedevaiops.agent.supervisor import reset_supervisor, run_task

        reset_chat_model()
        reset_checkpointer()
        reset_supervisor()

        result = await run_task("List all namespaces in the cluster.")
        assert result["task_id"]
        assert result["answer"]
        assert result["elapsed_ms"] > 0
