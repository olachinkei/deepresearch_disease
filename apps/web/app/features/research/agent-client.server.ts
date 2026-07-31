import { z } from "zod";

import { parseSseStream } from "~/shared/sse/parser";

import {
  type PublicResearchEvent,
} from "./public-events";
import { sourceSummaryListSchema } from "./source-summary";

const upstreamPartSchema = z
  .object({
    text: z.string().optional(),
  })
  .passthrough();

const upstreamEventSchema = z
  .object({
    id: z.string().optional(),
    author: z.string().optional(),
    partial: z.boolean().optional(),
    content: z
      .object({
        role: z.string().optional(),
        parts: z.array(upstreamPartSchema).optional(),
      })
      .passthrough()
      .optional(),
    customMetadata: z.record(z.string(), z.unknown()).optional(),
    custom_metadata: z.record(z.string(), z.unknown()).optional(),
  })
  .passthrough();

type UpstreamEvent = z.infer<typeof upstreamEventSchema>;

export type AgentRunRequest = {
  userId: string;
  conversationId: string;
  turnId: string;
  prompt: string;
  targetMolecule?: string;
  mechanism?: string;
  disease: "ischemic stroke";
  researchQuestion?: string;
};

export class AgentUnavailableError extends Error {
  constructor(message = "Agent service is unavailable.") {
    super(message);
    this.name = "AgentUnavailableError";
  }
}

export class AgentProtocolError extends Error {
  constructor(message = "Agent service returned an invalid event stream.") {
    super(message);
    this.name = "AgentProtocolError";
  }
}

interface AgentClient {
  run(
    input: AgentRunRequest,
    signal: AbortSignal,
  ): AsyncGenerator<PublicResearchEvent>;
  cancel(turnId: string): Promise<boolean>;
}

export function getAgentServiceUrl() {
  return process.env.AGENT_SERVICE_URL ?? "http://127.0.0.1:8001";
}

function cancelTimeoutMilliseconds() {
  const parsed = Number(process.env.AGENT_CANCEL_TIMEOUT_MS ?? 1_500);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 1_500;
}

function metadataOf(event: UpstreamEvent) {
  return event.customMetadata ?? event.custom_metadata ?? {};
}

function textOf(event: UpstreamEvent) {
  return (
    event.content?.parts
      ?.map((part) => part.text)
      .filter((value): value is string => typeof value === "string")
      .join("") ?? ""
  );
}

function safeText(value: unknown, fallback: string, maxLength: number) {
  return typeof value === "string" && value.trim()
    ? value.trim().slice(0, maxLength)
    : fallback;
}

function safeCount(value: unknown) {
  return typeof value === "number" &&
    Number.isInteger(value) &&
    value >= 0
    ? value
    : undefined;
}

function safeSequence(value: unknown) {
  return typeof value === "number" &&
    Number.isInteger(value) &&
    value >= 0
    ? value
    : undefined;
}

function eventContext(
  event: UpstreamEvent,
  metadata: Record<string, unknown>,
  context: { conversationId: string; turnId: string },
) {
  const sequence = safeSequence(
    metadata.event_sequence ?? metadata.eventSequence,
  );
  if (
    !event.id ||
    !/^[A-Za-z0-9_.:-]{1,128}$/u.test(event.id) ||
    sequence === undefined ||
    metadata.turn_id !== context.turnId ||
    metadata.conversation_id !== context.conversationId
  ) {
    return undefined;
  }
  return {
    eventId: event.id,
    sequence,
    data: {
      schemaVersion: "2.0" as const,
      ...context,
    },
  };
}

function safeSourceSummary(value: unknown) {
  const result = sourceSummaryListSchema.safeParse(value);
  return result.success ? result.data : undefined;
}

export function sanitizeAgentEvent(
  raw: unknown,
  context: { conversationId: string; turnId: string },
): PublicResearchEvent | undefined {
  const result = upstreamEventSchema.safeParse(raw);
  if (!result.success) {
    return undefined;
  }
  const event = result.data;
  const metadata = metadataOf(event);
  const kind = metadata.kind;
  const content = textOf(event);
  const safeContext = eventContext(event, metadata, context);
  if (!safeContext) {
    return undefined;
  }

  if (kind === "research_started") {
    return {
      eventId: safeContext.eventId,
      sequence: safeContext.sequence,
      type: "research_started",
      data: safeContext.data,
    };
  }
  if (kind === "search_progress") {
    const sourceCount = safeCount(
      metadata.source_count ?? metadata.sourceCount,
    );
    return {
      eventId: safeContext.eventId,
      sequence: safeContext.sequence,
      type: "search_progress",
      data: {
        ...safeContext.data,
        stage: safeText(metadata.stage, "retrieval", 80),
        message: safeText(content, "論文を検索しています。", 300),
        ...(sourceCount === undefined ? {} : { sourceCount }),
      },
    };
  }
  if (kind === "answer_delta" && content) {
    return {
      eventId: safeContext.eventId,
      sequence: safeContext.sequence,
      type: "answer_delta",
      data: {
        ...safeContext.data,
        delta: content.slice(0, 20_000),
      },
    };
  }
  if (kind === "completed") {
    const sourceCount = safeCount(
      metadata.source_count ?? metadata.sourceCount,
    );
    const sourceSummary = safeSourceSummary(
      metadata.source_summary ?? metadata.sourceSummary ?? metadata.sources,
    );
    return {
      eventId: safeContext.eventId,
      sequence: safeContext.sequence,
      type: "completed",
      data: {
        ...safeContext.data,
        answerMarkdown: content.slice(0, 150_000),
        ...(sourceCount === undefined ? {} : { sourceCount }),
        ...(sourceSummary === undefined ? {} : { sourceSummary }),
      },
    };
  }
  if (kind === "cancelled") {
    return {
      eventId: safeContext.eventId,
      sequence: safeContext.sequence,
      type: "cancelled",
      data: {
        ...safeContext.data,
        message: "調査をキャンセルしました。",
      },
    };
  }
  if (kind === "error") {
    return {
      eventId: safeContext.eventId,
      sequence: safeContext.sequence,
      type: "error",
      data: {
        ...safeContext.data,
        code: "internal_error",
        message: "調査中にエラーが発生しました。",
        retryable: true,
      },
    };
  }
  return undefined;
}

export class HttpAgentClient implements AgentClient {
  constructor(
    private readonly baseUrl = getAgentServiceUrl(),
    private readonly fetchImplementation: typeof fetch = fetch,
  ) {}

  async *run(
    input: AgentRunRequest,
    signal: AbortSignal,
  ): AsyncGenerator<PublicResearchEvent> {
    let response: Response;
    try {
      response = await this.fetchImplementation(
        `${this.baseUrl.replace(/\/$/u, "")}/run_sse`,
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            accept: "text/event-stream",
          },
          body: JSON.stringify({
            app_name: "deepresearch_agent",
            user_id: input.userId,
            session_id: input.conversationId,
            new_message: {
              role: "user",
              parts: [{ text: input.prompt }],
            },
            streaming: true,
            custom_metadata: {
              turn_id: input.turnId,
              conversation_id: input.conversationId,
              target_molecule: input.targetMolecule,
              mechanism: input.mechanism,
              disease: input.disease,
              research_question: input.researchQuestion,
            },
          }),
          signal,
        },
      );
    } catch (error) {
      if (signal.aborted) {
        throw error;
      }
      throw new AgentUnavailableError();
    }

    if (!response.ok || !response.body) {
      throw new AgentUnavailableError(
        `Agent service responded with ${response.status}.`,
      );
    }

    const seenEventIds = new Set<string>();
    let expectedSequence = 0;
    let started = false;
    let terminal = false;
    for await (const frame of parseSseStream(response.body)) {
      if (frame.data === "[DONE]") {
        break;
      }
      let raw: unknown;
      try {
        raw = JSON.parse(frame.data) as unknown;
      } catch {
        throw new AgentProtocolError();
      }
      const parsedRaw = upstreamEventSchema.safeParse(raw);
      if (!parsedRaw.success) {
        throw new AgentProtocolError();
      }
      if (parsedRaw.data.id && seenEventIds.has(parsedRaw.data.id)) {
        continue;
      }
      if (terminal) {
        continue;
      }
      const event = sanitizeAgentEvent(raw, {
        conversationId: input.conversationId,
        turnId: input.turnId,
      });
      if (!event || event.sequence !== expectedSequence) {
        throw new AgentProtocolError();
      }
      if (!started) {
        if (event.type !== "research_started" || event.sequence !== 0) {
          throw new AgentProtocolError();
        }
        started = true;
      } else if (event.type === "research_started") {
        throw new AgentProtocolError();
      }
      seenEventIds.add(event.eventId);
      expectedSequence += 1;
      terminal = ["completed", "cancelled", "error"].includes(event.type);
      yield event;
    }

    if ((!started || !terminal) && !signal.aborted) {
      throw new AgentProtocolError();
    }
  }

  async cancel(turnId: string) {
    try {
      const response = await this.fetchImplementation(
        `${this.baseUrl.replace(/\/$/u, "")}/runs/${encodeURIComponent(turnId)}/cancel`,
        {
          method: "POST",
          signal: AbortSignal.timeout(cancelTimeoutMilliseconds()),
        },
      );
      return response.status === 204 || response.status === 404;
    } catch {
      return false;
    }
  }
}
