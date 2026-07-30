from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

from deepresearch_agent.api.app import create_adk_app
from deepresearch_agent.settings import Settings


def live_settings() -> Settings:
    settings = Settings()
    runtime_dir = Path(".canary")
    runtime_dir.mkdir(exist_ok=True)
    settings.database_path = runtime_dir / "corpus.sqlite"
    settings.session_database_path = runtime_dir / "sessions.sqlite"
    return settings


async def main() -> int:
    """Run one fixed synthetic turn without accepting arbitrary workflow input."""

    settings = live_settings()
    if settings.runtime_mode != "live":
        print("live canary refused: AGENT_RUNTIME_MODE must be live", file=sys.stderr)
        return 2
    if not settings.live_gemini_enabled:
        print("live canary refused: GOOGLE_API_KEY is not configured", file=sys.stderr)
        return 2

    payload = {
        "app_name": "deepresearch_agent",
        "user_id": "synthetic-canary-user",
        "session_id": "synthetic-canary-conversation",
        "new_message": {
            "role": "user",
            "parts": [
                {
                    "text": (
                        "Using public evidence only, assess MMP9 inhibition as a "
                        "research hypothesis for ischemic stroke."
                    )
                }
            ],
        },
        "streaming": True,
        "custom_metadata": {
            "turn_id": "synthetic-canary-turn",
            "conversation_id": "synthetic-canary-conversation",
            "target_molecule": "MMP9",
            "mechanism": "inhibition",
            "disease": "ischemic stroke",
        },
    }

    app = create_adk_app(settings=settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://canary.test",
        timeout=180,
    ) as client:
        response = await client.post("/run_sse", json=payload)

    if response.status_code != 200:
        print(f"live canary failed with HTTP {response.status_code}", file=sys.stderr)
        return 1

    kinds: list[str] = []
    for line in response.text.splitlines():
        if not line.startswith("data: "):
            continue
        event = json.loads(line.removeprefix("data: "))
        metadata = event.get("customMetadata", {})
        kind = metadata.get("kind")
        if isinstance(kind, str):
            kinds.append(kind)

    if not kinds or kinds[-1] != "completed":
        terminal = kinds[-1] if kinds else "missing"
        print(f"live canary failed with terminal event: {terminal}", file=sys.stderr)
        return 1

    print("live canary passed with a completed terminal event")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
