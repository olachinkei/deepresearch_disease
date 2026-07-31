import { describe, expect, it } from "vitest";

import {
  decodePublicEvent,
  encodePublicEvent,
  type PublicResearchEvent,
} from "~/features/research/public-events";
import {
  consumeResearchStream,
  ResearchStreamProtocolError,
} from "~/features/research/stream-client";
import { parseSseStream } from "~/shared/sse/parser";

const conversationId = "d1aa5d43-f676-4f17-8028-f6f948745d6f";
const turnId = "2fc923fd-8779-4e43-8b2b-e6a1533b721b";

function fragmentedStream(parts: string[]) {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const part of parts) {
        controller.enqueue(encoder.encode(part));
      }
      controller.close();
    },
  });
}

function started(sequence = 0): PublicResearchEvent {
  return {
    eventId: `event-${sequence}`,
    sequence,
    type: "research_started",
    data: { schemaVersion: "2.0", conversationId, turnId },
  };
}

function delta(sequence: number, overrides: Partial<PublicResearchEvent["data"]> = {}): PublicResearchEvent {
  return {
    eventId: `event-${sequence}`,
    sequence,
    type: "answer_delta",
    data: {
      schemaVersion: "2.0",
      conversationId,
      turnId,
      delta: "結論",
      ...overrides,
    },
  };
}

function completed(sequence: number): PublicResearchEvent {
  return {
    eventId: `event-${sequence}`,
    sequence,
    type: "completed",
    data: {
      schemaVersion: "2.0",
      conversationId,
      turnId,
      answerMarkdown: "# 結論",
    },
  };
}

function responseFor(events: PublicResearchEvent[]) {
  return new Response(
    fragmentedStream(events.map((event) => encodePublicEvent(event))),
    { status: 200 },
  );
}

describe("SSE parsing", () => {
  it("handles fragmented and multiline frames", async () => {
    const frames = [];
    for await (const frame of parseSseStream(
      fragmentedStream([
        "id: event-0\r\nevent: search_progress\r\ndata: {\"a\":",
        "1}\r\n\r\nid: event-1\nevent: answer_delta\ndata: first",
        "\ndata: second\n\n",
      ]),
    )) {
      frames.push(frame);
    }

    expect(frames).toEqual([
      { event: "search_progress", data: '{"a":1}', id: "event-0" },
      { event: "answer_delta", data: "first\nsecond", id: "event-1" },
    ]);
  });

  it("validates start-to-terminal order and ignores duplicate IDs", async () => {
    const duplicate = delta(1);
    const received: string[] = [];
    await consumeResearchStream(
      responseFor([started(), duplicate, duplicate, completed(2)]),
      (event) => {
        received.push(event.type);
      },
    );

    expect(received).toEqual([
      "research_started",
      "answer_delta",
      "completed",
    ]);
  });

  it.each([
    {
      name: "out-of-order sequence",
      events: [started(), delta(2), completed(3)],
    },
    {
      name: "turn mismatch",
      events: [
        started(),
        delta(1, { turnId: "6a93a498-190b-4d8a-84ec-88a74b050270" }),
        completed(2),
      ],
    },
    {
      name: "terminal-free truncation",
      events: [started(), delta(1)],
    },
  ])("maps $name to a sanitized retryable error", async ({ events }) => {
    await expect(
      consumeResearchStream(responseFor(events), () => undefined),
    ).rejects.toMatchObject({
      name: "ResearchStreamProtocolError",
      code: "stream_protocol_error",
      retryable: true,
      message: "接続が途中で切断されました。もう一度お試しください。",
    });
  });

  it.each([
    {
      name: "malformed terminal",
      frame:
        'id: event-1\nevent: completed\ndata: {"eventId":"event-1","sequence":1}\n\n',
    },
    {
      name: "SSE and payload ID mismatch",
      frame:
        'id: other-event\nevent: completed\ndata: {"eventId":"event-1","sequence":1,"schemaVersion":"2.0","conversationId":"' +
        conversationId +
        '","turnId":"' +
        turnId +
        '","answerMarkdown":"# 結論"}\n\n',
    },
  ])("rejects $name", async ({ frame }) => {
    const invalid = new Response(
      fragmentedStream([encodePublicEvent(started()), frame]),
    );
    await expect(
      consumeResearchStream(invalid, () => undefined),
    ).rejects.toBeInstanceOf(ResearchStreamProtocolError);
  });

  it("ignores events after terminal", async () => {
    const received: string[] = [];
    await consumeResearchStream(
      responseFor([started(), completed(1), delta(2)]),
      (event) => {
        received.push(event.type);
      },
    );
    expect(received).toEqual(["research_started", "completed"]);
  });

  it("accepts only the versioned public union with matching SSE ID", () => {
    const event = delta(0);
    const encoded = encodePublicEvent(event);
    expect(encoded).toContain("id: event-0");
    expect(
      decodePublicEvent(
        "answer_delta",
        JSON.stringify({
          eventId: "event-0",
          sequence: 0,
          conversationId,
          turnId,
          delta: "missing version",
        }),
      ),
    ).toBeUndefined();
  });
});
