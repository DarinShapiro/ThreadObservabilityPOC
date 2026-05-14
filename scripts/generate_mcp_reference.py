from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_SRC = REPO_ROOT / "addons" / "thread-observability" / "app" / "src"
if str(APP_SRC) not in sys.path:
    sys.path.insert(0, str(APP_SRC))

from thread_observability.api.mcp_tools import RESOURCE_DEFS, TOOL_DEFS  # noqa: E402

DOC_PATH = REPO_ROOT / "documentation" / "06-mcp-tools-reference.md"


def _format_default(schema: dict[str, object]) -> str:
    if "default" not in schema:
        return ""
    return str(schema["default"])


def render_reference(now: datetime | None = None) -> str:
    generated_at = (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    lines: list[str] = [
        "# MCP Tools Reference",
        "",
        "This document is generated from `thread_observability.api.mcp_tools.TOOL_DEFS` and `RESOURCE_DEFS`.",
        f"Generated at: `{generated_at}`",
        f"Tool count: `{len(TOOL_DEFS)}`",
        f"Resource count: `{len(RESOURCE_DEFS)}`",
        "",
        "Shared background resource: [glossary.md](glossary.md)",
        "",
        "## Resources",
        "",
    ]

    for resource in RESOURCE_DEFS:
        lines.extend([
            f"### `{resource['name']}`",
            "",
            f"- URI: `{resource['uri']}`",
            f"- MIME type: `{resource['mimeType']}`",
            f"- Description: {resource['description']}",
            "",
        ])

    lines.extend(["## Tools", ""])
    for tool in TOOL_DEFS:
        lines.append(f"### `{tool['name']}`")
        lines.append("")
        lines.append(tool["description"])
        lines.append("")

        properties = tool.get("inputSchema", {}).get("properties", {})
        required = set(tool.get("inputSchema", {}).get("required", []))
        if not properties:
            lines.append("Arguments: none")
            lines.append("")
            continue

        lines.append("| Argument | Type | Required | Default | Description |")
        lines.append("| --- | --- | --- | --- | --- |")
        for name, schema in properties.items():
            schema_type = str(schema.get("type", "object"))
            required_text = "yes" if name in required else "no"
            default_text = _format_default(schema)
            description = str(schema.get("description", "")).replace("\n", " ").strip()
            lines.append(f"| `{name}` | `{schema_type}` | {required_text} | `{default_text}` | {description} |")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> None:
    DOC_PATH.write_text(render_reference(), encoding="utf-8")


if __name__ == "__main__":
    main()
