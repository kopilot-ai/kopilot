"""The hash-chained audit ledger.

Covers the envelope shape (RFC 0001 v0), the chain itself, what survives the
approval store's retention window, command integrity and redaction, and the
identity that ends up in `decision.authority`.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from kopilot.outputs.audit import (
    GENESIS_HASH,
    LEDGER_SCHEMA,
    Decision,
    Ledger,
    authority_for,
    command_digest,
    new_event_id,
    record_command_event,
    record_event,
    redact_command,
    verify_chain,
)

SECRET_CMD = (
    "kubectl create secret generic db-creds -n payments "
    "--from-literal=password=hunter2-swordfish --token=AKIAIOSFODNN7EXAMPLE12345"
)


def _entries(ledger: Ledger) -> list[dict]:
    return ledger.entries()


def _by_stage(ledger: Ledger, stage: str) -> list[dict]:
    return [e for e in ledger.entries() if e.get("context", {}).get("stage") == stage]


# ── Envelope ────────────────────────────────────────────────────────────────


class TestEnvelope:
    def test_carries_every_required_rfc_field(self, ledger):
        entry = record_event(
            "scale deployment api to 3 replicas",
            Decision.APPROVED,
            "human:riadh",
            ref="ns/payments",
        )
        assert entry["schema"] == LEDGER_SCHEMA
        assert len(entry["event_id"]) == 26
        assert entry["time"].endswith("Z")
        assert entry["plane"] == "govern"
        assert entry["agent"]["id"] == "kopilot"
        assert entry["agent"]["framework"].startswith("kopilot@")
        assert entry["subject"] == {"kind": "k8s", "ref": "ns/payments"}
        assert entry["action"] == "scale deployment api to 3 replicas"
        assert entry["decision"] == {"type": "approved", "authority": "human:riadh"}

    def test_policy_ref_only_when_policy_derived(self, ledger):
        plain = record_event("do a thing", Decision.OBSERVED, "none")
        granted = record_event(
            "do a thing", Decision.AUTOPILOTED, "policy:staging", policy_ref="staging"
        )
        assert "policy_ref" not in plain["decision"]
        assert granted["decision"]["policy_ref"] == "staging"

    def test_subject_ref_follows_the_namespaces_named(self, ledger):
        record_command_event("kubectl get pods -n staging", "kubectl", Decision.OBSERVED, "none")
        record_command_event("kubectl get pods -A", "kubectl", Decision.OBSERVED, "none")
        record_command_event("kubectl get nodes", "kubectl", Decision.OBSERVED, "none")
        assert [e["subject"]["ref"] for e in _entries(ledger)] == ["ns/staging", "ns/*", "cluster"]

    def test_event_ids_sort_by_time(self):
        ids = []
        for _ in range(3):
            ids.append(new_event_id())
            time.sleep(0.002)
        assert ids == sorted(ids)

    def test_authority_scheme_is_preserved(self):
        assert authority_for("riadh") == "human:riadh"
        assert authority_for("policy:staging") == "policy:staging"
        assert authority_for("") == "none"


# ── The chain ───────────────────────────────────────────────────────────────


class TestChain:
    def test_entries_link_to_their_predecessor(self, ledger):
        first = record_event("one", Decision.OBSERVED, "none")
        second = record_event("two", Decision.OBSERVED, "none")
        third = record_event("three", Decision.OBSERVED, "none")

        assert first["prev_hash"] == GENESIS_HASH
        assert second["prev_hash"] == first["hash"]
        assert third["prev_hash"] == second["hash"]
        assert all(len(e["hash"]) == 64 for e in (first, second, third))

    def test_verify_walks_a_clean_chain(self, ledger):
        for i in range(5):
            record_event(f"event {i}", Decision.OBSERVED, "none")
        result = verify_chain(ledger.path)
        assert result.ok
        assert result.entries == 5

    def test_verify_catches_an_edited_entry(self, ledger):
        for i in range(3):
            record_event(f"event {i}", Decision.OBSERVED, "none")

        lines = ledger.path.read_text().splitlines()
        tampered = json.loads(lines[1])
        tampered["decision"]["authority"] = "human:someone-else"
        lines[1] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
        ledger.path.write_text("\n".join(lines) + "\n")

        result = verify_chain(ledger.path)
        assert not result.ok
        assert result.broken_at == 2
        assert "hash does not match" in result.reason

    def test_verify_catches_a_removed_entry(self, ledger):
        for i in range(4):
            record_event(f"event {i}", Decision.OBSERVED, "none")

        lines = ledger.path.read_text().splitlines()
        del lines[1]
        ledger.path.write_text("\n".join(lines) + "\n")

        result = verify_chain(ledger.path)
        assert not result.ok
        assert result.broken_at == 2
        assert "prev_hash" in result.reason

    def test_chain_continues_after_a_restart(self, ledger):
        first = record_event("before restart", Decision.OBSERVED, "none")

        reopened = Ledger(ledger.path)
        assert reopened.tip == first["hash"]
        second = reopened.append({"action": "after restart"})
        assert second["prev_hash"] == first["hash"]
        assert verify_chain(ledger.path).ok

    def test_empty_ledger_verifies(self, tmp_path):
        result = verify_chain(tmp_path / "nothing.jsonl")
        assert result.ok
        assert result.entries == 0


# ── Command integrity ───────────────────────────────────────────────────────


class TestCommandIntegrity:
    def test_secrets_are_redacted_in_the_display_form(self):
        display = redact_command(SECRET_CMD)
        assert "hunter2-swordfish" not in display
        assert "AKIAIOSFODNN7EXAMPLE12345" not in display
        assert display.count("[redacted]") == 2
        # everything that is not a secret survives
        assert "kubectl create secret generic db-creds -n payments" in display

    def test_bearer_tokens_and_jwts_are_redacted(self):
        display = redact_command(
            'curl -H "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sIgNaTuRe" http://x'
        )
        assert "eyJhbGciOiJIUzI1NiJ9" not in display
        assert "Authorization:" in display

    def test_ordinary_commands_are_left_alone(self):
        cmd = "kubectl scale deployment api -n payments --replicas=3"
        assert redact_command(cmd) == cmd

    def test_digest_covers_the_whole_command(self, ledger):
        entry = record_command_event(SECRET_CMD, "kubectl", Decision.OBSERVED, "none")
        assert entry["evidence"]["digest"] == command_digest(SECRET_CMD)
        # the digest is over the real command, not the display form
        assert entry["evidence"]["digest"] != command_digest(entry["action"])
        assert entry["evidence"]["digest"].startswith("sha256:")
        assert len(entry["evidence"]["digest"]) == len("sha256:") + 64

    def test_no_two_hundred_character_truncation(self, ledger):
        long_command = "kubectl annotate pod api -n payments " + " ".join(
            f"note{i}=value{i}" for i in range(40)
        )
        assert len(long_command) > 400
        entry = record_command_event(long_command, "kubectl", Decision.OBSERVED, "none")
        assert entry["action"] == long_command
        assert entry["evidence"]["digest"] == command_digest(long_command)


# ── What the approval store writes ──────────────────────────────────────────


class TestApprovalTrail:
    def test_request_approve_and_consume_are_all_recorded(self, ledger):
        from kopilot.executor.approvals import ApprovalStore

        store = ApprovalStore()
        req = store.request("kubectl delete pod x -n staging", "kubectl", "destructive", "high")
        store.approve(req.id, decided_by="token:abc123")
        store.consume_if_approved("kubectl delete pod x -n staging")

        stages = [e["context"]["stage"] for e in _entries(ledger)]
        assert stages == ["approval_requested", "approval_granted", "approval_consumed"]

        requested, granted, consumed = _entries(ledger)
        assert requested["decision"] == {"type": "observed", "authority": "none"}
        assert granted["decision"] == {"type": "approved", "authority": "human:token:abc123"}
        assert consumed["decision"]["authority"] == "human:token:abc123"
        assert all(e["context"]["approval_id"] == req.id for e in _entries(ledger))
        assert verify_chain(ledger.path).ok

    def test_denial_is_recorded(self, ledger):
        from kopilot.executor.approvals import ApprovalStore

        store = ApprovalStore()
        req = store.request("helm uninstall prod -n prod", "helm", "destructive", "high")
        store.deny(req.id, decided_by="alice")

        denied = _by_stage(ledger, "approval_denied")[0]
        assert denied["decision"] == {"type": "denied", "authority": "human:alice"}

    def test_autopiloted_execution_names_the_policy(self, ledger):
        from kopilot.executor.approvals import ApprovalStore

        ApprovalStore().record_auto(
            command="kubectl scale deployment api -n staging --replicas=3",
            tool="kubectl",
            reason="destructive",
            risk="high",
            policy="staging-autopilot",
        )
        entry = _by_stage(ledger, "autopilot_granted")[0]
        assert entry["decision"]["type"] == "autopiloted"
        assert entry["decision"]["authority"] == "policy:staging-autopilot"
        assert entry["decision"]["policy_ref"] == "staging-autopilot"

    def test_settled_record_reaches_the_ledger_before_the_queue_drops_it(self, ledger, tmp_path):
        """The TTL cleanup empties SQLite; it never touches the ledger."""
        from kopilot.executor.approvals import ApprovalStore

        db = str(tmp_path / "approvals.db")
        store = ApprovalStore(ttl=0.01, db_path=db)
        req = store.request("kubectl delete pod y -n staging", "kubectl", "destructive", "high")
        store.deny(req.id, decided_by="alice")
        lines_before = len(ledger.path.read_text().splitlines())

        time.sleep(0.1)
        store.list()  # triggers the prune

        # gone from the working queue and from disk
        assert ApprovalStore(ttl=0.01, db_path=db).get(req.id) is None
        # but the full record is in the ledger, and the ledger only grew
        retired = _by_stage(ledger, "approval_retired")
        assert len(retired) == 1
        assert retired[0]["context"]["record"]["id"] == req.id
        assert retired[0]["context"]["record"]["status"] == "denied"
        assert retired[0]["context"]["record"]["command"] == "kubectl delete pod y -n staging"
        assert len(ledger.path.read_text().splitlines()) > lines_before
        assert verify_chain(ledger.path).ok

    def test_expiry_is_recorded(self, ledger):
        from kopilot.executor.approvals import ApprovalStore

        store = ApprovalStore(ttl=0.01)
        store.request("kubectl delete ns qa", "kubectl", "destructive", "high")
        time.sleep(0.05)
        store.list()
        assert _by_stage(ledger, "approval_expired")


# ── What the executor writes ────────────────────────────────────────────────


class TestExecutorTrail:
    @pytest.mark.asyncio
    async def test_execution_result_is_recorded(self, ledger, mock_subprocess):
        from kopilot.executor.middleware import run_kubectl

        await run_kubectl.ainvoke({"command": "kubectl get pods -n staging"})
        entry = _by_stage(ledger, "execution_result")[0]
        assert entry["decision"] == {"type": "observed", "authority": "policy:kopilot-safety"}
        assert entry["context"]["outcome"] == "completed"
        assert entry["context"]["output_sha256"]

    @pytest.mark.asyncio
    async def test_brake_refusal_is_recorded(self, ledger, mock_subprocess, autonomy_observe):
        from kopilot.executor.middleware import run_kubectl

        result = await run_kubectl.ainvoke({"command": "kubectl delete pod x -n staging"})
        assert "OBSERVE MODE" in result

        braked = _by_stage(ledger, "observe_refused")[0]
        assert braked["decision"]["type"] == "braked"
        assert braked["decision"]["authority"] == "policy:test-brake"
        assert braked["context"]["brakes"] == ["test-brake"]

    @pytest.mark.asyncio
    async def test_safety_block_is_recorded_as_a_policy_denial(self, ledger, mock_subprocess):
        from kopilot.executor.middleware import run_kubectl

        await run_kubectl.ainvoke({"command": "kubectl delete pod x -n kube-system"})
        blocked = _by_stage(ledger, "safety_blocked")[0]
        assert blocked["decision"] == {"type": "denied", "authority": "policy:kopilot-safety"}
        assert blocked["context"]["risk"] == "critical"

    @pytest.mark.asyncio
    async def test_autopiloted_run_records_grant_then_result(
        self, ledger, mock_subprocess, autonomy_staging
    ):
        from kopilot.executor.middleware import run_kubectl

        await run_kubectl.ainvoke(
            {"command": "kubectl scale deployment api -n staging --replicas=3"}
        )
        entries = _entries(ledger)
        stages = [e["context"]["stage"] for e in entries]
        assert stages == ["autopilot_granted", "autopilot_execution_result"]
        assert all(e["decision"]["authority"] == "policy:staging-autopilot" for e in entries)


# ── Re-evaluation at execution time ─────────────────────────────────────────


class TestExecuteApproved:
    @pytest.mark.asyncio
    async def test_runs_when_nothing_changed(self, ledger, mock_subprocess):
        from kopilot.executor.approvals import get_approval_store
        from kopilot.executor.middleware import execute_approved

        req = get_approval_store().request(
            "kubectl delete pod x -n staging", "kubectl", "destructive", "high"
        )
        get_approval_store().approve(req.id, decided_by="alice")

        outcome = await execute_approved(req)
        assert outcome.executed
        assert "mocked output" in outcome.output
        result = _by_stage(ledger, "approved_execution_result")[0]
        assert result["decision"]["authority"] == "human:alice"

    @pytest.mark.asyncio
    async def test_refuses_when_the_brake_engaged_after_approval(self, ledger, mock_subprocess):
        from kopilot.executor.approvals import get_approval_store
        from kopilot.executor.autonomy import get_engine
        from kopilot.executor.middleware import execute_approved

        store = get_approval_store()
        req = store.request("kubectl delete pod x -n staging", "kubectl", "destructive", "high")
        store.approve(req.id, decided_by="alice")

        get_engine().set_brake("incident-freeze")
        outcome = await execute_approved(req)

        assert not outcome.executed
        assert "REFUSED" in outcome.output
        assert "incident-freeze" in outcome.output
        assert "mocked output" not in outcome.output

        braked = _by_stage(ledger, "approved_execution_refused")[0]
        assert braked["decision"]["type"] == "braked"
        assert braked["decision"]["authority"] == "policy:incident-freeze"
        assert braked["context"]["approval_id"] == req.id
        assert not _by_stage(ledger, "approved_execution_result")

    @pytest.mark.asyncio
    async def test_refuses_when_safety_now_blocks_the_command(
        self, ledger, mock_subprocess, monkeypatch
    ):
        from kopilot.executor.approvals import get_approval_store
        from kopilot.executor.middleware import execute_approved

        store = get_approval_store()
        req = store.request("kubectl delete pod x -n payments", "kubectl", "destructive", "high")
        store.approve(req.id, decided_by="alice")

        # payments becomes a protected namespace after the approval was granted
        monkeypatch.setenv("SAFETY_PROTECTED_NAMESPACES", '["payments"]')
        import kopilot.config as cfg_mod

        cfg_mod._settings = None

        outcome = await execute_approved(req)
        assert not outcome.executed
        assert "BLOCKED" in outcome.output
        blocked = _by_stage(ledger, "approved_execution_blocked")[0]
        assert blocked["decision"] == {"type": "denied", "authority": "policy:kopilot-safety"}


# ── Identity at the API ─────────────────────────────────────────────────────


@pytest.fixture
def ledger_client(ledger, monkeypatch):
    """Authenticated client whose events land in the temp ledger."""
    from kopilot.inputs.api import create_app

    monkeypatch.setenv("API_AUTH_TOKEN", "sekrit-token")
    import kopilot.config as cfg_mod

    cfg_mod._settings = None
    return TestClient(create_app())


AUTH = {"Authorization": "Bearer sekrit-token"}


class TestApiIdentity:
    def test_approver_comes_from_the_token_not_the_header(
        self, ledger, ledger_client, mock_subprocess
    ):
        from kopilot.executor.approvals import get_approval_store

        req = get_approval_store().request(
            "kubectl delete pod x -n staging", "kubectl", "destructive", "high"
        )
        resp = ledger_client.post(
            f"/approvals/{req.id}/approve",
            headers={**AUTH, "X-Kopilot-Operator": "definitely-the-cto"},
        )
        assert resp.status_code == 200
        body = resp.json()

        assert body["decided_by"].startswith("token:")
        assert body["decided_by"] != "definitely-the-cto"
        assert body["operator_display"] == "definitely-the-cto"
        assert body["operator_display_advisory"] is True

        granted = _by_stage(ledger, "approval_granted")[0]
        assert granted["decision"]["authority"] == authority_for(body["decided_by"])
        assert granted["context"]["operator_display"] == {
            "claimed": "definitely-the-cto",
            "source": "x-kopilot-operator",
            "advisory": True,
        }

    def test_denial_records_the_authenticated_principal(self, ledger, ledger_client):
        from kopilot.executor.approvals import get_approval_store

        req = get_approval_store().request(
            "helm uninstall prod -n prod", "helm", "destructive", "high"
        )
        body = ledger_client.post(
            f"/approvals/{req.id}/deny", headers={**AUTH, "X-Kopilot-Operator": "someone"}
        ).json()
        assert body["decided_by"].startswith("token:")
        assert _by_stage(ledger, "approval_denied")[0]["decision"]["type"] == "denied"

    def test_brake_after_approval_refuses_through_the_api(
        self, ledger, ledger_client, mock_subprocess
    ):
        from kopilot.executor.approvals import get_approval_store
        from kopilot.executor.autonomy import get_engine, reset_engine

        req = get_approval_store().request(
            "kubectl delete pod x -n staging", "kubectl", "destructive", "high"
        )
        get_engine().set_brake("incident-freeze")
        try:
            body = ledger_client.post(f"/approvals/{req.id}/approve", headers=AUTH).json()
        finally:
            reset_engine()

        assert body["executed"] is False
        assert "REFUSED" in body["output"]
        assert _by_stage(ledger, "approved_execution_refused")
        assert verify_chain(ledger.path).ok
