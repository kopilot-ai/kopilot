# KubeDevAIOps

**Autonomous AI-powered Kubernetes DevOps Agent**

A fully autonomous multi-agent system that replaces a human DevOps engineer.
Instead of hardcoded scripts, it uses **specialised sub-agents** — each a
fully autonomous LLM agent with domain expertise, documentation, and the
ability to execute any command against your cluster.

```
┌─────────────────────────────────────────────────────────────┐
│                    Input Channels                           │
│  REST API │ Slack │ Webhooks │ K8s Events │ CRD Operator    │
└─────────────┬───────────────────────────────────────────────┘
              │
┌─────────────▼───────────────────────────────────────────────┐
│                  Supervisor Agent                           │
│  Classifies intent → delegates to specialised sub-agents    │
│  Synthesises results → returns to user                      │
└───┬─────────┬──────────┬──────────┬──────────┬──────────┬───┘
    │         │          │          │          │          │
┌───▼──┐ ┌───▼──┐ ┌─────▼──┐ ┌────▼───┐ ┌────▼───┐ ┌───▼───┐
│ Sec  │ │Admin │ │Network │ │Monitor │ │Trouble │ │ FinOps│
│Agent │ │Agent │ │ Agent  │ │ Agent  │ │ Agent  │ │ Agent │
└──┬───┘ └──┬───┘ └───┬────┘ └───┬────┘ └───┬────┘ └──┬────┘
   └────────┴─────────┴──────────┴──────────┴─────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│              Generic Executor Middleware                     │
│  run_kubectl │ run_helm │ run_shell │ read_resource          │
│                                                             │
│  Safety │ Audit │ Approval Gates │ Namespace Protection      │
└─────────────────────────────────────────────────────────────┘
                          │
              ┌───────────▼───────────┐
              │   Kubernetes Cluster  │
              └───────────────────────┘
```

## Key Architecture Principles

1. **No hardcoded tools** — The LLM constructs any command it needs. The
   executor middleware provides `run_kubectl`, `run_helm`, `run_shell`, and
   `read_resource` as generic capabilities.

2. **Skills = Sub-Agents** — Each skill is defined in a **YAML file** with a
   domain-specific system prompt and documentation. At runtime, it becomes
   a fully autonomous ReAct agent that reasons, plans, and executes.

3. **Dynamic skill loading** — Skills are loaded from:
   - Built-in YAML files shipped with the package
   - Extra directories (e.g. ConfigMap volumes in K8s)
   - AISkill CRDs applied to the cluster

4. **Supervisor pattern** — A coordinator agent routes user requests to the
   best sub-agent(s), allowing multi-domain tasks via parallel delegation.

5. **Safety-first execution** — Every command goes through a safety layer
   that blocks operations on protected namespaces, requires approval for
   destructive actions, and audits everything.

6. **Self-improving agents** — Optional reflection loop evaluates response
   quality and re-plans if the score is below threshold. Feedback is logged
   for continuous improvement.

7. **Multi-provider LLM** — Supports Ollama (local), Gemini, OpenAI,
   Azure OpenAI, and Anthropic. Switch providers via a single env var.

8. **Production observability** — Prometheus `/metrics` endpoint, structured
   audit logging, task history, rate limiting, and execution statistics.

## Skill Definitions (YAML)

Skills are **not Python code**. They are YAML definitions that instruct an LLM:

```yaml
name: security
display_name: Security Operations
description: Kubernetes security auditing and hardening

system_prompt: |
  You are an expert Kubernetes security engineer.
  - Audit RBAC roles and bindings for over-privilege
  - Check pod security contexts and standards
  - Evaluate NetworkPolicy coverage
  - Run compliance checks against CIS Benchmarks
  ...

documentation: |
  # Quick Reference
  - List RBAC: kubectl get roles,rolebindings,clusterroles -A
  - Pod security: kubectl get pods -A -o jsonpath='{...securityContext}'
  ...
```

### Built-in Skills

| Skill | Domain | What it can do |
|-------|--------|----------------|
| `security` | Security & Compliance | RBAC audit, pod security, network policies, CIS benchmarks |
| `administration` | Cluster Admin | Deployments, scaling, rollouts, Helm, namespaces |
| `networking` | Network Ops | Services, Ingress, DNS, connectivity, service mesh |
| `monitoring` | Observability | Resource usage, HPA, events, Prometheus, logging |
| `troubleshooting` | Incident Response | Root cause analysis, crash diagnostics, scheduling |
| `cost_optimization` | FinOps | Right-sizing, idle detection, orphaned resources |

### Adding Custom Skills

Drop a YAML file in `skills/builtin/` or mount it at runtime:

```yaml
name: my_custom_skill
display_name: My Custom Skill
description: Does something specific to my org
system_prompt: |
  You are an expert in [domain]. You know how to...
documentation: |
  Internal runbook for [domain]...
```

Set `SKILL_DIRS=/path/to/extra/skills` or mount a ConfigMap.

## Input Channels

| Channel | Protocol | Description |
|---------|----------|-------------|
| REST API | HTTP/JSON | `POST /tasks` with natural language prompt |
| Slack | Socket Mode | `@mention` or DM the bot |
| Webhooks | HTTP/JSON | `POST /webhook` from ServiceNow, PagerDuty, etc. |
| K8s Events | Watch API | Auto-investigates repeated Warning events |
| CRD | `AITask` | Apply a CRD manifest and the operator handles it |

## Safety & Guardrails

- **Protected namespaces**: `kube-system`, `kube-public`, `kube-node-lease`
  — all destructive operations blocked.
- **Approval gates**: Delete, drain, cordon operations require explicit
  approval before execution.
- **Risk assessment**: Every command is classified (LOW / MEDIUM / HIGH / CRITICAL)
  before execution.
- **Audit trail**: Structured logs of every command, delegation, and result.
- **Blocked patterns**: `rm -rf /`, `dd`, `mkfs` and similar are hard-blocked.

## Quick Start

### Prerequisites

- Python 3.11+ (tested on 3.13)
- One of: Ollama (local), Gemini API key, or OpenAI API key
- kubectl configured with cluster access
- Optional: Podman or Docker for container builds

### Install & Run

```bash
cd kubedevaiops
pip install -e ".[dev]"
cp .env.example .env    # Edit with your LLM provider settings
python -m kubedevaiops api --port 8080
```

### With Gemini

```bash
export LLM_PROVIDER=gemini
export GEMINI_API_KEY=your-api-key
export GEMINI_MODEL=gemini-2.5-flash
python -m kubedevaiops api
```

### With local Ollama

```bash
ollama pull qwen3:8b
export LLM_PROVIDER=ollama
export LLM_MODEL=qwen3:8b
python -m kubedevaiops api
```

### Submit a Task

```bash
curl -X POST http://localhost:8080/tasks \
  -H "Content-Type: application/json" \
  -d '{"prompt": "List all nodes and check their health"}'
```

### Submit with Reflection (self-improving)

```bash
curl -X POST http://localhost:8080/tasks \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Audit RBAC for over-privileged accounts", "reflect": true}'
```

### One-shot CLI

```bash
python -m kubedevaiops ask "What pods are running in kube-system?"
```

## Configuration

All settings via environment variables or `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `ollama` | `ollama`, `gemini`, `openai`, `azure_openai`, `anthropic` |
| `LLM_MODEL` | `gpt-oss:20b` | Model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint |
| `GEMINI_API_KEY` | (empty) | Google Gemini API key |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model name |
| `SAFETY_PROTECTED_NAMESPACES` | `kube-system,...` | JSON list |
| `SAFETY_REQUIRE_APPROVAL_DESTRUCTIVE` | `true` | Gate destructive ops |
| `SAFETY_MAX_CONCURRENT_TASKS` | `5` | Concurrent task limit |
| `ENABLED_SKILLS` | all 6 built-ins | JSON list of skill names |
| `SKILL_DIRS` | (empty) | Extra dirs (`;`-separated on Windows, `:`-separated on Linux) |
| `LOG_FORMAT` | `json` | `json` or `console` |
| `METRICS_ENABLED` | `true` | Enable `/metrics` endpoint |
| `METRICS_PORT` | `9090` | Prometheus metrics port |

## Deployment

### Helm Chart

```bash
helm install kubedevaiops ./helm/kubedevaiops \
  --set llm.ollamaUrl=http://ollama:11434 \
  --set llm.model="gpt-oss:20b"
```

### Direct Kubernetes

```bash
kubectl apply -f helm/kubedevaiops/crds/
kubectl apply -f deploy/quickstart.yaml
```

### Custom Resource

```yaml
apiVersion: kubedevaiops.io/v1alpha1
kind: AITask
metadata:
  name: security-audit
spec:
  prompt: "Audit RBAC and pod security across all namespaces"
```

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check with skills count and LLM provider |
| `/readyz` | GET | Readiness probe |
| `/skills` | GET | List loaded skills |
| `/tasks` | POST | Submit a task (accepts `prompt`, `reflect`, `namespace`) |
| `/tasks/history` | GET | Recent task history (configurable `?limit=`) |
| `/webhook` | POST | Webhook handler for external integrations |
| `/metrics` | GET | Prometheus-compatible metrics |

## Development

```bash
make dev                  # Install with dev dependencies (editable)
make test                 # Run pytest with coverage
make lint                 # Run ruff linter
make format               # Auto-format code
make run                  # Start the full agent locally
make build-podman         # Build container image with Podman
```

### Local K8s cluster (Podman + Kind)

```bash
podman machine start
bash scripts/setup-local-k8s.sh
kubectl apply -f helm/kubedevaiops/crds/
```

### Running Tests

```bash
# Unit tests only (fast, no external deps)
pytest tests/ -v --ignore=tests/test_integration_smoke.py

# Full suite including integration tests (requires Ollama + K8s)
pytest tests/ -v

# End-to-end smoke test with specific provider
python scripts/smoke_e2e.py gemini
python scripts/smoke_e2e.py ollama
```

## Project Structure

```
src/kubedevaiops/
├── agent/
│   ├── supervisor.py    # Main coordinator with reflection loop
│   ├── subagent.py      # Factory: builds agent from YAML skill definition
│   ├── llm.py           # Multi-provider LLM factory (Ollama, Gemini, OpenAI, etc.)
│   ├── memory.py        # Checkpointing, task context, incident memory
│   └── safety.py        # Risk assessment engine
├── executor/
│   └── middleware.py     # Generic tools + rate limiting + retries + audit
├── skills/
│   ├── base.py          # SkillRegistry (loads + caches sub-agents)
│   ├── loader.py        # YAML discovery (builtin + extra dirs, cross-platform)
│   └── builtin/         # 6 YAML skill definitions
├── inputs/
│   ├── api.py           # FastAPI REST gateway with /metrics
│   ├── slack.py         # Slack bot (Socket Mode)
│   └── k8s_events.py    # Warning event auto-investigation with backoff
├── operator/
│   └── handlers.py      # Kopf CRD handlers with status conditions
└── outputs/
    └── audit.py         # Structured audit logging

tests/
├── test_api.py           # REST gateway tests
├── test_config.py        # Configuration tests (all providers)
├── test_executor.py      # Executor middleware tests
├── test_llm.py           # LLM factory tests
├── test_memory.py        # Memory/checkpointer tests
├── test_middleware.py     # Rate limiter, safety, edge cases
├── test_operator.py      # Kopf handler tests
├── test_safety.py        # Safety guardrail tests
├── test_skills.py        # Skill loader tests
├── test_supervisor.py    # Supervisor agent tests (mocked LLM)
└── test_integration_smoke.py  # Live Ollama + Gemini + K8s tests
```

## Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Agent framework | LangChain v1+ / LangGraph | Production-grade multi-agent with checkpointing |
| Operator framework | Kopf | Python-native, decorator-based, lightweight |
| Local LLM | Ollama | Easy setup, multi-model, tool-calling support |
| Cloud LLM | Gemini | Good tool-calling, fast, cost-effective |
| Safety | Pre-flight checks | Every command assessed before execution |
| Observability | structlog + Prometheus | Structured JSON logs + metrics endpoint |
| Testing | pytest + pytest-asyncio | Async-first, 75+ tests, 67% coverage |

## License

MIT
