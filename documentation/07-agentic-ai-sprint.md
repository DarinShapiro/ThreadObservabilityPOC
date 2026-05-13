# Sprint Design: Agentic AI Integration

**Status:** Draft for review — *do not implement until accepted.*
**Owner:** @DarinShapiroMS
**Tracking epic:** GitHub Issue #6 (filed alongside this doc)

---

## 1. Goal

Let a user **stand on the Thread Observability diagnostics page and chat
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
   knows all 36 Thread Observability tools, automatically.

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
- **Transcripts are local.** Stored in our SQLite, pruned per the
  same retention policy. Export and clear buttons in the panel.
- **Tool-call transparency.** Every LLM-initiated tool call is
  displayed to the user; the raw JSON result is one click away.
- **No write tools by default.** The MCP toolset is read-only today.
  When write tools land (e.g., `close_issue`, `set_otbr_slug`, future
  reboot/recommission), each will need explicit `agent_can_invoke:
  false` until vetted. Track in `mcp_tools.py` per-tool metadata.

## 9. Open questions for review

1. **Streaming vs. request/response.** `conversation.process` is
   sync. For long agent turns, do we accept the latency or build a
   WebSocket relay to HA for partial deltas? *Recommendation: ship
   sync v1, add streaming in Phase 5 if users ask.*
2. **HA-native frontend pieces.** HA's frontend exposes a `<ha-conversation>`-
   ish chat component in some builds. Reuse for visual consistency
   inside our iframe, or build a small custom chat (more control,
   no version drift risk)? *Lean: custom, ~300 lines of JS.*
3. **MCP tool exposure granularity.** Should every existing MCP tool
   be exposed to the agent automatically, or do we need a curated
   subset to keep the tool list inside model context-window budgets?
   *Likely curate to ~15 most useful, expose the rest behind a
   "search_tools" meta-tool.*
4. **Conversation ID lifetime.** HA's default forgets after ~10 min.
   Do we keep our own session memory and re-prime the agent on
   reload? *Yes — and surface "this is a fresh conversation"
   visually whenever HA's memory has dropped.*
5. **Suggested prompts source.** Hard-coded heuristics in JS, or
   an MCP tool `get_suggested_prompts` that the panel calls each
   refresh? *MCP — keeps the logic on the server where AI consumers
   can also use it.*

## 10. Phases & rough sequencing

1. **Phase 1 — Transport + docs.** Issues #7, #8. No UI yet; just
   make Path A work end-to-end with HA's MCP Client integration.
2. **Phase 2 — Chat panel MVP.** Issues #9, #10. Static suggested
   prompts, sync turns, no page context yet.
3. **Phase 3 — Context-aware.** Issues #11, #12. Selection-aware
   prompts, tool-call surfacing.
4. **Phase 4 — Persistence + safety.** Issues #13, #14. Local
   transcripts, opt-in toggles, write-tool gating.
5. **Phase 5 — Polish.** Issue #15, streaming, voice (optional).

## 11. Out of scope (for now)

- Custom fine-tuned Thread model.
- Multi-user / multi-tenant chat sessions.
- Cross-installation aggregated learnings.
- Mobile-companion deep links.

---

*Review notes go in the epic (issue #6). Once accepted, child issues
move from "design draft" status to "ready" and we start Phase 1.*
