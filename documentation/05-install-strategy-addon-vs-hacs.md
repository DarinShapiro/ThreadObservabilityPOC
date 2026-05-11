# Install Strategy: Add-on Repository vs HACS

## Recommendation for ThreadPOC v1

Use a Home Assistant Add-on Repository as the primary deployment strategy.

Rationale:
- ThreadPOC needs long-running backend processes (core service + MCP service).
- Add-ons provide containerized runtime, options schema, startup/boot behavior, and data mounts.
- Add-on install flow is standard for users who need backend services.

## Where HACS Fits

HACS is a good optional companion channel, not the primary runtime channel for v1.

Use HACS for:
- Custom integration wrappers
- Lovelace cards and frontend enhancements
- Optional UX add-ons that sit on top of the add-on APIs

Do not rely on HACS alone for v1 backend runtime.

## Proposed Deployment Split

1. Add-on repository (required in v1)
- Hosts `addons/thread-observability`
- Installs and runs backend services
- Owns ingestion, enrichment, scheduler, MCP/API

2. HACS package (optional in v1.5+)
- Provides a native Home Assistant integration/card
- Consumes add-on APIs
- Offers richer in-HA dashboard UX

## User Experience Paths

### Path A: Add-on only (v1 default)
1. Add repository URL in Home Assistant Add-on Store.
2. Install add-on.
3. Configure options.
4. Use built-in web UI and API.

### Path B: Add-on + HACS companion (future)
1. Install add-on as in Path A.
2. Install companion integration/card through HACS.
3. Use Lovelace-native UX while backend remains in add-on.

## Decision Outcome

For this repository scaffold, the add-on repository model is implemented first.
HACS remains a desirable extension path for frontend/integration polish once v1 backend is stable.
