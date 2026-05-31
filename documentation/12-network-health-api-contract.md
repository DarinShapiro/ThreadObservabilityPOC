# Network Health API Contract

> This contract is now implemented for read-only HTTP routes under [#123](https://github.com/DarinShapiro/ThreadObservabilityPOC/issues/123). The frontend should render these facts. The AI should explain them.

## Purpose

Define a stable payload shape for:

1. `network_health`
2. `placement_candidates`

The contract is designed to support:

- dashboard summary cards
- topology overlays and ranked findings
- AI explanations and remediation suggestions
- replay fixtures and acceptance tests
- future floorplan-aware extensions without breaking field names

## Live routes

- `GET /v1/network/health` returns the `network_health` payload.
- `GET /v1/network/placement-candidates` returns the placement-candidate wrapper payload.

## Live MCP tools

- `get_network_health` returns the same deterministic `network_health` payload exposed by `GET /v1/network/health`.
- `get_placement_candidates` returns the same placement-candidate wrapper payload exposed by `GET /v1/network/placement-candidates`.

The internal builder currently computes both payloads together. The HTTP layer publishes them as two separate route shapes so clients do not need to strip embedded placement data from `network_health` responses.

## Contract rules

1. Deterministic computed facts and human-facing explanation text must be separate.
2. Unknown and stale states must be represented explicitly.
3. Reason codes must be machine-readable and carry structured evidence.
4. Backend owns transformation and scoring logic; the UI should not derive health metrics.
5. Field names should be stable enough for direct prompt consumption.

## `network_health` shape

```json
{
  "computed_at": "2026-05-30T22:15:00Z",
  "as_of": "2026-05-30T22:14:42Z",
  "score": 0.71,
  "band": "watch",
  "confidence": 0.86,
  "summary": {
    "router_count": 4,
    "end_device_count": 11,
    "strong_router_target": 2,
    "router_redundancy_pct": 0.5,
    "path_diversity_pct": 0.5,
    "distinct_partitions": 1,
    "data_freshness_seconds": 18
  },
  "component_scores": {
    "router_redundancy": 0.5,
    "path_diversity": 0.52,
    "link_quality": 0.83,
    "stability": 0.74,
    "bottleneck_penalty": 0.38,
    "partition_penalty": 0.0
  },
  "reason_codes": [
    "LOW_NETWORK_REDUNDANCY"
  ],
  "nodes": [],
  "edges": [],
  "findings": []
}
```

## Required top-level fields

| Field | Type | Notes |
|---|---|---|
| `computed_at` | ISO timestamp | When this payload was computed |
| `as_of` | ISO timestamp | Freshest evidence timestamp feeding the score |
| `score` | float 0..1 | Overall network score |
| `band` | enum | `healthy`, `watch`, `investigate`, `critical`, or `unknown_stale` |
| `confidence` | float 0..1 | Confidence in the score |
| `summary` | object | Fleet counts and high-level ratios |
| `component_scores` | object | Deterministic sub-scores |
| `reason_codes` | string[] | Network-level degradations |
| `nodes` | object[] | Node-level health records |
| `edges` | object[] | Important edge health records |
| `findings` | object[] | Ranked explanations or risk statements |

## Node record

Each node in `nodes` should follow this shape.

```json
{
  "eui64": "1122334455667788",
  "friendly_name": "Hallway Router",
  "role": "router",
  "device_kind": "router",
  "score": 0.43,
  "band": "investigate",
  "confidence": 0.91,
  "reason_codes": [
    "LOW_ROUTER_REDUNDANCY",
    "NO_ALTERNATE_PATH"
  ],
  "metrics": {
    "strong_neighbor_count": 1,
    "usable_neighbor_count": 2,
    "alternate_path_count": 0,
    "best_path_quality": 0.46,
    "parent_change_delta_24h": null,
    "retry_delta_1h": 4,
    "articulation_risk": true,
    "bridge_dependency": 0.78
  },
  "evidence": [
    {
      "reason_code": "LOW_ROUTER_REDUNDANCY",
      "details": {
        "strong_neighbor_count": 1,
        "target": 2
      }
    }
  ]
}
```

### Node rules

- Router-class nodes must expose neighbor/path/bottleneck metrics.
- End devices must expose parent metrics instead of router redundancy fields.
- Null is preferred over fake zero when a field is not applicable.
- A router should never pretend to have `parent_change_delta_24h = 0` if the metric is not meaningful.

## Edge record

Each important edge in `edges` should follow this shape.

```json
{
  "source_eui64": "1122334455667788",
  "target_eui64": "8877665544332211",
  "score": 0.41,
  "band": "weak",
  "confidence": 0.79,
  "reason_codes": [
    "WEAK_RSSI",
    "WEAK_BRIDGE_EDGE"
  ],
  "metrics": {
    "rssi": -86,
    "lqi": 1,
    "retry_delta_1h": 7,
    "age_seconds": 42,
    "reverse_rssi": -74,
    "symmetry": 0.4,
    "is_bridge": true
  },
  "evidence": [
    {
      "reason_code": "WEAK_BRIDGE_EDGE",
      "details": {
        "affected_nodes": [
          "aabbccddeeff0001",
          "aabbccddeeff0002"
        ]
      }
    }
  ]
}
```

### Edge rules

- Only include edges relevant to visualization or explanation. This is not a raw dump requirement.
- The frontend should not recompute edge health from RSSI/LQI.
- `is_bridge` and other topology-critical fields belong in the payload, not the UI.

## Finding record

`findings` is a ranked explanation layer built on deterministic facts.

```json
{
  "finding_id": "weak-bridge-1122-8877",
  "severity": "investigate",
  "reason_code": "WEAK_BRIDGE_EDGE",
  "title": "Weak bridge between hallway and office router clusters",
  "summary": "A single weak edge currently carries the only usable path between two router groups.",
  "affected_nodes": [
    "1122334455667788",
    "8877665544332211"
  ],
  "evidence": [
    {
      "type": "edge",
      "source_eui64": "1122334455667788",
      "target_eui64": "8877665544332211",
      "score": 0.41
    }
  ]
}
```

`title` and `summary` are deterministic backend prose, not LLM-generated text.

## `placement_candidates` shape

```json
{
  "computed_at": "2026-05-30T22:15:00Z",
  "as_of": "2026-05-30T22:14:42Z",
  "confidence": 0.74,
  "candidates": [
    {
      "candidate_id": "hallway-outlet-1",
      "location_label": "Hallway outlet between office and bedroom",
      "recommendation_type": "mains_powered_thread_router",
      "device_examples": [
        "thread outlet",
        "thread plug"
      ],
      "score_delta": 0.14,
      "redundancy_delta": 0.25,
      "path_diversity_delta": 0.34,
      "bottleneck_reduction": 0.41,
      "affected_nodes": [
        "aabbccddeeff0001",
        "aabbccddeeff0002",
        "aabbccddeeff0003"
      ],
      "bottlenecks_reduced": [
        "1122334455667788"
      ],
      "reason_codes": [
        "ADD_ROUTER_FOR_ALTERNATE_PATH",
        "RELIEVE_BOTTLENECK_ROUTER"
      ],
      "assumptions": [
        "Assumes a mains-powered router can be installed at this location.",
        "Assumes neighboring links would be comparable to nearby observed router links."
      ],
      "confidence": 0.71
    }
  ]
}
```

## Placement candidate rules

- `recommendation_type` should stay coarse and deterministic, for example `mains_powered_thread_router`.
- The payload should justify why a router class is recommended; it should not merely rank locations.
- The contract must support AI narration such as: a mains-powered Thread plug near location X would likely create an alternate path for devices A, B, and C and reduce dependence on router Y.
- Exact physical coordinates are out of scope for this draft. `location_label` may be an area, outlet, room transition, or abstract placement zone.

## Unknown and stale handling

Unknown or stale fields must be explicit.

Examples:

- `band: "unknown_stale"`
- `confidence < 0.5`
- `reason_codes` contains `STALE_LINK_DATA` or `LOW_CONFIDENCE_SNAPSHOT`
- nullable metrics remain `null`, not `0`

## Minimal deterministic reason-code set

- `LOW_ROUTER_REDUNDANCY`
- `NO_ALTERNATE_PATH`
- `WEAK_BACKBONE_PATH`
- `WEAK_BRIDGE_EDGE`
- `HIGH_BOTTLENECK_CENTRALITY`
- `ARTICULATION_ROUTER`
- `MARGINAL_PARENT_LINK`
- `PARENT_FLAPPING`
- `PARTITION_RISK`
- `PARTITION_SPLIT`
- `LOW_CONFIDENCE_SNAPSHOT`
- `ADD_ROUTER_FOR_REDUNDANCY`
- `ADD_ROUTER_FOR_ALTERNATE_PATH`
- `RELIEVE_BOTTLENECK_ROUTER`

## Example narrative mappings

The AI should be able to map deterministic fields to operator guidance without inventing new evidence.

Example mapping:

- facts: `LOW_ROUTER_REDUNDANCY`, `NO_ALTERNATE_PATH`, candidate with `recommendation_type = mains_powered_thread_router`
- operator-facing explanation: "The hallway router currently has only one strong neighbor and no alternate usable path. Adding a mains-powered Thread router near the hallway outlet would likely improve resilience for Bedroom Sensor, Office Plug, and Hallway Sensor."

## Companion documents

- Deterministic formulas and thresholds: [11-network-health-scoring-spec.md](11-network-health-scoring-spec.md)
- Related backlog issue: [#123](https://github.com/DarinShapiro/ThreadObservabilityPOC/issues/123)