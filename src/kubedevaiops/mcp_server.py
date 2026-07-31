"""MCP server surface for Kopilot."""

from __future__ import annotations

import json
import uuid
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from kubedevaiops.agent.memory import TaskContext
from kubedevaiops.agent.supervisor import run_task
from kubedevaiops.interop import (
    PUBLIC_REPO_URL,
    get_agent_manifest,
    get_portable_skill_manifest,
    list_portable_skill_manifests,
)


def create_mcp_server() -> MCPServer:
    """Create an MCP server exposing Kopilot task and skill surfaces."""
    mcp = MCPServer(
        name="Kopilot",
        instructions=(
            "Use Kopilot to inspect Kubernetes environments with "
            "evidence-first, approval-gated workflows. Start with "
            "skill discovery, then run tasks."
        ),
        website_url=PUBLIC_REPO_URL,
    )

    @mcp.tool()
    def agent_manifest() -> dict:
        """Return Kopilot's public interoperability manifest."""
        return get_agent_manifest()

    @mcp.tool()
    def list_portable_skills() -> list[dict]:
        """List enabled Kopilot skills in a portable manifest format."""
        return list_portable_skill_manifests()

    @mcp.tool()
    def get_portable_skill(name: str) -> dict:
        """Return the full portable manifest for one skill."""
        manifest = get_portable_skill_manifest(name)
        if manifest is None:
            raise ToolError(f"Unknown skill '{name}'")
        return manifest

    @mcp.tool()
    async def run_kopilot_task(
        prompt: str,
        reflect: bool = False,
        user: str = "mcp",
    ) -> dict:
        """Run a natural-language Kubernetes task through Kopilot."""
        ctx = TaskContext(task_id=str(uuid.uuid4()), channel="mcp", user=user)
        return await run_task(prompt, context=ctx, reflect=reflect)

    @mcp.resource("kopilot://agent/manifest")
    def agent_manifest_resource() -> str:
        """Expose Kopilot's interop manifest as a resource."""
        return json.dumps(get_agent_manifest(), indent=2)

    @mcp.resource("kopilot://skills")
    def skills_resource() -> str:
        """Expose the portable skill catalog as a resource."""
        return json.dumps(list_portable_skill_manifests(), indent=2)

    @mcp.resource("kopilot://skills/{name}")
    def skill_resource(name: str) -> str:
        """Expose a single portable skill manifest as a resource."""
        manifest = get_portable_skill_manifest(name)
        if manifest is None:
            raise ToolError(f"Unknown skill '{name}'")
        return json.dumps(manifest, indent=2)

    return mcp


def serve_mcp(
    transport: Literal["stdio", "sse", "streamable-http"] = "stdio",
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Run the Kopilot MCP server."""
    server = create_mcp_server()
    if transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(transport=transport, host=host, port=port)
