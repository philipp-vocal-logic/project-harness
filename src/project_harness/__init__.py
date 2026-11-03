"""Zero-noise CLI project harness enforcing .policy rules and Git TDD workflows."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from project_harness.model import DEFAULT_ACTIVE_STATUS


def trace(
    *targets: str,
    id: str | None = None,
    status: str = DEFAULT_ACTIVE_STATUS,
    links: Sequence[str] = (),
) -> Callable[[Any], Any]:
    """Annotate a test function with traceability links and optional node id or status."""
    del targets, id, status, links
    return lambda func: func


__all__ = ["trace"]
