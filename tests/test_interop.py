"""Tests for agent interoperability helpers."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from kopilot.interop import (  # type: ignore[import-untyped]
    get_agent_manifest,
    get_portable_skill_manifest,
    list_portable_skill_manifests,
)
from kopilot.mcp_server import create_mcp_server  # type: ignore[import-untyped]


def test_portable_skill_manifest_includes_safety_and_distribution():
    manifest = get_portable_skill_manifest("cost_optimization")

    assert manifest is not None
    assert manifest["distribution"]["current_source"] == "builtin_yaml"
    assert manifest["safety"]["approval_gated_destructive"] is True
    assert "system_prompt" in manifest["prompting"]


def test_agent_manifest_exposes_mcp_and_agent_bridge():
    manifest = get_agent_manifest()

    assert manifest["protocols"]["mcp"]["status"] == "available"
    assert manifest["protocols"]["portable_skills"]["status"] == "available"
    assert manifest["protocols"]["agent_to_agent"]["status"] == "planned"


def test_portable_skill_list_contains_enabled_skills():
    names = {skill["name"] for skill in list_portable_skill_manifests()}

    assert "cost_optimization" in names
    assert "security" in names


def test_create_mcp_server_returns_mcpserver():
    server = create_mcp_server()

    assert isinstance(server, MCPServer)
