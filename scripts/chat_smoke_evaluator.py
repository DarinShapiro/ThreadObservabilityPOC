from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import httpx


_DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "local": "http://127.0.0.1:11434/v1",
}

_DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "cerebras": "llama3.1-8b",
    "local": "llama3.1",
}

_FAILURE_STAGES = {"initial_answer", "audit_prompt", "audit_verdict", "rewrite_answer", "unknown"}


def _llm_call_delay_s() -> float:
    raw = str(os.getenv("THREAD_OBS_LLM_CALL_DELAY_S", "0.35") or "0.35").strip()
    try:
        delay = float(raw)
    except ValueError:
        return 0.35
    return max(0.0, delay)


def _load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("evaluation payload must be a JSON object")
    return payload


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return text[: max(0, limit - 32)] + f"... [truncated {omitted} chars]"


def _extract_json_object(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("evaluator did not return JSON") from None
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("evaluator response must be a JSON object")
    return payload


def _build_json_repair_request(*, model: str, response_text: str, schema: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Rewrite the provided output as one valid JSON object only. "
                    "Do not add markdown, commentary, or surrounding text. "
                    "Preserve the original meaning as closely as possible."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Target schema:\n{schema}\n\n"
                    f"Original output to repair:\n{response_text}"
                ),
            },
        ],
        "temperature": 0,
        "stream": False,
    }


def _repair_json_object(
    text: str,
    *,
    base_url: str,
    api_key: str,
    model: str,
    schema: str,
) -> dict[str, Any]:
    repair_response = _post_chat_completions(
        base_url=base_url,
        api_key=api_key,
        body=_build_json_repair_request(model=model, response_text=text, schema=schema),
    )
    repair_content = _extract_message_text(repair_response)
    return _extract_json_object(repair_content)


def _format_eval_bundle(payload: dict[str, Any]) -> str:
    reviewer_prompt = str(payload.get("reviewer_prompt") or "").strip()
    bundle = {
        "case_name": payload.get("case_name"),
        "conversation_id": payload.get("conversation_id"),
        "user_message": payload.get("user_message"),
        "initial_answer": payload.get("initial_answer"),
        "final_answer": payload.get("final_answer"),
        "tool_names": payload.get("tool_names") or [],
        "transcript_event_kinds": payload.get("transcript_event_kinds") or [],
        "ai_eval": payload.get("ai_eval") or {},
        "reviewer_prompt_excerpt": _truncate(reviewer_prompt, 3000),
    }
    return json.dumps(bundle, indent=2, ensure_ascii=True)


def _build_eval_request(eval_payload: dict[str, Any], *, model: str, temperature: float) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a strict evaluator for live smoke tests of a Thread diagnostics assistant. "
                    "Judge whether the final answer satisfies the case rubric using the user question, final answer, "
                    "initial answer, tool usage, transcript event kinds, and reviewer prompt excerpt. "
                    "Return JSON only with this exact shape: "
                    '{"verdict":"pass"|"fail","summary":"short single-paragraph rationale",'
                    '"reasons":["specific failure or success reasons"],"confidence":0.0}. '
                    "Fail answers that overclaim beyond the evidence, tell the user to call internal tools, or rely on "
                    "missing historical or RF anchors while still answering definitively."
                ),
            },
            {
                "role": "user",
                "content": _format_eval_bundle(eval_payload),
            },
        ],
        "temperature": temperature,
        "stream": False,
    }


def _format_failure_bundle(payload: dict[str, Any]) -> str:
    transcript = payload.get("transcript") or {}
    bundle = {
        "case_name": payload.get("case_name"),
        "conversation_id": payload.get("conversation_id"),
        "user_message": payload.get("user_message"),
        "initial_answer": payload.get("initial_answer"),
        "final_answer": payload.get("final_answer"),
        "tool_names": payload.get("tool_names") or [],
        "judge_summary": payload.get("judge_summary"),
        "judge_reasons": payload.get("judge_reasons") or [],
        "reviewer_prompt_excerpt": _truncate(str(payload.get("reviewer_prompt") or "").strip(), 3000),
        "transcript_excerpt": _truncate(json.dumps(transcript, ensure_ascii=True), 12000),
    }
    return json.dumps(bundle, indent=2, ensure_ascii=True)


def _build_failure_review_request(review_payload: dict[str, Any], *, model: str, temperature: float) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are reviewing a failed direct-chat smoke case for a Thread diagnostics assistant. "
                    "Use the full transcript excerpt, including the audit/reviewer step, to diagnose why the case failed. "
                    "Return JSON only with this exact shape: "
                    '{"summary":"short paragraph","failure_stage":"initial_answer"|"audit_prompt"|"audit_verdict"|"rewrite_answer"|"unknown",'
                    '"findings":["specific observations tied to the transcript"],"suggested_fix":"short concrete fix",'
                    '"confidence":0.0}. '
                    "Use audit_prompt when the embedded reviewer instructions were too weak, audit_verdict when the reviewer had enough evidence to reject the answer but accepted it anyway, "
                    "rewrite_answer when the retry still failed after the reviewer intervened, and initial_answer when the failure is primarily in the first candidate answer."
                ),
            },
            {
                "role": "user",
                "content": _format_failure_bundle(review_payload),
            },
        ],
        "temperature": temperature,
        "stream": False,
    }


def _post_chat_completions(*, base_url: str, api_key: str, body: dict[str, Any]) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        delay = _llm_call_delay_s()
        if delay > 0:
            time.sleep(delay)
        with httpx.Client(timeout=60.0) as client:
            response = client.post(f"{base_url.rstrip('/')}/chat/completions", headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        raise RuntimeError(f"evaluator HTTP {exc.response.status_code}: {detail}") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"evaluator connection failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("evaluator response must be a JSON object")
    return payload


def _extract_message_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("evaluator response is missing choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise RuntimeError("evaluator response is missing message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("evaluator response is missing content")
    return content.strip()


def _normalize_verdict(payload: dict[str, Any]) -> dict[str, Any]:
    verdict = str(payload.get("verdict") or "fail").strip().lower()
    reasons = payload.get("reasons")
    if not isinstance(reasons, list):
        reasons = []
    normalized_reasons = [str(reason).strip() for reason in reasons if str(reason).strip()]
    summary = str(payload.get("summary") or "").strip()
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if verdict not in {"pass", "fail"}:
        verdict = "fail"
    return {
        "verdict": verdict,
        "summary": summary,
        "reasons": normalized_reasons,
        "confidence": max(0.0, min(1.0, confidence)),
    }


def _normalize_failure_review(payload: dict[str, Any]) -> dict[str, Any]:
    summary = str(payload.get("summary") or "").strip()
    failure_stage = str(payload.get("failure_stage") or "unknown").strip().lower()
    findings = payload.get("findings")
    if not isinstance(findings, list):
        findings = []
    normalized_findings = [str(finding).strip() for finding in findings if str(finding).strip()]
    suggested_fix = str(payload.get("suggested_fix") or "").strip()
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if failure_stage not in _FAILURE_STAGES:
        failure_stage = "unknown"
    return {
        "summary": summary,
        "failure_stage": failure_stage,
        "findings": normalized_findings,
        "suggested_fix": suggested_fix,
        "confidence": max(0.0, min(1.0, confidence)),
    }


def load_evaluator_settings(prefix: str = "THREAD_OBS_SMOKE_EVAL") -> dict[str, Any]:
    provider = str(os.getenv(f"{prefix}_PROVIDER", "openai")).strip().lower() or "openai"
    base_url = str(os.getenv(f"{prefix}_BASE_URL", _DEFAULT_BASE_URLS.get(provider, ""))).strip()
    model = str(os.getenv(f"{prefix}_MODEL", _DEFAULT_MODELS.get(provider, ""))).strip()
    api_key = str(os.getenv(f"{prefix}_API_KEY", "")).strip()
    temperature_text = str(os.getenv(f"{prefix}_TEMPERATURE", "0")).strip() or "0"
    try:
        temperature = float(temperature_text)
    except ValueError as exc:
        raise ValueError(f"invalid {prefix}_TEMPERATURE: {temperature_text}") from exc
    if not base_url:
        raise ValueError(f"missing {prefix}_BASE_URL")
    if not model:
        raise ValueError(f"missing {prefix}_MODEL")
    if provider != "local" and not api_key:
        raise ValueError(f"missing {prefix}_API_KEY")
    return {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "temperature": temperature,
    }


def evaluate_smoke_case(eval_payload: dict[str, Any], *, prefix: str = "THREAD_OBS_SMOKE_EVAL") -> dict[str, Any]:
    settings = load_evaluator_settings(prefix)
    request_body = _build_eval_request(
        eval_payload,
        model=str(settings["model"]),
        temperature=float(settings["temperature"]),
    )
    response_payload = _post_chat_completions(
        base_url=str(settings["base_url"]),
        api_key=str(settings["api_key"]),
        body=request_body,
    )
    content = _extract_message_text(response_payload)
    try:
        parsed = _extract_json_object(content)
    except ValueError:
        try:
            parsed = _repair_json_object(
                content,
                base_url=str(settings["base_url"]),
                api_key=str(settings["api_key"]),
                model=str(settings["model"]),
                schema='{"verdict":"pass"|"fail","summary":"short single-paragraph rationale","reasons":["specific failure or success reasons"],"confidence":0.0}',
            )
        except Exception:
            return {
                "verdict": "fail",
                "summary": "Evaluator returned malformed JSON; counted as a failed judgment so the smoke run can continue.",
                "reasons": [f"Raw evaluator output was not valid JSON: {_truncate(content, 400)}"],
                "confidence": 0.0,
            }
    return _normalize_verdict(parsed)


def review_failed_smoke_case(review_payload: dict[str, Any], *, prefix: str = "THREAD_OBS_SMOKE_EVAL") -> dict[str, Any]:
    settings = load_evaluator_settings(prefix)
    request_body = _build_failure_review_request(
        review_payload,
        model=str(settings["model"]),
        temperature=float(settings["temperature"]),
    )
    response_payload = _post_chat_completions(
        base_url=str(settings["base_url"]),
        api_key=str(settings["api_key"]),
        body=request_body,
    )
    content = _extract_message_text(response_payload)
    try:
        parsed = _extract_json_object(content)
    except ValueError:
        try:
            parsed = _repair_json_object(
                content,
                base_url=str(settings["base_url"]),
                api_key=str(settings["api_key"]),
                model=str(settings["model"]),
                schema='{"summary":"short paragraph","failure_stage":"initial_answer"|"audit_prompt"|"audit_verdict"|"rewrite_answer"|"unknown","findings":["specific observations tied to the transcript"],"suggested_fix":"short concrete fix","confidence":0.0}',
            )
        except Exception:
            return {
                "summary": "Failure-review output was malformed JSON.",
                "failure_stage": "unknown",
                "findings": [f"Raw failure review output was not valid JSON: {_truncate(content, 400)}"],
                "suggested_fix": "Tighten the evaluator prompt or keep the JSON repair pass in place.",
                "confidence": 0.0,
            }
    return _normalize_failure_review(parsed)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a chat smoke case with a reviewer model.")
    parser.add_argument("--mode", choices=("evaluate", "review"), default="evaluate")
    parser.add_argument("--input-file", required=True, help="JSON file containing the evaluation payload")
    args = parser.parse_args()

    payload = _load_json(args.input_file)
    if args.mode == "review":
        print(json.dumps(review_failed_smoke_case(payload), ensure_ascii=True))
    else:
        print(json.dumps(evaluate_smoke_case(payload), ensure_ascii=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - CLI failure path
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc