# Upgrading

## CRDs first, every release

Helm's `crds/` directory convention applies here: the chart installs the
CustomResourceDefinitions in `helm/kopilot/crds/` once, on the first
`helm install`, and then leaves them alone. `helm upgrade` never touches
them, no matter how much `AITask`, `AISkill`, or `AIPolicy`'s schema
changed in the new chart version. That's Helm's own design, not a bug in
this chart, and it means an upgrade that adds or changes a CRD field needs
a manual step before or after the Helm upgrade:

```bash
kubectl apply -f helm/kopilot/crds/
helm upgrade kopilot oci://ghcr.io/kopilot-ai/charts/kopilot --version <new-version> \
  --namespace kopilot --reuse-values
```

Run the `kubectl apply` every time you upgrade, even when you don't
remember a CRD changing. It's a no-op when nothing did.

## Pending approvals do not migrate

Decide every pending approval before you upgrade. The approval queue
(`APPROVALS_DB_PATH`, `/data/approvals.db` on the default chart) is not
versioned against the running code, so a request queued on the old version
does not carry forward cleanly to the new one. Clear the queue with
`GET /approvals?status=pending` plus an approve or deny for each, then
upgrade with an empty queue.

## Where to look for the rest

Version-specific breaking changes and their migration steps live in
[CHANGELOG.md](../CHANGELOG.md) under each release, not here. This file
covers the two steps every upgrade needs regardless of version; check the
changelog entry for the version you're moving to for anything else it
requires.
