export type SseFrame = {
  event?: string;
  data: string;
  id?: string;
};

function parseFrame(rawFrame: string): SseFrame | undefined {
  let event: string | undefined;
  let id: string | undefined;
  const data: string[] = [];

  for (const rawLine of rawFrame.split(/\r?\n/u)) {
    if (!rawLine || rawLine.startsWith(":")) {
      continue;
    }
    const separator = rawLine.indexOf(":");
    const field = separator === -1 ? rawLine : rawLine.slice(0, separator);
    let value = separator === -1 ? "" : rawLine.slice(separator + 1);
    if (value.startsWith(" ")) {
      value = value.slice(1);
    }
    if (field === "event") {
      event = value;
    } else if (field === "data") {
      data.push(value);
    } else if (field === "id") {
      id = value;
    }
  }

  if (data.length === 0) {
    return undefined;
  }
  return { event, data: data.join("\n"), id };
}

export async function* parseSseStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<SseFrame> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        buffer += decoder.decode();
        break;
      }
      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.match(/\r?\n\r?\n/u);
      while (boundary?.index !== undefined) {
        const rawFrame = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        const frame = parseFrame(rawFrame);
        if (frame) {
          yield frame;
        }
        boundary = buffer.match(/\r?\n\r?\n/u);
      }
    }

    const finalFrame = parseFrame(buffer);
    if (finalFrame) {
      yield finalFrame;
    }
  } finally {
    reader.releaseLock();
  }
}
