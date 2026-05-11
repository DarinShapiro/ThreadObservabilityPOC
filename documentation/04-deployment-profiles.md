# Deployment Profiles

## Overview

This document defines deployment options for the HA Reasoning Platform. Choose based on your hardware, privacy requirements, and reasoning needs.

---

## Profile 1: Yellow-Only (Recommended Baseline)

**Best for**: Most users; full functionality without external hardware

### Components

| Component | Location | Notes |
|-----------|----------|-------|
| Log collector | HA Yellow | Add-on container |
| Enrichment engine | HA Yellow | Add-on container |
| SQLite database | HA storage | Mounted in add-on |
| InfluxDB | HA storage or Docker addon | Optional; can skip for MVP |
| Deterministic reasoners | HA Yellow | Add-on container |
| MCP server | HA Yellow | Separate process inside same add-on container |
| Web UI | HA Yellow | Add-on web service |

### Resource Budget

| Component | CPU | RAM | Storage |
|-----------|-----|-----|---------|
| Core platform | 5-10% | 50-100 MB | <100 MB |
| Thread adapter | 2-5% | 30 MB | -- |
| InfluxDB (if used) | 2-5% | 100-150 MB | 2-5 GB |
| Web UI | 1-2% | 20 MB | -- |
| **Total** | **~15%** | **200-250 MB** | **2-5 GB** |

### Configuration

```yaml
# add-on options
thread_observability:
  profile: yellow-only
  
  data:
    storage_path: /data
    sqlite_path: /data/thread_obs.db
    
  influxdb:
    enabled: false  # Can enable if InfluxDB add-on is installed
    host: localhost
    port: 8086
    
  retention:
    full_resolution_days: 3
    sampled_archive_days: 14
    
  ai:
    enabled: false
    
  resource_limits:
    cpu_percent: 20      # Hard cap
    memory_mb: 250       # Hard cap
    disk_gb: 5
```

### HA Add-on Manifest

```json
{
  "name": "Thread Network Observability",
  "version": "1.0.0",
  "slug": "thread-observability",
  "description": "Monitor and diagnose Thread network connectivity",
  "url": "https://github.com/your-org/thread-observability",
  "host_network": true,
  "ports": {
    "8080/tcp": 8080
  },
  "ports_description": {
    "8080/tcp": "Web UI and MCP server"
  },
  "map": ["config:rw", "run"],
  "environment": {},
  "options": {
    "type": "object",
    "properties": {
      "profile": {"type": "string", "enum": ["yellow-only", "yellow-plus-sidecar"]},
      "log_paths": {"type": "array"},
      "retention_days": {"type": "integer"}
    }
  },
  "schema": {}
}
```

### Constraints

- ✅ Works out of the box
- ✅ No external dependencies
- ✅ Privacy-preserving (all local)
- ⚠️ Limited to deterministic reasoning (no LLM)
- ⚠️ No model-assisted parsing (v1 uses deterministic profiles only)
- ⚠️ Batch reprocessing not recommended (contends with HA core)

### Startup Flow

1. Add-on starts
2. Process A (core service) starts ingestion/reasoning pipeline
3. Process B (MCP service) starts MCP endpoint service
4. Thread collector begins tailing log files
5. Events normalized and written to SQLite + InfluxDB
6. Topology analyzer hydrates from InfluxDB
7. Internal scheduler runs deterministic maintenance jobs on default cadence
8. Web UI available at `http://homeassistant.local:8080`
9. MCP server listens on localhost:8080 for Claude Desktop or scripts
10. User-centric workflows (summaries, notifications) are triggered by HA automations

---

## Profile 2: Yellow + Sidecar (Recommended for Power Users)

**Best for**: Advanced users, heavy analysis, model-assisted parsing, reprocessing

### Architecture

```
┌──────────────────────────┐
│    HA Yellow Add-on      │
│                          │
│ • Log collection         │
│ • Real-time enrichment   │
│ • Topology updates       │
│ • Anomaly detection      │
│ • MCP server (read-only) │
└──────────────────────────┘
           ↕ (TCP 5432)
┌──────────────────────────┐
│  Sidecar (NAS/Mini PC)   │
│                          │
│ • Model-assisted parsing │
│ • Historical reprocessing│
│ • Heavy analytics        │
│ • Root cause reasoning   │
│ • Batch jobs            │
└──────────────────────────┘
           ↕ (TCP 8086)
┌──────────────────────────┐
│   Shared Storage         │
│                          │
│ • SQLite (read-only copy)|
│ • InfluxDB (primary)     │
│ • Shared cache           │
└──────────────────────────┘
```

### Sidecar Hardware Recommendations

- Mini PC: Intel N100, 8GB RAM, 256GB SSD
- NAS: Synology DS923+ or similar
- Old laptop: 4GB+ RAM, modern CPU
- Raspberry Pi 4B: 4GB+ (limited, suitable for light batch jobs)

### Components on Yellow

Same as Profile 1, but:
- SQLite is read-only for reasoners (writes happen on sidecar)
- MCP server exposes both Yellow + Sidecar insights

### Components on Sidecar

| Component | Technology | Notes |
|-----------|-----------|-------|
| Model inference | Ollama / llama.cpp | Local LLM, privacy-first |
| Parser discovery | Claude / Llama2 | Adapt to log format changes |
| Batch reprocessor | Python + asyncio | Reparse historical logs |
| Analytics engine | Pandas + scikit-learn | Anomaly detection refinement |
| Database mirror | PostgreSQL + TimescaleDB | Optional; higher performance |
| MCP reasoner tools | Custom server | Exposes reasoning capabilities |

### Configuration

```yaml
# HA Yellow add-on config
thread_observability:
  profile: yellow-plus-sidecar
  
  sidecar:
    enabled: true
    host: 192.168.1.100  # Sidecar IP
    port: 5432
    shared_storage:
      type: nfs          # Or SMB
      path: /mnt/sidecar
      
  database:
    storage_path: /mnt/sidecar/influx-data
    sqlite_path: /mnt/sidecar/thread_obs.db
    
  # ... rest as Profile 1

# Sidecar config (standalone Python app or Docker)
sidecar_config:
  models:
    enabled: true
    ollama_host: localhost:11434
    default_model: llama2-7b
    
  parsers:
    auto_discovery: true
    model: llama2-7b
    
  batch_reprocessing:
    max_concurrent_jobs: 2
    log_buffer_mb: 500
    
  storage:
    sqlite_path: /mnt/shared/thread_obs.db
    influx_host: 192.168.1.50
    influx_port: 8086
```

### Sidecar Deployment Options

**Option A: Docker (recommended)**
```bash
docker run -d \
  --name thread-sidecar \
  --network host \
  -v /mnt/sidecar:/data \
  -v /mnt/influx:/var/lib/influxdb \
  thread-observability-sidecar:latest
```

**Option B: Standalone Python**
```bash
pip install thread-observability-sidecar
thread-sidecar --config /etc/thread-sidecar.yaml
```

**Option C: NAS (Synology, TrueNAS)**
Use vendor-specific package management or Docker.

### Constraints

- ✅ Full deterministic reasoning on Yellow
- ✅ Heavy model inference on sidecar (no contention)
- ✅ Historical log reprocessing
- ✅ Extensible for energy/climate modules on sidecar
- ⚠️ Requires network connectivity between Yellow + Sidecar
- ⚠️ Sidecar must be always-on for model features
- ⚠️ Added complexity (two systems to manage)

### Network Requirements

- Yellow ↔ Sidecar: 100 Mbps (typical home LAN sufficient)
- Sidecar → InfluxDB: 10 Mbps (low bandwidth, high latency tolerance)
- Latency: <100ms recommended (same LAN)

### Monitoring Sidecar Health

MCP tool: `get_sidecar_status()`
```json
{
  "status": "connected",
  "last_heartbeat": "2025-05-11T14:30:00Z",
  "uptime_hours": 72,
  "models": {
    "llama2-7b": {"loaded": true, "inference_time_avg_ms": 850}
  },
  "queue": {
    "pending_jobs": 3,
    "oldest_job_age_minutes": 12
  }
}
```

---

## Profile 3: Yellow + Cloud (Privacy-Conscious Advanced Users)

**Best for**: Users who want model inference but won't run local LLM

### Architecture

```
┌──────────────────────┐
│  HA Yellow Add-on    │
│                      │
│ • Log collection     │
│ • Real-time insights │
│ • MCP server         │
└──────────────────────┘
           ↕ (HTTPS)
┌──────────────────────┐
│  Cloud API Provider  │
│  (OpenAI/Anthropic)  │
│                      │
│ • LLM reasoning      │
│ • Root cause analysis│
│ • Natural language   │
└──────────────────────┘
```

### Configuration

```yaml
thread_observability:
  profile: yellow-plus-cloud
  
  ai:
    enabled: true
    provider: openai        # or anthropic
    api_key: ${SECRET}      # Via HA secrets
    model: gpt-4
    
  privacy:
    send_raw_logs: false        # No
    send_aggregated_events: true # Yes, minimized
    redact_device_names: true    # Optional
    audit_log: true
    
  rate_limits:
    requests_per_hour: 10
    concurrent_requests: 1
```

### What Data is Sent to Cloud

Only when reasoning is requested:
- Anonymized topology snapshot
- Aggregated anomaly list (not raw logs)
- Redacted device names (e.g., "Device-A" instead of "Kitchen Light")
- Time-series statistics (not individual samples)

### What Stays Local

- Raw log files
- Full event history
- Device identities
- Automations and secrets

### Constraints

- ✅ Privacy-conscious (minimized data)
- ✅ No local compute (Yellow stays light)
- ✅ Powerful reasoning (GPT-4 level)
- ⚠️ Cost per query (~0.01-0.10 USD)
- ⚠️ Requires internet connection
- ⚠️ External provider dependency
- ⚠️ No reasoning during outages

### Cost Estimate

Assume 10 queries/day, ~1000 tokens per query:
- OpenAI: ~$0.10/day ($3/month)
- Anthropic: ~$0.15/day ($4.50/month)

---

## Profile 4: Air-Gapped (Offline Home Lab)

**Best for**: Users with no internet, highly sensitive environments

### Components

| Component | Location | Notes |
|-----------|----------|-------|
| Everything | HA Yellow only | No external access |
| Models | Sidecar (Ollama) | Must be on LAN |
| LLM | local/offline | quantized models only |

### Configuration

```yaml
thread_observability:
  profile: air-gapped
  
  ai:
    provider: local-only
    model_path: /data/models/llama2-7b-q4.gguf
    max_inference_mb: 4000  # Limit to Yellow's headroom
    
  network:
    allow_external: false
    allow_cloud_reasoning: false
```

### Constraints

- ✅ 100% local control
- ✅ No cloud dependency
- ✅ Maximum privacy
- ⚠️ Limited model quality (quantized, smaller models)
- ⚠️ Slower inference (CPU-only, likely)
- ⚠️ Limited reasoning capability

---

## Migration Path

### Start with Yellow-Only

1. Deploy Profile 1 (Yellow add-on)
2. Verify log ingestion, topology, anomaly detection work
3. Use MCP tools with deterministic reasoning
4. Monitor CPU/memory on Yellow

### Upgrade to Yellow + Sidecar

If you want model-assisted features:
1. Deploy sidecar (NAS or mini PC)
2. Configure network connectivity
3. Enable model-assisted parsing in add-on config
4. Sidecar begins consuming job queue from Yellow
5. Yellow remains responsive for real-time monitoring

### Experiment with Cloud

If you want to test reasoning without running sidecar:
1. Set `ai.provider: openai`
2. Add API key via HA secrets
3. MCP tool `explain_incident(incident_id)` now calls OpenAI
4. Monitor costs and quality
5. Decide whether to commit to sidecar or keep cloud

---

## Comparison Matrix

| Feature | Yellow-Only | Yellow+Sidecar | Yellow+Cloud | Air-Gapped |
|---------|-------------|----------------|--------------|-----------|
| Real-time topology | ✅ | ✅ | ✅ | ✅ |
| Anomaly detection | ✅ | ✅ | ✅ | ✅ |
| Deterministic reasoning | ✅ | ✅ | ✅ | ✅ |
| Model-assisted parsing | ❌ | ✅ | ✅ | ⚠️ slow |
| Root cause analysis | ❌ | ✅ | ✅ | ⚠️ slow |
| Cross-domain reasoning | ❌ | ✅ | ✅ | ❌ |
| Privacy (no cloud) | ✅ | ✅ | ❌ | ✅ |
| Always-on (no deps) | ✅ | ⚠️ sidecar needed | ❌ internet needed | ✅ |
| Cost | $0 | +$0 (if existing hardware) | ~$3-5/month | $0 |
| Complexity | ⭐ | ⭐⭐ | ⭐ | ⭐ |

---

## Recommended for Users

- **Default (most users)**: Yellow-Only → Profile 1
- **Power users, debugging enthusiasts**: Yellow-Only → Yellow+Sidecar → Profile 2
- **Cloud-preferring, budget-conscious**: Yellow-Only → Yellow+Cloud → Profile 3
- **Privacy-first, offline**: Yellow-Only → Air-Gapped → Profile 4
- **Experimenters**: Yellow-Only → Yellow+Cloud (cheap test) → Yellow+Sidecar (if good)

