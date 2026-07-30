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
import { feedbackQueue } from "~/shared/database/schema";

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
    const feedback = await new FeedbackRepository(testDatabase.db).enqueue({
      turnId,
      userId,
      vote: "down",
      reason: "unsupported_claim",
      comment: "Citation 2 does not support the claim.",
    });
    let sentBody: Record<string, unknown> | undefined;
    const client = new FeedbackSyncClient(
      "http://agent.test",
      async (_input, init) => {
        sentBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
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
    expect(sentBody).toEqual({
      feedback_id: feedback.id,
      turn_id: turnId,
      rating: "down",
      reason: "unsupported_claim",
      comment: "Citation 2 does not support the claim.",
    });
    expect(stored?.syncStatus).toBe("synced");
    expect(stored?.weaveFeedbackId).toBe("weave-feedback-1");
  });

  it("keeps an unresolved Weave turn trace pending with backoff", async () => {
    const feedback = await new FeedbackRepository(testDatabase.db).enqueue({
      turnId,
      userId,
      vote: "up",
    });
    const now = new Date("2026-07-30T00:00:00.000Z");
    const client = new FeedbackSyncClient("http://agent.test", async () =>
      Response.json({ status: "pending" }),
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
  });
});
