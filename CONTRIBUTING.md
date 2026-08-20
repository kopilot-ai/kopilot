# Contributing to Kopilot

## Dev setup

```bash
git clone https://github.com/kopilot-ai/kopilot && cd kopilot
python3.13 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ --ignore=tests/test_integration_smoke.py   # fast, no external deps
```

`make lint` runs ruff; `make helm-lint` checks the chart. CI runs both plus
the test matrix on 3.11, 3.12, and 3.13.

For a live cluster, `bash scripts/setup-local-k8s.sh` creates a kind cluster
(Podman or Docker) with the CRDs and metrics-server installed.

## What a good PR looks like

- Tests first. Safety and autonomy changes need adversarial cases, not only
  happy paths; see tests/test_safety.py for the style.
- One change per PR. Refactors travel separately from behavior changes.
- The changelog gets an entry under Unreleased when behavior changes.
- Sign off your commits (`git commit -s`) to certify the
  [Developer Certificate of Origin](https://developercertificate.org/).

## Safety model changes

Anything that widens what the agent may execute without a human (new
autonomy semantics, approval bypass paths, pattern-gate changes) needs
consensus of all maintainers and a test that attempts to abuse the new
surface. When in doubt, open an issue before writing code.
