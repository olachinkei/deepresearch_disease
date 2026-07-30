from __future__ import annotations

import pytest

from deepresearch_agent.infrastructure.sessions import AdkSessionStateStore


@pytest.mark.asyncio
async def test_session_reinjects_research_state_and_compacts_every_four_turns(tmp_path) -> None:
    store = AdkSessionStateStore(tmp_path / "sessions.sqlite")
    first = await store.merge(
        user_id="user-1",
        session_id="conversation-1",
        values={
            "target_molecule": "NLRP3",
            "mechanism": "inhibition",
            "disease": "ischemic stroke",
            "recent_question": "first",
        },
    )
    assert first["turn_count"] == 1
    assert first["target_molecule"] == "NLRP3"

    state = first
    for question in ("second", "third", "fourth"):
        state = await store.merge(
            user_id="user-1",
            session_id="conversation-1",
            values={
                "target_molecule": None,
                "mechanism": None,
                "disease": None,
                "recent_question": question,
            },
        )

    assert state["turn_count"] == 4
    assert state["target_molecule"] == "NLRP3"
    assert state["mechanism"] == "inhibition"
    assert state["disease"] == "ischemic stroke"
    assert state["recent_questions"] == []
    assert state["last_compaction_turn"] == 4
    assert all(item in state["conversation_summary"] for item in ("first", "second", "fourth"))
