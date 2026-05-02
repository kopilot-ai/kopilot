"""Public surfaces for MCP, portable skills, and agent discovery."""

from __future__ import annotations

from typing import Any

from kubedevaiops.config import get_settings
from kubedevaiops.skills.loader import SkillDefinition, discover_skills

PRODUCT_VERSION = "0.1.0"
PUBLIC_REPO_URL = "https://github.com/kopilot-ai/kopilot"
PUBLIC_SITE_URL = "https://kopilot-ai.github.io"


def _source_kind(defn: SkillDefinition) -> str:
    source = defn.source.lower()
    if "builtin" in source:
        return "builtin_yaml"
    return "external_yaml"


def _skill_summary(defn: SkillDefinition) -> dict[str, Any]:
    settings = get_settings()
    return {
        "name": defn.name,
        "display_name": defn.display_name,
        "description": defn.description,
        "category": defn.category,
        "distribution": {
            "format": "yaml-skill",
            "current_source": _source_kind(defn),
            "planned_sources": ["aiskill_crd"],
        },
        "execution": {
            "mode": "delegated_subagent",
            "tools": [
                "run_kubectl",
                "run_helm",
                "run_shell",
                "read_resource",
            ],
        },
        "safety": {
            "read_first": True,
            "approval_gated_destructive": (
                settings.safety.require_approval_destructive
            ),
            "protected_namespaces": settings.safety.protected_namespaces,
        },
    }


def list_portable_skill_manifests() -> list[dict[str, Any]]:
    """Return a portable summary for every enabled skill."""
    return [_skill_summary(defn) for defn in discover_skills()]


def get_portable_skill_manifest(name: str) -> dict[str, Any] | None:
    """Return a portable manifest for a single enabled skill."""
    for defn in discover_skills():
        if defn.name != name:
            continue
        manifest = _skill_summary(defn)
        manifest["prompting"] = {
            "system_prompt": defn.system_prompt,
            "documentation": defn.documentation,
        }
        return manifest
    return None


def get_agent_manifest() -> dict[str, Any]:
    """Describe Kopilot's public interop surfaces."""
    settings = get_settings()
    portable_skills = list_portable_skill_manifests()

    return {
        "product": {
            "name": "Kopilot",
            "package_name": "kubedevaiops",
            "version": PRODUCT_VERSION,
            "repository_url": PUBLIC_REPO_URL,
            "website_url": PUBLIC_SITE_URL,
        },
        "task_api": {
            "status": "available",
            "style": "async_http_task_api",
            "submit_endpoint": "/tasks",
            "history_endpoint": "/tasks/history",
            "supports_reflection": True,
            "metadata_field": "metadata",
        },
        "protocols": {
            "mcp": {
                "status": "available",
                "command": ["kopilot", "mcp"],
                "transports": ["stdio", "sse", "streamable-http"],
                "tool_names": [
                    "agent_manifest",
                    "list_portable_skills",
                    "get_portable_skill",
                    "run_kopilot_task",
                ],
                "resource_uris": [
                    "kopilot://agent/manifest",
                    "kopilot://skills",
                    "kopilot://skills/{name}",
                ],
            },
            "portable_skills": {
                "status": "available",
                "list_endpoint": "/skills/portable",
                "detail_endpoint": "/skills/portable/{name}",
                "current_sources": ["builtin_yaml", "external_yaml"],
                "planned_sources": ["aiskill_crd"],
            },
            "agent_to_agent": {
                "status": "planned",
                "current_bridge": {
                    "manifest_endpoint": "/.well-known/agent-manifest.json",
                    "async_task_submit": "/tasks",
                },
                "notes": (
                    "ACP has converged into the broader agent-to-agent layer. "
                    "Kopilot ships an async task API and discovery "
                    "manifest now, while fuller agent-to-agent "
                    "protocol support remains a follow-up."
                ),
            },
        },
        "skills": {
            "enabled": [skill["name"] for skill in portable_skills],
            "count": len(portable_skills),
        },
        "safety": {
            "read_first": True,
            "approval_gated_destructive": (
                settings.safety.require_approval_destructive
            ),
            "protected_namespaces": settings.safety.protected_namespaces,
        },
    }
