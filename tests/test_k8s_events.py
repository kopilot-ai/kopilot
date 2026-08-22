"""The Kubernetes event watcher is opt-in.

Event reasons and messages are attacker-controllable text that the watcher
turns into an LLM prompt, so it must not start unless someone asked for it.
"""

from __future__ import annotations

import pytest

from kopilot.inputs.k8s_events import K8sEventWatcher


@pytest.mark.asyncio
async def test_watcher_does_not_start_by_default(monkeypatch):
    called = False

    def _fail_load():
        nonlocal called
        called = True

    monkeypatch.setattr(K8sEventWatcher, "_load_config", staticmethod(_fail_load))

    watcher = K8sEventWatcher()
    await watcher.start()

    assert not called, "watcher loaded kube config despite being disabled"
    assert watcher._running is False
    assert watcher._watch_thread is None
    assert watcher._consumer_task is None


@pytest.mark.asyncio
async def test_stop_is_a_no_op_when_never_started():
    await K8sEventWatcher().stop()


@pytest.mark.asyncio
async def test_watcher_starts_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("WATCHERS_K8S_EVENTS_ENABLED", "true")
    import kopilot.config as cfg_mod
    cfg_mod._settings = None

    monkeypatch.setattr(K8sEventWatcher, "_load_config", staticmethod(lambda: None))

    watcher = K8sEventWatcher()
    monkeypatch.setattr(watcher, "_watch_blocking", lambda loop: None)
    await watcher.start()
    try:
        assert watcher._running is True
        assert watcher._consumer_task is not None
    finally:
        await watcher.stop()
