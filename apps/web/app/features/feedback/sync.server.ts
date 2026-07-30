import { z } from "zod";

import type { AppDatabase } from "~/shared/database/client.server";

import {
  feedbackIdempotencyKey,
  FeedbackRepository,
} from "./repository.server";

const syncResponseSchema = z
  .object({
    status: z.enum(["synced", "pending"]),
    feedback_id: z.string().optional(),
    trace_id: z.string().optional(),
  })
  .strict();

type FeedbackRecord = Awaited<
  ReturnType<FeedbackRepository["listReady"]>
>[number];

export class FeedbackSyncClient {
  constructor(
    private readonly baseUrl =
      process.env.AGENT_SERVICE_URL ?? "http://127.0.0.1:8001",
    private readonly fetchImplementation: typeof fetch = fetch,
  ) {}

  async sync(record: FeedbackRecord) {
    const idempotencyKey = feedbackIdempotencyKey(record);
    const response = await this.fetchImplementation(
      `${this.baseUrl.replace(/\/$/u, "")}/feedback/sync`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          accept: "application/json",
          "idempotency-key": idempotencyKey,
        },
        body: JSON.stringify({
          feedback_id: idempotencyKey,
          turn_id: record.turnId,
          rating: record.vote,
          reason: record.reason ?? undefined,
          comment: record.comment ?? undefined,
        }),
      },
    );
    if (!response.ok) {
      throw new Error(`Feedback sync failed with status ${response.status}.`);
    }
    return syncResponseSchema.parse(await response.json());
  }
}

export async function syncFeedbackQueue(
  database: AppDatabase,
  options: {
    limit?: number;
    now?: Date;
    client?: FeedbackSyncClient;
  } = {},
) {
  const repository = new FeedbackRepository(database);
  const records = await repository.listReady(
    options.limit ?? 50,
    options.now ?? new Date(),
  );
  const client = options.client ?? new FeedbackSyncClient();
  const result = { synced: 0, pending: 0, failed: 0 };

  for (const record of records) {
    const claimed = await repository.claimForSync(
      record.id,
      record.revision,
      options.now ?? new Date(),
    );
    if (!claimed) {
      continue;
    }
    const attempts = record.attempts + 1;
    try {
      const response = await client.sync(record);
      if (response.status === "synced") {
        await repository.markSynced(
          record.id,
          record.revision,
          response.feedback_id,
        );
        result.synced += 1;
      } else {
        await repository.deferPending(
          record.id,
          record.revision,
          attempts,
          options.now ?? new Date(),
        );
        result.pending += 1;
      }
    } catch (error) {
      await repository.markFailed(
        record.id,
        record.revision,
        attempts,
        error instanceof Error ? error.message : "Unknown sync error.",
        options.now ?? new Date(),
      );
      result.failed += 1;
    }
  }
  return result;
}
