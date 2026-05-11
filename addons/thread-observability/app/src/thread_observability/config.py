"""Configuration helpers for add-on options."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ServiceConfig:
    """Minimal configuration placeholder for service startup."""

    log_level: str = "info"
    timezone: str = "UTC"
