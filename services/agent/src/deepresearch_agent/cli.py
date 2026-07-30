from __future__ import annotations

import uvicorn

from deepresearch_agent.settings import get_settings


def serve() -> None:
    settings = get_settings()
    uvicorn.run(
        "deepresearch_agent.api.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
