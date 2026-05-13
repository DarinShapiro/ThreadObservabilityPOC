"""Small server-side web search helper for direct chat.

Uses DuckDuckGo's HTML results page with a narrow parser to avoid adding a
heavier search dependency. Results are intentionally compact and bounded.
"""

from __future__ import annotations

import html
import re
from urllib.parse import urlencode

import httpx

_RESULT_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text or "")).strip()


async def search_web(query: str, *, max_results: int = 5) -> dict[str, object]:
    q = str(query or "").strip()
    if not q:
        return {"error": "query required", "results": []}
    limit = max(1, min(int(max_results), 10))
    url = "https://html.duckduckgo.com/html/?" + urlencode({"q": q})
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": "ThreadObservability/0.11 web-search",
    }
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        body = resp.text

    results: list[dict[str, str]] = []
    for match in _RESULT_RE.finditer(body):
        title = _strip_html(match.group("title"))
        href = html.unescape(match.group("url"))
        if not title or not href:
            continue
        results.append({"title": title, "url": href})
        if len(results) >= limit:
            break
    return {"query": q, "count": len(results), "results": results}