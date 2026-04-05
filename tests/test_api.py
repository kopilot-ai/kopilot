"""Tests for the FastAPI REST gateway."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import kubedevaiops.inputs.api as api_module
from kubedevaiops.inputs.api import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


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


def test_webhook(client, monkeypatch):
    mock = AsyncMock(return_value={
        "task_id": "t-2",
        "answer": "Resolved.",
        "risk_level": "low",
        "elapsed_ms": 55,
        "attempts": 1,
    })
    monkeypatch.setattr(api_module, "run_task", mock)
    resp = client.post("/webhook", json={
        "source": "servicenow",
        "payload": {"prompt": "Pod crash in prod"},
    })
    assert resp.status_code == 200


def test_webhook_missing_prompt(client):
    resp = client.post("/webhook", json={
        "source": "test",
        "payload": {},
    })
    assert resp.status_code == 400
