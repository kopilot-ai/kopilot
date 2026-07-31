"""End-to-end smoke test against live K8s + LLM providers."""

import asyncio
import os
import sys


def setup_gemini():
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("Set GEMINI_API_KEY in the environment before running the Gemini smoke test.")
    os.environ["LLM_PROVIDER"] = "gemini"
    os.environ["GEMINI_MODEL"] = "gemini-2.5-flash"
    os.environ["LLM_MODEL"] = "gemini-2.5-flash"


def setup_ollama():
    os.environ["LLM_PROVIDER"] = "ollama"
    os.environ["LLM_MODEL"] = "qwen3:8b"
    os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434"


def reset_all():
    import kubedevaiops.config as c
    c._settings = None
    from kubedevaiops.agent.llm import reset_chat_model
    from kubedevaiops.agent.memory import reset_checkpointer
    from kubedevaiops.agent.supervisor import reset_supervisor
    reset_chat_model()
    reset_checkpointer()
    reset_supervisor()


async def run_test(provider: str, prompt: str):
    os.environ["LOG_FORMAT"] = "console"
    os.environ["SAFETY_REQUIRE_APPROVAL_DESTRUCTIVE"] = "true"

    if provider == "gemini":
        setup_gemini()
    else:
        setup_ollama()

    reset_all()

    from kubedevaiops.agent.supervisor import run_task

    print(f"\n{'='*60}")
    print(f"Provider: {provider}")
    print(f"Prompt: {prompt}")
    print(f"{'='*60}")

    result = await run_task(prompt)

    print(f"Task ID: {result['task_id']}")
    print(f"Elapsed: {result['elapsed_ms']}ms")
    print(f"Attempts: {result['attempts']}")
    print(f"Risk: {result['risk_level']}")
    print(f"\nAnswer:")
    print(result["answer"][:1000])
    print(f"{'='*60}\n")

    return result


async def main():
    provider = sys.argv[1] if len(sys.argv) > 1 else "gemini"

    prompts = [
        "List all namespaces in the Kubernetes cluster.",
        "Check the health of all pods in kube-system namespace.",
        "What CRDs are installed in the cluster?",
    ]

    for prompt in prompts:
        try:
            result = await run_test(provider, prompt)
            assert result["answer"], f"Empty answer for: {prompt}"
            print(f"PASS: {prompt[:60]}")
        except Exception as e:
            print(f"FAIL: {prompt[:60]} - {e}")

    print("\n=== All smoke tests completed ===")


if __name__ == "__main__":
    asyncio.run(main())
