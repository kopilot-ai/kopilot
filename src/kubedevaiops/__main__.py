"""CLI entry point for Kopilot."""

from __future__ import annotations

import asyncio
import threading
from typing import Literal, cast

import click
import structlog
import uvicorn

from kubedevaiops.config import get_settings


def _configure_logging() -> None:
    cfg = get_settings().observability
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            (
                structlog.processors.JSONRenderer()
                if cfg.log_format == "json"
                else structlog.dev.ConsoleRenderer()
            ),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.PrintLoggerFactory(),
    )


@click.group()
def cli():
    """Kopilot - Autonomous AI Kubernetes operations agent."""
    _configure_logging()


@cli.command()
@click.option("--host", default=None)
@click.option("--port", default=None, type=int)
def serve(host: str | None, port: int | None):
    """Start the full agent: API + operator + event watcher + Slack."""
    cfg = get_settings()
    h, p = host or cfg.api.host, port or cfg.api.port

    logger = structlog.get_logger("kubedevaiops")
    logger.info("starting", mode="full", host=h, port=p, skills=cfg.enabled_skills)

    _start_operator_thread()

    from kubedevaiops.inputs.slack import start_slack_bot
    start_slack_bot()

    # The event watcher is started inside the app lifespan so it runs on
    # uvicorn's event loop (a separate asyncio.run() would close its loop
    # and kill the watcher before the server starts).
    from kubedevaiops.inputs.api import create_app
    uvicorn.run(create_app(with_event_watcher=True), host=h, port=p, log_level="info")


@cli.command()
def operator():
    """Run only the Kopf operator."""
    import kopf

    import kubedevaiops.operator.handlers  # noqa: F401 — registers Kopf decorators

    kopf.run(clusterwide=True)


@cli.command()
@click.option("--host", default=None)
@click.option("--port", default=None, type=int)
def api(host: str | None, port: int | None):
    """Run only the REST API server."""
    cfg = get_settings()
    from kubedevaiops.inputs.api import create_app
    uvicorn.run(
        create_app(),
        host=host or cfg.api.host,
        port=port or cfg.api.port,
        log_level="info",
    )


@cli.command()
@click.argument("prompt")
def ask(prompt: str):
    """One-shot: ask the agent a question."""
    async def _ask():
        from kubedevaiops.agent.supervisor import run_task
        result = await run_task(prompt)
        click.echo(f"\n{result.get('answer', 'No response.')}\n")

    asyncio.run(_ask())


@cli.command(name="mcp")
@click.option(
    "--transport",
    default="stdio",
    type=click.Choice(["stdio", "sse", "streamable-http"]),
)
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8000, type=int)
def mcp_server(transport: str, host: str, port: int):
    """Run Kopilot as an MCP server."""
    from kubedevaiops.mcp_server import serve_mcp

    selected_transport = cast(
        Literal["stdio", "sse", "streamable-http"],
        transport,
    )
    serve_mcp(transport=selected_transport, host=host, port=port)


def _start_operator_thread() -> threading.Thread:
    import kopf

    import kubedevaiops.operator.handlers  # noqa: F401 — registers Kopf decorators

    t = threading.Thread(target=lambda: kopf.run(clusterwide=True), daemon=True, name="kopf")
    t.start()
    return t


if __name__ == "__main__":
    cli()
