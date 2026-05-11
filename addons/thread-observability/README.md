# Thread Observability Add-on

This add-on ingests Thread/Matter logs, enriches with Home Assistant metadata, and exposes API/MCP endpoints for diagnostics.

## Included in this scaffold

- Add-on manifest and build metadata
- Two-process service model in one container:
  - Core service process
  - MCP service process
- Minimal runnable FastAPI endpoints:
  - Core API on port 8099 (`/health`, `/v1/health/snapshot`, `/v1/issues/active`, `/v1/topology`)
  - MCP API on port 8100 (`/health`, `/mcp/tools`, `/mcp/call/{tool_name}`)
- Placeholder Python package layout
- GitHub Actions CI workflow for add-on lint/build

## Not included yet

- Full ingestion implementation
- UI implementation
- MCP tool implementations

## Local development notes

1. Build the add-on image with Home Assistant add-on tooling.
2. Install from repository URL in Home Assistant.
3. Configure options in add-on settings.
