import { randomUUID } from "node:crypto";

import { eq } from "drizzle-orm";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ConversationRepository } from "~/features/conversation/repository.server";
import { FeedbackRepository } from "~/features/feedback/repository.server";
import {
  FeedbackSyncClient,
  syncFeedbackQueue,
} from "~/features/feedback/sync.server";
import { IdentityRepository } from "~/features/identity/repository.server";
import { researchRequestSchema } from "~/features/research/schema";
import {
  feedbackQueue,
  feedbackRevisions,
} from "~/shared/database/schema";

import { createTestDatabase } from "../test-database";

describe("feedback queue", () => {
  let testDatabase: Awaited<ReturnType<typeof createTestDatabase>>;
  let userId: string;
  let turnId: string;

  beforeEach(async () => {
    testDatabase = await createTestDatabase();
    userId = randomUUID();
    await new IdentityRepository(testDatabase.db).create({
      id: userId,
      displayName: "研究者A",
    });
    const conversations = new ConversationRepository(testDatabase.db);
    const conversation = await conversations.create({
      userId,
      title: "Feedback test",
      research: researchRequestSchema.parse({ disease: "ischemic stroke" }),
    });
    const turn = await conversations.beginTurn({
      conversationId: conversation.id,
      userId,
      query: "Question",
    });
    turnId = turn.turnId;
    await conversations.appendAssistantMessage({
      conversationId: conversation.id,
      turnId,
      content: "Answer",
    });
    await conversations.markCompleted(turnId);
  });

  afterEach(async () => {
    await testDatabase.cleanup();
  });

  it("enqueues feedback and syncs only through the server-side client", async () => {
    const feedback = await new FeedbackRepository(testDatabase.db).upsert({
      turnId,
      userId,
      vote: "down",
      reason: "unsupported_claim",
      comment: "Citation 2 does not support the claim.",
    });
    let sentBody: Record<string, unknown> | undefined;
    let idempotencyKey: string | null | undefined;
    const client = new FeedbackSyncClient(
      "http://agent.test",
      async (_input, init) => {
        sentBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
        idempotencyKey = new Headers(init?.headers).get("idempotency-key");
        return Response.json({
          status: "synced",
          feedback_id: "weave-feedback-1",
          trace_id: "weave-trace-1",
        });
      },
    );

    const result = await syncFeedbackQueue(testDatabase.db, { client });
    const [stored] = await testDatabase.db
      .select()
      .from(feedbackQueue)
      .where(eq(feedbackQueue.id, feedback.id));

    expect(result).toEqual({ synced: 1, pending: 0, failed: 0 });
    expect(idempotencyKey).toBe(`${feedback.id}:r1`);
    expect(sentBody).toEqual({
      feedback_id: `${feedback.id}:r1`,
      turn_id: turnId,
      rating: "down",
      reason: "unsupported_claim",
      comment: "Citation 2 does not support the claim.",
    });
    expect(stored?.syncStatus).toBe("synced");
    expect(stored?.weaveFeedbackId).toBe("weave-feedback-1");
  });

  it("keeps an unresolved Weave turn trace pending with backoff", async () => {
    const feedback = await new FeedbackRepository(testDatabase.db).upsert({
      turnId,
      userId,
      vote: "up",
    });
    const now = new Date("2026-07-30T00:00:00.000Z");
    const sentIds: string[] = [];
    const client = new FeedbackSyncClient(
      "http://agent.test",
      async (_input, init) => {
        const body = JSON.parse(String(init?.body)) as { feedback_id: string };
        sentIds.push(body.feedback_id);
        return Response.json({ status: "pending" });
      },
    );

    const result = await syncFeedbackQueue(testDatabase.db, { client, now });
    const [stored] = await testDatabase.db
      .select()
      .from(feedbackQueue)
      .where(eq(feedbackQueue.id, feedback.id));

    expect(result.pending).toBe(1);
    expect(stored?.syncStatus).toBe("pending");
    expect(stored?.attempts).toBe(1);
    expect(new Date(stored?.nextAttemptAt ?? 0).getTime()).toBeGreaterThan(
      now.getTime(),
    );

    const retryAt = new Date(stored?.nextAttemptAt ?? now);
    const retry = await syncFeedbackQueue(testDatabase.db, {
      client,
      now: retryAt,
    });
    expect(retry.pending).toBe(1);
    expect(sentIds).toEqual([`${feedback.id}:r1`, `${feedback.id}:r1`]);
    expect(
      await new FeedbackRepository(testDatabase.db).claimForSync(
        feedback.id,
        feedback.revision,
        retryAt,
      ),
    ).toBe(false);
  });

  it("returns the same record for duplicate input and revises changed feedback", async () => {
    const repository = new FeedbackRepository(testDatabase.db);
    const first = await repository.upsert({
      turnId,
      userId,
      vote: "up",
    });
    const duplicate = await repository.upsert({
      turnId,
      userId,
      vote: "up",
    });
    expect(duplicate.id).toBe(first.id);
    expect(duplicate.revision).toBe(1);

    await repository.markSynced(
      first.id,
      first.revision,
      `${first.id}:r1`,
    );
    const changed = await repository.upsert({
      turnId,
      userId,
      vote: "down",
      reason: "incomplete",
      comment: "Synthetic local comment",
    });
    expect(changed.id).toBe(first.id);
    expect(changed.revision).toBe(2);
    expect(changed.syncStatus).toBe("pending");
    expect(changed.attempts).toBe(0);
    expect(changed.weaveFeedbackId).toBeNull();

    await repository.markSynced(first.id, first.revision, `${first.id}:r1`);
    const afterLateRevisionOneSync = await repository.findForTurn(turnId, userId);
    expect(afterLateRevisionOneSync?.syncStatus).toBe("pending");

    const active = await repository.listForTurns([turnId], userId);
    const history = await testDatabase.db.select().from(feedbackRevisions);
    expect(active).toHaveLength(1);
    expect(active[0]?.comment).toBe("Synthetic local comment");
    expect(history).toEqual([
      expect.objectContaining({
        feedbackId: first.id,
        revision: 1,
        vote: "up",
        syncStatus: "synced",
      }),
    ]);
  });

  it("allows only one worker to claim a feedback revision", async () => {
    const feedback = await new FeedbackRepository(testDatabase.db).upsert({
      turnId,
      userId,
      vote: "up",
    });
    let requests = 0;
    let releaseRequest: (() => void) | undefined;
    const requestStarted = new Promise<void>((resolve) => {
      releaseRequest = resolve;
    });
    const client = new FeedbackSyncClient("http://agent.test", async () => {
      requests += 1;
      await requestStarted;
      return Response.json({
        status: "synced",
        feedback_id: `${feedback.id}:r1`,
      });
    });

    const firstWorker = syncFeedbackQueue(testDatabase.db, { client });
    const secondWorker = syncFeedbackQueue(testDatabase.db, { client });
    await new Promise((resolve) => setTimeout(resolve, 0));
    releaseRequest?.();
    const results = await Promise.all([firstWorker, secondWorker]);

    expect(requests).toBe(1);
    expect(results.reduce((total, result) => total + result.synced, 0)).toBe(1);
  });
});
