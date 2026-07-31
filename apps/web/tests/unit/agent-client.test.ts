import { describe, expect, it } from "vitest";

import {
  AgentProtocolError,
  AgentUnavailableError,
  getAgentServiceUrl,
  HttpAgentClient,
  sanitizeAgentEvent,
} from "~/features/research/agent-client.server";

const conversationId = "d1aa5d43-f676-4f17-8028-f6f948745d6f";
const turnId = "2fc923fd-8779-4e43-8b2b-e6a1533b721b";

function sseResponse(events: unknown[], includeDone = true) {
  const body = events
    .map((raw, sequence) => {
      const event = raw as {
        id?: string;
        customMetadata?: Record<string, unknown>;
      };
      return `data: ${JSON.stringify({
        ...event,
        id: event.id ?? `agent-event-${sequence}`,
        customMetadata: {
          conversation_id: conversationId,
          turn_id: turnId,
          event_sequence: sequence,
          ...event.customMetadata,
        },
      })}\n\n`;
    })
    .join("")
    .concat(includeDone ? "data: [DONE]\n\n" : "");
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
          customMetadata: { kind: "research_started" },
        },
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
            source_count: 2,
            source_summary: [
              {
                id: "E1",
                title: "First excerpt",
                url: "https://example.test/same-document",
                sourceType: "web",
                verificationStatus: "verified",
              },
              {
                id: "E2",
                title: "Second excerpt",
                url: "https://example.test/same-document",
                sourceType: "web",
                verificationStatus: "unverified",
              },
            ],
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
    expect(events.at(-1)).toMatchObject({
      type: "completed",
      data: {
        sourceCount: 2,
        sourceSummary: [
          { id: "E1", verificationStatus: "verified" },
          { id: "E2", verificationStatus: "unverified" },
        ],
      },
    });
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

  it("drops an unsafe source summary without exposing its raw fields", () => {
    const event = sanitizeAgentEvent(
      {
        id: "unsafe-source-event",
        content: { parts: [{ text: "# Safe answer" }] },
        customMetadata: {
          kind: "completed",
          conversation_id: conversationId,
          turn_id: turnId,
          event_sequence: 0,
          source_count: 1,
          source_summary: [
            {
              id: "E1",
              title: "Unsafe source",
              url: "javascript:RAW_INTERNAL_EXCERPT",
              sourceType: "web",
              verificationStatus: "verified",
              excerpt: "RAW_INTERNAL_EXCERPT",
            },
          ],
        },
      },
      { conversationId, turnId },
    );

    expect(event).toMatchObject({
      type: "completed",
      data: { sourceCount: 1 },
    });
    expect(event?.data).not.toHaveProperty("sourceSummary");
    expect(JSON.stringify(event)).not.toContain("RAW_INTERNAL_EXCERPT");
  });

  it("deduplicates event IDs and requires an ordered terminal stream", async () => {
    const duplicate = {
      id: "agent-event-1",
      content: { parts: [{ text: "検索中" }] },
      customMetadata: {
        kind: "search_progress",
        event_sequence: 1,
        stage: "retrieval",
      },
    };
    const client = new HttpAgentClient("http://agent.test", async () =>
      sseResponse([
        { customMetadata: { kind: "research_started" } },
        duplicate,
        duplicate,
        {
          id: "agent-event-2",
          content: { parts: [{ text: "# 結論" }] },
          customMetadata: { kind: "completed", event_sequence: 2 },
        },
      ]),
    );

    const events = await collectEvents(client);
    expect(events.map((event) => event.type)).toEqual([
      "research_started",
      "search_progress",
      "completed",
    ]);
  });

  it.each([
    {
      name: "out-of-order sequence",
      events: [
        { customMetadata: { kind: "research_started" } },
        {
          customMetadata: {
            kind: "search_progress",
            event_sequence: 2,
          },
        },
      ],
    },
    {
      name: "turn mismatch",
      events: [
        { customMetadata: { kind: "research_started" } },
        {
          customMetadata: {
            kind: "search_progress",
            turn_id: "other-turn",
          },
        },
      ],
    },
    {
      name: "terminal-free truncation",
      events: [
        { customMetadata: { kind: "research_started" } },
        { customMetadata: { kind: "search_progress" } },
      ],
    },
  ])("rejects $name as an agent protocol error", async ({ events }) => {
    const client = new HttpAgentClient("http://agent.test", async () =>
      sseResponse(events, false),
    );
    await expect(collectEvents(client)).rejects.toBeInstanceOf(
      AgentProtocolError,
    );
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

  it("bounds an unresponsive upstream cancel request", async () => {
    const previous = process.env.AGENT_CANCEL_TIMEOUT_MS;
    process.env.AGENT_CANCEL_TIMEOUT_MS = "5";
    let aborted = false;
    const client = new HttpAgentClient("http://agent.test", async (_input, init) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener(
          "abort",
          () => {
            aborted = true;
            reject(new Error("synthetic timeout detail"));
          },
          { once: true },
        );
      }),
    );

    try {
      await expect(client.cancel(turnId)).resolves.toBe(false);
      expect(aborted).toBe(true);
    } finally {
      if (previous === undefined) {
        delete process.env.AGENT_CANCEL_TIMEOUT_MS;
      } else {
        process.env.AGENT_CANCEL_TIMEOUT_MS = previous;
      }
    }
  });
});

async function collectEvents(client: HttpAgentClient) {
  const events = [];
  for await (const event of client.run(
    {
      userId: "internal-user-uuid",
      conversationId,
      turnId,
      prompt: "synthetic prompt",
      disease: "ischemic stroke",
    },
    new AbortController().signal,
  )) {
    events.push(event);
  }
  return events;
}
