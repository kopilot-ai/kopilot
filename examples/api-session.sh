#!/usr/bin/env bash
# Scripted demo of the Kopilot REST API, including the approval workflow.
# Requires: a running `kopilot serve` (or port-forwarded pod), curl, jq.
#
#   KOPILOT_URL=http://localhost:8080 KOPILOT_TOKEN=... ./api-session.sh
set -euo pipefail

URL="${KOPILOT_URL:-http://localhost:8080}"
AUTH=()
if [[ -n "${KOPILOT_TOKEN:-}" ]]; then
  AUTH=(-H "Authorization: Bearer ${KOPILOT_TOKEN}")
fi

echo "==> Health"
curl -fsS "$URL/health" | jq .

echo "==> Loaded skills"
curl -fsS "$URL/skills" | jq -r '.[].name'

echo "==> Submit a read-only task"
curl -fsS -X POST "$URL/tasks" "${AUTH[@]}" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "List all nodes and summarise their health"}' | jq .

echo "==> Submit a task that needs a destructive step"
curl -fsS -X POST "$URL/tasks" "${AUTH[@]}" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Delete the failed pods in namespace default"}' | jq .

echo "==> Pending approvals"
curl -fsS "$URL/approvals?status=pending" "${AUTH[@]}" | jq .

APPROVAL_ID=$(curl -fsS "$URL/approvals?status=pending" "${AUTH[@]}" | jq -r '.[0].id // empty')
if [[ -n "$APPROVAL_ID" ]]; then
  echo "==> Approving $APPROVAL_ID"
  curl -fsS -X POST "$URL/approvals/$APPROVAL_ID/approve" "${AUTH[@]}" \
    -H "X-Kopilot-Operator: $(whoami)" | jq .

  echo "==> Re-run the task; the approved command now executes"
  curl -fsS -X POST "$URL/tasks" "${AUTH[@]}" \
    -H "Content-Type: application/json" \
    -d '{"prompt": "Retry deleting the failed pods in namespace default"}' | jq .
fi

echo "==> Task history"
curl -fsS "$URL/tasks/history?limit=5" "${AUTH[@]}" | jq .
