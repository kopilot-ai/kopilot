"""Slack bot integration — routes messages through the supervisor agent."""

from __future__ import annotations

import asyncio
import threading
import uuid

import structlog

from kubedevaiops.config import get_settings
from kubedevaiops.outputs.audit import log_event

logger = structlog.get_logger(__name__)


def start_slack_bot() -> threading.Thread | None:
    cfg = get_settings().slack
    if not cfg.bot_token or not cfg.app_token:
        logger.info("slack.disabled")
        return None

    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    from slack_bolt.async_app import AsyncApp

    app = AsyncApp(token=cfg.bot_token, signing_secret=cfg.signing_secret)

    @app.event("app_mention")
    async def handle_mention(event, say):
        await _handle(event, say)

    @app.event("message")
    async def handle_dm(event, say):
        if event.get("channel_type") == "im":
            await _handle(event, say)

    async def _handle(event: dict, say):
        from kubedevaiops.agent.supervisor import run_task
        from kubedevaiops.agent.memory import TaskContext

        text = event.get("text", "").strip()
        if not text:
            return

        task_id = str(uuid.uuid4())
        ctx = TaskContext(task_id=task_id, channel="slack", user=event.get("user", ""))
        log_event("slack.message", task_id=task_id)

        await say(f"Working on it (`{task_id}`)...")
        try:
            result = await run_task(text, context=ctx)
            await say(result.get("answer", "Done."))
        except Exception as exc:
            await say(f"Error: {exc}")

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        handler = AsyncSocketModeHandler(app, cfg.app_token)
        loop.run_until_complete(handler.start_async())

    t = threading.Thread(target=_run, daemon=True, name="slack-bot")
    t.start()
    logger.info("slack.started")
    return t
