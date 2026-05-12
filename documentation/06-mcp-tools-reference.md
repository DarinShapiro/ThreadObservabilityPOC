# MCP Tool Reference

Auto-generated from the live `/mcp/tools` registry on the Thread Observability add-on.

- **Add-on version**: 0.10.0
- **Schema version**: 19
- **Tool count**: 36
- **Generated**: 2026-05-12T23:01:01Z (UTC)

All read tools return a top-level `{data, meta}` envelope. `meta` carries `as_of`, `data_source`, `cache_age_s`, `stale_after_s`, and the latest `pipeline_tick` block - clients should consult `meta` to decide whether the response is fresh enough.

Endpoints:

- `GET  /mcp/tools` - list the registry
- `POST /mcp/call/{tool_name}` with body `{\
- `POST /mcp/rpc` - JSON-RPC envelope

---

## Triage entry points (Phase 3)

### `start_triage`

Use when: starting any new investigation, or as the first call in a session. Returns the consolidated environment (addon/HA/OTBR/Matter/network/pipeline versions) plus the health snapshot plus active issues plus a `recommended_next` list of up to 3 follow-up tool calls chosen from the catalog. Returns: {as_of, environment, health, active_issues_count, active_issues[<=10], recommended_next[<=3]}. Caveats: snapshot from SQLite cache; refresh by waiting one pipeline tick.

*No arguments.*

### `get_environment`

Use when: you need versions/identity of every relevant component in one shot — addon version, HA Core version, Supervisor version, OTBR add-on state, Matter Server add-on state, Thread network identity (name/pan_id/channel/leader), and pipeline runner state. Returns: {addon, home_assistant, otbr, matter_server, network, pipeline}. Caveats: Supervisor calls may fail outside the HA container; those sections fall back to `{error: ...}`.

*No arguments.*

### `get_pipeline_health`

Use when: data looks stale, the dashboard is empty, or the model needs to know whether the pipeline is actually running. Returns the last N pipeline ticks (newest first) plus a summary including consecutive_failed_ticks, stages_currently_failing, avg_duration_seconds, and the current runner state. Returns: {summary: {...}, recent_ticks: [...]}. Caveats: only ticks recorded in schema v18+ are visible; backfill is not retroactive.

**Arguments:**

| Name | Type | Required | Description |
|---|---|---|---|
| `limit` | integer | no |  |

### `get_health_snapshot`

Return current health snapshot: node counts by status (healthy / stale / offline), active issue counts, and data freshness age.

*No arguments.*

## Mesh state and topology

### `get_mesh_state`

Use when: starting a triage session or answering 'what does the mesh look like right now?'. Returns the live Thread mesh: nodes + links + partition_id, computed deterministically from the SQLite event log and most-recent Matter discovery tick. Phantom nodes are excluded by default. Returns: {nodes:[{eui64, role, partition_id, parent_eui64, last_rssi, last_lqi, status, ...}], links:[...], partition_id, computed_at, node_count, link_count}. Caveats: SQLite-cached. Check meta.cache_age_s on the response; if stale, call ingest_now to force a refresh.

**Arguments:**

| Name | Type | Required | Description |
|---|---|---|---|
| `freshness_minutes` | integer | no | Window (minutes) for inferring current parent links. Default 60. |
| `include_phantoms` | boolean | no | If true, include phantom (stale-reference) nodes in the snapshot. Default false. |

### `list_all_nodes`

Use when: enumerating every known Thread node (including phantoms) or building a device-by-device inventory. Returns: {nodes:[{eui64, friendly_name, role, area, device_id, status, first_seen, last_seen, last_rssi, last_lqi, ...}], count}. Ordered most-recently-seen first. Use ``status_filter='phantom'`` to drill into stale-reference cleanup candidates. Caveats: SQLite-cached; check meta.cache_age_s.

**Arguments:**

| Name | Type | Required | Description |
|---|---|---|---|
| `status_filter` | enum(healthy/stale/offline/phantom) | no | Restrict to nodes whose status matches this value. |

### `analyze_node`

Use when: drilling into a single suspected-bad EUI-64. One-call structured payload: node metadata, parent + neighbors, open issues, recent closed issues, unified timeline (events + issue lifecycle + observer events), per-node baselines (parent_change rate this period vs. previous, status_change count), and full playbook entries matching the union of issue kinds. Prefer over composing list_all_nodes + list_active_issues + query_history + lookup_playbook by hand. Returns: rich JSON keyed by section. Caveats: timeline_hours and baseline_days are capped; very large windows truncate.

**Arguments:**

| Name | Type | Required | Description |
|---|---|---|---|
| `eui64` | string | yes |  |
| `timeline_hours` | integer | no |  |
| `baseline_days` | integer | no |  |

### `list_topology_history`

Tier 4. List topology snapshot summaries (id, captured_at, hash, partition_id, node_count, link_count) newest-first. Snapshot bodies are NOT returned — use ``get_topology_history_entry`` or ``diff_topology_history`` to drill in.

**Arguments:**

| Name | Type | Required | Description |
|---|---|---|---|
| `since` | string | no | ISO-8601 lower bound |
| `until` | string | no | ISO-8601 upper bound |
| `limit` | integer | no |  |

### `get_topology_history_entry`

Tier 4. Return a persisted topology snapshot row. Pass ``snapshot_id`` to fetch one by id, or ``at`` (ISO-8601) to fetch the most-recent snapshot captured on or before that time. With no arguments, returns the newest snapshot.

**Arguments:**

| Name | Type | Required | Description |
|---|---|---|---|
| `snapshot_id` | integer | no |  |
| `at` | string | no | ISO-8601 timestamp |

### `diff_topology_history`

Tier 4. Return a structured diff between two topology snapshots: added/removed nodes, per-node role/partition/parent transitions, and added/removed links. ``snapshot_id_a`` is the older / baseline, ``snapshot_id_b`` is the newer / candidate.

**Arguments:**

| Name | Type | Required | Description |
|---|---|---|---|
| `snapshot_id_a` | integer | yes |  |
| `snapshot_id_b` | integer | yes |  |

## Counter time-series (Phase 4)

### `get_counter_series`

Use when: investigating whether a node's MAC/MLE counters are climbing (tx_retry, tx_err_cca, parent_change, attach_attempt). Returns the time-series of selected counter values for one node over [since, until], plus per-counter deltas (last - first). Detects counter resets (re-attach) and reports them explicitly instead of misreading them as a huge negative spike. Returns: {eui64, since, until, resolution, series: [{observed_at, counters}, ...], deltas: {<name>: {delta, reset_detected, first, last}}}. Caveats: requires Phase 4 schema (v19+); samples only exist for ticks recorded after upgrade.

**Arguments:**

| Name | Type | Required | Description |
|---|---|---|---|
| `eui64` | string | yes |  |
| `counter_names` | array | no |  |
| `since` | string | no | ISO-8601; default 6h ago |
| `until` | string | no | ISO-8601; default now |
| `resolution` | enum(raw/5min) | no |  |

### `compare_node_counters`

Use when: a node looks unhealthy and you want to know whether a peer on the same partition is degrading the same way. Returns counter series for two nodes side-by-side over the same window, plus a peer_summary flagging counters where one side's delta is at least 2x the other. Returns: {a: {series, deltas}, b: {series, deltas}, peer_summary: {flagged, flagged_count}}. Caveats: requires Phase 4 schema (v19+); use list_all_nodes to find a healthy peer first.

**Arguments:**

| Name | Type | Required | Description |
|---|---|---|---|
| `eui64_a` | string | yes |  |
| `eui64_b` | string | yes |  |
| `counter_names` | array | no |  |
| `since` | string | no |  |
| `until` | string | no |  |
| `resolution` | enum(raw/5min) | no |  |

## History and events

### `query_history`

Tier 4 unified timeline. Return a single newest-first stream that merges canonical events, issue open/close lifecycle, and observer (addon/OTBR/Matter Server) outage windows over a time range. Each row is normalized to {ts, source, kind, eui64?, severity?, details, ref_id} so an AI consultant can correlate Thread-side, issue-side and observer-side activity in one round-trip. Filter by eui64, kind list, or source list.

**Arguments:**

| Name | Type | Required | Description |
|---|---|---|---|
| `since` | string | yes | ISO-8601 lower bound (inclusive). Required. |
| `until` | string | no | ISO-8601 upper bound (inclusive). Defaults to now. |
| `eui64` | string | no |  |
| `kinds` | array | no | Optional kind allow-list. Examples: ['attach','parent_change'], ['issue.opened','issue.closed'], ['observer.outage','observer.outage.ended']. |
| `sources` | array | no | Optional source allow-list. Defaults to all three. |
| `limit` | integer | no |  |

### `get_recent_logs`

Return recent add-on log lines from the add-on's internal file logger.

**Arguments:**

| Name | Type | Required | Description |
|---|---|---|---|
| `lines` | integer | no | Number of log lines to return (default 100, max 200). |

## Issues and reasoning

### `list_active_issues`

Return all currently-open Thread network issues from the SQLite issues table.

*No arguments.*

### `close_issue`

Manually close an active issue by id.

**Arguments:**

| Name | Type | Required | Description |
|---|---|---|---|
| `id` | integer | yes |  |

## Discovery and sync

### `sync_ha_devices`

Use when: HA shows a Thread device the addon hasn't seen yet, or after a fresh commission, or when phantom counts look wrong. Queries the HA device registry for Thread/Zigbee devices and correlates IEEE addresses with extracted EUI64 nodes. Auto-populates friendly_name and device_id for matching nodes. Returns: {matched, updated, ...}. Caveats: This is a mutation (writes friendly_name/device_id back to SQLite); not a read tool.

*No arguments.*

### `list_thread_datasets`

Return the Thread Border Router credential datasets known to Home Assistant (network_name, extended_pan_id, channel, source, preferred). Pair with get_node_metadata or analyze_node to determine whether a node reporting an unexpected extended_pan_id is on a stale Thread dataset. Cached for 5 minutes.

*No arguments.*

### `list_otbr_candidates`

Return Supervisor add-ons that look like OpenThread Border Router hosts (slug or name contains 'openthread', 'otbr', or 'silabs-multiprotocol'). Use to discover the slug to feed into set_otbr_slug.

*No arguments.*

### `set_otbr_slug`

Set the OTBR add-on slug used by the background ingestion loop. Resets the cursor so the next poll will re-scan all currently-available log lines.

**Arguments:**

| Name | Type | Required | Description |
|---|---|---|---|
| `slug` | string | yes |  |

### `ingest_now`

Run one OTBR ingestion pass synchronously: fetch logs from Supervisor, parse new lines, insert canonical events. Returns line/event counts.

**Arguments:**

| Name | Type | Required | Description |
|---|---|---|---|
| `slug` | string | no | Optional slug override. |

### `get_ingest_state`

Return the current OTBR ingestion state: configured slug, lines processed, events inserted, last event timestamp, last run timestamp, last error.

*No arguments.*

## Storage and config

### `get_storage_stats`

Return SQLite store stats (schema version, file size, row counts per table, oldest/newest event timestamps) plus the active time-series backend.

*No arguments.*

### `get_timeseries_health`

Probe the time-series backend (Influx if configured, else SQLite fallback) and return status.

*No arguments.*

### `get_config`

Return the typed add-on configuration (merged from /data/options.json plus env overrides).

*No arguments.*

## Playbooks

### `list_playbooks`

Tier 4. Return summaries (id, title, applies_to) of every Thread/Matter playbook in the bundled corpus. Use ``lookup_playbook`` to fetch full entries.

*No arguments.*

### `lookup_playbook`

Tier 4. Return playbook entries matching one of: an exact ``playbook_id``; an issue ``kind`` (returns every playbook whose applies_to includes the kind); or a free-text ``query`` (case-insensitive substring across id/title/summary). Each entry includes summary, evidence_to_collect, remediation_steps, references.

**Arguments:**

| Name | Type | Required | Description |
|---|---|---|---|
| `playbook_id` | string | no |  |
| `kind` | string | no |  |
| `query` | string | no |  |

## Home Assistant / Supervisor lifecycle

### `ha_get_addon_state`

Return Supervisor's view of this add-on: install state, current version, latest available version, boot/watchdog flags, ingress URL, and raw info. Use this from VS Code to verify a deploy without opening the HA UI.

*No arguments.*

### `ha_get_addon_logs`

Return the tail of the Supervisor container log for an add-on. Defaults to this add-on (self) when ``slug`` is omitted; pass a Supervisor add-on slug (e.g. ``core_openthread_border_router``, ``core_matter_server``) to fetch that add-on's container log instead. Captures s6-overlay/startup output that the in-process Python logger misses. Use this to diagnose crash loops, boot failures, or correlate OTBR/Matter server events with Thread mesh state.

**Arguments:**

| Name | Type | Required | Description |
|---|---|---|---|
| `lines` | integer | no | Lines to return (default 200, max 1000). |
| `slug` | string | no | Supervisor add-on slug. Omit (or null) for this add-on's own logs. |

### `ha_get_supervisor_logs`

Return the tail of the Home Assistant Supervisor's own log. Useful for diagnosing why Supervisor rejected or killed the add-on (permissions, port conflicts, AppArmor, image pull failures).

**Arguments:**

| Name | Type | Required | Description |
|---|---|---|---|
| `lines` | integer | no | Lines to return (default 200, max 1000). |

### `ha_check_for_update`

Force Supervisor to re-scan add-on repositories, then report current vs latest version. Returns {current, latest, update_available, auto_update, state}. Use right after pushing a new version bump to avoid waiting for Supervisor's periodic poll.

*No arguments.*

### `ha_restart_addon`

Ask Supervisor to restart this add-on (fast; no image rebuild). Use after config or option changes to verify behaviour without a full deploy.

*No arguments.*

### `ha_update_addon`

Update this add-on to the latest version available in the store (equivalent to clicking 'Update' in the HA UI). Supervisor pulls the new image / rebuilds from source and restarts. Resolves the store-side slug from /store/addons (NOT /addons/self/info, whose slug carries a repo-hash prefix that the store endpoint rejects on some installs, silently clearing the install). Pass dry_run=true to verify the resolved endpoint without dispatching the update. Pair with ha_check_for_update first.

**Arguments:**

| Name | Type | Required | Description |
|---|---|---|---|
| `dry_run` | boolean | no | If true, resolve the slug and report what endpoint would be called, without POSTing. Default false. |

### `ha_rebuild_addon`

Ask Supervisor to rebuild this add-on from its repository source, then restart. Use after pushing a new commit so VS Code can complete the change→deploy→observe loop without manual uninstall/reinstall.

*No arguments.*

### `ha_set_auto_update`

Enable or disable Supervisor's auto-update flag for this add-on.

**Arguments:**

| Name | Type | Required | Description |
|---|---|---|---|
| `enabled` | boolean | yes | True to enable, false to disable. |

### `ha_reinstall_addon`

Uninstall then reinstall this add-on from the store. Destructive: clears the add-on container and terminates the MCP process making the call (the HTTP response will be cut short). Treat connection-reset as expected success and poll ha_get_addon_state afterwards.

*No arguments.*
