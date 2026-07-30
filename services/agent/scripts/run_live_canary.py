from __future__ import annotations

import asyncio
import sys

from deepresearch_agent.application.synthesis import AdkSynthesizer
from deepresearch_agent.domain.models import (
    Evidence,
    EvidenceStage,
    ResearchInput,
    SourceKind,
    VerificationStatus,
)
from deepresearch_agent.settings import Settings


def live_settings() -> Settings:
    return Settings()


async def main() -> int:
    """Run one fixed synthetic turn without accepting arbitrary workflow input."""

    settings = live_settings()
    if settings.runtime_mode != "live":
        print("live canary refused: AGENT_RUNTIME_MODE must be live", file=sys.stderr)
        return 2
    if not settings.live_gemini_enabled:
        print("live canary refused: GOOGLE_API_KEY is not configured", file=sys.stderr)
        return 2
    if not settings.allow_public_content_to_gemini:
        print(
            "live canary refused: public/synthetic Gemini sending is disabled",
            file=sys.stderr,
        )
        return 2

    synthesizer = AdkSynthesizer(model=settings.model)
    draft = await synthesizer.synthesize(
        research_input=ResearchInput(
            research_question=(
                "Summarize the supplied synthetic evidence for an ischemic-stroke "
                "drug-discovery demonstration."
            )
        ),
        evidence=[
            Evidence(
                id="E-SYNTHETIC-CANARY-1",
                document_id="synthetic-canary-document",
                source_kind=SourceKind.PUBLIC,
                title="Synthetic ischemic-stroke canary evidence",
                excerpt=(
                    "This synthetic excerpt verifies structured generation only and "
                    "does not assert a biomedical finding."
                ),
                evidence_stage=EvidenceStage.UNKNOWN,
                verification_status=VerificationStatus.VERIFIED,
                provenance=["code-owned:live-canary"],
            )
        ],
        safe_trace_metadata={
            "app.turn_id": "synthetic-canary-turn",
            "app.conversation_id": "synthetic-canary-conversation",
            "app.model_id": settings.model,
            "app.prompt_version": settings.prompt_version,
            "app.prompt_sha256": settings.prompt_sha256,
            "app.input_data_classification": "synthetic",
            "app.output_data_classification": "synthetic",
        },
    )
    if not draft.answer_markdown.strip():
        print("live canary failed with an empty structured answer", file=sys.stderr)
        return 1

    print(
        "live canary passed with structured output for "
        f"{settings.model} prompt {settings.prompt_version} "
        f"({settings.prompt_sha256[:12]})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
