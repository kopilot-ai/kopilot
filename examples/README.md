# Kopilot examples

Runnable examples for every input surface. Apply the CRDs first:

```bash
kubectl apply -f ../helm/kubedevaiops/crds/
```

| File | What it shows |
|------|---------------|
| `aitask-health-check.yaml` | Read-only cluster health investigation via the operator |
| `aitask-cost-review.yaml` | Cost-optimization review with reflection enabled |
| `aitask-incident.yaml` | High-priority incident investigation scoped to one namespace |
| `aipolicy-safety.yaml` | An AIPolicy resource documenting guardrail intent |
| `skills/deployment-runbook.yaml` | A custom YAML skill (org-specific runbook agent) |
| `custom-skills-configmap.yaml` | Mount custom skills through the Helm chart |
| `values-gemini.yaml` | Helm values for Google Gemini |
| `values-openai.yaml` | Helm values for OpenAI |
| `values-anthropic.yaml` | Helm values for Anthropic Claude |
| `api-session.sh` | Scripted REST demo: submit task, list approvals, approve, retry |

## REST quick reference

```bash
# Submit a task
curl -s -X POST http://localhost:8080/tasks \
  -H "Authorization: Bearer $KOPILOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Find over-provisioned deployments across all namespaces"}'

# Review and approve a gated command
curl -s http://localhost:8080/approvals -H "Authorization: Bearer $KOPILOT_TOKEN"
curl -s -X POST http://localhost:8080/approvals/<id>/approve \
  -H "Authorization: Bearer $KOPILOT_TOKEN" \
  -H "X-Kopilot-Operator: alice"
```
