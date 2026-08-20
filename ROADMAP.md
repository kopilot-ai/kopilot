# Kopilot Roadmap

The direction: a platform pilot you promote as trust grows. Autonomy is a
dial the operator controls, never a default the tool assumes, and every
autonomous action stays on the same audit trail as human approvals.

## Now (0.3.x)

- The autonomy dial in production shape: observe / copilot / autopilot,
  driven by AIPolicy CRDs, with the emergency brake documented and tested
- One-command client install: OCI Helm chart and versioned images on ghcr
- Durable approval queue (SQLite) so restarts lose nothing

## Next

- Autonomy reporting: what ran on autopilot, where, and what it would have
  asked for at level 1, so teams can promote namespaces with evidence
- Scheduled tasks (AITask cron) for recurring cost and hygiene sweeps
- PyPI publication and a versioned container SBOM

## Later

- Multi-replica coordination for the approval queue and event watcher
- Autonomy levels per skill (cost cleanup on autopilot, security read-only)
- CNCF Sandbox donation: governance and license are already aligned;
  submission tracked in the repo issues

## Not planned

- A hosted control plane. Kopilot stays self-hosted; your cluster, your
  keys, your audit log.
- Vendor-exclusive LLM features. Everything must work against the
  self-hosted Ollama path.
