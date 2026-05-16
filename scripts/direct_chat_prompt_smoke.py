from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from api_surface_smoke import _seed_store
from chat_smoke_evaluator import evaluate_smoke_case, review_failed_smoke_case

from thread_observability.api.http_api import _render_chat_message
from thread_observability.config import AIConfig
from thread_observability.services import chat_memory, direct_chat
from thread_observability.storage.sqlite_store import SQLiteStore, reset_store_for_tests


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPTS = REPO_ROOT / "addons" / "thread-observability" / "app" / "tests" / "fixtures" / "chat_prompt_regression.json"


def _llm_call_delay_s() -> float:
    raw = str(os.getenv("THREAD_OBS_LLM_CALL_DELAY_S", "0.35") or "0.35").strip()
    try:
        delay = float(raw)
    except ValueError:
        return 0.35
    return max(0.0, delay)


def _install_direct_chat_delay() -> tuple[float, Any]:
    delay = _llm_call_delay_s()
    original = direct_chat._post_chat_completions
    if delay <= 0:
        return delay, original

    async def delayed_post_chat_completions(target, body):  # noqa: ANN001
        await asyncio.sleep(delay)
        return await original(target, body)

    direct_chat._post_chat_completions = delayed_post_chat_completions
    return delay, original


def _load_prompts(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    prompts = payload.get("prompts")
    if not isinstance(prompts, list):
        raise ValueError(f"prompt fixture at {path} is missing a prompts list")
    return [row for row in prompts if isinstance(row, dict) and str(row.get("prompt") or "").strip()]


def _load_targets(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        cfg = AIConfig(enabled=True, chat_backend="direct")
        target = direct_chat.require_direct_chat_target(cfg)
        return [
            {
                "name": f"{target.provider}:{target.model}",
                "target": target,
            }
        ]

    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("targets") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"target fixture at {path} is missing a targets list")

    loaded: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        provider = str(row.get("provider") or "").strip()
        model = str(row.get("model") or "").strip()
        base_url = str(row.get("base_url") or "").strip()
        api_key = str(row.get("api_key") or "").strip()
        api_key_env = str(row.get("api_key_env") or "").strip()
        if not api_key and api_key_env:
            api_key = str(os.getenv(api_key_env, "")).strip()
        if not provider or not model or not base_url:
            raise ValueError(f"invalid target row in {path}: provider, model, and base_url are required")
        if provider != "local" and not api_key:
            raise ValueError(f"invalid target row in {path}: missing api_key or api_key_env for {provider}:{model}")
        target = direct_chat.DirectChatTarget(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=float(row.get("temperature", 0.2)),
        )
        loaded.append({
            "name": str(row.get("name") or f"{provider}:{model}").strip(),
            "target": target,
        })
    if not loaded:
        raise ValueError(f"target fixture at {path} does not contain any valid targets")
    return loaded


def _transcript_event_kinds(transcript: dict[str, Any]) -> list[str]:
    events = transcript.get("events") if isinstance(transcript, dict) else []
    if not isinstance(events, list):
        return []
    return [str(event.get("kind") or "").strip() for event in events if isinstance(event, dict) and str(event.get("kind") or "").strip()]


def _initial_answer(transcript: dict[str, Any]) -> str:
    events = transcript.get("events") if isinstance(transcript, dict) else []
    if not isinstance(events, list):
        return ""
    for event in events:
        if not isinstance(event, dict) or event.get("kind") != "assistant_completion":
            continue
        response = event.get("response") if isinstance(event.get("response"), dict) else {}
        choices = response.get("choices") if isinstance(response.get("choices"), list) else []
        if not choices:
            continue
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def _reviewer_prompt(transcript: dict[str, Any]) -> str:
    events = transcript.get("events") if isinstance(transcript, dict) else []
    if not isinstance(events, list):
        return ""
    blocks: list[str] = []
    for event in events:
        if not isinstance(event, dict) or event.get("kind") not in {"audit_review", "answer_review"}:
            continue
        request = event.get("request") if isinstance(event.get("request"), dict) else {}
        messages = request.get("messages") if isinstance(request.get("messages"), list) else []
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                blocks.append(content.strip())
    return "\n\n".join(blocks)


def _default_ai_eval(case: dict[str, Any]) -> dict[str, Any]:
    category = str(case.get("category") or "").strip().lower()
    criteria = [
        "Pass only if the answer addresses the user's question directly and grounds its claims in evidence gathered during the turn.",
        "Fail if the answer tells the user to call internal MCP tools, backend functions, or services directly.",
        "Fail if the answer hides missing evidence instead of saying what remains uncertain.",
    ]
    if category == "history":
        criteria.append(
            "Fail if the answer makes a definitive historical change claim without explicit current-vs-historical anchors in the gathered evidence."
        )
    if category in {"rf", "counters"}:
        criteria.append(
            "Fail if the answer makes a definitive RF-causation claim without node-grounded RF evidence tied to the claimed condition."
        )
    if category == "node":
        criteria.append(
            "Fail if the answer labels a node stable or unstable without referring to actual recent attach, parent, partition, or availability evidence."
        )
    return {"pass_criteria": criteria}


def _judge_payload(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    transcript = result.get("transcript") if isinstance(result.get("transcript"), dict) else {}
    tool_calls = result.get("tool_calls") if isinstance(result.get("tool_calls"), list) else []
    return {
        "case_name": str(case.get("id") or case.get("prompt") or "unknown"),
        "conversation_id": str(result.get("conversation_id") or ""),
        "user_message": str(case.get("prompt") or "").strip(),
        "initial_answer": _initial_answer(transcript),
        "final_answer": str(((result.get("response") or {}).get("text") if isinstance(result.get("response"), dict) else "") or "").strip(),
        "tool_names": [str(row.get("name") or "").strip() for row in tool_calls if isinstance(row, dict) and str(row.get("name") or "").strip()],
        "transcript_event_kinds": _transcript_event_kinds(transcript),
        "reviewer_prompt": _reviewer_prompt(transcript),
        "ai_eval": case.get("ai_eval") if isinstance(case.get("ai_eval"), dict) else _default_ai_eval(case),
    }


def _failure_review_payload(case: dict[str, Any], result: dict[str, Any], judgment: dict[str, Any]) -> dict[str, Any]:
    base = _judge_payload(case, result)
    base.update(
        {
            "judge_summary": str(judgment.get("summary") or "").strip(),
            "judge_reasons": judgment.get("reasons") if isinstance(judgment.get("reasons"), list) else [],
            "transcript": result.get("transcript") if isinstance(result.get("transcript"), dict) else {},
        }
    )
    return base


async def _run_case(target: direct_chat.DirectChatTarget, case: dict[str, Any]) -> dict[str, Any]:
    prompt = str(case.get("prompt") or "").strip()
    conversation_id = f"direct-smoke-{str(case.get('id') or 'case')}-{datetime.now(tz=UTC).strftime('%Y%m%d%H%M%S%f')}"
    session_context = chat_memory.build_prompt_context(conversation_id)
    rendered_message = _render_chat_message(prompt, None, session_context)
    return await direct_chat.direct_chat_turn(
        target=target,
        message=prompt,
        rendered_message=rendered_message,
        conversation_id=conversation_id,
    )


def run_prompt_smoke(prompts_path: Path, targets_path: Path | None, limit: int | None) -> int:
    prompts = _load_prompts(prompts_path)
    if limit is not None:
        prompts = prompts[: max(0, limit)]
    targets = _load_targets(targets_path)
    _, original_post_chat_completions = _install_direct_chat_delay()

    all_results: list[dict[str, Any]] = []
    try:
        for target_row in targets:
            target_name = str(target_row["name"])
            target = target_row["target"]

            store = SQLiteStore(Path.cwd() / ".tmp-direct-chat-smoke.db")
            reset_store_for_tests(store)
            chat_memory.reset()
            _seed_store(store)
            try:
                for case in prompts:
                    result = asyncio.run(_run_case(target, case))
                    judgment = evaluate_smoke_case(_judge_payload(case, result))
                    failure_review = None
                    if judgment.get("verdict") != "pass":
                        failure_review = review_failed_smoke_case(_failure_review_payload(case, result, judgment))
                    all_results.append(
                        {
                            "target": target_name,
                            "case_id": str(case.get("id") or "unknown"),
                            "prompt": str(case.get("prompt") or "").strip(),
                            "judgment": judgment,
                            "failure_review": failure_review,
                            "duration_ms": int(result.get("duration_ms") or 0),
                        }
                    )
                    chat_memory.reset()
            finally:
                reset_store_for_tests(None)
                store.close()
                try:
                    (Path.cwd() / ".tmp-direct-chat-smoke.db").unlink(missing_ok=True)
                except OSError:
                    pass
    finally:
        direct_chat._post_chat_completions = original_post_chat_completions

    failures = [row for row in all_results if str((row.get("judgment") or {}).get("verdict") or "") != "pass"]

    for row in all_results:
        judgment = row["judgment"]
        print(
            f"[{row['target']}] {row['case_id']}: {judgment.get('verdict', 'fail').upper()} "
            f"({row['duration_ms']} ms) - {judgment.get('summary', '')}"
        )
        failure_review = row.get("failure_review")
        if isinstance(failure_review, dict):
            print(
                f"  transcript review: stage={failure_review.get('failure_stage', 'unknown')} "
                f"fix={failure_review.get('suggested_fix', '')}"
            )

    by_target: dict[str, tuple[int, int]] = {}
    for row in all_results:
        target = str(row["target"])
        passed, total = by_target.get(target, (0, 0))
        total += 1
        if str((row.get("judgment") or {}).get("verdict") or "") == "pass":
            passed += 1
        by_target[target] = (passed, total)

    print("\nModel summary:")
    for target, (passed, total) in by_target.items():
        print(f"- {target}: {passed}/{total} passed")

    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run pre-authored user queries directly through direct_chat_turn, judge the final answers with an AI reviewer, "
            "and review the full transcript with AI when a case fails."
        )
    )
    parser.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS, help="Path to the prompt corpus JSON.")
    parser.add_argument("--targets", type=Path, default=None, help="Optional JSON file listing model targets to compare.")
    parser.add_argument("--limit", type=int, default=None, help="Optional max number of prompt cases to run.")
    args = parser.parse_args()
    return run_prompt_smoke(args.prompts.resolve(), args.targets.resolve() if args.targets else None, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())