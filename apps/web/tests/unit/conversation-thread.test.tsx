// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConversationThread } from "~/features/conversation/conversation-thread";

describe("ConversationThread recovery states", () => {
  it("shows recovery advice and turn ID for a terminal error", () => {
    render(
      <ConversationThread
        active={{
          conversation: {
            id: "conversation-id",
            title: "Synthetic terminal error",
            disease: "ischemic stroke",
            targetMolecule: null,
            mechanism: null,
            researchQuestion: null,
          },
          turns: [
            {
              id: "terminal-turn-id",
              sequence: 1,
              status: "error",
              errorCode: "scope_rejected",
              retryable: false,
            },
          ],
          messages: [],
          feedbackByTurn: {},
        }}
        busy={false}
        feedbackByTurn={{}}
        messages={[
          {
            id: "user-message",
            turnId: "terminal-turn-id",
            role: "user",
            content: "Synthetic public question",
            createdAt: "2026-07-31T00:00:00Z",
          },
        ]}
        onCancel={async () => undefined}
        onFeedback={async () => undefined}
        onFollowUp={async () => undefined}
        onRetry={async () => undefined}
        streamingAnswer=""
      />,
    );

    expect(
      screen.getByText(
        "入力内容を確認し、問題が続く場合はturn IDを添えて管理者へ連絡してください。",
        { exact: false },
      ),
    ).toBeTruthy();
    expect(screen.getByText("Turn ID: terminal-turn-id")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "元の条件で再試行" }),
    ).toBeNull();
  });
});
