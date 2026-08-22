# RFC 0001: one ledger event schema for Gate, Govern, and Meter

Status: draft v0, 2026-08-22. Owner: Riadh.

The kopilot-ai platform is three planes with one promise: prove an agent before
release, govern its authority in production, account for every action. The
promise only holds if all three planes write to one auditable stream. This RFC
fixes the event envelope they share, so contractops results, kopilot decisions,
and future metering samples land in the same ledger and can answer one
question: what did this agent do, under whose authority, at what cost.

## Envelope

Every event is one JSON object. Fields marked * are required.

```json
{
  "schema": "kopilot-ai/ledger-event/v0",          // *
  "event_id": "01J8ZQ8Z3F9V5J2M4X6T8RWCK0",        // * ULID, sortable
  "time": "2026-08-22T11:04:05.123Z",              // * RFC 3339 UTC
  "plane": "govern",                                // * gate | govern | meter
  "agent": {                                        // * who acted
    "id": "kopilot/skill/cost-optimization",        // * stable identity
    "model": "gemini-2.5-pro",
    "framework": "kopilot@0.4.0",
    "run_id": "aitask-7f3a"                         // groups events of one run
  },
  "subject": {                                      // * what it acted on
    "kind": "k8s",                                  // k8s | repo | provider
    "ref": "cluster-a/ns/payments/deploy/api"       // plane-appropriate path
  },
  "action": "scale deployment api to 3 replicas",   // * human-readable
  "decision": {                                     // * the authority record
    "type": "approved",       // approved | denied | autopiloted | braked
                              // | gate_pass | gate_fail | observed
    "authority": "human:riadh",   // human:<id> | policy:<AIPolicy ref>
                                  // | contract:<pack@version> | none
    "policy_ref": "AIPolicy/payments-autopilot"     // when policy-derived
  },
  "evidence": {
    "digest": "sha256:…",       // hash of the full artifact (diff, transcript,
                                // contract report) stored outside the ledger
    "uri": "s3://… or file://…" // where the artifact lives
  },
  "cost": {                     // optional; Meter's home, others may fill it
    "tokens_in": 1284,
    "tokens_out": 96,
    "usd": 0.0041
  },
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736"    // OTel trace, optional
}
```

## Per-plane mapping

- **Gate (contractops).** One event per contract verdict at release time:
  `plane: gate`, `decision.type: gate_pass | gate_fail`,
  `decision.authority: contract:<policy-pack@version>`, `subject.kind: repo`
  with `ref: <repo>@<commit>`, evidence digest over the full contract report.
  Multi-trial stability runs stay in the report; the ledger records verdicts,
  not trials.
- **Govern (kopilot).** One event per approval, denial, autopiloted execution,
  or brake: what the approvals store already records, lifted into this
  envelope. `decision.authority` distinguishes a human sign-off from an
  AIPolicy grant, which is the core of the what-you-sign-is-what-runs story.
- **Meter.** One `decision.type: observed` event per costed action, or
  aggregates exported to Prometheus with `event_id` sampling for drill-down.
  Meter has no code yet; this envelope is its birth certificate.

## Non-goals for v0

No transport mandate (append to the existing kopilot ledger table, a JSONL
file, or an OTLP log record all qualify), no signing (the digest field leaves
room; tamper evidence is a v1 concern), no query API. Version bumps go in the
`schema` string.

## First implementation steps

1. kopilot: emit v0 envelopes alongside the current ledger rows (adapter, no
   migration).
2. contractops: add `--ledger <path>` to the gate command, writing one JSONL
   envelope per verdict.
3. Meter: consumes both, priced by a provider rate table. Separate design.
