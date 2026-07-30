import { randomUUID } from "node:crypto";

import {
  and,
  asc,
  eq,
  isNull,
  lte,
  or,
  sql,
} from "drizzle-orm";

import type { AppDatabase } from "~/shared/database/client.server";
import { feedbackQueue } from "~/shared/database/schema";

import type { FeedbackInput } from "./schema";

export class FeedbackRepository {
  constructor(private readonly db: AppDatabase) {}

  async enqueue(input: FeedbackInput & { turnId: string; userId: string }) {
    const id = randomUUID();
    await this.db.insert(feedbackQueue).values({
      id,
      turnId: input.turnId,
      userId: input.userId,
      vote: input.vote,
      reason: input.reason,
      comment: input.comment || undefined,
      syncStatus: "pending",
    });
    const [record] = await this.db
      .select()
      .from(feedbackQueue)
      .where(eq(feedbackQueue.id, id))
      .limit(1);
    if (!record) {
      throw new Error("Failed to enqueue feedback.");
    }
    return record;
  }

  async listReady(limit = 50, now = new Date()) {
    return this.db
      .select()
      .from(feedbackQueue)
      .where(
        and(
          or(
            eq(feedbackQueue.syncStatus, "pending"),
            eq(feedbackQueue.syncStatus, "failed"),
          ),
          or(
            isNull(feedbackQueue.nextAttemptAt),
            lte(feedbackQueue.nextAttemptAt, now.toISOString()),
          ),
        ),
      )
      .orderBy(asc(feedbackQueue.createdAt))
      .limit(limit);
  }

  async markSyncing(id: string) {
    await this.db
      .update(feedbackQueue)
      .set({
        syncStatus: "syncing",
        updatedAt: sql`CURRENT_TIMESTAMP`,
      })
      .where(eq(feedbackQueue.id, id));
  }

  async markSynced(id: string, weaveFeedbackId?: string) {
    await this.db
      .update(feedbackQueue)
      .set({
        syncStatus: "synced",
        weaveFeedbackId: weaveFeedbackId ?? null,
        lastError: null,
        nextAttemptAt: null,
        updatedAt: sql`CURRENT_TIMESTAMP`,
      })
      .where(eq(feedbackQueue.id, id));
  }

  async markFailed(id: string, attempts: number, error: string, now = new Date()) {
    const retryDelayMs = Math.min(60 * 60 * 1_000, 2 ** attempts * 5_000);
    await this.db
      .update(feedbackQueue)
      .set({
        syncStatus: "failed",
        attempts,
        lastError: error.slice(0, 500),
        nextAttemptAt: new Date(now.getTime() + retryDelayMs).toISOString(),
        updatedAt: sql`CURRENT_TIMESTAMP`,
      })
      .where(eq(feedbackQueue.id, id));
  }

  async deferPending(id: string, attempts: number, now = new Date()) {
    const retryDelayMs = Math.min(15 * 60 * 1_000, 2 ** attempts * 2_000);
    await this.db
      .update(feedbackQueue)
      .set({
        syncStatus: "pending",
        attempts,
        nextAttemptAt: new Date(now.getTime() + retryDelayMs).toISOString(),
        updatedAt: sql`CURRENT_TIMESTAMP`,
      })
      .where(eq(feedbackQueue.id, id));
  }
}
