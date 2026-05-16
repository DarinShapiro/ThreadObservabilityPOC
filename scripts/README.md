# Script Helpers

This folder contains local developer helpers and repository-maintenance scripts.
They are not imported by the add-on at runtime.

- `assess.ps1` runs focused assessment checks against a live environment.
- `api_surface_smoke.py` runs the HTTP API in-process with FastAPI `TestClient`, seeded SQLite state, and local stubs so the API surface can be regression-tested without deploying to Home Assistant. It covers health, dev status, topology/history, partitions, routing, stale links, network data, assessment, chat telemetry, and the prompt corpus through `/v1/chat/turn`.

Run it locally with `PYTHONPATH=addons/thread-observability/app/src python scripts/api_surface_smoke.py`.
- `chat-smoke.ps1` exercises chat flows against the add-on, including persisted transcript inspection through `/v1/chat/transcript/{conversation_id}` when transcript persistence is enabled. Semantic pass/fail now comes from `chat_smoke_evaluator.py`, which calls an OpenAI-compatible reviewer model using `THREAD_OBS_SMOKE_EVAL_PROVIDER`, `THREAD_OBS_SMOKE_EVAL_BASE_URL`, `THREAD_OBS_SMOKE_EVAL_MODEL`, `THREAD_OBS_SMOKE_EVAL_API_KEY`, and optional `THREAD_OBS_SMOKE_EVAL_TEMPERATURE`.
- `direct_chat_prompt_smoke.py` is the prompt-focused Python smoke path: it takes a list of pre-authored likely user queries, runs them through the real `direct_chat_turn` orchestration in-process, uses AI to judge each final answer, and when a case fails asks AI to review the full transcript including the embedded reviewer/audit step. It also supports model comparison with `--targets <json>`.
- `dashboard-loop.ps1` repeats dashboard-oriented checks during live validation.
- `generate_mcp_reference.py` regenerates the MCP reference documentation from the live tool registry.
- `test_real_logs.py` is an ad hoc OTBR parser smoke helper for quickly checking a few real log lines outside the automated test suite.