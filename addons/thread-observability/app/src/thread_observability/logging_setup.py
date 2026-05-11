"""Shared logging configuration for all add-on services.

Sets up a root logger that writes to both stdout (for HA log viewer)
and /data/thread-observability/addon.log (for MCP get_recent_logs tool).
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(os.getenv("THREAD_OBS_DATA_DIR", "/data/thread-observability"))
LOG_FILE = LOG_DIR / "addon.log"
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
MAX_BYTES = 2 * 1024 * 1024  # 2 MB per file
BACKUP_COUNT = 2              # keep addon.log + 2 rotated copies


def configure_logging(service: str, level: str = "info") -> None:
    """Configure root logger with stdout + rotating file handler.

    Call once at process startup before uvicorn takes over.
    """
    numeric = getattr(logging, level.upper(), logging.INFO)

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(numeric)

    # Remove any handlers already attached (e.g. from a previous call)
    root.handlers.clear()

    fmt = logging.Formatter(LOG_FORMAT)

    # Stdout handler — picked up by s6/HA log viewer
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    root.addHandler(stream_handler)

    # Rotating file handler — read by get_recent_logs MCP tool
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    logging.getLogger(service).info(
        "logging initialised — level=%s file=%s", level.upper(), LOG_FILE
    )
