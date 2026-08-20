<p align="center">
  <img src="brand/kopilot-icon.svg" alt="kopilot" width="88">
</p>

<h1 align="center">kopilot</h1>

<p align="center"><b>The platform pilot for Kubernetes. It investigates on its own, asks before it acts, and takes the controls only where you grant them.</b></p>

<p align="center">
  <a href="https://kopilot-roan.vercel.app">Website</a> ·
  <a href="https://kopilot-roan.vercel.app/docs">Docs</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/license-Apache--2.0-2FBF71" alt="Apache-2.0">
  <img src="https://img.shields.io/badge/cluster_changes-approval--gated-FF4F1F" alt="Approval gated">
  <img src="https://img.shields.io/badge/deployment-self--hosted-10141F" alt="Self-hosted">
</p>

kopilot is an open-source AI agent for Kubernetes operations. A supervisor
routes plain-language requests to skill sub-agents that investigate your
cluster with real `kubectl` and `helm` through one audited executor. What it
may do without you is a dial you control.

<p align="center">
  <img src="docs/assets/kopilot-dial.gif" alt="The autonomy dial cycling through copilot, autopilot, and observe" width="520">
</p>

## The autonomy dial

| Level | Name | What happens |
|---|---|---|
| 0 | Observe | Every mutating command is refused. An `AIPolicy` with `autonomyLevel: 0` is an emergency brake you can `kubectl apply` mid-incident. |
| 1 | Copilot | The default. Investigations run freely; destructive commands wait as approval requests. |
| 2 | Autopilot | Namespace-scoped grants. kopilot acts alone only when every namespace a command names sits inside a grant. |

```yaml
apiVersion: kopilot-ai.github.io/v1alpha1
kind: AIPolicy
metadata:
  name: staging-autopilot
spec:
  autonomyLevel: 2
  namespaces: [staging, qa]
```

Grants reconcile live and are revoked by deletion. CRITICAL commands, opaque
payloads (`kubectl exec`, `cp`, `attach`), and shell commands never qualify
for autopilot, and protected namespaces such as `kube-system` stay refused
whatever any policy says. Every autonomous act lands in the same approval
ledger a human signs, recorded as `policy:<name>`.

## What you sign is what runs

Destructive commands pause as approval requests. Approving one executes the
exact command you reviewed and returns its output, so no model rephrases
anything between your review and the cluster. Approvals are single-use,
expire in 10 minutes, and journal to SQLite so they survive restarts.

## Install

```bash
helm install kopilot oci://ghcr.io/kopilot-ai/charts/kopilot \
  --version 0.4.0 --namespace kopilot --create-namespace \
  --set llm.provider=ollama
```

The chart ships the CRDs, RBAC, hardened security contexts, and the approval
volume; set `persistence.enabled=true` for a PVC that survives pod
rescheduling. Images are published for amd64 and arm64. Five LLM providers
are one env var away (Ollama self-hosted by default; OpenAI, Azure OpenAI,
Anthropic, Gemini), and no feature depends on a single vendor.

Or run from source:

```bash
git clone https://github.com/kopilot-ai/kopilot && cd kopilot
pip install -e .
cp .env.example .env    # pick your LLM provider
kopilot serve
```

Then ask it something:

```bash
kopilot ask "what pods are failing and why?"
```

## Skills are YAML, and they load live

Six sub-agents ship built in: security, administration, networking,
monitoring, troubleshooting, cost optimization. Each is a YAML definition
(system prompt plus reference docs) compiled into an autonomous agent.
Apply an `AISkill` resource with your own `systemPrompt` and it joins the
registry without a restart; delete it and it leaves the same way.

## Surfaces

CLI (`kopilot serve|api|operator|ask|mcp`), a bearer-authenticated REST API,
an MCP server for agent clients, HMAC-verified webhooks, Slack, `AITask`
resources, and auto-investigation of Kubernetes warning events. Prometheus
metrics are served as `kopilot_*`. Full reference:
[API and CLI](https://kopilot-roan.vercel.app/docs/api) ·
[Configuration](https://kopilot-roan.vercel.app/docs/configuration) ·
[Deployment](https://kopilot-roan.vercel.app/docs/deployment).

## The website

<p align="center">
  <a href="https://kopilot-roan.vercel.app"><img src="docs/assets/kopilot-site-hero.png" alt="kopilot website" width="720"></a>
</p>

## Honest limits

- Single replica: the approval queue and event watcher are process-local.
- The safety layer is defense in depth. RBAC on the service account is the
  real boundary.
- The model can be wrong. The dial constrains what runs, not what it
  concludes.
- CRD-driven skills and grants apply in serve mode, where the operator
  shares the process with the executor.

## Development

```bash
make dev      # editable install with dev dependencies
make test     # pytest (190+ tests, including adversarial safety cases)
make lint     # ruff
make helm-lint
```

`bash scripts/setup-local-k8s.sh` brings up a kind cluster with the CRDs
installed. See [CONTRIBUTING.md](CONTRIBUTING.md); safety-model changes need
adversarial tests and maintainer consensus.

## License

Apache-2.0. Brand assets and usage rules live in [`brand/`](brand/).
