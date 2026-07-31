import { z } from "zod";

const eventContext = {
  schemaVersion: z.literal("2.0"),
  conversationId: z.uuid(),
  turnId: z.uuid(),
} as const;

const eventEnvelope = {
  eventId: z.string().min(1).max(128).regex(/^[A-Za-z0-9_.:-]+$/u),
  sequence: z.number().int().nonnegative(),
} as const;

const researchStartedSchema = z
  .object({
    ...eventContext,
  })
  .strict();

const searchProgressSchema = z
  .object({
    ...eventContext,
    stage: z.string().max(80),
    message: z.string().max(300),
    sourceCount: z.number().int().nonnegative().optional(),
  })
  .strict();

const answerDeltaSchema = z
  .object({
    ...eventContext,
    delta: z.string().max(20_000),
  })
  .strict();

export const sourceSummarySchema = z
  .object({
    id: z.string().min(1).max(120),
    title: z.string().min(1).max(300),
    url: z
      .url()
      .refine((value) => /^https?:\/\//u.test(value), "URL must be HTTP(S).")
      .optional(),
    sourceType: z.enum(["internal", "web"]),
  })
  .strict();

const completedSchema = z
  .object({
    ...eventContext,
    answerMarkdown: z.string().max(150_000),
    sourceCount: z.number().int().nonnegative().optional(),
    sourceSummary: z.array(sourceSummarySchema).max(12).optional(),
  })
  .strict();

const cancelledSchema = z
  .object({
    ...eventContext,
    message: z.string().max(300),
  })
  .strict();

const errorSchema = z
  .object({
    ...eventContext,
    code: z.enum([
      "invalid_request",
      "agent_unavailable",
      "agent_protocol_error",
      "internal_error",
    ]),
    message: z.string().max(300),
    retryable: z.boolean(),
  })
  .strict();

const publicEventSchema = z.discriminatedUnion("type", [
  z.object({
    ...eventEnvelope,
    type: z.literal("research_started"),
    data: researchStartedSchema,
  }),
  z.object({
    ...eventEnvelope,
    type: z.literal("search_progress"),
    data: searchProgressSchema,
  }),
  z.object({
    ...eventEnvelope,
    type: z.literal("answer_delta"),
    data: answerDeltaSchema,
  }),
  z.object({
    ...eventEnvelope,
    type: z.literal("completed"),
    data: completedSchema,
  }),
  z.object({
    ...eventEnvelope,
    type: z.literal("cancelled"),
    data: cancelledSchema,
  }),
  z.object({
    ...eventEnvelope,
    type: z.literal("error"),
    data: errorSchema,
  }),
]);

export type PublicResearchEvent = z.infer<typeof publicEventSchema>;

export function encodePublicEvent(event: PublicResearchEvent) {
  return (
    `id: ${event.eventId}\nevent: ${event.type}\n` +
    `data: ${JSON.stringify({
      eventId: event.eventId,
      sequence: event.sequence,
      ...event.data,
    })}\n\n`
  );
}

export function decodePublicEvent(eventName: string | undefined, data: string) {
  if (!eventName) {
    return undefined;
  }
  try {
    const parsed = z
      .object({
        eventId: eventEnvelope.eventId,
        sequence: eventEnvelope.sequence,
      })
      .passthrough()
      .parse(JSON.parse(data) as unknown);
    const { eventId, sequence, ...eventData } = parsed;
    return publicEventSchema.parse({
      eventId,
      sequence,
      type: eventName,
      data: eventData,
    });
  } catch {
    return undefined;
  }
}
