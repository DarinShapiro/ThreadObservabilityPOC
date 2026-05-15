# Script Helpers

This folder contains local developer helpers and repository-maintenance scripts.
They are not imported by the add-on at runtime.

- `assess.ps1` runs focused assessment checks against a live environment.
- `chat-smoke.ps1` exercises chat flows against the add-on.
- `dashboard-loop.ps1` repeats dashboard-oriented checks during live validation.
- `generate_mcp_reference.py` regenerates the MCP reference documentation from the live tool registry.
- `test_real_logs.py` is an ad hoc OTBR parser smoke helper for quickly checking a few real log lines outside the automated test suite.