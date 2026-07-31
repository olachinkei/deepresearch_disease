import { parseSseStream } from "~/shared/sse/parser";

import {
  decodePublicEvent,
  type PublicResearchEvent,
} from "./public-events";

const TERMINAL_EVENTS = new Set<PublicResearchEvent["type"]>([
  "completed",
  "cancelled",
  "error",
]);

export class ResearchStreamProtocolError extends Error {
  readonly code = "stream_protocol_error";
  readonly retryable = true;

  constructor() {
    super("接続が途中で切断されました。もう一度お試しください。");
    this.name = "ResearchStreamProtocolError";
  }
}

export async function consumeResearchStream(
  response: Response,
  onEvent: (event: PublicResearchEvent) => void | Promise<void>,
) {
  if (!response.ok) {
    throw new Error(`Research request failed with status ${response.status}.`);
  }
  if (!response.body) {
    throw new Error("Research response did not include a stream.");
  }

  const seenEventIds = new Set<string>();
  let expectedSequence = 0;
  let started = false;
  let terminal = false;
  let conversationId: string | undefined;
  let turnId: string | undefined;

  for await (const frame of parseSseStream(response.body)) {
    if (terminal) {
      continue;
    }
    if (frame.id && seenEventIds.has(frame.id)) {
      continue;
    }
    const event = decodePublicEvent(frame.event, frame.data);
    if (
      !event ||
      !frame.id ||
      frame.id !== event.eventId ||
      event.sequence !== expectedSequence
    ) {
      throw new ResearchStreamProtocolError();
    }
    if (seenEventIds.has(event.eventId)) {
      continue;
    }
    if (!started) {
      if (event.type !== "research_started" || event.sequence !== 0) {
        throw new ResearchStreamProtocolError();
      }
      started = true;
      conversationId = event.data.conversationId;
      turnId = event.data.turnId;
    } else if (
      event.type === "research_started" ||
      event.data.conversationId !== conversationId ||
      event.data.turnId !== turnId
    ) {
      throw new ResearchStreamProtocolError();
    }
    seenEventIds.add(event.eventId);
    expectedSequence += 1;
    terminal = TERMINAL_EVENTS.has(event.type);
    await onEvent(event);
  }

  if (!terminal) {
    throw new ResearchStreamProtocolError();
  }
}
