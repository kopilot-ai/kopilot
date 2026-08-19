"""SQLite persistence for the approval store.

The store keeps its in-memory semantics; when constructed with a db_path it
journals every transition to SQLite and reloads state on startup, so pending
approvals survive a process restart.
"""

from __future__ import annotations

import time

from kubedevaiops.executor.approvals import (
    ApprovalStatus,
    ApprovalStore,
)


def _db(tmp_path):
    return str(tmp_path / "approvals.db")


class TestSQLitePersistence:
    def test_pending_request_survives_restart(self, tmp_path):
        path = _db(tmp_path)
        store = ApprovalStore(db_path=path)
        req = store.request("kubectl delete pod x -n staging", "run_kubectl", "destructive", "HIGH")

        reloaded = ApprovalStore(db_path=path)
        got = reloaded.get(req.id)
        assert got is not None
        assert got.status is ApprovalStatus.PENDING
        assert got.command == "kubectl delete pod x -n staging"
        assert got.tool == "run_kubectl"
        assert got.risk == "HIGH"

    def test_approval_survives_restart_and_is_consumable(self, tmp_path):
        path = _db(tmp_path)
        store = ApprovalStore(db_path=path)
        req = store.request("kubectl delete pod x", "run_kubectl", "destructive", "HIGH")
        assert store.approve(req.id, decided_by="alice") is not None

        reloaded = ApprovalStore(db_path=path)
        consumed = reloaded.consume_if_approved("kubectl delete pod x")
        assert consumed is not None
        assert consumed.id == req.id
        assert consumed.decided_by == "alice"

        # consumption is itself persisted
        third = ApprovalStore(db_path=path)
        assert third.consume_if_approved("kubectl delete pod x") is None
        assert third.get(req.id).status is ApprovalStatus.CONSUMED

    def test_denial_survives_restart(self, tmp_path):
        path = _db(tmp_path)
        store = ApprovalStore(db_path=path)
        req = store.request("kubectl drain node1", "run_kubectl", "destructive", "HIGH")
        store.deny(req.id, decided_by="bob")

        reloaded = ApprovalStore(db_path=path)
        assert reloaded.get(req.id).status is ApprovalStatus.DENIED
        assert reloaded.consume_if_approved("kubectl drain node1") is None

    def test_stale_pending_expires_after_reload(self, tmp_path):
        path = _db(tmp_path)
        store = ApprovalStore(ttl=0.05, db_path=path)
        req = store.request("kubectl delete ns qa", "run_kubectl", "destructive", "HIGH")
        time.sleep(0.1)

        reloaded = ApprovalStore(ttl=0.05, db_path=path)
        got = reloaded.get(req.id)
        assert got is None or got.status is ApprovalStatus.EXPIRED

    def test_settled_requests_are_pruned_from_disk(self, tmp_path):
        path = _db(tmp_path)
        store = ApprovalStore(ttl=0.01, db_path=path)
        req = store.request("kubectl delete pod y", "run_kubectl", "destructive", "HIGH")
        store.deny(req.id)
        time.sleep(0.1)
        store.list()  # triggers expiry + prune past retention window

        reloaded = ApprovalStore(ttl=0.01, db_path=path)
        assert reloaded.get(req.id) is None

    def test_memory_only_store_unchanged_without_path(self):
        store = ApprovalStore()
        req = store.request("kubectl delete pod z", "run_kubectl", "destructive", "HIGH")
        fresh = ApprovalStore()
        assert fresh.get(req.id) is None
