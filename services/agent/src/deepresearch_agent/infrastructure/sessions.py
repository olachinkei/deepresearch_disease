from __future__ import annotations

from pathlib import Path
from typing import Any

from deepresearch_agent.observability.otel import enforce_privacy_environment

enforce_privacy_environment()

from google.adk.events import Event, EventActions  # noqa: E402
from google.adk.sessions import DatabaseSessionService  # noqa: E402


class AdkSessionStateStore:
    """Stores compact research conditions in ADK's SQLite session service."""

    def __init__(self, path: Path, *, app_name: str = "deepresearch_agent") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._app_name = app_name
        self._service = DatabaseSessionService(
            db_url=f"sqlite+aiosqlite:///{path.resolve().as_posix()}"
        )

    async def merge(
        self,
        *,
        user_id: str,
        session_id: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        session = await self._service.get_session(
            app_name=self._app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if session is None:
            state, _ = merge_research_state({}, values)
            await self._service.create_session(
                app_name=self._app_name,
                user_id=user_id,
                session_id=session_id,
                state=state,
            )
            return state
        state, state_delta = merge_research_state(session.state, values)
        await self._service.append_event(
            session,
            Event(
                author="research_state",
                actions=EventActions(state_delta=state_delta),
                custom_metadata={"kind": "state_update"},
            ),
        )
        return state


def merge_research_state(
    current: dict[str, Any],
    values: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    incoming = dict(values)
    recent_question = incoming.pop("recent_question", None)
    turn_count = int(current.get("turn_count", 0)) + 1
    state_delta = {key: value for key, value in incoming.items() if value is not None}
    state_delta["turn_count"] = turn_count
    recent_questions = list(current.get("recent_questions", []))
    if recent_question:
        recent_questions.append(recent_question)
    if turn_count % 4 == 0:
        previous_summary = str(current.get("conversation_summary", ""))
        compacted = " | ".join([previous_summary, *recent_questions]).strip(" |")
        state_delta["conversation_summary"] = compacted[-2000:]
        state_delta["recent_questions"] = []
        state_delta["last_compaction_turn"] = turn_count
    else:
        state_delta["recent_questions"] = recent_questions[-3:]
        state_delta.setdefault(
            "conversation_summary",
            str(current.get("conversation_summary", "")),
        )
    return {**current, **state_delta}, state_delta
