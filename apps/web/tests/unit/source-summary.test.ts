import { describe, expect, it } from "vitest";

import {
  buildAssistantMessageMetadata,
  parseAssistantMessageMetadata,
} from "~/features/conversation/message-metadata";
import {
  sourceSummaryListSchema,
  sourceSummarySchema,
} from "~/features/research/source-summary";

describe("structured source summary", () => {
  it("canonicalizes safe public URLs and strips fragments", () => {
    const source = sourceSummarySchema.parse({
      id: "E1",
      title: " Public source ",
      url: "HTTPS://Example.ORG/paper#internal-fragment",
      sourceType: "web",
      verificationStatus: "verified",
    });

    expect(source).toEqual({
      id: "E1",
      title: "Public source",
      url: "https://example.org/paper",
      sourceType: "web",
      verificationStatus: "verified",
    });
  });

  it("never exposes an internal source URL", () => {
    const source = sourceSummarySchema.parse({
      id: "I1",
      title: "Synthetic internal index",
      url: "https://internal.example.test/document/secret-id",
      sourceType: "internal",
      verificationStatus: "unverified",
    });

    expect(source.sourceType).toBe("internal");
    expect(source.url).toBeUndefined();
  });

  it.each([
    {
      name: "dangerous URL scheme",
      source: {
        id: "E1",
        title: "Unsafe",
        url: "javascript:alert(1)",
        sourceType: "web",
      },
    },
    {
      name: "URL credentials",
      source: {
        id: "E1",
        title: "Unsafe",
        url: "https://user:secret@example.org/paper",
        sourceType: "web",
      },
    },
    {
      name: "unknown source type",
      source: {
        id: "E1",
        title: "Unknown",
        sourceType: "partner_database",
      },
    },
  ])("rejects $name", ({ source }) => {
    expect(sourceSummarySchema.safeParse(source).success).toBe(false);
  });

  it("rejects malformed or raw persisted metadata", () => {
    const malformed = JSON.stringify({
      schemaVersion: "1.0",
      sourceCount: 1,
      toolResponse: "RAW_INTERNAL_EXCERPT",
    });
    expect(parseAssistantMessageMetadata(malformed)).toBeUndefined();
    expect(parseAssistantMessageMetadata("not-json")).toBeUndefined();

    expect(
      buildAssistantMessageMetadata({
        sourceCount: 1,
        sourceSummary: [
          {
            id: "E1",
            title: "Safe source",
            sourceType: "web",
            verificationStatus: "verified",
          },
        ],
      }),
    ).toMatchObject({ schemaVersion: "1.0", sourceCount: 1 });
  });

  it("rejects duplicate source IDs", () => {
    const source = {
      id: "E1",
      title: "Duplicate evidence",
      sourceType: "web",
      verificationStatus: "verified",
    } as const;
    expect(sourceSummaryListSchema.safeParse([source, source]).success).toBe(
      false,
    );
  });
});
