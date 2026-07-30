import { parseSseStream } from "~/shared/sse/parser";

import {
  decodePublicEvent,
  type PublicResearchEvent,
} from "./public-events";

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

  for await (const frame of parseSseStream(response.body)) {
    const event = decodePublicEvent(frame.event, frame.data);
    if (event) {
      await onEvent(event);
    }
  }
}
