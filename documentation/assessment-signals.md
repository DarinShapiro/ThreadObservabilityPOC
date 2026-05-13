# Thread network assessment signals

Working catalog of conditions the **Background Diagnostics** feature
(see [07-agentic-ai-sprint.md §11](07-agentic-ai-sprint.md)) should
investigate. This is both:

1. A reference for the AI agent — surfaced via MCP resources so each
   tool description (#16) can link relevant signals.
2. A working knowledge base that grows as we troubleshoot more real
   Thread networks.

Severity is free-form for now (`watch`, `investigate`, `critical`).
A signal can move severities based on duration / repetition.

---

## Critical (likely outages)

| Signal | Detection | Why it matters |
|---|---|---|
| OTBR unresponsive | `ha_get_addon_state` reports OTBR add-on not running, OR our last successful OTBR call > 5 min ago | Border router down = no Thread off-mesh routing |
| Data age > 30 min | `get_health_snapshot.data_age_seconds > 1800` | Pipeline isn't running; everything below is stale |
| All routers offline | `node_counts` shows zero healthy routers | Mesh is effectively dead |
| Matter Server unreachable | Matter integration health probe fails | Thread devices may keep working but Matter control plane is broken |

## Investigate (real anomalies, agent should look)

| Signal | Detection | Notes |
|---|---|---|
| **Poor link quality** | RSSI ≤ -85 dBm sustained over multiple ticks, or LQI ≤ 2 | Threshold vendor-dependent; document deltas not absolutes when possible |
| **Parent flapping** | `parent_change_count` delta ≥ 3 in 1 h on a SED/MED | Symptom of weak signal, interference, or REED churn |
| **Retry storms** | `mac_tx_retry_count` delta spikes ≥ 4× the node's 24-h baseline | Often correlates with parent flapping |
| **Stale routing entries** | Neighbor or route_table rows whose `last_seen` hasn't updated in > 3 ticks while the reporter is healthy | Routing table desync, sometimes a hint at a half-dead router |
| **mDNS / SRP issues** | Matter advertises a node but our discovery hasn't seen it in N ticks; OR SRP server reports clients with stale leases | Often the user-visible symptom is "device disappeared from Home Assistant" |
| **Partition split** | `distinct_thread_networks > 1` for > 5 min | Split-brain. Could be intentional (separate networks) but usually isn't |
| **Phantom router** | A neighbor reports a router we never directly discovered | Routing topology disagrees with our discovery |
| **Network forming churn** | Leader EUI64 changed > 2× in 24 h | Election instability |
| **Counter rollover anomalies** | Negative deltas after rollover-correction not matching expected counter width | Possible vendor counter bug — log, don't auto-alert |

## Watch (might matter, don't pester)

| Signal | Detection | Notes |
|---|---|---|
| Single retransmission spike | One-tick spike that doesn't sustain | Could be transient interference |
| New EUI64 ever seen | First-time discovery of a node | Informational unless it correlates with a Matter pairing the user just did |
| Single phantom report | One reporter sees a phantom router exactly once | Wait for a second sighting before promoting |
| Duplicate physical device groups change | `duplicate_physical_device_groups` count drifts | Usually benign (re-pairings); investigate only on growth trend |

## Not actionable (don't surface)

- Steady-state counter growth that matches baseline.
- Brief data-age spikes during pipeline tick boundary.
- RSSI/LQI variance within ±5 of baseline.

---

## Notes for the AI agent

- Always ground a verdict in **at least two pieces of evidence** — a
  single counter delta isn't enough. The verdict envelope reserves
  the `evidence` array for this reason.
- Prefer **deltas over absolutes** when a node has baseline data.
  Vendors and use cases differ wildly; absolute RSSI thresholds
  punish FED-heavy installs.
- When uncertain, return `verdict: "watch"` with a clear headline
  and let the next assessment confirm or clear. False
  `investigate` verdicts are more costly than false `watch`.
- Reference this document via the per-tool background block from
  #16 when describing what fields like `parent_change_count` mean.
