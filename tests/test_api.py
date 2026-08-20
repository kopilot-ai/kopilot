"""Tests for the FastAPI REST gateway."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import kubedevaiops.inputs.api as api_module
from kubedevaiops.inputs.api import create_app

WEBHOOK_SECRET = "test-webhook-secret"


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.fixture
def auth_client(monkeypatch):
    """Client for an app with bearer auth enabled."""
    monkeypatch.setenv("API_AUTH_TOKEN", "sekrit-token")
    import kubedevaiops.config as cfg_mod
    cfg_mod._settings = None
    return TestClient(create_app())


@pytest.fixture
def webhook_client(monkeypatch):
    """Client for an app with the webhook secret configured."""
    monkeypatch.setenv("API_WEBHOOK_SECRET", WEBHOOK_SECRET)
    import kubedevaiops.config as cfg_mod
    cfg_mod._settings = None
    return TestClient(create_app())


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_app_metadata_branded():
    app = create_app()
    assert app.title == "Kopilot"
    assert app.description == "Approval-gated AI Kubernetes operations agent"


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["skills_loaded"] >= 0
    assert "llm_provider" in data


def test_readyz(client):
    assert client.get("/readyz").status_code == 200


def test_list_skills(client):
    resp = client.get("/skills")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_list_portable_skills(client):
    resp = client.get("/skills/portable")
    assert resp.status_code == 200
    assert any(skill["name"] == "cost_optimization" for skill in resp.json())


def test_interop_manifest(client):
    resp = client.get("/interop")
    assert resp.status_code == 200
    data = resp.json()
    assert data["protocols"]["mcp"]["status"] == "available"
    assert data["protocols"]["portable_skills"]["status"] == "available"


def test_well_known_agent_manifest(client):
    resp = client.get("/.well-known/agent-manifest.json")
    assert resp.status_code == 200
    assert resp.json()["task_api"]["submit_endpoint"] == "/tasks"


def test_metrics_endpoint(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "kubedevaiops" in resp.text


def test_task_history_empty(client):
    resp = client.get("/tasks/history")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_submit_task(client, monkeypatch):
    mock = AsyncMock(return_value={
        "task_id": "t-1",
        "answer": "All healthy.",
        "risk_level": "low",
        "elapsed_ms": 42,
        "attempts": 1,
    })
    monkeypatch.setattr(api_module, "run_task", mock)
    resp = client.post("/tasks", json={"prompt": "check pods"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "All healthy."
    assert data["elapsed_ms"] == 42


def test_submit_task_with_reflect(client, monkeypatch):
    mock = AsyncMock(return_value={
        "task_id": "t-r",
        "answer": "Reflected response.",
        "risk_level": "low",
        "elapsed_ms": 100,
        "attempts": 2,
    })
    monkeypatch.setattr(api_module, "run_task", mock)
    resp = client.post("/tasks", json={"prompt": "check pods", "reflect": True})
    assert resp.status_code == 200


def test_submit_task_empty_prompt_rejected(client):
    resp = client.post("/tasks", json={"prompt": ""})
    assert resp.status_code == 422


def test_task_failure_does_not_leak_details(client, monkeypatch):
    mock = AsyncMock(side_effect=RuntimeError("secret internal path /etc/foo"))
    monkeypatch.setattr(api_module, "run_task", mock)
    resp = client.post("/tasks", json={"prompt": "check pods"})
    assert resp.status_code == 500
    assert "secret internal path" not in resp.text


# ── Authentication ──────────────────────────────────────────────────────────


def test_auth_required_when_token_set(auth_client):
    assert auth_client.post("/tasks", json={"prompt": "hi"}).status_code == 401
    assert auth_client.get("/tasks/history").status_code == 401
    assert auth_client.get("/metrics").status_code == 401
    assert auth_client.get("/approvals").status_code == 401


def test_auth_wrong_token_rejected(auth_client):
    resp = auth_client.get(
        "/tasks/history", headers={"Authorization": "Bearer wrong-token"}
    )
    assert resp.status_code == 401


def test_auth_valid_token_accepted(auth_client):
    resp = auth_client.get(
        "/tasks/history", headers={"Authorization": "Bearer sekrit-token"}
    )
    assert resp.status_code == 200


def test_health_open_without_auth(auth_client):
    assert auth_client.get("/health").status_code == 200


# ── Webhooks ────────────────────────────────────────────────────────────────


def test_webhook_disabled_without_secret(client):
    resp = client.post("/webhook", json={"source": "x", "payload": {"prompt": "y"}})
    assert resp.status_code == 403


def test_webhook_rejects_bad_signature(webhook_client):
    body = json.dumps({"source": "servicenow", "payload": {"prompt": "hi"}}).encode()
    resp = webhook_client.post(
        "/webhook", content=body,
        headers={"X-Kopilot-Signature": "sha256=deadbeef", "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


def test_webhook_valid_signature(webhook_client, monkeypatch):
    mock = AsyncMock(return_value={
        "task_id": "t-2",
        "answer": "Resolved.",
        "risk_level": "low",
        "elapsed_ms": 55,
        "attempts": 1,
    })
    monkeypatch.setattr(api_module, "run_task", mock)
    body = json.dumps({"source": "servicenow", "payload": {"prompt": "Pod crash"}}).encode()
    resp = webhook_client.post(
        "/webhook", content=body,
        headers={"X-Kopilot-Signature": _sign(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["answer"] == "Resolved."


def test_webhook_missing_prompt(webhook_client):
    body = json.dumps({"source": "test", "payload": {}}).encode()
    resp = webhook_client.post(
        "/webhook", content=body,
        headers={"X-Kopilot-Signature": _sign(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 400


# ── Approvals API ───────────────────────────────────────────────────────────


def test_approvals_flow(client, mock_subprocess):
    from kubedevaiops.executor.approvals import get_approval_store

    store = get_approval_store()
    req = store.request(
        command="kubectl delete pod x -n staging", tool="kubectl",
        reason="destructive", risk="high",
    )

    listed = client.get("/approvals").json()
    assert any(item["id"] == req.id for item in listed)

    # Default approve executes the signed command and consumes the approval
    resp = client.post(f"/approvals/{req.id}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "consumed"
    assert resp.json()["executed"] is True

    # A second decision on the same id is a 404 (no longer pending)
    assert client.post(f"/approvals/{req.id}/deny").status_code == 404


def test_approvals_deny(client):
    from kubedevaiops.executor.approvals import get_approval_store

    req = get_approval_store().request(
        command="helm uninstall prod-release", tool="helm",
        reason="destructive", risk="high",
    )
    resp = client.post(f"/approvals/{req.id}/deny")
    assert resp.status_code == 200
    assert resp.json()["status"] == "denied"


def test_approvals_unknown_status_filter(client):
    assert client.get("/approvals", params={"status": "bogus"}).status_code == 400


def test_autonomy_endpoint_requires_auth(auth_client):
    r = auth_client.get("/autonomy")
    assert r.status_code == 401


def test_autonomy_endpoint_snapshot(auth_client, autonomy_staging):
    r = auth_client.get("/autonomy", headers={"Authorization": "Bearer sekrit-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["observe"] is False
    assert body["grants"] == [{"name": "staging-autopilot", "namespaces": ["staging"]}]


def test_approve_executes_the_signed_command(auth_client, mock_subprocess):
    """Approving runs the exact reviewed command and returns its output."""
    from kubedevaiops.executor.approvals import get_approval_store

    req = get_approval_store().request(
        "kubectl scale deployment sleeper -n e2e --replicas=1",
        "kubectl", "destructive", "high",
    )
    r = auth_client.post(
        f"/approvals/{req.id}/approve",
        headers={"Authorization": "Bearer sekrit-token"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "consumed"
    assert body["executed"] is True
    assert "mocked output" in body["output"]


def test_approve_without_execute_keeps_old_flow(auth_client, mock_subprocess):
    from kubedevaiops.executor.approvals import ApprovalStatus, get_approval_store

    store = get_approval_store()
    req = store.request("kubectl delete pod p -n e2e", "kubectl", "destructive", "high")
    r = auth_client.post(
        f"/approvals/{req.id}/approve?execute=false",
        headers={"Authorization": "Bearer sekrit-token"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    assert r.json()["executed"] is False
    # the standing approval is consumable by the agent as before
    assert store.consume_if_approved("kubectl delete pod p -n e2e") is not None
    assert store.get(req.id).status is ApprovalStatus.CONSUMED
