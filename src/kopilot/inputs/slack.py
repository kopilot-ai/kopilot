"""Slack bot integration — routes messages through the supervisor agent."""

from __future__ import annotations

import asyncio
import threading
import uuid

import structlog

from kopilot.config import get_settings
from kopilot.outputs.audit import log_event

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
        from kopilot.agent.memory import TaskContext
        from kopilot.agent.supervisor import run_task

        text = event.get("text", "").strip()
        if not text:
            return

        user = event.get("user", "")
        if cfg.allowed_users and user not in cfg.allowed_users:
            log_event("slack.denied", user=user)
            await say("Sorry, you are not authorised to run Kopilot tasks.")
            return

        task_id = str(uuid.uuid4())
        ctx = TaskContext(task_id=task_id, channel="slack", user=user)
        log_event("slack.message", task_id=task_id, user=user)

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
