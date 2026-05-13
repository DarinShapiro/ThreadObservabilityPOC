# Overnight Progress — Phase 4 Background Diagnostics

_Morning report. You went to bed; I kept building. Pytest green at every gate, all changes pushed to `main`._

## What shipped

### Commit `c1bb142` — Phase 4 backend (#18, #19, #22)
- **Schema v20 → v22** in `storage/sqlite_store.py`
  - `assessment_schedule` — single-row state machine, intentionally **not** wiped by `reset_persistent_state` (state survives addon restarts/updates by design)
  - `assessment_findings` — `finding_key` dedup, `suppress_until`, `seen_count` (re-occurrence bumps count + keeps max confidence)
  - `assessment_feedback` — outcome capture for quality metrics
- **`services/assessment/scheduler.py`** — adaptive cadence state machine
  - States: `probation → relaxing → steady → heightened → engaged → disabled`
  - Healthy verdicts: exponential decay of interval (×2, capped)
  - `investigate`: drops to `heightened` with halved interval
  - Daily call budget with UTC-midnight rollover (defaults to 12/day) — `force=True` cannot bypass exhaustion
- **`services/assessment/engine.py`** — verdict envelope pipeline
  - Strict JSON parser: rejects bad verdicts, requires ≥2 evidence for `investigate`, caps headline at 120 chars, strips markdown fences
  - `finding_key_for(env)` = `sha1(eui64 || '|' || finding_type)` → stable dedup
  - One retry on parse failure → degrades to synthetic `parse_failure` `ok` verdict so loop never crashes
  - Persists `investigate` findings; `ok` clears open findings with same key; suppressed keys return early without spending budget
  - `VerdictAgent` is a `Protocol` so HA `conversation.process` integration from #10 can plug in later with no engine changes
- **`services/assessment/feedback.py`** — `mark_outcome` (resolved/wrong/ignored_dismissed/ignored_expired) + `quality_summary` rolling precision + noisy-signal-type list (>25% wrong, n≥3)
- **4 new MCP tools** in `api/mcp_tools.py`: `get_assessment_state`, `list_assessment_findings`, `mark_finding_outcome`, `get_assessment_quality`
- **Config**: `AssessmentConfig` pydantic model + `assessment:` block in `config.yaml` (both `options:` and `schema:`)
- **Version**: `0.10.6 → 0.11.0`
- **CHANGELOG**: new `0.11.0` entry summarizing the above
- **Tests**: 36 new unit tests across 4 files. Full suite **286 passed / 1 skipped**.

### Commit `<endpoints>` — HTTP endpoints
- `GET  /v1/assessment/state` — proxies `AssessmentScheduler.snapshot()`; returns `state`, `current_interval_seconds`, `next_check_at`, `calls_today`, `daily_budget`, etc.
- `GET  /v1/assessment/findings?state=open&limit=50`
- `POST /v1/assessment/findings/{id}/dismiss` (body: `{"suppress_seconds": 86400}`)
- `POST /v1/assessment/findings/{id}/feedback` (body: `{"outcome": "resolved|wrong|ignored_dismissed|ignored_expired", "notes": "..."}`)
- `GET  /v1/assessment/quality?since_hours=168` — precision + noisy types

All compute is **server-side** per your separation-of-concerns rule — these endpoints emit shaped JSON ready for any client (dashboard, MCP, AI reasoner).

## What I intentionally did **not** do

1. **Dashboard UI (#20)** — I held off on editing `dashboard.html`. Reasons:
   - The dashboard is your primary smoke-test surface; breaking it overnight would block you in the morning.
   - The wiring is small now that endpoints exist (header chip + banner + side panel). Easier to do together when you can eyeball it.
   - Endpoints are ready and shaped so the JS only needs to fetch + render.
2. **#21 HA device + Repairs entity** — user-gated; you said you wanted to discuss approach.
3. **No background loop wired into the FastAPI lifespan yet** — engine is callable but not scheduled. I left this as a deliberate gate because:
   - It needs a real `VerdictAgent` (HA `conversation.process` integration from #10) to be useful.
   - Without a wired agent it'd just run the degrade path on a timer — harmless but pointless.
   - Once #10 lands or you wire a stub agent, adding the loop to `_lifespan` is ~10 lines using existing `_periodic` helper.

## How to validate this morning

```powershell
cd C:\Users\darin_jwxgczt\Documents\ThreadPOC\addons\thread-observability\app
..\..\..\.venv\Scripts\python.exe -m pytest -q --tb=short
```
Expect: `286 passed, 1 skipped`.

To eyeball the new endpoints without rebuilding the addon, you can ask MCP:

```
mcp_thread-observ_get_assessment_state
mcp_thread-observ_list_assessment_findings
mcp_thread-observ_get_assessment_quality
```

(They'll return empty/initial state because no background loop has run yet — that's expected.)

## Known unknowns / open questions for the morning

- **Real LLM envelope quality** — the parser is strict by design; first real verdicts may fail validation and exercise the degrade-on-parse-failure path. Expected and recoverable, but worth watching the logs once #10 lands.
- **`finding_type` taxonomy** — engine accepts whatever the verdict returns. Worth pinning a controlled vocabulary in the SYSTEM_PROMPT before turning the loop on for real (otherwise dedup will fragment across slightly different spellings).
- **`suppress_until` semantics in the engine** — early return short-circuits before calling the agent at all, saving budget. Make sure that's the intent (yes per #22 design) and not "still call, just don't surface".
- **HA Repairs entity (#21) version-sensitivity** — Supervisor API surface for repairs has shifted across HA versions; will need to gate that work behind a supervisor capability probe.

## Suggested order when you're back

1. Skim the diff in `c1bb142` (~2k lines, mostly tests + schema strings).
2. Pull `main`, run pytest locally to mirror the gate I ran.
3. Decide on `finding_type` vocabulary before #20 UI work — that pins what the dashboard headline strings look like.
4. Pair on #20 (header chip + banner + side panel) — endpoints are ready.
5. Wire the engine into the FastAPI lifespan once #10 (HA conversation.process) is unblocked.

— GH Copilot
