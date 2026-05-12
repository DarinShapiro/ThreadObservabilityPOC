"""OTBR ``MGMT_DIAG_GET`` second-witness polling.

This module is the active half of Tier 2 #1: every cycle we ask the
Border Router to send a CoAP diagnostic-get to each known router and
record the answer as an independent witness alongside the router's own
Matter cluster-53 self-report.

Why bother when cluster 53 already gives us MAC counters? Because the
cluster 53 numbers come from the router itself — they're useless for
detecting a router that is failing to forward or is silently lying
about its frame counters. The OTBR's view of the same router is a
second observation point: divergence between the two readings is the
signal that the mesh and the device disagree about reality.

The signal we surface (reasoner rule ``mesh_disagreement``) is a
percentage delta on the cumulative MAC TX counter between successive
ticks. We persist every snapshot so an operator can replay history.

This module is opt-in via ``ThreadObsConfig.enable_otbr_diagnostics`` —
each call to the BR generates CoAP traffic on the mesh and adds load
proportional to the router count.
"""

from __future__ import annotations

import logging
from typing import Any

from ..storage.sqlite_store import SQLiteStore
from . import otbr_rest

log = logging.getLogger(__name__)


def _coerce_int(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return int(s, 16) if s.lower().startswith("0x") else int(s)
        except ValueError:
            return None
    return None


def _extract_mac_counters(payload: dict[str, Any]) -> dict[str, int | None]:
    """Pull MAC counter fields out of a ``/diagnostics`` response.

    The OTBR REST wrapper renders TLV 17 (MacCounters) as a nested
    object. Field names vary across OTBR versions, so we accept several
    spellings. Missing fields stay ``None``.
    """
    mc = (
        payload.get("MacCounters")
        or payload.get("mac_counters")
        or payload.get("macCounters")
        or {}
    )
    if not isinstance(mc, dict):
        return {k: None for k in (
            "tx_total", "tx_retry", "tx_err",
            "rx_total", "rx_err", "rx_dup",
        )}
    return {
        "tx_total": _coerce_int(
            mc.get("IfOutUcastPkts") or mc.get("tx_total") or mc.get("TxTotal")
        ),
        "tx_retry": _coerce_int(
            mc.get("IfOutRetries") or mc.get("tx_retry") or mc.get("TxRetry")
        ),
        "tx_err": _coerce_int(
            mc.get("IfOutErrors") or mc.get("tx_err") or mc.get("TxErrAbort")
        ),
        "rx_total": _coerce_int(
            mc.get("IfInUcastPkts") or mc.get("rx_total") or mc.get("RxTotal")
        ),
        "rx_err": _coerce_int(
            mc.get("IfInErrors") or mc.get("rx_err") or mc.get("RxErrNoFrame")
        ),
        "rx_dup": _coerce_int(
            mc.get("IfInDup") or mc.get("rx_dup") or mc.get("RxDuplicated")
        ),
    }


def _extract_child_table(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    ct = (
        payload.get("ChildTable")
        or payload.get("child_table")
        or payload.get("childTable")
    )
    if isinstance(ct, list):
        return [c for c in ct if isinstance(c, dict)]
    return None


async def poll_otbr_diagnostics(
    store: SQLiteStore,
    base_url: str,
    *,
    partition_id: int | None = None,
) -> dict[str, int]:
    """Poll ``MGMT_DIAG_GET`` for every router in the partition.

    Returns ``{routers_polled, snapshots_recorded, fetch_errors}``.
    Safe to call when the BR is unreachable — we just return zeros.

    The router list is pulled fresh each call from ``/node/routers``
    rather than from our store so this module can run before / without
    our discovery pass having seen the router yet.
    """
    summary = {"routers_polled": 0, "snapshots_recorded": 0, "fetch_errors": 0}
    routers = await otbr_rest.fetch_otbr_routers(base_url)
    if not routers:
        return summary

    for router in routers:
        eui = otbr_rest._otbr_eui_from(router)
        rloc16_raw = (
            router.get("Rloc16")
            or router.get("rloc16")
            or router.get("RLOC16")
        )
        rloc16 = _coerce_int(rloc16_raw)
        if not eui or rloc16 is None:
            continue
        summary["routers_polled"] += 1
        diag = await otbr_rest.fetch_otbr_diagnostics(base_url, rloc16)
        if diag is None:
            summary["fetch_errors"] += 1
            continue
        macs = _extract_mac_counters(diag)
        try:
            store.insert_otbr_diagnostic(
                target_eui64=eui,
                target_rloc16=rloc16,
                partition_id=partition_id,
                mac_tx_total=macs["tx_total"],
                mac_tx_retry=macs["tx_retry"],
                mac_tx_err=macs["tx_err"],
                mac_rx_total=macs["rx_total"],
                mac_rx_err=macs["rx_err"],
                mac_rx_dup=macs["rx_dup"],
                mle_counters=None,
                child_table=_extract_child_table(diag),
                extra=diag,
            )
            summary["snapshots_recorded"] += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("otbr_diagnostics: persist failed for %s: %s", eui, exc)
            summary["fetch_errors"] += 1
    return summary
