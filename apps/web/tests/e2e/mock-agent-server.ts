import { createServer } from "node:http";

const cancelledTurns = new Set<string>();
const failedOncePrompts = new Set<string>();

function sendEvent(
  response: import("node:http").ServerResponse,
  input: {
    kind: string;
    conversationId: string;
    turnId: string;
    sequence: number;
    eventId?: string;
    text?: string;
    metadata?: Record<string, unknown>;
  },
) {
  response.write(
    `data: ${JSON.stringify({
      id: input.eventId ?? `${input.turnId}:${input.sequence}`,
      author: "deepresearch_agent",
      content: {
        role: "model",
        parts: input.text ? [{ text: input.text }] : [],
      },
      partial: input.kind === "answer_delta",
      customMetadata: {
        kind: input.kind,
        turn_id: input.turnId,
        conversation_id: input.conversationId,
        event_sequence: input.sequence,
        ...input.metadata,
      },
    })}\n\n`,
  );
}

const server = createServer((request, response) => {
  if (request.method === "GET" && request.url === "/healthz") {
    response.writeHead(200, { "content-type": "application/json" });
    response.end('{"status":"ok"}');
    return;
  }

  const cancelMatch = request.url?.match(/^\/runs\/([^/]+)\/cancel$/u);
  if (request.method === "POST" && cancelMatch?.[1]) {
    cancelledTurns.add(decodeURIComponent(cancelMatch[1]));
    response.writeHead(204);
    response.end();
    return;
  }

  if (request.method !== "POST" || request.url !== "/run_sse") {
    response.writeHead(404);
    response.end();
    return;
  }

  const chunks: Uint8Array[] = [];
  request.on("data", (chunk: Uint8Array) => chunks.push(chunk));
  request.on("end", () => {
    const body = JSON.parse(Buffer.concat(chunks).toString("utf8")) as {
      new_message: { parts: Array<{ text: string }> };
      custom_metadata: { turn_id: string };
      session_id: string;
    };
    const prompt = body.new_message.parts.map((part) => part.text).join("");
    const turnId = body.custom_metadata.turn_id;
    const conversationId = body.session_id;
    response.writeHead(200, {
      "content-type": "text/event-stream",
      "cache-control": "no-cache",
    });
    sendEvent(response, {
      kind: "research_started",
      conversationId,
      turnId,
      sequence: 0,
    });
    sendEvent(response, {
      kind: "search_progress",
      conversationId,
      turnId,
      sequence: prompt.includes("out-of-order") ? 2 : 1,
      text: "公開論文を検索しています。",
      metadata: { stage: "retrieval", source_count: 3 },
    });
    if (prompt.includes("duplicate-frame")) {
      sendEvent(response, {
        kind: "search_progress",
        conversationId,
        turnId,
        sequence: 1,
        eventId: `${turnId}:1`,
        text: "公開論文を検索しています。",
        metadata: { stage: "retrieval", source_count: 3 },
      });
    }

    if (prompt.includes("truncated-stream") || prompt.includes("out-of-order")) {
      response.end();
      return;
    }
    if (prompt.includes("turn-mismatch")) {
      sendEvent(response, {
        kind: "answer_delta",
        conversationId,
        turnId: crypto.randomUUID(),
        sequence: 2,
        text: "mismatched event",
      });
      response.end();
      return;
    }

    const failOnce =
      prompt.includes("retry-once") && !failedOncePrompts.has("retry-once");
    if (failOnce) {
      failedOncePrompts.add("retry-once");
    }
    if (prompt.includes("agent-error") || failOnce) {
      setTimeout(() => {
        sendEvent(response, {
          kind: "error",
          conversationId,
          turnId,
          sequence: 2,
        });
        response.end("data: [DONE]\n\n");
      }, 80);
      return;
    }

    const delay = prompt.includes("slow-cancel")
      ? 2_000
      : prompt.includes("observe-progress")
        ? 1_000
        : 250;
    setTimeout(() => {
      if (cancelledTurns.has(turnId)) {
        sendEvent(response, {
          kind: "cancelled",
          conversationId,
          turnId,
          sequence: 2,
        });
        response.end("data: [DONE]\n\n");
        return;
      }
      sendEvent(response, {
        kind: "answer_delta",
        conversationId,
        turnId,
        sequence: 2,
        text: "# 結論\nNLRP3 inhibition は前臨床段階で有望です。\n\n",
      });
      sendEvent(response, {
        kind: "completed",
        conversationId,
        turnId,
        sequence: 3,
        text: [
          "# 結論",
          "NLRP3 inhibition は前臨床段階で有望です。",
          "",
          "## Negative evidence",
          "臨床的な有効性は未確立です。",
          "",
          "## References",
          "[Mock publication](https://example.org/paper-1)",
        ].join("\n"),
        metadata: {
          source_count: 3,
          source_summary: [
            {
              id: "mock-1",
              title: "Mock publication",
              url: "https://example.org/paper-1",
              sourceType: "web",
              verificationStatus: "verified",
            },
          ],
        },
      });
      response.end("data: [DONE]\n\n");
    }, delay);
  });
});

server.listen(18_001, "127.0.0.1");
