# Backlog Roadmap

This document is the repository-tracked execution plan for the remaining GitHub backlog.
It turns the open issues into a dependency-ordered sequence so implementation lands in coherent slices instead of as an unstructured queue.

Planning principles:

- Backend computes facts; UI renders them. Keep business logic out of the dashboard and prompts.
- Prefer lightweight validation loops during implementation; avoid full expensive ingest cycles unless a slice specifically needs them.
- Reuse shared backend payloads for UI, MCP, and AI so every consumer sees the same evidence.
- Close grouped backlog items together when one implementation actually resolves the shared scope.

## Current state

Closed foundation work already landed:

- #14 chat options and runtime enforcement
- #16 MCP descriptions, glossary resource, and generated reference docs
- #33 graph recent-change overlays and history summary
- #66 evaluator-guided direct-chat answer retry loop

Open issues still driving execution order:

- #7 MCP: add SSE / Streamable-HTTP transport for HA MCP-Client compatibility
- #8 Docs: HA MCP-Client setup walkthrough for Thread Mesh Detective
- #21 HA integration: device + entities + Repairs + events + blueprint
- #6 [Epic] Agentic AI chat integration sprint
- #5 Redesign issue definitions (tracking)

## Phase 0: backlog hygiene

Status: in progress

Goals:

- Collapse duplicate execution threads where one implementation already resolved multiple diagnostics or graph issues.
- Keep #5 scoped to issue-definition redesign work instead of using it as the umbrella for all remaining product work.
- Keep documentation, GitHub issue state, and the runtime surface synchronized after each completed slice.

Exit criteria:

- Duplicate or already-landed issues are closed with verification notes.
- The remaining issue list maps to real workstreams rather than historical fragments.

## Phase 1: transport compatibility

Primary issue: #7

Goals:

- Add SSE or Streamable-HTTP MCP transport support so Home Assistant MCP clients can connect without relying on the current JSON-RPC-only path.
- Preserve the existing tool/resource contract while expanding transport compatibility.

Exit criteria:

- MCP transport works with Home Assistant MCP client expectations.
- Transport capability is documented and validated with a focused client-level test loop.

Dependencies:

- None. This is the next execution slice because #8 depends on it.

## Phase 2: operator setup documentation

Primary issue: #8

Goals:

- Write the Home Assistant MCP-client setup walkthrough against the transport that actually ships.
- Document prerequisites, add-on options, MCP endpoint shape, and basic verification steps.

Exit criteria:

- A user can configure the HA MCP client against this add-on without reverse-engineering the repo.
- The walkthrough reflects the current tool/resource surface and references the generated MCP docs.

Dependencies:

- Depends on #7 so the transport and URLs are stable before documenting them.

## Phase 3: Home Assistant product wiring

Primary issue: #21

Goals:

- Correlate Thread nodes to Home Assistant devices and entities.
- Add Repairs, events, and a blueprint-worthy operator workflow surface.
- Keep the evidence model server-side so UI, MCP, and chat can all consume the same identity and diagnostic facts.

Exit criteria:

- Operators can pivot from mesh nodes to HA objects without manual cross-referencing.
- Repair/event surfaces exist for meaningful operator actions, not just raw telemetry.
- Integration behavior is covered by focused tests rather than dashboard-only checks.

Dependencies:

- Independent of #7 and #8 at the runtime level, but easier to land after the MCP transport/docs work because the external integration story will be clearer.

## Phase 4: agentic AI integration hardening

Primary issue: #6

Goals:

- Finish the remaining agentic chat/integration sprint work that is not already closed under #14 and #66.
- Ensure chat transport, optioning, answer validation, and shared evidence payloads behave like one coherent product surface.

Exit criteria:

- Direct chat, MCP usage, and Home Assistant-facing AI integration all follow the same grounding and evidence rules.
- Remaining sprint tasks are either completed or split into smaller tracked implementation issues.

Dependencies:

- Benefits from Phases 1-3 because transport compatibility, docs, and HA identity wiring reduce ambiguity for AI-assisted flows.

## Phase 5: issue-definition redesign

Primary issue: #5

Goals:

- Redesign issue/rule definitions after the stronger diagnostics, graph facts, and HA correlations are in place.
- Avoid duplicating logic that belongs in topology, diagnostics, or integration layers.

Exit criteria:

- Issue candidates are backed by stable backend evidence.
- The issue surface is explainable, testable, and useful to UI, MCP, and AI consumers.
- Rule logic is narrower and less redundant than the current placeholder system.

Dependencies:

- Last by design. This work should consume the stronger evidence model delivered by earlier phases instead of guessing it up front.

## Recommended execution order

1. #7 transport compatibility
2. #8 HA MCP-client walkthrough
3. #21 HA device/entity/repair wiring
4. #6 remaining agentic AI integration work
5. #5 issue-definition redesign

## Validation strategy by phase

- For transport and API slices: use focused HTTP or client-compatibility tests before broader manual checks.
- For documentation slices: verify paths, endpoint names, and option names against the live code rather than hand-maintained notes.
- For HA integration slices: prefer fixture-backed tests and focused API checks over full ingest loops.
- For issue redesign: validate rules against persisted evidence and targeted scenario tests, not prompt-only reasoning.
