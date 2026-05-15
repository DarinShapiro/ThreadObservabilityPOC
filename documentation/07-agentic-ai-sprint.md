# Sprint Design: Agentic AI Integration

**Status:** Draft for review — *do not implement until accepted.*
**Owner:** @darinshapiro
**Tracking epic:** GitHub Issue #6 (filed alongside this doc)

---

## 1. Goal

Let a user **stand on the Thread Mesh Detective diagnostics page and chat
with an AI agent about their Thread network** — using their existing Home
Assistant conversation agent as the LLM, and our MCP server as the tool
source. No API keys live in this add-on, ever.

The same MCP tools should remain available from *every* HA AI surface
(Assist voice, mobile app, HA dashboard chat) — so a power user gets a
single, consistent agent that knows their Thread network whether they're
on this diagnostics page or asking by voice from the kitchen.

## 2. Non-goals

- We will **not** bake an LLM client (OpenAI / Anthropic / Ollama) into
  the add-on. HA's conversation agent already does that, configurably,
  with the user's chosen provider and key.
- We will **not** build our own auth/identity layer. The chat surface
  runs behind HA Ingress; the backend uses the existing Supervisor
  token to call HA's `conversation.process`.
- We will **not** ship voice STT/TTS in v1. Users who want voice
  already get it through Assist once the MCP integration is wired up.
- We will **not** add Thread-Observability-specific system prompts that
  override the user's agent settings. The agent is theirs; we only
  inject *page context* and *tools*.

## 3. Why this shape (architecture rationale)

Home Assistant 2025+ ships **two complementary MCP integrations** plus
a stable **Conversation / LLM API**:

| HA piece               | What it does                                              | How we use it                                    |
|------------------------|-----------------------------------------------------------|--------------------------------------------------|
| Conversation agents    | OpenAI / Anthropic / Google / Ollama / custom             | The LLM that actually talks to the user.         |
| LLM Hass API           | Tool-call abstraction over HA services & entities         | Untouched — we don't fight HA's own tools.       |
| MCP **Server** add-on  | Exposes HA's LLM tools as an MCP server (HA → outside LLM)| Out of scope for us.                             |
| MCP **Client** integ.  | Registers an *external* MCP server as a tool source       | **This is how our tools reach the user's agent.**|
| `conversation.process` | Service: text in → agent reply out, with `conversation_id`| Backbone of the in-page chat panel.              |

That gives us a clean split:

```
                ┌──────────────────────────────────────────┐
                │ Home Assistant (user's chosen LLM agent) │
                │   ├── LLM Hass API tools (HA-native)     │
                │   └── MCP-Client tool sources            │
                │         └── thread-observability MCP ◄───┼─── our addon, port 8100
                └──────────────────────────────────────────┘
                            ▲                          ▲
                            │ conversation.process     │ JSON-RPC / SSE
                            │                          │
       ┌────────────────────┴────┐              ┌──────┴───────┐
       │  Dashboard chat panel   │              │  Assist UI,  │
       │  (this sprint, in-page) │              │  mobile app, │
       │                         │              │  voice, etc. │
       └─────────────────────────┘              └──────────────┘
```

**Two user-visible surfaces, one backend.** The dashboard chat panel and
Assist voice both end up calling the *same* HA conversation agent, which
in turn calls the *same* MCP tools. There is exactly one place that
makes LLM choices: HA's integration page.

## 4. Integration paths offered to users

### Path A — HA-native (one-time setup, always available)

1. User opens HA → *Settings → Devices & services → Add integration → "MCP Client"*.
2. Enters `http://9e5048e8-thread-observability:8100/mcp/sse` (the add-on hostname).
3. Picks a conversation agent (existing or new).
4. From that moment on, Assist on phone / voice / panel / mobile app
   knows all 36 Thread Mesh Detective tools, automatically.

### Path B — In-page chat panel (the sprint deliverable)

A right-side drawer on `/dashboard.html` that:

- lists the user's HA conversation agents and lets them pick one,
- maintains a `conversation_id` so HA keeps short-term memory,
- sends each turn through our backend's `/v1/chat/turn`, which proxies
  to `conversation.process` with a small **page-context block**
  describing what the user is currently looking at,
- streams the reply back, surfaces tool-call traces in a collapsible
  "🔧 tools used" panel,
- collapses to a bottom sheet on narrow viewports.

Path B implies Path A — without the MCP Client registration, the agent
has no Thread tools and the chat is just "ask GPT about Thread in
general". The setup wizard in the chat panel will detect this and link
to the integration page.

## 5. Component breakdown / issues

| #   | Title                                                           | Phase | Notes                                                                 |
|-----|-----------------------------------------------------------------|-------|-----------------------------------------------------------------------|
| 6   | **Epic** — Agentic AI chat integration                          | —     | Tracker, links all of the below.                                      |
| 7   | MCP: add SSE / Streamable-HTTP transport                        | 1     | Required for HA's MCP-Client integration. JSON-RPC POST stays.        |
| 8   | Docs: HA MCP-Client setup walkthrough                           | 1     | README section + screenshot, plus a *"Setup required"* card in panel. |
| 9   | Dashboard: chat panel skeleton + agent picker                   | 2     | Pure UI shell, no real LLM calls yet (uses mocked replies).           |
| 10  | Backend: `/v1/chat/turn` proxy to `conversation.process`        | 2     | Includes Supervisor auth, conversation_id pass-through, error mapping.|
| 11  | Page-context injection                                          | 3     | Selected node, filters, time window, summary stats → `<context>` block. |
| 12  | Tool-call surfacing in chat UI                                  | 3     | Read tool_calls/intent_extras from the agent's response if present.   |
| 13  | Conversation persistence + retention                            | 4     | SQLite `chat_turns`, retention from existing config; per-conv export. |
| 14  | Add-on options: chat enable / default agent / page-context toggle | 4   | Defaults: enabled=false until user opts in (privacy).                 |
| 15  | Telemetry: `chat_turns` aggregations + `get_chat_stats` MCP tool| 5     | Tool & MCP-only; surfaces in `get_pipeline_health` envelope.          |

## 6. UX sketch (chat panel)

```
┌─────────────────────────────────────── Dashboard ────────────────────────────────┐
│  [ Network ] [ Nodes ] [ Logs ]              Last refresh 03:28  ⟳   💬 Chat (Δ) │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   ┌──────────────┐  │
│  │  Headline / hot-spots / partitions / nodes table        │   │  Chat panel  │  │
│  │                                                         │   │              │  │
│  │                                                         │   │ Agent: ▼     │  │
│  │                                                         │   │ Claude/HA    │  │
│  │                                                         │   │ ----------   │  │
│  │                                                         │   │ • "Why is    │  │
│  │                                                         │   │   Eve Door   │  │
│  │                                                         │   │   flapping?" │  │
│  │                                                         │   │ • "Show RX/  │  │
│  │                                                         │   │   TX trends" │  │
│  │                                                         │   │              │  │
│  │                                                         │   │ ┌──────────┐ │  │
│  │                                                         │   │ │ Ask…    >│ │  │
│  │                                                         │   │ └──────────┘ │  │
│  └─────────────────────────────────────────────────────────┘   └──────────────┘  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

Key behaviours:

- **Quick action: "Ask about this node"** appears when the user clicks
  a row in the nodes table. Inserts the node EUI64 into the prompt and
  bumps it into `page_context.selected_node`.
- **Suggested prompts** rotate based on what's anomalous in the
  current snapshot (e.g., "Why are there two partitions right now?"
  when `distinct_thread_networks > 1`).
- **Tool-call disclosure**: each agent message can have a fold-out
  list showing which MCP tools were called, with arguments and a link
  to the raw JSON result (the same one the agent saw). This keeps the
  AI legible — the user can verify the agent isn't hallucinating.
- **"New conversation"** button resets `conversation_id` (clears HA's
  short-term memory for this conversation only).

## 7. Page-context schema (v0 draft)

Sent on every turn from the panel JS to `/v1/chat/turn`:

```json
{
  "page": "dashboard",
  "viewport": "wide",
  "selected_node_eui64": "EE3F4567ABCDEF12",
  "filters": {
    "status": "stale",
    "role": null,
    "area": "Living Room",
    "search": ""
  },
  "time_window": "24h",
  "snapshot_summary": {
    "total_nodes": 15,
    "stale_nodes": 0,
    "distinct_thread_networks": 2,
    "data_age_seconds": 76.5,
    "active_issue_count": 0,
    "issue_detection_paused": true
  }
}
```

The backend renders this into a short system / user pre-amble so the
agent never has to call tools just to learn what page the user is on.
Token cost is bounded: only IDs and counts go in, not full payloads.

## 8. Privacy & safety posture

- **Opt-in.** Chat is disabled by default. Users enable it in add-on
  options. Page-context inclusion is a separate toggle (defaults on).
- **No API keys here.** The add-on never sees the user's LLM key —
  only HA's Supervisor token, scoped to the `conversation` and
  `services` HA APIs.
- **No transcript persistence in v1.** Conversations are
  in-browser-tab only; reload starts a fresh session. The agent
  rederives state from `page_context` + tools each turn. (See
  decision 3 in §9.)
- **Tool-call transparency.** Every LLM-initiated tool call is
  displayed to the user; the raw JSON result is one click away.
- **No write tools by default.** The MCP toolset is read-only today.
  When write tools land (e.g., `close_issue`, `set_otbr_slug`, future
  reboot/recommission), each will need explicit `agent_can_invoke:
  false` until vetted. Track in `mcp_tools.py` per-tool metadata.

## 9. Resolved design decisions (2026-05-12)

All five open questions resolved by @darinshapiro:

1. **Transport: sync now, hybrid streaming later.** Ship `conversation.process`
   sync in v1 (#10). Phase 5 adds a hybrid path that streams when the
   selected agent supports it and **automatically falls back to sync**
   when it doesn't (Ollama-via-HA, local intent agent, older HA
   builds). Wire format already reserves a `streaming` flag so the
   schema is forward-compatible.

2. **Tool exposure: all tools, richer descriptions, plus web search.**
   Do **not** curate down to a subset. Instead, invest in
   high-quality MCP tool descriptions and a per-tool "background"
   block so the agent gets the context it needs (HA version, OTBR
   version, Thread / Matter spec links, semantic notes on what each
   field means). Filed as **issue #16**. Also expose a web-search
   tool to the agent for looking up spec / errata / community
   knowledge — filed as **issue #17**.

3. **No conversation persistence in v1.** Context comes from the
   data we pull each turn, not from a long-running memory store.
   This means a refresh starts a clean session, and that's fine —
   the agent re-derives state from `page_context` + tool calls.
   Issue #13 closed as not planned for v1. Can be reopened later if
   transcript-search / "what did the AI say last week" becomes a
   real ask.

4. **Custom chat UI component.** Build our own small chat surface
   (~300 lines of JS). HA's frontend chat bits are internal /
   version-drifty, and we want full control of tool-call disclosure
   and page-context wiring. No change to #9.

5. **Suggested prompts come from `start_triage`.** The dashboard
   chat panel calls `start_triage` on open (and after each refresh
   tick), and renders its `recommended_next` plus a small set of
   triage-derived questions as the suggested prompts. No separate
   `get_suggested_prompts` MCP tool needed — `start_triage` already
   produces the right signal. Updated scope in #11.

## 10. New issues from the design review

- **#16** — Enrich MCP tool descriptions with versions, spec refs,
  field semantics, and per-tool background blocks (supports decision
  2 above; replaces the proposed curation strategy).
- **#17** — Add `web_search` MCP tool so the agent can pull in
  authoritative external references (Thread spec, Matter spec, HA
  release notes, vendor docs) as part of an answer.

## 11. Background Diagnostics (proactive AI assessment)

Reactive chat (a user opens the drawer and asks) is half the story.
The other half is **silent-by-default assessment**: the addon
quietly checks the network on an adaptive cadence and only surfaces
when there's something worth a conversation. Internally this
feature is called **Background Diagnostics**; the on-page indicator
label is **Adaptive Monitoring**.

### 11.1 Adaptive cadence (state machine with budget cap)

```
                  ┌──────────────┐
   install ──────►│ probation    │  every 15 min, for first 3 checks
                  └──────┬───────┘
                         │ all clear
                         ▼
                  ┌──────────────┐
                  │ relaxing     │  1h → 6h → 24h (doubling on clean)
                  └──────┬───────┘
                         │ N consecutive clean checks
                         ▼
                  ┌──────────────┐
                  │ steady       │  every 24h (max idle interval) ◄──────┐
                  └──────┬───────┘                                       │
                         │ verdict=investigate                            │ all clear
                         ▼                                                │ × M checks
                  ┌──────────────┐                                        │
                  │ heightened   │  30 min → 1h → 6h ─► relaxing ─────────┘
                  └──────┬───────┘
                         │ user investigates / finding open
                         ▼
                  ┌──────────────┐
                  │ engaged      │  every 5 min (decays after 60 min idle)
                  └──────────────┘
```

Configurable in addon options:

| Key | Default | |
|---|---|---|
| `assessment.enabled` | install-time radio | One-time prompt; runtime override via switch entity |
| `assessment.probation_interval_minutes` | 15 | |
| `assessment.probation_checks` | 3 | |
| `assessment.relaxing_initial_hours` | 1 | Doubles each clean check |
| `assessment.relaxing_max_hours` | 24 | The "steady" interval |
| `assessment.heightened_initial_minutes` | 30 | After a fresh finding |
| `assessment.heightened_max_hours` | 6 | Before falling back to relaxing |
| `assessment.engaged_interval_minutes` | 5 | While a chat investigation is active |
| `assessment.engaged_decay_minutes` | 60 | Engaged persists this long after chat closes |
| `assessment.daily_budget_calls` | 12 | Rolling-24h cap on assessment LLM calls |

Always-on rules:
- Run once on addon start/upgrade. (Schedule state persists across
  updates — we don't reset to probation on every release.)
- Event triggers (partition change, new node, OTBR restart, fresh
  stale-link cluster, etc.) fire off-cadence assessments, debounced
  to once per 60 s.
- Budget cap drops cadence-driven checks first when exceeded; event
  triggers are higher-signal and take priority.
- Schedule state persists in SQLite (`assessment_schedule`) and
  survives addon updates.

### 11.2 Verdict envelope

```json
{
  "verdict": "investigate" | "watch" | "ok",
  "severity": "watch" | "investigate" | "critical",
  "confidence": 0.0,
  "headline": "Eve Door & Window has changed parent 4 times in the last hour",
  "evidence": [
    {"tool": "get_counter_series", "key_finding": "parent_change_count delta = 4 (1h)"},
    {"tool": "get_mesh_state",     "key_finding": "parent flips between R-A and R-B"}
  ],
  "suggested_starter_prompt": "Why is Eve Door & Window flapping parents?",
  "evidence_id": "evid-c8af..."
}
```

`severity` is free-form for now (`watch`, `investigate`, and
eventually `critical` for true outages — OTBR unresponsive, all
routers offline, data age >30 min). Specifics will evolve as we
catalog signals (see `documentation/assessment-signals.md`).

### 11.3 Surfacing — four channels, one source of truth

| Channel | When | Audience |
|---|---|---|
| **HA Repairs** | Each `investigate` finding → `issue_registry/create_issue` with deep-link to evidence panel. Auto-clears when next assessment confirms resolved. | Humans, system-wide badge in HA sidebar. **Primary surface.** |
| **In-page banner** | When user is on the dashboard | "I noticed X. [Investigate with me] [Show me what you saw] [Not now] [Dismiss for 24h]" |
| **HA entities** | Always present | Automation surface for users who want push/voice/lights |
| **`thread_observability_finding` event** | Each assessment with a finding | Power-user automations needing structured payload |

Dedup key: `finding_key = hash(eui64 + finding_type)`. Re-confirmed
findings update existing repair/notification, do not duplicate.

### 11.4 HA Device + entity inventory

Registered via Supervisor discovery as a single `thread_observability`
device. Curated entity set, network-level not per-node:

**Sensors**

| Entity | State | Attributes |
|---|---|---|
| `binary_sensor.thread_observability_finding_active` | `on` if ≥1 open investigate finding | `top_finding`, `severity` |
| `sensor.thread_observability_open_findings` | count | per-finding list (capped) |
| `sensor.thread_observability_latest_finding` | headline (truncated) | full verdict envelope, `evidence_url` |
| `sensor.thread_observability_assessment_state` | `probation`/`relaxing`/`steady`/`heightened`/`engaged`/`disabled` | `since`, `reason` |
| `sensor.thread_observability_next_assessment` | timestamp | |
| `sensor.thread_observability_data_age_seconds` | seconds | already computed |
| `sensor.thread_observability_node_count` | total | `{stale, offline, healthy, distinct_partitions}` |
| `sensor.thread_observability_distinct_partitions` | integer | partition IDs |

**Buttons**

- `button.thread_observability_run_assessment` — force an off-cadence assessment (respects daily budget)
- `button.thread_observability_acknowledge_findings` — dismiss-all for 24h

**Switch**

- `switch.thread_observability_ai_assessments` — runtime override of the addon option

**Update entity**

- `update.thread_observability` — standard HA update flow

**Events**

- `thread_observability_finding` — fired on each new/re-confirmed finding
- `thread_observability_finding_cleared` — fired when a previously-open finding resolves

### 11.5 Header indicator (in-page)

Small chip in the dashboard header:

```
Adaptive Monitoring · steady · next check 14 h
```

States: `idle` (green dot, when disabled — but only shown if user enabled it
once), `steady`/`relaxing`/`probation` (green), `heightened` (amber),
`engaged` (blue), `assessing now` (animated). Hover for details. Click
opens the Background Diagnostics panel (configuration + history).

### 11.6 Cost expectations

| Install profile | Assessments / day | Notes |
|---|---|---|
| Healthy network, steady state | ~1 | One LLM call chain per day |
| Network with chronic open finding | 4–6 | Heightened mode |
| User actively investigating | ~12 / hr (engaged window) | Decays back after 60 min |

Sub-dollar/month for typical agents; trivial for self-hosted Ollama.
Whole feature gated behind `assessment.enabled` (opt-in at install).

## 12. Phases & rough sequencing (updated)

1. **Phase 1 — Transport + docs + tool enrichment.** Issues #7, #8,
   #16. Path A works end-to-end with HA's MCP Client and the tools
   the agent sees come with rich descriptions and background blocks.
2. **Phase 2 — Chat panel MVP.** Issues #9, #10. Sync turns,
   triage-derived suggested prompts, no page context yet.
3. **Phase 3 — Context-aware + web search.** Issues #11, #12, #17.
   Selection-aware prompts, tool-call surfacing, agent can call
   `web_search`.
4. **Phase 4 — Background Diagnostics.** Issues #18 (adaptive
   scheduler), #19 (assessment engine + verdict envelope), #20
   (in-page surfacing + header indicator + dismiss/suppress), #21
   (HA device + entities + repairs + events + blueprint), #22
   (feedback / outcome tool). #14 (options) gains the assessment
   config keys.
5. **Phase 5 — Polish.** Issue #15 (telemetry, including assessment
   precision and dismissal rate), hybrid streaming transport with
   sync fallback, voice (optional).

## 13. Out of scope (for now)

- Custom fine-tuned Thread model.
- Multi-user / multi-tenant chat sessions.
- Cross-installation aggregated learnings.
- Mobile-companion deep links.

---

*Review notes go in the epic (issue #6). Once accepted, child issues
move from "design draft" status to "ready" and we start Phase 1.*
