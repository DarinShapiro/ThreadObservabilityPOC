from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

_SESSION_TTL_SECONDS = 6 * 60 * 60
_MAX_FACTS = 8
_MAX_RECENT_TOOLS = 6
_MAX_GOAL_CHARS = 240
_NODE_EUI64_RE = re.compile(r"\b([0-9a-f]{16})\b", re.IGNORECASE)


@dataclass(slots=True)
class SessionFact:
    key: str
    text: str
    source: str
    observed_at: float


@dataclass(slots=True)
class ChatSessionState:
    conversation_id: str
    created_at: float
    updated_at: float
    current_goal: str | None = None
    selected_node_eui64: str | None = None
    selected_partition_ids: list[int] = field(default_factory=list)
    confirmed_facts: list[SessionFact] = field(default_factory=list)
    recent_tools: list[str] = field(default_factory=list)


class ChatSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, ChatSessionState] = {}

    def ensure_session(self, conversation_id: str | None) -> ChatSessionState:
        session_id = str(conversation_id or f"chat-{uuid.uuid4()}").strip()
        now = time.time()
        self._prune(now)
        existing = self._sessions.get(session_id)
        if existing is not None:
            existing.updated_at = now
            return existing
        state = ChatSessionState(
            conversation_id=session_id,
            created_at=now,
            updated_at=now,
        )
        self._sessions[session_id] = state
        return state

    def build_prompt_context(self, conversation_id: str | None) -> dict[str, Any] | None:
        if not conversation_id:
            return None
        state = self._sessions.get(str(conversation_id).strip())
        if state is None:
            return None
        payload: dict[str, Any] = {}
        if state.current_goal:
            payload["current_goal"] = state.current_goal
        focus: dict[str, Any] = {}
        if state.selected_node_eui64:
            focus["selected_node_eui64"] = state.selected_node_eui64
        if state.selected_partition_ids:
            focus["selected_partition_ids"] = state.selected_partition_ids[:4]
        if focus:
            payload["focus"] = focus
        if state.confirmed_facts:
            payload["confirmed_facts"] = [fact.text for fact in state.confirmed_facts[:_MAX_FACTS]]
        if state.recent_tools:
            payload["recent_tools"] = state.recent_tools[:_MAX_RECENT_TOOLS]
        return payload or None

    def record_turn(
        self,
        *,
        conversation_id: str,
        message: str,
        page_context: dict[str, Any] | None,
        tool_calls: list[dict[str, Any]] | None,
    ) -> ChatSessionState:
        state = self.ensure_session(conversation_id)
        state.updated_at = time.time()
        goal = " ".join(str(message or "").split())
        state.current_goal = goal[:_MAX_GOAL_CHARS] if goal else state.current_goal

        selected_node = None
        if isinstance(page_context, dict):
            selected_node = page_context.get("selected_node_eui64")
            snapshot = page_context.get("snapshot_summary") if isinstance(page_context.get("snapshot_summary"), dict) else None
            if snapshot:
                partition_count = int(snapshot.get("partition_count") or 0)
                distinct_networks = int(snapshot.get("distinct_thread_networks") or 0)
                if partition_count > 1:
                    self._set_fact(
                        state,
                        key="dashboard_partition_count",
                        text=f"Dashboard snapshot shows {partition_count} Thread partitions.",
                        source="page_context",
                    )
                if distinct_networks > 1:
                    self._set_fact(
                        state,
                        key="dashboard_distinct_networks",
                        text=f"Dashboard snapshot shows {distinct_networks} distinct Thread networks.",
                        source="page_context",
                    )
        if not selected_node:
            selected_node = self._extract_node_eui64(message)
        if selected_node:
            state.selected_node_eui64 = str(selected_node).lower()

        for call in tool_calls or []:
            name = str(call.get("name") or "").strip()
            if not name:
                continue
            state.recent_tools = [tool for tool in state.recent_tools if tool != name]
            state.recent_tools.insert(0, name)
            state.recent_tools = state.recent_tools[:_MAX_RECENT_TOOLS]
            result = call.get("result") if isinstance(call.get("result"), dict) else call.get("result")
            self._derive_facts_from_tool(state, name, result)
        return state

    def reset(self) -> None:
        self._sessions.clear()

    def _set_fact(self, state: ChatSessionState, *, key: str, text: str, source: str) -> None:
        now = time.time()
        fact = SessionFact(key=key, text=text, source=source, observed_at=now)
        state.confirmed_facts = [item for item in state.confirmed_facts if item.key != key]
        state.confirmed_facts.insert(0, fact)
        state.confirmed_facts = state.confirmed_facts[:_MAX_FACTS]

    def _derive_facts_from_tool(self, state: ChatSessionState, name: str, result: Any) -> None:
        if name == "analyze_node" and isinstance(result, dict):
            node = result.get("node") if isinstance(result.get("node"), dict) else {}
            eui64 = str(result.get("eui64") or node.get("eui64") or "").strip().lower()
            if eui64:
                state.selected_node_eui64 = eui64
            friendly = node.get("friendly_name") or eui64
            status = node.get("status")
            partition_id = node.get("partition_id")
            if friendly and (status or partition_id is not None):
                bits = [str(friendly)]
                if status:
                    bits.append(f"is currently {status}")
                if partition_id is not None:
                    bits.append(f"on partition {partition_id}")
                    self._push_partition(state, partition_id)
                self._set_fact(
                    state,
                    key=f"node_status:{eui64 or friendly}",
                    text="Node focus: " + ", ".join(bits) + ".",
                    source=name,
                )
            timeline = result.get("timeline") if isinstance(result.get("timeline"), list) else []
            timeline_kinds = [str(row.get("kind") or "") for row in timeline if isinstance(row, dict) and row.get("kind")]
            notable = [kind for kind in timeline_kinds if kind in {"re_attached_node", "parent_change", "status_change", "issue.opened", "issue.closed"}]
            if notable:
                joined = ", ".join(dict.fromkeys(notable))
                self._set_fact(
                    state,
                    key=f"node_timeline:{eui64 or friendly}",
                    text=f"Recent node timeline includes: {joined}.",
                    source=name,
                )
            physical_identity = result.get("physical_identity") if isinstance(result.get("physical_identity"), dict) else None
            if physical_identity and int(physical_identity.get("duplicate_count") or 0) > 1:
                self._set_fact(
                    state,
                    key=f"physical_identity:{eui64 or friendly}",
                    text=f"Physical identity appears under {int(physical_identity.get('duplicate_count') or 0)} EUI64s.",
                    source=name,
                )
            return
        if name == "query_history" and isinstance(result, list):
            kinds = [str(row.get("kind") or "") for row in result if isinstance(row, dict) and row.get("kind")]
            notable = [kind for kind in kinds if kind in {"re_attached_node", "parent_change", "status_change", "issue.opened", "issue.closed"}]
            if notable:
                joined = ", ".join(dict.fromkeys(notable[:4]))
                self._set_fact(
                    state,
                    key="recent_history_kinds",
                    text=f"Recent history confirms: {joined}.",
                    source=name,
                )
            partition_ids = []
            for row in result:
                if not isinstance(row, dict):
                    continue
                details = row.get("details") if isinstance(row.get("details"), dict) else None
                if details and details.get("partition_id") is not None:
                    partition_ids.append(int(details.get("partition_id")))
            for partition_id in partition_ids[:4]:
                self._push_partition(state, partition_id)
            return
        if name == "get_mesh_state" and isinstance(result, dict):
            partitions = result.get("all_partitions") if isinstance(result.get("all_partitions"), list) else None
            if partitions is None:
                nodes = result.get("nodes") if isinstance(result.get("nodes"), list) else []
                partitions = sorted({row.get("partition_id") for row in nodes if isinstance(row, dict) and row.get("partition_id") is not None})
            for partition_id in partitions[:4]:
                self._push_partition(state, partition_id)
            if len(partitions) > 1:
                self._set_fact(
                    state,
                    key="mesh_partitions",
                    text=f"Current mesh state shows {len(partitions)} active partitions.",
                    source=name,
                )
            return
        if name == "start_triage" and isinstance(result, dict):
            health = result.get("health") if isinstance(result.get("health"), dict) else {}
            summary = health.get("summary") if isinstance(health.get("summary"), dict) else {}
            offline_nodes = int(summary.get("offline_nodes") or 0)
            distinct_networks = int(summary.get("distinct_thread_networks") or 0)
            if offline_nodes > 0:
                self._set_fact(
                    state,
                    key="triage_offline_nodes",
                    text=f"Triage snapshot reports {offline_nodes} offline nodes.",
                    source=name,
                )
            if distinct_networks > 1:
                self._set_fact(
                    state,
                    key="triage_distinct_networks",
                    text=f"Triage snapshot reports {distinct_networks} distinct Thread networks.",
                    source=name,
                )

    def _push_partition(self, state: ChatSessionState, partition_id: Any) -> None:
        try:
            value = int(partition_id)
        except (TypeError, ValueError):
            return
        if value in state.selected_partition_ids:
            return
        state.selected_partition_ids.insert(0, value)
        state.selected_partition_ids = state.selected_partition_ids[:4]

    def _extract_node_eui64(self, text: str) -> str | None:
        match = _NODE_EUI64_RE.search(str(text or ""))
        if not match:
            return None
        return match.group(1).lower()

    def _prune(self, now: float) -> None:
        stale = [
            key
            for key, session in self._sessions.items()
            if (now - session.updated_at) > _SESSION_TTL_SECONDS
        ]
        for key in stale:
            self._sessions.pop(key, None)


_STORE = ChatSessionStore()


def ensure_session(conversation_id: str | None) -> ChatSessionState:
    return _STORE.ensure_session(conversation_id)


def build_prompt_context(conversation_id: str | None) -> dict[str, Any] | None:
    return _STORE.build_prompt_context(conversation_id)


def record_turn(
    *,
    conversation_id: str,
    message: str,
    page_context: dict[str, Any] | None,
    tool_calls: list[dict[str, Any]] | None,
) -> ChatSessionState:
    return _STORE.record_turn(
        conversation_id=conversation_id,
        message=message,
        page_context=page_context,
        tool_calls=tool_calls,
    )


def reset() -> None:
    _STORE.reset()