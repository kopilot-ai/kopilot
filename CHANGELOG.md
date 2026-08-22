# Changelog

All notable changes to Kopilot will be documented in this file.

The format loosely follows Keep a Changelog and is intended to become the
release companion for future tags and GitHub releases.

## [0.5.0] - 2026-08-22

The production-hardening release. An external review proved that the dial
only gated destructive verbs: `kubectl apply -n kube-system`, a helm
upgrade into a protected namespace, and `base64 -d | sh` all ran without
approval, even with the emergency brake engaged. This release closes that
and everything found around it.

### Changed (breaking)

- **Every mutation gates, not just destructive verbs.** The safety layer
  is now a parser with allowlists of read-only verbs; anything else,
  including any command it cannot parse (pipes to `sh`, variable
  indirection, base64 payloads), waits for approval at level 1 and is
  refused at level 0. Protected-namespace checks apply to every mutation
  and match whole names. Reads still run free.
- **RBAC ships least-privilege.** The ClusterRole loses cluster-wide
  Secret reads and all write verbs on pods, serviceaccounts, namespaces,
  and daemonsets. Deletions and pod creation sit behind
  `rbac.extendedWrite`, Secret reads behind `rbac.extendedRead`. An
  approved deletion on the default tier now gets a 403 from the API
  server; that is the intended double lock.
- **Auth is on by default.** The chart generates a per-release bearer
  token (kept across upgrades) unless `api.authToken` or
  `existingSecret` overrides it. The quickstart drops its NodePort for
  ClusterIP plus port-forward.
- **A namespaced AIPolicy stays in its namespace.** An `autonomyLevel: 2`
  grant naming any other namespace is rejected `Invalid` with a
  `NamespaceEscape` condition. The level-0 brake stays cluster-wide.
- `AUTONOMY_LEVEL` is validated to 0-2; negative values used to disable
  observe mode entirely.

### Added

- **A real audit ledger.** Hash-chained JSONL beside the approvals
  database, one RFC 0001 envelope per authority decision: requests,
  grants, denials, autopiloted runs, brake refusals, results. Full
  64-char SHA-256 chain, fsync on every write, restart continues the
  chain, `verify_chain` walks it. Settled approvals are retired into the
  ledger before leaving the queue instead of being deleted. Approver
  identity comes from the authenticated credential; the
  `X-Kopilot-Operator` header is advisory display only. Approved
  executions re-check safety and the brake at execution time.
- **The operator survives restarts.** `@kopf.on.resume` handlers rebuild
  the brake, autopilot grants, and skills from live CRs; an AITask caught
  mid-`Executing` is marked Failed instead of being lost or re-run.
- **Supply chain.** Publishing gates on tests and runs on tags only;
  images ship with SBOM, provenance, and cosign keyless signatures;
  dependencies are hash-locked; the base image and every action are
  pinned; Trivy scans the image in CI; dependabot watches all three
  ecosystems. The runtime image drops pip entirely.
- `docs/UPGRADING.md`, an approval-loop walkthrough in the README and
  chart NOTES, and RFC 0001 (`docs/rfc/0001-ledger-event-schema.md`),
  the shared Gate/Govern/Meter event schema.

Migration from 0.4.0:

- CRDs are not upgraded by `helm upgrade`: run
  `kubectl apply -f helm/kopilot/crds/` before upgrading.
- Commands that previously ran unattended at level 1 (apply, create,
  edit, rollout, helm install) now request approval. Autopilot at
  level 2 within granted namespaces is unchanged.
- If a workflow needs deletions or pod creation, set
  `rbac.extendedWrite=true`.
- Clients must send `Authorization: Bearer <token>`; the token is in the
  release Secret.
- Pending approvals do not migrate: decide them before upgrading.

## [0.4.0] - 2026-08-20

### Changed (breaking)

- **One name everywhere.** The `kubedevaiops` era ends: the Python package,
  CLI, Helm chart, container paths, and metrics prefix are all `kopilot`.
  The CRD API group moves from `kubedevaiops.io` to `kopilot-ai.github.io`,
  because a CRD group should be a domain the project actually controls and
  the GitHub Pages domain is the one we do; `kubedevaiops.io` was never ours
  to squat.

Migration from 0.3.x:

- CLI: `kubedevaiops` is gone; call `kopilot`. Python imports:
  `import kopilot` replaces `import kubedevaiops`.
- Helm: the chart is `oci://ghcr.io/kopilot-ai/charts/kopilot`. Uninstall the
  old release, delete the old CRDs
  (`kubectl delete crd aitasks.kubedevaiops.io aiskills.kubedevaiops.io aipolicies.kubedevaiops.io`),
  then install 0.4.0; new CRDs install with the chart. AITask/AISkill/AIPolicy
  resources must be re-applied with `apiVersion: kopilot-ai.github.io/v1alpha1`.
- Metrics: dashboards tracking `kubedevaiops_*` series switch to `kopilot_*`.
- Custom skills mounted at `/etc/kubedevaiops/skills` now mount at
  `/etc/kopilot/skills` (chart handles this; only hand-rolled manifests care).
- Pending approvals do not migrate: decide them before upgrading.

### Added

- The dial: new brand identity in `brand/` (mark, wordmark, icon, palette,
  usage rules) and a Canva brand board.

## [0.3.0] - 2026-08-20

### Added

- **The autonomy dial**: a three-level autonomy engine at the executor.
  Level 0 refuses every mutation (an AIPolicy with `autonomyLevel: 0` is a
  kubectl-applyable emergency brake), level 1 keeps approval-gated copilot,
  level 2 grants namespace-scoped autopilot. Autopilot acts only when the
  command names its namespaces explicitly and they all sit inside a grant;
  CRITICAL commands, opaque payloads, and shell are never auto-approved.
  Autonomous executions land in the approval ledger as `policy:<name>`.
  Configure via `AUTONOMY_LEVEL`/`AUTONOMY_AUTOPILOT_NAMESPACES` or live
  AIPolicy CRDs; `GET /autonomy` reports the effective state.
- **What you sign is what runs**: approving a request executes the exact
  reviewed command immediately and returns its output. Live testing showed
  the consume-on-retry flow rarely completes because the LLM rewrites the
  command on the next task; `?execute=false` keeps the old flow.
- **Durable approvals**: `APPROVALS_DB_PATH` journals every approval
  transition to SQLite and reloads state on startup, so pending approvals
  survive a restart. Unset keeps the previous memory-only behavior. The Helm
  chart sets `/data/approvals.db` on an emptyDir by default;
  `persistence.enabled=true` provisions a PVC that survives rescheduling.
- **AISkill CRDs load for real**: in `kopilot serve`, applying an AISkill
  (new additive spec fields `displayName`, `systemPrompt`, `documentation`)
  registers a live sub-agent in the skill registry; disabling or deleting the
  resource unregisters it. A skill without `systemPrompt` reports `Invalid`
  instead of pretending to load. Standalone `kopilot operator` still only
  updates status — the serve replica owns the registry.
- **Helm chart published as an OCI artifact**: version tags push the chart to
  `oci://ghcr.io/kopilot-ai/charts/kubedevaiops`, so clients install without
  cloning the repo. Container images gain semver tags (`0.3.0`, `0.3`)
  and the chart's default image tag follows its appVersion.
- Governance for a CNCF-track project: Apache-2.0 relicense (from MIT),
  CNCF code of conduct, contributing guide with DCO, maintainers, security
  policy, governance, adopters.

### Fixed

- The image declared a non-numeric user, so any install with the chart's
  `runAsNonRoot` failed with CreateContainerConfigError; the user is now
  UID 10001 and the pod sets `fsGroup` so `/data` is writable.
- kubectl/helm downloads in the image were hardcoded to amd64; arm64 builds
  shipped emulated binaries that crash with a Go lfstack runtime error on
  the first command. Downloads follow TARGETARCH, kubectl moves to v1.33.4,
  and releases publish linux/amd64 and linux/arm64.
- The chart's Service rendered a metrics port with value 0, which made every
  `helm install` fail; the metrics port block is gone (`/metrics` is on the
  API port).
- The chart's ClusterRole was missing `customresourcedefinitions` read
  (kopf crashes at startup without it) and the `*/scale` subresources
  (`kubectl scale` was Forbidden in-cluster).

### Security

- **Shell denylist bypass closed**: the root-deletion guard only fired when the
  path was followed by whitespace or end-of-string, so any trailing character
  defeated it — `rm -rf --no-preserve-root /;` and `sh -c 'rm -rf /'` both
  reached the executor. Patterns now terminate on any shell boundary,
  `--no-preserve-root` is blocked outright whatever the target, and
  `chown`/`chmod` on `/`, `init 0`, and raw writes to block devices are added.
- **`kubectl exec`/`cp`/`attach` are no longer unguarded**: these carry an
  opaque payload into a running container, so `kubectl exec -n kube-system
  etcd-master -- etcdctl del / --prefix` previously rated LOW and executed.
  They are now treated as state-changing: refused outright against protected
  namespaces, approval-gated everywhere else.

### Changed

- `LOG_LEVEL` is applied (it configured nothing before)
- Version is single-sourced from `__version__` across the API, health
  endpoint, and interop manifest
- Approval store drops settled requests past their retention window instead of
  growing one entry per gated command
- `AITask.spec.reflect` added to the CRD schema — the operator read it but the
  structural schema pruned it, so it was always false
- Helm chart no longer publishes a metrics port nothing listens on
  (`/metrics` is served on the API port)

### Removed

- Dead settings that silently did nothing: `SAFETY_DRY_RUN_DEFAULT` (and the
  uncalled `assess_action` path it fed), `K8S_NAMESPACE`/`KUBECONFIG`
  (`K8sSettings` was never read), `SLACK_ENABLED`, `METRICS_ENABLED`,
  `METRICS_PORT`

### Fixed

- README: Helm example used a value key that does not exist
  (`llm.ollamaUrl` → `ollama.baseUrl`); interop endpoints added to the API table
- `metrics_enabled`/`metrics_port` settings actually removed from the config
  model (a previous entry claimed the removal)

## [0.2.0] - 2026-07-31

### Added

- **Human-in-the-loop approval workflow**: destructive commands create pending
  approval requests reviewable via `GET /approvals` and decidable via
  `POST /approvals/{id}/approve|deny`; approvals are single-use with a
  10-minute TTL, and every transition is audit-logged
- Bearer-token authentication (`API_AUTH_TOKEN`) on task, history, metrics,
  and approval endpoints
- HMAC-SHA256 webhook signature verification (`API_WEBHOOK_SECRET`); webhooks
  are disabled until a secret is configured
- Per-task risk propagation: task results now report the highest risk level
  the safety layer assessed for any attempted command
- Anthropic provider support via `langchain-anthropic`
  (`ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL`)
- Slack user allowlist (`SLACK_ALLOWED_USERS`)
- `examples/` directory: AITask/AIPolicy manifests, custom skill, per-provider
  Helm values, and a scripted REST/approval demo
- GitHub Actions CI (ruff, pytest on 3.11–3.13, Docker build, helm lint,
  kubeconform) and a GHCR release workflow
- Adversarial safety test-suite covering namespace-flag variants, chained
  commands, obfuscated invocations, and approval flows (130+ tests)

### Changed

- Safety assessor rebuilt: detects `patch`/`replace`/`scale`/`taint`/`evict`/
  `--prune`/helm `uninstall|rollback|delete`, `--namespace=`/`-n=` syntax,
  namespace-as-resource deletions, chained commands, and obfuscated
  invocations; all-namespace destructive commands require approval
- `read_resource` no longer builds shell strings from model input: ConfigMap
  names are validated, URLs fetched via argv exec, and file reads restricted
  to `SAFETY_READ_PATHS`
- Subprocess execution hardened: process-group kill on timeout, zombie
  reaping, 2 MB streamed output cap, and no automatic retry of mutating
  commands
- Kubernetes event watcher moved off the event loop (worker thread + queue)
  and now survives `kopilot serve` startup; investigations are bounded by a
  semaphore and keep strong task references
- Operator handlers are idempotent across kopf retries (spec-hash guard),
  follow Kubernetes condition conventions for `lastTransitionTime`, and no
  longer re-raise after recording a terminal failure
- MCP server migrated to mcp SDK 2.x (`MCPServer`)
- CORS is disabled by default (`API_CORS_ORIGINS=[]`); wildcard-with-
  credentials configuration removed
- Dockerfile: pinned, checksum-verified kubectl/helm downloads; fixed
  README-missing build failure; fixed the runtime user's home directory
- Helm chart: securityContext, existingSecret support, all five LLM
  providers, custom-skills ConfigMap mounting, corrected repository URLs
- Quickstart manifest uses a scoped ClusterRole instead of cluster-admin

### Fixed

- Anthropic provider crashed at import (wrong LangChain package)
- Reflection step crashed on providers returning list-shaped message content
- Smoke-test script no longer embeds an API key placeholder; keys are read
  from the environment

### Security

- If you cloned this repository before 0.2.0: a Google Gemini API key was
  committed to git history in `scripts/smoke_e2e.py` and later redacted.
  That key must be treated as compromised and rotated.

## [0.1.0]

### Added

- Public `kopilot` CLI entrypoint alongside the legacy `kubedevaiops` command
- README-first trust surfaces for cost optimization, safety, and proof assets
- Roadmap and changelog documents for public project hygiene
- Structured GitHub issue templates for bugs and feature requests

### Changed

- Public package metadata now points to the `kopilot-ai/kopilot` repository
- API and CLI branding now use the public `Kopilot` product name
- Onboarding docs now explain the package-install-name vs CLI-name split
