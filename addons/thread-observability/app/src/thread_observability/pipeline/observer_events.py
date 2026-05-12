"""Observer-side disruption tracking (Tier 3).

Records restart / outage windows for the *ingestion stack* — this
add-on plus the upstream add-ons our pipeline depends on — so the
reasoner can annotate (and downgrade) issues that fire while we were
temporarily blind.

The model: every disruption is a row in ``observer_events`` with a
``started_at`` and an optional ``ended_at``. The reasoner queries for
events overlapping a candidate issue's trigger window and, if it
finds any, attaches a ``suppressed_by`` annotation to the issue's
evidence and reduces severity. We deliberately *annotate, not drop* —
a real outage that happens to coincide with a routine restart still
deserves a record, just one with extra context.

What we track today:

* ``addon:self``                                — this add-on
  (recorded on every cold start; the single biggest source of
  self-inflicted false positives)
* ``addon:core_openthread_border_router``       — OTBR
* ``addon:core_matter_server``                  — Matter Server

What we do NOT track yet (and why):

* HA Core reloads — too frequent and don't directly affect our
  pipeline.
* NetworkManager IPv6 / journald events — require host journal
  access (``host_dbus: true`` and friends); same privilege
  escalation as the deferred Tier 2 #4 DBus push.
* BBR transitions via ``ot-ctl`` — requires shelling into the OTBR
  container, which Supervisor doesn't expose. Only relevant in
  multi-BR deployments.

Polling strategy: per-tick, GET ``/addons/<slug>/info`` for each
tracked slug and compare the ``state`` field against the value we
saw last tick. A transition out of ``started`` opens an event;
a transition back into ``started`` closes it.
"""

from __future__ import annotations

import logging
from typing import Any

from ..storage.sqlite_store import SQLiteStore

log = logging.getLogger(__name__)

# Slugs we monitor. ``self`` is special-cased to the
# Supervisor's ``/addons/self/info`` alias.
TRACKED_SLUGS: tuple[str, ...] = (
    "self",
    "core_openthread_border_router",
    "core_matter_server",
)

# In-memory cache of the last observed (state, open_event_id) per slug.
# Process-local; survives across poll ticks but not addon restarts —
# which is fine, because the *next* poll after a restart will see the
# "started" state and not falsely open a fresh event.
_last_state: dict[str, str] = {}
_open_event_ids: dict[str, int] = {}


def record_self_start(store: SQLiteStore, *, version: str | None = None) -> int:
    """Record an instantaneous ``addon:self`` start event.

    Called once at process startup. ``started_at`` and ``ended_at`` are
    both set to the current time — this is a point-in-time marker
    rather than a window, so the reasoner only considers it for the
    immediate post-start suppression grace period. Returns the row id.

    This is intentionally cheap and synchronous; it must succeed even
    if the Supervisor API is unreachable.
    """
    from datetime import UTC, datetime  # local import to avoid module-load cost

    now = datetime.now(tz=UTC).isoformat()
    return store.insert_observer_event(
        source="addon:self",
        kind="start",
        started_at=now,
        ended_at=now,
        details={"version": version} if version else None,
    )


async def poll_supervisor_addons(store: SQLiteStore) -> dict[str, int]:
    """Poll Supervisor for the state of each tracked add-on.

    Compares each slug's current ``state`` against the value we cached
    last tick. State transitions:

    * any → ``started``  : closes the open event (if we have one).
    * ``started`` → other: opens a new ``outage`` event.
    * first observation  : just caches; no event.

    Returns a small summary dict for logging. Safe to call when the
    Supervisor API is unreachable — we just return zeros.
    """
    # Import lazily so unit tests that don't exercise the supervisor
    # path don't need httpx mocking.
    from ..api import supervisor_client

    summary = {"polled": 0, "opened": 0, "closed": 0, "errors": 0}
    for slug in TRACKED_SLUGS:
        summary["polled"] += 1
        try:
            info = await supervisor_client._get_json(  # noqa: SLF001
                f"/addons/{slug}/info"
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("observer_events: %s info fetch failed: %s", slug, exc)
            summary["errors"] += 1
            continue
        state = str(info.get("state") or "").lower() or "unknown"
        prev = _last_state.get(slug)
        _last_state[slug] = state

        source = f"addon:{slug}" if slug != "self" else "addon:self"
        if prev is None:
            # First observation: cache only.
            continue
        if prev == state:
            continue

        if state == "started":
            open_id = _open_event_ids.pop(slug, None)
            if open_id is not None:
                store.close_observer_event(open_id)
                summary["closed"] += 1
        elif prev == "started":
            # Just left the healthy state. Open an outage window.
            event_id = store.insert_observer_event(
                source=source,
                kind="outage",
                details={"prev_state": prev, "new_state": state},
            )
            if event_id:
                _open_event_ids[slug] = event_id
                summary["opened"] += 1
    return summary


def _reset_state_for_tests() -> None:
    """Test helper — clear the module-level state caches."""
    _last_state.clear()
    _open_event_ids.clear()
