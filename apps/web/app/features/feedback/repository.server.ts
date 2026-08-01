import { randomUUID } from "node:crypto";

import {
  and,
  asc,
  eq,
  inArray,
  isNull,
  lte,
  or,
  sql,
} from "drizzle-orm";

import type { AppDatabase } from "~/shared/database/client.server";
import {
  feedbackQueue,
  feedbackRevisions,
} from "~/shared/database/schema";

import type { FeedbackInput } from "./schema";

function readyForSync(now: Date) {
  const nowIso = now.toISOString();
  return or(
    and(
      or(
        eq(feedbackQueue.syncStatus, "pending"),
        eq(feedbackQueue.syncStatus, "failed"),
      ),
      or(
        isNull(feedbackQueue.nextAttemptAt),
        lte(feedbackQueue.nextAttemptAt, nowIso),
      ),
    ),
    and(
      eq(feedbackQueue.syncStatus, "syncing"),
      lte(feedbackQueue.nextAttemptAt, nowIso),
    ),
  );
}

export class FeedbackRepository {
  constructor(private readonly db: AppDatabase) {}

  async upsert(input: FeedbackInput & { turnId: string; userId: string }) {
    const id = randomUUID();
    const comment = input.comment || null;
    await this.db
      .insert(feedbackQueue)
      .values({
        id,
        turnId: input.turnId,
        userId: input.userId,
        vote: input.vote,
        reason: input.reason,
        comment,
        syncStatus: "pending",
      })
      .onConflictDoNothing({
        target: [feedbackQueue.turnId, feedbackQueue.userId],
      });

    for (let attempt = 0; attempt < 3; attempt += 1) {
      const record = await this.findForTurn(input.turnId, input.userId);
      if (!record) {
        throw new Error("Failed to save feedback.");
      }
      if (
        record.vote === input.vote &&
        record.reason === (input.reason ?? null) &&
        record.comment === comment
      ) {
        return record;
      }
      await this.db
        .insert(feedbackRevisions)
        .values({
          id: `${record.id}:r${record.revision}`,
          feedbackId: record.id,
          turnId: record.turnId,
          userId: record.userId,
          vote: record.vote,
          reason: record.reason,
          comment: record.comment,
          revision: record.revision,
          syncStatus: record.syncStatus,
          attempts: record.attempts,
          nextAttemptAt: record.nextAttemptAt,
          lastError: record.lastError,
          weaveFeedbackId: record.weaveFeedbackId,
          createdAt: record.createdAt,
          updatedAt: record.updatedAt,
        })
        .onConflictDoNothing();
      const result = await this.db
        .update(feedbackQueue)
        .set({
          vote: input.vote,
          reason: input.reason ?? null,
          comment,
          revision: record.revision + 1,
          syncStatus: "pending",
          attempts: 0,
          nextAttemptAt: null,
          lastError: null,
          weaveFeedbackId: null,
          updatedAt: sql`CURRENT_TIMESTAMP`,
        })
        .where(
          and(
            eq(feedbackQueue.id, record.id),
            eq(feedbackQueue.revision, record.revision),
          ),
        );
      if (result.rowsAffected === 1) {
        const updated = await this.findForTurn(input.turnId, input.userId);
        if (updated) {
          return updated;
        }
      }
    }
    throw new Error("Feedback was updated concurrently; retry the request.");
  }

  async findForTurn(turnId: string, userId: string) {
    const [record] = await this.db
      .select()
      .from(feedbackQueue)
      .where(
        and(
          eq(feedbackQueue.turnId, turnId),
          eq(feedbackQueue.userId, userId),
        ),
      )
      .limit(1);
    return record;
  }

  async listForTurns(turnIds: string[], userId: string) {
    if (turnIds.length === 0) {
      return [];
    }
    return this.db
      .select()
      .from(feedbackQueue)
      .where(
        and(
          inArray(feedbackQueue.turnId, turnIds),
          eq(feedbackQueue.userId, userId),
        ),
      );
  }

  async listReady(limit = 50, now = new Date()) {
    return this.db
      .select()
      .from(feedbackQueue)
      .where(readyForSync(now))
      .orderBy(asc(feedbackQueue.createdAt))
      .limit(limit);
  }

  async claimForSync(id: string, revision: number, now = new Date()) {
    const leaseExpiresAt = new Date(now.getTime() + 5 * 60 * 1_000).toISOString();
    const result = await this.db
      .update(feedbackQueue)
      .set({
        syncStatus: "syncing",
        nextAttemptAt: leaseExpiresAt,
        updatedAt: sql`CURRENT_TIMESTAMP`,
      })
      .where(
        and(
          eq(feedbackQueue.id, id),
          eq(feedbackQueue.revision, revision),
          readyForSync(now),
        ),
      );
    return result.rowsAffected === 1;
  }

  async markSynced(id: string, revision: number, weaveFeedbackId?: string) {
    await this.db
      .update(feedbackQueue)
      .set({
        syncStatus: "synced",
        weaveFeedbackId: weaveFeedbackId ?? null,
        lastError: null,
        nextAttemptAt: null,
        updatedAt: sql`CURRENT_TIMESTAMP`,
      })
      .where(
        and(
          eq(feedbackQueue.id, id),
          eq(feedbackQueue.revision, revision),
        ),
      );
    await this.db
      .update(feedbackRevisions)
      .set({
        syncStatus: "synced",
        weaveFeedbackId: weaveFeedbackId ?? null,
        lastError: null,
        nextAttemptAt: null,
      })
      .where(
        and(
          eq(feedbackRevisions.feedbackId, id),
          eq(feedbackRevisions.revision, revision),
        ),
      );
  }

  async markFailed(
    id: string,
    revision: number,
    attempts: number,
    error: string,
    now = new Date(),
  ) {
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
      .where(
        and(
          eq(feedbackQueue.id, id),
          eq(feedbackQueue.revision, revision),
        ),
      );
    await this.db
      .update(feedbackRevisions)
      .set({
        syncStatus: "failed",
        attempts,
        lastError: error.slice(0, 500),
        nextAttemptAt: new Date(now.getTime() + retryDelayMs).toISOString(),
      })
      .where(
        and(
          eq(feedbackRevisions.feedbackId, id),
          eq(feedbackRevisions.revision, revision),
        ),
      );
  }

  async deferPending(
    id: string,
    revision: number,
    attempts: number,
    now = new Date(),
  ) {
    const retryDelayMs = Math.min(15 * 60 * 1_000, 2 ** attempts * 2_000);
    await this.db
      .update(feedbackQueue)
      .set({
        syncStatus: "pending",
        attempts,
        nextAttemptAt: new Date(now.getTime() + retryDelayMs).toISOString(),
        updatedAt: sql`CURRENT_TIMESTAMP`,
      })
      .where(
        and(
          eq(feedbackQueue.id, id),
          eq(feedbackQueue.revision, revision),
        ),
      );
    await this.db
      .update(feedbackRevisions)
      .set({
        syncStatus: "pending",
        attempts,
        nextAttemptAt: new Date(now.getTime() + retryDelayMs).toISOString(),
      })
      .where(
        and(
          eq(feedbackRevisions.feedbackId, id),
          eq(feedbackRevisions.revision, revision),
        ),
      );
  }
}

export type FeedbackRecord = NonNullable<
  Awaited<ReturnType<FeedbackRepository["findForTurn"]>>
>;

export function feedbackIdempotencyKey(record: FeedbackRecord) {
  return `${record.id}:r${record.revision}`;
}
