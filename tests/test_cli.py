"""Tests for the public CLI contract."""

from __future__ import annotations

import tomllib
from pathlib import Path

from click.testing import CliRunner

from kopilot.__main__ import cli


def test_cli_help_uses_kopilot_branding():
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "Kopilot - Autonomous AI Kubernetes operations agent." in result.output
    assert "ask" in result.output
    assert "mcp" in result.output
    assert "serve" in result.output


def test_pyproject_exposes_public_and_legacy_entrypoints():
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]

    assert scripts["kopilot"] == "kopilot.__main__:cli"
    assert scripts["kopilot"] == scripts["kopilot"]


def test_mcp_command_help():
    result = CliRunner().invoke(cli, ["mcp", "--help"])

    assert result.exit_code == 0
    assert "Run Kopilot as an MCP server." in result.output
    assert "stdio" in result.output
