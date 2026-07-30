import { describe, expect, it } from "vitest";

import {
  decodePublicEvent,
  encodePublicEvent,
} from "~/features/research/public-events";
import { consumeResearchStream } from "~/features/research/stream-client";
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

describe("SSE parsing", () => {
  it("handles fragmented and multiline frames", async () => {
    const frames = [];
    for await (const frame of parseSseStream(
      fragmentedStream([
        "event: search_progress\r\ndata: {\"a\":",
        "1}\r\n\r\nevent: answer_delta\ndata: first",
        "\ndata: second\n\n",
      ]),
    )) {
      frames.push(frame);
    }

    expect(frames).toEqual([
      { event: "search_progress", data: '{"a":1}', id: undefined },
      { event: "answer_delta", data: "first\nsecond", id: undefined },
    ]);
  });

  it("accepts only the versioned public union", async () => {
    const valid = encodePublicEvent({
      type: "answer_delta",
      data: {
        schemaVersion: "1.0",
        conversationId,
        turnId,
        delta: "結論",
      },
    });
    const invalid =
      'event: tool_response\ndata: {"secret":"raw tool output"}\n\n';
    const received: string[] = [];
    await consumeResearchStream(
      new Response(fragmentedStream([valid, invalid]), {
        status: 200,
      }),
      (event) => {
        received.push(event.type);
      },
    );

    expect(received).toEqual(["answer_delta"]);
    expect(
      decodePublicEvent(
        "answer_delta",
        JSON.stringify({
          conversationId,
          turnId,
          delta: "missing version",
        }),
      ),
    ).toBeUndefined();
  });
});
