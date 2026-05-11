"""Entrypoints for core and MCP service processes."""

from __future__ import annotations

import argparse

from thread_observability.services.core_service import run_core_service
from thread_observability.services.mcp_service import run_mcp_service


def cli() -> None:
    parser = argparse.ArgumentParser(description="Thread Observability service entrypoint")
    parser.add_argument("--service", choices=["core", "mcp"], required=True)
    args = parser.parse_args()

    if args.service == "core":
        run_core_service()
    else:
        run_mcp_service()


if __name__ == "__main__":
    cli()
