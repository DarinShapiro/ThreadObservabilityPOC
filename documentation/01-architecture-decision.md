# HA Reasoning Platform: Architecture Decision Document

## Overview

This document describes the architecture for a general-purpose Home Assistant reasoning platform, with Thread network observability as the v1 module.

**Goal**: Build an extensible platform for continuous monitoring, anomaly detection, and AI-assisted reasoning over HA data, designed to run on constrained hardware (HA Yellow) with optional sidecar/external compute for heavy reasoning tasks.

---

## Design Principles

1. **Local-first, privacy-preserving**: All raw data stays local by default; model inference is opt-in and configurable.
2. **Deterministic baseline**: Core functionality works without any AI; models enhance, not replace.
3. **Extensible for new domains**: Thread v1 proves the platform; energy, climate, security follow.
4. **Resource-aware**: Designed for HA Yellow constraints; self-aware about capacity.
5. **Fault-tolerant**: Graceful degradation when any component is unavailable.

---

## Tier Architecture

### Tier 1: Core Platform (always on HA Yellow)
- **Data ingestion**: Pluggable adapters (logs, HA state, events)
- **Enrichment engine**: Metadata correlation, time-sync, caching
- **Storage layer**: SQLite (config/mappings) + InfluxDB (time-series)
- **MCP server process**: Separate process in the same add-on container, exposing read-only reasoning APIs for any module
- **Internal scheduler**: Platform maintenance jobs (ingestion, normalization, metadata refresh, retention, watchdog) + provenance logging

### Scheduling Model (v1)

- **Internal scheduler (required)**: Keeps the platform functioning without user setup.
- **HA automations (user-facing)**: Triggers user-centric workflows such as daily AI summaries, notifications, and remediation flows.
- **Rule**: Platform-maintenance jobs run under the covers; preference-driven workflows are configured in HA automations.

### Default Internal Job Cadence (v1)

- Ingestion tick: every 5-15 seconds (or event-driven tailing)
- Topology recompute: every 30-60 seconds plus event-triggered updates
- Metadata sync: every 10-30 minutes
- Backlog/watchdog check: every 1 minute
- Retention/downsampling: hourly or daily

### Process Model (v1)

- **Process A (core service)**: Ingestion, enrichment, storage writes, deterministic reasoners, internal scheduler.
- **Process B (MCP service)**: Dedicated MCP server process for tool endpoints.
- **Packaging**: Both processes run inside one HA add-on container for simple install/upgrade.
- **Communication**: Localhost HTTP/IPC and shared storage contracts; no external dependency required.

### Tier 2: Reasoner Modules (Yellow or sidecar)
- **Deterministic**: Always run on Yellow (topology analysis, anomaly detection)
- **Model-assisted**: Optional sidecar/external (LLM root cause, cross-domain reasoning)

### Tier 3: Action Executors (external, optional)
- HA automation triggers
- Configuration remediation
- Multi-provider MoE decision logic

---

## Component Diagram

```
┌─────────────────────────────────────────────────────────┐
│                   HA Yellow Add-on                      │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌───────────────────────────┐  ┌───────────────────┐  │
│  │ Process A: Core Service   │  │ Process B: MCP    │  │
│  │                           │  │ Service            │  │
│  │ • Data ingestion adapters │  │ • MCP tool API     │  │
│  │ • Enrichment engine       │  │ • Query handlers   │  │
│  │ • Storage writer/queries  │  │ • Provider-agnostic│  │
│  │ • Deterministic reasoners │  │   interface        │  │
│  │ • Scheduler               │  │                    │  │
│  └───────────────┬───────────┘  └─────────┬─────────┘  │
│                  │ localhost HTTP/IPC      │            │
│                  └──────────────┬──────────┘            │
│                                 ↓                       │
│  ┌──────────────────────────────────────────────────┐  │
│  │ Shared Storage Layer (SQLite + InfluxDB)        │  │
│  │ • SQLite: config, mappings, state               │  │
│  │ • InfluxDB: time-series metrics + anomalies     │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
└─────────────────────────────────────────────────────────┘
                           ↕
        ┌──────────────────────────────────────┐
        │  Optional Sidecar (Heavy AI/Batch)  │
        │  • Model-assisted reasoning          │
        │  • Historical reprocessing           │
        │  • Expensive analytics              │
        └──────────────────────────────────────┘
                           ↕
        ┌──────────────────────────────────────┐
        │  Optional External (Cloud/LLM)      │
        │  • Remote model inference            │
        │  • Cross-home correlation (opt-in)  │
        └──────────────────────────────────────┘
```

---

## Data Flow

1. **Ingestion**: Adapters pull/stream from HA logs, event bus, state history.
2. **Normalization**: Convert to canonical event schema (timestamp, source, entity_ref, metric, value, context).
3. **Enrichment**: Add HA metadata (device name, area, type, automations).
4. **Storage**: Write normalized events to InfluxDB; aggregate anomaly scores.
5. **Reasoning**: Deterministic modules query storage, compute topology/anomalies, emit findings.
6. **API**: MCP process exposes findings for external reasoning (LLM, HA automations) and queries core data through localhost/IPC contracts.
7. **Feedback**: Optional loop: executor applies HA automation recommendations.

---

## Storage Schema

### SQLite (Metadata + Config)

```sql
-- Node identity mappings
CREATE TABLE node_mappings (
  id TEXT PRIMARY KEY,
  eui64 TEXT UNIQUE,
  thread_node_id TEXT,
  ha_entity_id TEXT,
  ha_device_id TEXT,
  friendly_name TEXT,
  area TEXT,
  confidence REAL,
  last_seen TIMESTAMP,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);

-- Parser versions + profiles
CREATE TABLE parser_registry (
  id TEXT PRIMARY KEY,
  version TEXT,
  adapter_name TEXT,
  signature TEXT,
  confidence REAL,
  test_samples TEXT,  -- JSON array
  created_at TIMESTAMP
);

-- Scheduled tasks + provenance
CREATE TABLE task_executions (
  id TEXT PRIMARY KEY,
  task_name TEXT,
  reasoner_module TEXT,
  model_provider TEXT,
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  status TEXT,
  findings TEXT,  -- JSON
  evidence_ids TEXT  -- JSON array of event_ids
);
```

### InfluxDB (Time-Series)

```
Measurement: events
  Tags: source_adapter, entity_ref, event_type, severity
  Fields: value, confidence
  Timestamp: nanoseconds

Measurement: anomalies
  Tags: entity_ref, anomaly_type, module
  Fields: confidence_score, severity
  Timestamp: nanoseconds

Measurement: metrics
  Tags: entity_ref, metric_type (rssi, lqi, parent_churn, etc.)
  Fields: value
  Timestamp: nanoseconds
```

---

## Resource Budget (HA Yellow)

| Component | CPU | RAM | Storage |
|-----------|-----|-----|---------|
| Core platform | 5-10% | 50-100 MB | <100 MB |
| Ingestion (Thread logs) | 2-5% | 30-50 MB | -- |
| InfluxDB (operating buffer) | 2-5% | 100-150 MB | -- |
| Deterministic reasoners | 1-3% | 20-30 MB | -- |
| **Total baseline** | ~15% | **200-250 MB** | **2-5 GB** |

**Fault detection**: Monitor query latency and event count vs. expected rate. If backlog exceeds threshold, pause non-critical reasoners.

---

## Retention + Downsampling Policy

```yaml
retention:
  full_resolution: 3 days       # Temporal drift troubleshooting
  sampled_archive: 14 days      # Trend analysis
  anomaly_records: 30 days      # Historical incidents
  provenance_log: 7 days        # Model reasoning audit

downsampling:
  enabled: true
  threshold: 3 days             # After 3 days, aggregate
  interval: 5 minutes           # Sampled bucket size
  aggregations: [mean, max, min, last]
```

---

## Privacy + Security Model

**Default**: Local-only, no model calls
**Opt-in**: External reasoners with user consent
**Audit**: All reasoning task execution is logged with:
- What data was queried
- Which provider processed it
- What inference was produced
- When/how results were used

```yaml
ai:
  enabled: false                  # Default off
  provider: local                 # Ollama first if enabled
  fallback_to_cloud: false        # Explicit per-provider config
  data_retention_for_training: false
  audit_log: true
```

---

## Extensibility Checklist

For future modules (energy, climate, security), verify:
- [ ] Can adapt data source without modifying core?
- [ ] Can define module-specific anomaly types?
- [ ] Can query module facts via generic MCP tools?
- [ ] Can register internal maintenance jobs with safe defaults?
- [ ] Can integrate with HA automations/helpers?

---

## What's NOT in v1

- Model-assisted parsing
- Sidecar orchestration
- HA automation writer
- Multi-provider MoE routing
- Energy/climate/security modules
- Cross-home correlation
- GPU acceleration

v1 proves the platform with Thread observability alone.

