"""OTBR DBus push-event scaffold (Tier 2 #4 — not yet wired).

Today the only way we hear about OTBR partition / role transitions is by
polling. That works, but the cycle is on the order of tens of seconds —
short enough for charts, too long for an operator who is watching a
device drop out in real time.

The OTBR daemon publishes the same transitions as DBus signals on the
host bus (``org.openthread.BorderRouter.<iface>``):

* ``StateChanged``       — overall device state (disabled / detached /
                            child / router / leader)
* ``RoleChanged``        — leader-vs-router transitions
* ``PartitionIdChanged`` — fires the instant the BR re-attaches to a
                            different partition

Subscribing to those signals would give us sub-second event latency for
the partition-split / leader-election scenarios that today only show up
on the next poll cycle.

Why this file is a stub: enabling the subscription requires two changes
that we deliberately defer:

1. ``addons/thread-observability/config.yaml`` must declare
   ``host_dbus: true`` to receive the host bus. That elevates the
   add-on's privileges and is worth a separate review.
2. ``requirements.txt`` must add ``dbus_next`` (a pure-Python asyncio
   DBus client). Light dependency, but new.

Until both land, ``start_dbus_listener`` is a no-op. The
``enable_otbr_dbus_push`` config flag is wired through so an operator
can flip it on once the prerequisites are in place; today flipping it
just changes which log message we emit.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


async def start_dbus_listener(config: Any, store: Any) -> None:
    """Start (or stub-out) the OTBR DBus signal subscription.

    ``config`` is the ``ThreadObsConfig`` instance; ``store`` is the
    ``SQLiteStore`` that real signal handlers would write events into.
    Both unused today.

    Returns immediately. When the real implementation lands it will
    instead spawn a long-lived background task; the caller (the
    pipeline scheduler) should treat the return value as "fire and
    forget" either way.
    """
    enabled = bool(getattr(config, "enable_otbr_dbus_push", False))
    if not enabled:
        log.debug("otbr_dbus: push disabled by config; relying on poll")
        return
    log.warning(
        "otbr_dbus: enable_otbr_dbus_push is set, but the DBus listener is "
        "not implemented yet — config.yaml needs host_dbus:true and "
        "dbus_next must be installed. Continuing without push events."
    )
