# Network Health Scoring Specification (Draft)

> Working draft for [#122](https://github.com/DarinShapiro/ThreadObservabilityPOC/issues/122). This document defines a deterministic backend-owned scoring model for Thread mesh health. The AI layer should explain these scores, not invent them.

## Purpose

The scoring model must answer four questions consistently:

1. How healthy is each observed link?
2. How resilient is each node?
3. How healthy is the mesh as a whole?
4. Where would one additional mains-powered Thread router improve resilience the most?

The model is intentionally deterministic so that:

- replay fixtures can validate it
- the dashboard can visualize the same facts the AI sees
- the AI can produce auditable recommendations from machine-readable evidence

## Design rules

1. Compute health in the backend. The UI renders it; the AI explains it.
2. Prefer graph resilience over naive averages. A mesh with one excellent link and no alternate path is not healthy.
3. Score routers, end devices, and whole-network health separately.
4. Carry explicit freshness and confidence penalties. Unknown is not healthy.
5. Expose machine-readable reason codes alongside numeric scores.

## Input signals

The scoring model may consume the following deterministic inputs when available:

| Signal | Source | Notes |
|---|---|---|
| `rssi_avg` / `rssi_last` | neighbor links, OTBR router links, event fallback | Normalize to 0..1; stronger is better |
| `lqi_in` / `lqi_out` or Matter `LinkQuality` | neighbor links, route-table links | Treat Matter `LinkQuality` as quantized 0..3 |
| `tx_retry_count` deltas | counter time-series | Prefer rates or deltas over absolute counts |
| frame / message error rates | neighbor links or diagnostics | Optional penalty inputs |
| observation age | timestamps on node/link evidence | Old observations reduce confidence |
| asymmetry | A→B vs B→A RSSI/LQI mismatch | Penalize one-way or unstable links |
| parent changes | counter deltas / events | End-device stability signal, not router health |
| route table / next-hop data | topology snapshot | Required for path diversity and bottleneck analysis |
| partition / split state | topology snapshot | Required for network-wide risk |
| node role / device kind | node enrichment | Routers, REEDs, SEDs, FEDs score differently |

If a signal is missing, the score should degrade through confidence rather than treating the missing value as healthy or zero.

## Normalization

All atomic inputs normalize to 0..1 before weighting.

### RSSI normalization

Clamp RSSI to the range `[-95, -55]` dBm.

$$
RSSI_n = \mathrm{clamp}\left(\frac{rssi + 95}{40}, 0, 1\right)
$$

Default bands:

| RSSI | Band |
|---|---|
| `>= -70` | strong |
| `-85 .. -70` | usable |
| `< -85` | weak |

### LQI normalization

When LQI is 0..3:

$$
LQI_n = \frac{lqi}{3}
$$

When LQI is 0..255:

$$
LQI_n = \mathrm{clamp}\left(\frac{lqi}{255}, 0, 1\right)
$$

Default bands for Matter `LinkQuality`:

| LQI | Band |
|---|---|
| `3` | strong |
| `2` | usable |
| `0..1` | weak |

### Retry normalization

Use deltas or rates over a fixed window, preferably 1 hour. Clamp to a default nuisance ceiling of 20 retries per window.

$$
RetryPenalty_n = \mathrm{clamp}\left(\frac{retry\_delta}{20}, 0, 1\right)
$$

$$
Retry_n = 1 - RetryPenalty_n
$$

### Freshness normalization

Use a hard freshness horizon of 30 minutes for link observations.

$$
Freshness_n = \mathrm{clamp}\left(1 - \frac{age\_seconds}{1800}, 0, 1\right)
$$

If age exceeds 30 minutes, the link enters `unknown_stale` regardless of the last observed RSSI/LQI.

### Symmetry normalization

If both directions are known, penalize large RSSI mismatches.

$$
Symmetry_n = \mathrm{clamp}\left(1 - \frac{|rssi_{ab} - rssi_{ba}|}{20}, 0, 1\right)
$$

If only one direction is known, set `Symmetry_n = 0.5` and attach a low-confidence reason code.

## Edge-quality score

Each observed router-to-router, router-to-parent, or parent-to-child edge receives a normalized quality score.

$$
Q_{edge} = 0.35 \cdot RSSI_n + 0.25 \cdot LQI_n + 0.20 \cdot Retry_n + 0.10 \cdot Freshness_n + 0.10 \cdot Symmetry_n
$$

If retry or symmetry data is unavailable, renormalize the remaining weights rather than forcing missing inputs to zero.

### Edge bands

| Score | Band |
|---|---|
| `>= 0.75` | strong |
| `0.50 .. 0.74` | usable |
| `0.25 .. 0.49` | weak |
| `< 0.25` | critical |
| any stale-only evidence | unknown_stale |

### Edge reason codes

- `WEAK_RSSI`
- `POOR_LQI`
- `HIGH_RETRY_RATE`
- `ASYMMETRIC_LINK`
- `STALE_LINK_DATA`
- `ONE_WAY_LINK_EVIDENCE`

## Router-health score

Router health is primarily about redundancy and alternate paths, not only raw radio strength.

For each router-class node, compute:

- `strong_neighbor_count`: router neighbors with `Q_edge >= 0.75`
- `usable_neighbor_count`: router neighbors with `Q_edge >= 0.50`
- `alternate_path_count`: distinct next hops toward the border-router-adjacent backbone or partition leader with usable quality
- `best_path_quality`: minimum edge quality along the strongest known path to the backbone
- `articulation_risk`: 1 if the node is a graph articulation point, else 0
- `bridge_dependency`: fraction of downstream routers whose best path depends on this node
- `load_headroom`: optional normalized headroom if child/load limits are known

### Router redundancy target

Default policy: a healthy router should have at least **2 strong router neighbors**.

$$
Redundancy_n = \min\left(1, \frac{strong\_neighbor\_count}{2}\right)
$$

For networks with fewer than 3 routers total, degrade gracefully:

$$
TargetStrongNeighbors = \min(2, \max(1, router\_count - 1))
$$

### Router path score

$$
Path_n = 0.60 \cdot best\_path\_quality + 0.40 \cdot \min\left(1, \frac{alternate\_path\_count}{1}\right)
$$

### Router stability score

$$
Stability_n = 0.70 \cdot Retry_n + 0.30 \cdot Freshness_n
$$

### Router bottleneck score

Treat higher bottleneck as worse, then invert it.

$$
Bottleneck_n = 1 - \mathrm{clamp}(0.7 \cdot articulation\_risk + 0.3 \cdot bridge\_dependency, 0, 1)
$$

### Router health formula

$$
H_{router} = 0.40 \cdot Redundancy_n + 0.25 \cdot Path_n + 0.20 \cdot Stability_n + 0.15 \cdot Bottleneck_n
$$

### Router bands

| Score | Band |
|---|---|
| `>= 0.80` | healthy |
| `0.60 .. 0.79` | watch |
| `0.40 .. 0.59` | investigate |
| `< 0.40` | critical |

### Router reason codes

- `LOW_ROUTER_REDUNDANCY`
- `NO_ALTERNATE_PATH`
- `WEAK_BACKBONE_PATH`
- `HIGH_BOTTLENECK_CENTRALITY`
- `ARTICULATION_ROUTER`

## End-device health score

End-device health is parent-centric.

For each SED/FED, compute:

- `parent_edge_quality`
- `parent_stability`: inverse of parent-change churn over 24h
- `parent_router_health`
- `retry_stability`: inverse of retry spikes when available

### Parent stability normalization

$$
ParentStability_n = 1 - \mathrm{clamp}\left(\frac{parent\_change\_delta\_{24h}}{3}, 0, 1\right)
$$

### End-device formula

$$
H_{end} = 0.45 \cdot parent\_edge\_quality + 0.25 \cdot ParentStability_n + 0.20 \cdot parent\_router\_health + 0.10 \cdot Retry_n
$$

### End-device reason codes

- `MARGINAL_PARENT_LINK`
- `PARENT_FLAPPING`
- `FRAGILE_PARENT_ROUTER`
- `STALE_PARENT_EVIDENCE`

## Network-wide health score

Network health should summarize redundancy, path diversity, and structural risk.

Compute:

- `router_redundancy_pct`: fraction of router-class nodes meeting the strong-neighbor target
- `path_diversity_pct`: fraction of router-class nodes with at least one alternate usable path
- `link_quality_avg`: weighted average of important router/router and router/parent edges
- `stability_avg`: average node stability across routers and attached end devices
- `bottleneck_penalty`: normalized penalty from articulation points and bridge concentration
- `partition_penalty`: 0 when one healthy partition, rising toward 1 for splits or near-splits
- `confidence_avg`: average freshness/confidence across the evidence set

$$
H_{network} = 0.30 \cdot RouterRedundancy + 0.20 \cdot PathDiversity + 0.20 \cdot LinkQuality + 0.15 \cdot Stability + 0.10 \cdot (1 - BottleneckPenalty) + 0.05 \cdot (1 - PartitionPenalty)
$$

Confidence should be reported separately and may cap the displayed band:

$$
Confidence = confidence\_avg
$$

If `Confidence < 0.50`, the UI and AI should say the score is low-confidence even if the numeric score is high.

### Network reason codes

- `LOW_NETWORK_REDUNDANCY`
- `WEAK_BRIDGE_EDGE`
- `PARTITION_SPLIT`
- `PARTITION_RISK`
- `OVERCONCENTRATED_ROUTING`
- `LOW_CONFIDENCE_SNAPSHOT`

## Recommendation opportunity scoring

Recommendation ranking is not the same as health scoring. It asks which intervention yields the largest resilience gain.

For each candidate placement or remediation hypothesis, estimate:

- `redundancy_delta`
- `path_diversity_delta`
- `bottleneck_reduction`
- `affected_nodes_count`
- `confidence`

$$
OpportunityScore = 0.35 \cdot redundancy\_delta + 0.30 \cdot path\_diversity\_delta + 0.20 \cdot bottleneck\_reduction + 0.15 \cdot affected\_nodes\_norm
$$

Default recommendation type for routing remediation: **mains-powered Thread router**. Battery end devices should never be recommended as routing improvements.

### Recommendation reason codes

- `ADD_ROUTER_FOR_REDUNDANCY`
- `ADD_ROUTER_FOR_ALTERNATE_PATH`
- `RELIEVE_BOTTLENECK_ROUTER`
- `STABILIZE_END_DEVICE_PARENTING`
- `INSUFFICIENT_CONFIDENCE_FOR_PLACEMENT`

## Confidence rules

Confidence is a first-class output.

Start each node/edge/network confidence at `1.0`, then apply penalties:

- stale link evidence older than 10 minutes: `-0.15`
- stale link evidence older than 30 minutes: cap at `0.25`
- one-way link evidence only: `-0.20`
- missing retry counters for a metric that depends on them: `-0.10`
- topology snapshot older than 10 minutes: `-0.20`
- partition disagreement across sources: `-0.20`

Clamp the final confidence to `[0,1]`.

## Machine-readable output requirements

Every degraded score should emit:

- numeric score
- health band
- confidence
- reason codes
- structured evidence references

Minimum evidence context per reason code:

| Reason code | Required context |
|---|---|
| `LOW_ROUTER_REDUNDANCY` | node id, strong-neighbor count, target count |
| `WEAK_BRIDGE_EDGE` | edge endpoints, edge score, affected downstream nodes |
| `HIGH_BOTTLENECK_CENTRALITY` | node id, bridge dependency, impacted path count |
| `MARGINAL_PARENT_LINK` | child id, parent id, edge quality, parent stability |
| `STALE_LINK_DATA` | edge or node id, age_seconds, last_observed_at |

## Validation scenarios

The scoring harness should include at least these fixtures.

### Scenario A: healthy small mesh

- 3 routers with strong mutual links
- each router has at least 2 strong neighbors
- no splits, low retries, fresh data

Expected:

- all router scores `>= 0.80`
- network score `>= 0.85`
- no critical reason codes

### Scenario B: weak bridge between clusters

- two router clusters connected by one weak edge
- no split yet, but alternate path count is zero across the bridge

Expected:

- bridge edge emits `WEAK_BRIDGE_EDGE`
- at least one router emits `NO_ALTERNATE_PATH`
- network score falls into `investigate`

### Scenario C: articulation router

- one router is the only path between the border-router-adjacent region and downstream nodes

Expected:

- articulation router emits `ARTICULATION_ROUTER`
- opportunity engine recommends adding a mains-powered router in the bridging zone

### Scenario D: healthy end devices on mediocre router mesh

- router redundancy is mediocre
- end devices still have strong, stable parent links

Expected:

- network score is reduced
- end-device scores remain healthy
- AI can distinguish “mesh under-redundant” from “devices currently broken”

### Scenario E: stale-data snapshot

- topology and link data older than freshness horizon

Expected:

- confidence `< 0.50`
- stale reason codes emitted
- UI/AI language must say that current health is low-confidence

## Pseudocode outline

```text
load nodes, links, counters, topology snapshot
normalize signals per edge
compute Q_edge for each observed edge
classify strong / usable / weak / unknown_stale edges

for each router:
  count strong and usable router neighbors
  compute alternate usable paths
  compute best path quality to backbone
  detect articulation / bottleneck risk
  compute H_router

for each end device:
  compute parent edge quality
  compute parent stability
  incorporate parent router health
  compute H_end

aggregate router and edge metrics into H_network
compute confidence and attach reason codes
rank placement candidates by projected score delta
return deterministic facts for UI and AI consumption
```

## Open questions

1. Whether retry normalization should be node-class specific once enough real baselines exist.
2. Whether route concentration should use exact betweenness centrality or a cheaper path-dependency proxy.
3. How aggressively to penalize one-way evidence when OTBR and Matter sources disagree.
4. Whether border routers should have a stricter redundancy target than ordinary routers.

## Companion work

- API contract and example payloads: [#123](https://github.com/DarinShapiro/ThreadObservabilityPOC/issues/123)
- Network-tab hotspot feature using strongest available links: [#104](https://github.com/DarinShapiro/ThreadObservabilityPOC/issues/104)
- Floorplan-aware placement extensions: [#101](https://github.com/DarinShapiro/ThreadObservabilityPOC/issues/101)