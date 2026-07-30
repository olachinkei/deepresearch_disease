import { describe, expect, it } from "vitest";

import {
  AgentUnavailableError,
  getAgentServiceUrl,
  HttpAgentClient,
  sanitizeAgentEvent,
} from "~/features/research/agent-client.server";

const conversationId = "d1aa5d43-f676-4f17-8028-f6f948745d6f";
const turnId = "2fc923fd-8779-4e43-8b2b-e6a1533b721b";

function sseResponse(events: unknown[]) {
  const body = events
    .map((event) => `data: ${JSON.stringify(event)}\n\n`)
    .join("")
    .concat("data: [DONE]\n\n");
  return new Response(body, {
    headers: { "content-type": "text/event-stream" },
  });
}

describe("HttpAgentClient", () => {
  it("uses the canonical service env and port", () => {
    const previous = process.env.AGENT_SERVICE_URL;
    delete process.env.AGENT_SERVICE_URL;
    expect(getAgentServiceUrl()).toBe("http://127.0.0.1:8001");
    process.env.AGENT_SERVICE_URL = "http://agent.internal:9000";
    expect(getAgentServiceUrl()).toBe("http://agent.internal:9000");
    if (previous === undefined) {
      delete process.env.AGENT_SERVICE_URL;
    } else {
      process.env.AGENT_SERVICE_URL = previous;
    }
  });

  it("sends the ADK request contract and yields sanitized events", async () => {
    let requestBody: Record<string, unknown> | undefined;
    const mockFetch: typeof fetch = async (_input, init) => {
      requestBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return sseResponse([
        {
          author: "deepresearch_agent",
          partial: true,
          content: { parts: [{ text: "根拠を統合中" }] },
          customMetadata: {
            kind: "search_progress",
            stage: "retrieval",
            source_count: 4,
            display_name: "秘密の表示名",
            tool_response: "内部本文",
          },
        },
        {
          author: "deepresearch_agent",
          content: { parts: [{ text: "# 結論\n引用付き回答" }] },
          customMetadata: {
            kind: "completed",
            source_count: 4,
          },
        },
      ]);
    };
    const client = new HttpAgentClient("http://agent.test", mockFetch);
    const events = [];
    for await (const event of client.run(
      {
        userId: "internal-user-uuid",
        conversationId,
        turnId,
        prompt: "research prompt",
        disease: "ischemic stroke",
      },
      new AbortController().signal,
    )) {
      events.push(event);
    }

    expect(requestBody).toMatchObject({
      app_name: "deepresearch_agent",
      user_id: "internal-user-uuid",
      session_id: conversationId,
      custom_metadata: {
        turn_id: turnId,
        conversation_id: conversationId,
        disease: "ischemic stroke",
      },
    });
    const serialized = JSON.stringify(events);
    expect(serialized).toContain("引用付き回答");
    expect(serialized).not.toContain("秘密の表示名");
    expect(serialized).not.toContain("内部本文");
  });

  it("does not expose unknown tool events", () => {
    expect(
      sanitizeAgentEvent(
        {
          author: "tool",
          content: { parts: [{ text: "RAW_INTERNAL_EXCERPT" }] },
          customMetadata: { kind: "execute_tool", api_key: "secret" },
        },
        { conversationId, turnId },
      ),
    ).toBeUndefined();
  });

  it("raises a typed unavailable error without leaking fetch errors", async () => {
    const client = new HttpAgentClient("http://offline", async () => {
      throw new Error("ECONNREFUSED with secret URL");
    });
    const collect = async () => {
      const iterator = client.run(
        {
          userId: "user",
          conversationId,
          turnId,
          prompt: "prompt",
          disease: "ischemic stroke",
        },
        new AbortController().signal,
      );
      await iterator.next();
    };
    await expect(collect()).rejects.toBeInstanceOf(AgentUnavailableError);
  });
});
