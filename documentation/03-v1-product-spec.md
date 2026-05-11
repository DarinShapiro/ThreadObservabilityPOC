# V1 Product Specification: Thread Network Observability

## Vision

**Goal**: Enable Home Assistant users to quickly diagnose and troubleshoot Thread network connectivity issues by visualizing topology, correlating logs with device metadata, and surfacing actionable anomalies.

**Success**: A user can identify why a Thread device is unreachable in <2 minutes without reading raw logs.

---

## User Stories

### Story 1: "Why is my kitchen light offline?"

**Actor**: Homeowner with 30+ Thread devices

**Flow**:
1. User opens Thread observability app (add-on web UI)
2. Views network topology graph; kitchen light is red (unreachable)
3. Clicks kitchen light node → drills into details
4. Sees: parent node, RSSI trend (dropping over 2 hours), last attach time, recent parent changes
5. Sees related events: 3 attach failures in last hour, parent switched twice
6. Recommendation: "Parent link unstable; try moving kitchen router closer or check interference"

**Outcome**: User identifies root cause without HA logs.

---

### Story 2: "Network stability check"

**Actor**: Network administrator / power user

**Flow**:
1. User opens app dashboard
2. Sees summary: X devices attached, Y offline, Z with poor link quality
3. "Issues" panel shows top 5 problems (offline nodes, parent churn, low RSSI)
4. User can filter by area (e.g., "Basement") to focus troubleshooting
5. Can export incident timeline for cross-referencing with HA automations or power events

**Outcome**: User quickly assesses network health without drilling into 1000s of log lines.

---

### Story 3: "Schedule periodic health checks via LLM"

**Actor**: Advanced user or integrator

**Flow**:
1. User has Claude Desktop connected to HA via MCP
2. User asks: "Is my Thread network healthy?"
3. Claude uses MCP tools: `get_network_topology()`, `list_active_issues()`
4. Claude generates natural language summary: "Your Thread network has 28 healthy devices. The bathroom sensor has been offline for 1 hour with repeated attach failures; parent link quality is marginal."
5. User can ask follow-up: "What changed 1 hour ago in my home?" (cross-domain reasoning, future work)

**Outcome**: LLM becomes a natural interface for network diagnostics.

---

## Core Features (v1)

### 1. Real-Time Thread Log Ingestion

**What**:
- Continuous tail/poll of `/config/logs/matter_*.log` and `/config/logs/thread_*.log`
- Parse known log formats for attach/detach/rejoin/link_quality events
- Normalize to platform canonical events
- Store in InfluxDB time-series

**Config**:
```yaml
thread:
  enabled: true
  log_paths:
    - /config/logs/thread_*.log
    - /config/logs/matter_*.log
  parser_profiles: ["ha-2025-05"]
```

**Retention**: 3 days full-resolution, 14 days sampled

---

### 2. Device Enrichment

**What**:
- For each node detected in logs, correlate with HA device metadata
- Query HA device registry for EUI-64, entity_id, friendly_name, area
- Cache metadata with TTL to avoid hammering HA API

**Result**: Log events now say "Kitchen Light (bedroom, light, Philips)" instead of "EUI64:aabbccdd"

---

### 3. Topology Graph Engine

**What**:
- Maintain current Thread network topology from device events
- Compute node roles: router, end device, sleepy end device, leader
- Compute parent-child relationships and routing paths
- Update in real-time as attach/detach events arrive

**Storage**: In-memory graph + SQLite for identity + InfluxDB for metrics

---

### 4. Topology Visualization (Web UI)

**What**:
- Interactive graph view of Thread network
- Node colors: green (healthy), yellow (degraded), red (offline), gray (unknown)
- Node size: signal strength or routing table size
- Hover: show device name, RSSI, LQI, parent
- Click: drill into detailed node page

**Layout**: Force-directed graph or hierarchical tree

**Responsive**: Mobile-friendly for quick checks

---

### 5. Node Detail Page

**What**:
- Device name, friendly name, area, device type, manufacturer/model
- Current role (router, end device, sleepy)
- Parent node (with parent's name)
- Routing information (if applicable)
- Link quality trend (RSSI, LQI over past 3 days with downsampling)
- Recent events: attach/detach/rejoin timeline
- Health score: 0-100 based on stability and link quality

**Metrics**:
- RSSI (signal strength, dB)
- LQI (link quality, 0-255)
- Packet loss (inferred from retries)
- Parent changes (in past 24h)
- Time since attach

---

### 6. Anomaly Detection

**What**:
- Deterministic rules detect common issues:
  - **Offline**: No activity > threshold (e.g., 1 hour)
  - **Parent churn**: >N parent changes in time window (default 3 in 60min)
  - **Attach storm**: >N failed attach attempts before success (default 5)
  - **Link quality drop**: RSSI drops >X dB in Y minutes (default 20dB in 10min)
  - **Sleepy timeout**: Sleepy device didn't check in (expected periodic checkin)

**Output**: Anomaly event with severity (info/warning/error), entity, and evidence

**Storage**: Written to InfluxDB anomalies table

---

### 7. Issues Dashboard

**What**:
- Real-time list of active anomalies, sorted by severity
- Top 5 format: device name, issue type, severity, time detected
- Filterable by area, severity, issue type
- Click to view full incident record with evidence

---

### 8. MCP Server + Tools

**Tools**:
1. `get_network_topology()`
   - Returns: nodes (id, name, role, parent, health), edges (parent->child)
   - No parameters; real-time snapshot
   - ~100ms response for 50 nodes

2. `get_node_details(node_id)`
   - Returns: all fields from Node Detail Page
   - Includes full event timeline (last 100 events)
   - ~50ms response

3. `list_active_issues(severity_threshold?)`
   - Returns: current anomalies, sorted by severity
   - Optional filter: severity level
   - ~50ms response

**Implementation**: FastAPI + MCP wrapper, runs on HA Yellow alongside add-on

---

### 9. Internal Scheduler (Platform Maintenance)

**What**:
- Runs platform-maintenance jobs without requiring user-created automations
- Keeps ingestion, normalization, topology state, and retention healthy
- Records execution history for observability and troubleshooting

**Default Cadence**:
- Ingestion tick: 5-15 seconds (or event-driven file tail)
- Topology recompute: 30-60 seconds plus event-triggered updates
- Metadata refresh: 10-30 minutes
- Backlog watchdog: every 1 minute
- Retention/downsampling: hourly or daily

**User impact**:
- Base module works out-of-the-box with no HA automation setup
- HA automations are reserved for user-centric workflows (daily summaries, notifications)

---

### 10. Prefetch + Cache Policy (Latency by Design)

**Goal**:
- Keep UI and automation-facing APIs responsive even when HA API latency is variable

**Policy**:
- Prefetch HA entity and device metadata at startup before first UI request
- Maintain two cache tiers:
  - L1 in-memory cache for hot reads
  - L2 SQLite cache for restart persistence
- Use stale-while-revalidate for metadata:
  - Serve cached metadata immediately
  - Refresh asynchronously in background
- Use event-driven invalidation when HA registry/entity update signals are available
- Never block core health calculations on metadata refresh

**Default cache TTLs (v1)**:
- Device and area metadata: 30-120 minutes
- Entity state metadata used for labels/context: 30-120 seconds
- Startup cache warmup target: complete first pass within 120 seconds

**UI/API behavior**:
- UI endpoints return pre-enriched cached models, not live HA API joins
- On cache miss, return partial result with freshness flags and asynchronously enrich
- Every payload includes metadata freshness fields (`metadata_cached_at`, `metadata_age_seconds`, `metadata_source`)

---

### 11. Dependency-Aware SLO Model

**SLO classes**:
- **Platform-owned SLOs**: Measured within ingestion, normalization, detection, publication pipeline
- **Dependency-aware SLOs**: Include external HA API contribution and are evaluated with budget attribution

**Budget attribution (v1)**:
- End-to-end event-to-issue latency budget is split into:
  - Platform processing budget
  - HA dependency budget (metadata enrichment only)
- Core health freshness SLO is platform-owned and must hold even when HA metadata is delayed

**Baseline calibration**:
- Run first 7 days in baseline mode and measure HA API p50/p95/timeout rates
- Use observed baseline to tune metadata TTLs and refresh cadence
- Preserve fixed platform-owned targets regardless of HA variability

---

### 12. Degraded-Mode UX Rules

**Principle**:
- Health state remains real-time; metadata can degrade gracefully

**Rules**:
- If HA API is slow/unavailable, continue publishing health snapshots and active issues using cached metadata
- Show explicit metadata staleness indicators in UI and automation payloads
- Do not block topology/issue endpoints waiting for live HA metadata
- Apply exponential backoff with jitter for metadata refresh retries
- Shed non-critical jobs before impacting health snapshot freshness

**Freshness state guidance**:
- `data_age_seconds <= 60`: nominal
- `60 < data_age_seconds <= 180`: degraded
- `data_age_seconds > 180`: stale/non-nominal

---

### 13. Data Retention + Privacy

**Default**:
- Local-only: no model calls, no cloud uploads
- SQLite: always local
- InfluxDB: always local (HA add-on or external sidecar, but local network only)

**Config**:
```yaml
retention:
  full_resolution_days: 3
  sampled_archive_days: 14
  anomaly_records_days: 30

ai:
  enabled: false  # No model calls in v1
  provider: local
  fallback_to_cloud: false
```

---

## Out of Scope (v1)

- Model-assisted parsing (deterministic only)
- Sidecar coordination
- HA automation generation ("auto-remediate")
- Energy/climate/security modules
- Cross-home benchmarking
- Multi-provider MoE reasoning
- GPU acceleration
- Cloud reasoning

These move to v1.5 or v2.

---

## User Interface Mockup

### Main Dashboard

```
┌─────────────────────────────────────────────┐
│  Thread Network Observability               │
├─────────────────────────────────────────────┤
│                                             │
│  Network Summary:  28 devices | 26 healthy  │
│                    1 offline  | 1 degraded   │
│                                             │
│  ┌─────────────────────────────────────────┐│
│  │                                         ││
│  │        [TOPOLOGY GRAPH]                 ││
│  │        (Interactive - force-directed)   ││
│  │                                         ││
│  │  🟢 Kitchen Light    🟡 Bathroom Sensor││
│  │      ↑                      ↑            ││
│  │  🟢 Kitchen Router      🔴 Garage Door ││
│  │      ↑                                   ││
│  │  🟢 Thread Border Router (Leader)      ││
│  │                                         ││
│  └─────────────────────────────────────────┘│
│                                             │
│  Top Issues:                                │
│  1. 🔴 Garage Door (offline 45 min)        │
│  2. 🟡 Basement Light (3 parent changes)   │
│  3. 🟡 Living Room Switch (low RSSI)       │
│  4. 🟠 Kitchen Sensor (attach failures)    │
│  5. ⓘ  Kitchen Light (degraded parent)     │
│                                             │
└─────────────────────────────────────────────┘
```

### Node Detail Page

```
┌─────────────────────────────────────────────┐
│  Kitchen Light                              │
├─────────────────────────────────────────────┤
│  Area: Kitchen                              │
│  Device: Philips Hue Light                  │
│  Role: Sleepy End Device                    │
│  Parent: Kitchen Router                     │
│  Parent Health: Good (RSSI -65 dB)          │
│  Last Seen: 2 minutes ago                   │
│                                             │
│  ┌─────────────────────────────────────────┐│
│  │ RSSI Trend (3 days)                     ││
│  │                                  ▄▄▄    ││
│  │                              ▄▄▄▀   ▀▄▄ ││
│  │                          ▄▄▀             ││
│  │                      ▄▄▀                ││
│  │  -60                                    ││
│  │  -70 ▄▀▀▀                               ││
│  │  -80 ▀                                  ││
│  │                                         ││
│  │  3d ago      1d ago     Now             ││
│  └─────────────────────────────────────────┘│
│                                             │
│  Metrics:                                   │
│  • RSSI: -68 dB (good)                      │
│  • LQI: 220 (excellent)                     │
│  • Parent Changes (24h): 0                  │
│  • Attach Failures (24h): 0                 │
│                                             │
│  Recent Events:                             │
│  • 14:30  Attach successful                │
│  • 14:15  Detached from Kitchen Router     │
│  • 14:10  Attach failed (retried)          │
│  • 13:45  Changed parent to Dining Router  │
│  (View all 100+ events)                    │
│                                             │
└─────────────────────────────────────────────┘
```

---

## Implementation Plan

### Phase 1: Core Platform (Week 1)
- [ ] Storage layer (SQLite + InfluxDB)
- [ ] Data adapter interface
- [ ] Enrichment engine (HA device correlation)
- [ ] MCP server skeleton
- [ ] Internal scheduler with default maintenance jobs

### Phase 2: Thread Module (Week 2)
- [ ] Thread log adapter (deterministic parsing)
- [ ] Thread reasoner (topology + anomalies)
- [ ] Topology graph engine
- [ ] MCP tools (#1, #2, #3)

### Phase 3: UI (Week 3)
- [ ] Web dashboard (topology, summary, issues)
- [ ] Node detail page
- [ ] Responsive design

### Phase 4: Polish + Testing (Week 4)
- [ ] End-to-end testing
- [ ] Documentation
- [ ] Add-on packaging for GitHub repo
- [ ] User guide

---

## Success Metrics (v1)

### Acceptance Criteria (Pass/Fail)

| Metric | SLO / Threshold | Pass Criteria |
|--------|------------------|---------------|
| Health freshness | 99.5% of health snapshots are <= 60s stale; worst-case <= 180s during normal operation | `health_snapshot.data_age_seconds` distribution meets SLO for a 7-day run |
| Automation readiness | 99% of anomaly records include severity, affected_entity, confidence, and detected_at | Schema completeness check over anomaly records meets threshold |
| Detection timeliness | Median event-to-issue latency <= 15s; P95 <= 45s | Measured from normalized event ingest timestamp to issue publication timestamp |
| Query responsiveness | P95 <= 500ms for `list_active_issues`; P95 <= 750ms for `get_network_topology` at 50 nodes | API latency benchmark passes under steady-state load |
| Metadata cache effectiveness | HA metadata cache hit ratio >= 95% after warmup | L1+L2 cache telemetry meets threshold during 7-day run |
| Dependency attribution | 100% of latency records include `latency_budget_source` attribution (`platform` or `ha_dependency`) | Event-to-issue and API telemetry include attribution fields |
| Signal quality | False positive rate for critical issues < 10%; missed critical incidents < 5% in labeled validation windows | Comparison against manually labeled incident windows meets both targets |
| Platform reliability | Ingestion uptime >= 99.9%; no data loss on service restart | Uptime monitor and restart replay checks pass |
| Resource guardrails (HA Yellow) | CPU avg < 15% (P95 < 25%); RAM avg < 250 MB; no OOM restarts | 7-day resource telemetry run within limits |
| Deterministic baseline | 0 model calls required for core health/anomaly features | All v1 workflows pass with `ai.enabled=false` |

### Validation Plan

1. Health freshness validation
- Run continuous ingestion for 7 days.
- Record `computed_at` and `data_age_seconds` on every published health snapshot.
- Pass if SLO targets are met and stale snapshots are flagged with reason.

2. Event-to-issue latency validation
- Inject synthetic attach/detach/parent-churn patterns into test logs.
- Measure latency from normalized event write to issue publish.
- Pass if median and P95 thresholds are met.

3. Automation readiness validation
- Run schema-completeness query over all anomalies generated during test window.
- Verify required fields and stable severity taxonomy.
- Pass if completeness >= 99%.

4. API performance validation
- Execute load tests for `list_active_issues` and `get_network_topology` at 1 RPS, 5 RPS, and 10 RPS.
- Test with 50-node and 100-node synthetic topology datasets.
- Pass if P95 latency thresholds are satisfied in target profile (Yellow-only baseline).

5. Signal quality validation
- Build a labeled corpus of known-good and known-bad windows from real/synthetic logs.
- Compare detected critical incidents vs labels.
- Pass if false positives and misses stay within thresholds.

6. Reliability and restart validation
- Force controlled restarts during active ingestion.
- Verify event continuity and no duplicate critical issues after recovery.
- Pass if uptime and no-data-loss criteria are met.

7. Resource guardrail validation
- Run 7-day soak test on HA Yellow baseline profile.
- Capture CPU, RAM, and scheduler job duration telemetry.
- Pass if resource and stability thresholds remain within limits.

8. Prefetch/cache validation
- Start from cold cache and verify startup prefetch completion target.
- Validate cache hit ratio after warmup and stale-while-revalidate behavior.
- Pass if UI/API response SLOs hold without live HA API calls on hot path.

9. Dependency-aware attribution validation
- For each latency record, verify `latency_budget_source` tagging.
- Produce weekly report splitting platform vs HA dependency latency contributions.
- Pass if attribution coverage is complete.

---

## Metrics Instrumentation (v1)

To make SLOs auditable, v1 emits standardized telemetry records for ingestion, issue publication, API queries, and health snapshots.

### Canonical Telemetry Fields

| Field | Type | Description |
|------|------|-------------|
| event_id | string | Stable UUID for normalized event |
| entity_ref | string | Canonical entity identifier (EUI-64/entity_id) |
| source_adapter | string | Adapter name (`thread`, `matter`, etc.) |
| event_observed_at | timestamp | Timestamp reported by source log/event |
| event_ingested_at | timestamp | When raw line/event entered ingestion pipeline |
| event_normalized_at | timestamp | When normalized event was committed to storage |
| issue_id | string | Stable UUID for anomaly/incident |
| issue_published_at | timestamp | When issue was made visible to APIs/MCP |
| snapshot_id | string | Stable UUID for health snapshot |
| snapshot_computed_at | timestamp | When health snapshot computation completed |
| snapshot_data_max_observed_at | timestamp | Most recent source timestamp included in snapshot |
| snapshot_data_age_seconds | number | Computed staleness of snapshot data |
| api_name | string | Endpoint/tool name (`list_active_issues`, `get_network_topology`) |
| api_request_started_at | timestamp | API request start time |
| api_response_sent_at | timestamp | API response completion time |
| api_latency_ms | number | End-to-end API latency |
| scheduler_job_name | string | Internal maintenance job identifier |
| scheduler_run_started_at | timestamp | Job execution start time |
| scheduler_run_completed_at | timestamp | Job execution end time |
| scheduler_run_duration_ms | number | Job duration |
| scheduler_run_status | string | `success`, `retry`, `failed`, `skipped` |
| cpu_percent | number | Process/container CPU usage sample |
| memory_mb | number | Process/container memory usage sample |

### Derived Metrics Formulas

1. Health freshness
- `snapshot_data_age_seconds = snapshot_computed_at - snapshot_data_max_observed_at`

2. Event-to-issue latency
- `event_to_issue_latency_seconds = issue_published_at - event_normalized_at`

3. API latency
- `api_latency_ms = api_response_sent_at - api_request_started_at`

4. Scheduler duration
- `scheduler_run_duration_ms = scheduler_run_completed_at - scheduler_run_started_at`

5. Ingestion uptime
- `% uptime = successful_ingestion_intervals / total_expected_intervals * 100`

### Time Semantics and Clock Rules

- Store all timestamps in UTC.
- Preserve source timestamp (`event_observed_at`) and platform timestamps (`*_ingested_at`, `*_normalized_at`).
- If source timestamp is missing/unparseable, set `event_observed_at` to `event_ingested_at` and mark `timestamp_quality=derived`.
- Emit monotonic sequence IDs per adapter to detect ordering gaps.

### Minimal Telemetry Measurements

InfluxDB measurements (or equivalent) required for v1:

1. `health_snapshots`
- Tags: `module`, `profile`
- Fields: `snapshot_data_age_seconds`, `healthy_nodes`, `degraded_nodes`, `offline_nodes`

2. `issue_pipeline`
- Tags: `module`, `issue_type`, `severity`
- Fields: `event_to_issue_latency_seconds`, `confidence`

3. `api_performance`
- Tags: `api_name`, `module`
- Fields: `api_latency_ms`, `status_code`, `result_count`

4. `scheduler_runs`
- Tags: `job_name`, `module`, `status`
- Fields: `scheduler_run_duration_ms`, `retry_count`

5. `runtime_resources`
- Tags: `component` (`core_service`, `mcp_service`)
- Fields: `cpu_percent`, `memory_mb`

### Required Automation-Facing Fields

Every health/anomaly payload exposed to HA automations or MCP must include:

- `computed_at`
- `data_age_seconds`
- `severity`
- `affected_entity`
- `confidence`
- `issue_id` (for anomalies)

Automations should treat snapshots as stale if `data_age_seconds > 60` by default (configurable).

### Example Health Snapshot Payload

```json
{
  "snapshot_id": "7ccf7a3b-7ea5-4e54-bf50-6f5f4c1d2e10",
  "module": "thread",
  "computed_at": "2026-05-11T17:10:12Z",
  "data_max_observed_at": "2026-05-11T17:09:36Z",
  "data_age_seconds": 36,
  "summary": {
    "healthy_nodes": 26,
    "degraded_nodes": 1,
    "offline_nodes": 1
  },
  "active_issues": [
    {
      "issue_id": "f8d4bc36-1cd0-4e02-bf39-f4cb3b6e48b8",
      "severity": "error",
      "affected_entity": "aabbccddee001122",
      "confidence": 0.93
    }
  ]
}
```

### Acceptance Gate for Instrumentation

v1 is not complete until all Success Metrics can be computed from emitted telemetry without manual log interpretation.

---

## Future Directions (v1.5+)

- Model-assisted log parsing (shape discovery)
- Root cause analysis ("Why is this node offline?")
- Sidecar for heavy reprocessing
- Energy/climate/security modules
- Cross-domain correlation ("Bathroom fan failed + high humidity spike")
- HA automation generation
- Multi-provider reasoning

