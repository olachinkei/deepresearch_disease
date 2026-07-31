import { describe, expect, it } from "vitest";

import {
  isRetryableTurnError,
  turnRecoveryMessage,
} from "~/features/conversation/turn-state";
import type { TurnStatusView } from "~/features/conversation/view-model";

describe("turn recovery state", () => {
  it.each([
    "agent_protocol_error",
    "agent_unavailable",
    "internal_error",
    "stream_protocol_error",
    "turn_deadline_exceeded",
  ])("classifies %s as retryable", (code) => {
    expect(isRetryableTurnError(code)).toBe(true);
  });

  it("keeps unknown terminal errors non-retryable with safe recovery advice", () => {
    const turn: TurnStatusView = {
      id: "synthetic-turn-id",
      sequence: 1,
      status: "error",
      errorCode: "scope_rejected",
      retryable: false,
    };

    expect(isRetryableTurnError(turn.errorCode)).toBe(false);
    expect(turnRecoveryMessage(turn)).toContain(
      "turn IDを添えて管理者へ連絡してください",
    );
    expect(turnRecoveryMessage(turn)).not.toContain(turn.errorCode);
  });
});
