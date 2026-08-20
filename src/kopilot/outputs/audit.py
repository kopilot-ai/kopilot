"""Structured audit trail.

Every significant agent action (task created, plan approved, command executed,
etc.) is recorded here for compliance and observability.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

audit_logger = structlog.get_logger("kopilot.audit")


def log_event(event_type: str, **kwargs: Any) -> None:
    """Write an audit entry with ISO-8601 timestamp."""
    audit_logger.info(
        event_type,
        timestamp=datetime.now(UTC).isoformat(),
        **kwargs,
    )
