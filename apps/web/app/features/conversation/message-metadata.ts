import { z } from "zod";

import { sourceSummaryListSchema } from "~/features/research/source-summary";

const assistantMessageMetadataSchema = z
  .object({
    schemaVersion: z.literal("1.0"),
    sourceCount: z.number().int().nonnegative().optional(),
    sourceSummary: sourceSummaryListSchema.optional(),
  })
  .strict()
  .superRefine((metadata, context) => {
    if (
      metadata.sourceCount !== undefined &&
      metadata.sourceSummary !== undefined &&
      metadata.sourceCount < metadata.sourceSummary.length
    ) {
      context.addIssue({
        code: "custom",
        path: ["sourceCount"],
        message: "Source count cannot be smaller than the summary.",
      });
    }
  });

export type AssistantMessageMetadata = z.infer<
  typeof assistantMessageMetadataSchema
>;

export function buildAssistantMessageMetadata(input: {
  sourceCount?: number;
  sourceSummary?: unknown;
}) {
  return assistantMessageMetadataSchema.parse({
    schemaVersion: "1.0",
    ...(input.sourceCount === undefined
      ? {}
      : { sourceCount: input.sourceCount }),
    ...(input.sourceSummary === undefined
      ? {}
      : { sourceSummary: input.sourceSummary }),
  });
}

export function parseAssistantMessageMetadata(value: string | null) {
  if (!value) {
    return undefined;
  }
  try {
    const parsed = assistantMessageMetadataSchema.safeParse(JSON.parse(value));
    return parsed.success ? parsed.data : undefined;
  } catch {
    return undefined;
  }
}

export function serializeAssistantMessageMetadata(
  metadata: AssistantMessageMetadata,
) {
  return JSON.stringify(assistantMessageMetadataSchema.parse(metadata));
}
