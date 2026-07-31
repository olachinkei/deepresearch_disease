import { randomUUID } from "node:crypto";

import { eq } from "drizzle-orm";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ConversationRepository } from "~/features/conversation/repository.server";
import { buildAssistantMessageMetadata } from "~/features/conversation/message-metadata";
import { IdentityRepository } from "~/features/identity/repository.server";
import { researchRequestSchema } from "~/features/research/schema";
import { transcriptMessages } from "~/shared/database/schema";

import { createTestDatabase } from "../test-database";

describe("conversation repositories", () => {
  let testDatabase: Awaited<ReturnType<typeof createTestDatabase>>;

  beforeEach(async () => {
    testDatabase = await createTestDatabase();
  });

  afterEach(async () => {
    await testDatabase.cleanup();
  });

  it("persists an owned multi-turn transcript and status", async () => {
    const userId = randomUUID();
    await new IdentityRepository(testDatabase.db).create({
      id: userId,
      displayName: "研究者A",
    });
    const repository = new ConversationRepository(testDatabase.db);
    const research = researchRequestSchema.parse({
      displayName: "研究者A",
      targetMolecule: "NLRP3",
      mechanism: "inhibition",
      disease: "ischemic stroke",
      researchQuestion: "Assess target validity.",
    });
    const conversation = await repository.create({
      userId,
      title: "NLRP3 · inhibition",
      research,
    });
    const first = await repository.beginTurn({
      conversationId: conversation.id,
      userId,
      query: "private agent prompt",
      displayQuery: "NLRP3の標的妥当性を調査",
    });
    await repository.appendAssistantMessage({
      conversationId: conversation.id,
      turnId: first.turnId,
      content: "# 結論\n根拠があります。",
      metadata: buildAssistantMessageMetadata({
        sourceCount: 4,
        sourceSummary: [
          {
            id: "I1",
            title: "Synthetic internal record",
            url: "https://internal.example.test/raw-location",
            sourceType: "internal",
            verificationStatus: "unverified",
          },
        ],
      }),
    });
    await repository.markCompleted(first.turnId);
    const second = await repository.beginTurn({
      conversationId: conversation.id,
      userId,
      query: "Compare negative evidence.",
    });

    const detail = await repository.getDetail(conversation.id, userId);
    expect(detail?.turns.map((turn) => turn.sequence)).toEqual([1, 2]);
    expect(detail?.turns[0]?.status).toBe("completed");
    expect(detail?.turns[1]?.id).toBe(second.turnId);
    expect(detail?.messages.map((message) => message.content)).toEqual([
      "NLRP3の標的妥当性を調査",
      "# 結論\n根拠があります。",
      "Compare negative evidence.",
    ]);
    expect(detail?.messages[1]?.sourceMetadata).toMatchObject({
      schemaVersion: "1.0",
      sourceCount: 4,
      sourceSummary: [
        {
          id: "I1",
          sourceType: "internal",
          verificationStatus: "unverified",
        },
      ],
    });
    expect(detail?.messages[1]?.sourceMetadata?.sourceSummary?.[0]?.url).toBeUndefined();
  });

  it("does not return another local user's conversation", async () => {
    const identities = new IdentityRepository(testDatabase.db);
    const firstUser = randomUUID();
    const secondUser = randomUUID();
    await identities.create({ id: firstUser, displayName: "A" });
    await identities.create({ id: secondUser, displayName: "B" });
    const repository = new ConversationRepository(testDatabase.db);
    const conversation = await repository.create({
      userId: firstUser,
      title: "Private research",
      research: researchRequestSchema.parse({
        disease: "ischemic stroke",
      }),
    });

    expect(
      await repository.findOwned(conversation.id, secondUser),
    ).toBeUndefined();
  });

  it("allows exactly one terminal transition in a complete/cancel race", async () => {
    const userId = randomUUID();
    await new IdentityRepository(testDatabase.db).create({
      id: userId,
      displayName: "競合テスト",
    });
    const repository = new ConversationRepository(testDatabase.db);
    const conversation = await repository.create({
      userId,
      title: "Atomic transition",
      research: researchRequestSchema.parse({
        disease: "ischemic stroke",
        researchQuestion: "Synthetic race test.",
      }),
    });
    const turn = await repository.beginTurn({
      conversationId: conversation.id,
      userId,
      query: "Synthetic race test.",
    });

    const [completed, cancelled] = await Promise.all([
      repository.completeRunningTurn({
        conversationId: conversation.id,
        turnId: turn.turnId,
        content: "# Synthetic completed answer",
      }),
      repository.cancelRunningTurn(turn.turnId),
    ]);

    expect([completed, cancelled].filter(Boolean)).toHaveLength(1);
    const detail = await repository.getDetail(conversation.id, userId);
    expect(detail?.turns[0]?.status).toBe(
      completed ? "completed" : "cancelled",
    );
    expect(
      detail?.messages.filter((message) => message.role === "assistant"),
    ).toHaveLength(completed ? 1 : 0);
  });

  it("keeps terminal turns unchanged when cancel is repeated", async () => {
    const userId = randomUUID();
    await new IdentityRepository(testDatabase.db).create({
      id: userId,
      displayName: "再送テスト",
    });
    const repository = new ConversationRepository(testDatabase.db);
    const conversation = await repository.create({
      userId,
      title: "Idempotent cancel",
      research: researchRequestSchema.parse({
        disease: "ischemic stroke",
      }),
    });
    const completedTurn = await repository.beginTurn({
      conversationId: conversation.id,
      userId,
      query: "Complete first.",
    });
    expect(await repository.markCompleted(completedTurn.turnId)).toBe(true);
    expect(await repository.cancelRunningTurn(completedTurn.turnId)).toBe(false);

    const cancelledTurn = await repository.beginTurn({
      conversationId: conversation.id,
      userId,
      query: "Cancel first.",
    });
    expect(await repository.cancelRunningTurn(cancelledTurn.turnId)).toBe(true);
    expect(await repository.cancelRunningTurn(cancelledTurn.turnId)).toBe(false);
    expect(await repository.markFailed(cancelledTurn.turnId)).toBe(false);

    const detail = await repository.getDetail(conversation.id, userId);
    expect(detail?.turns.map((turn) => turn.status)).toEqual([
      "completed",
      "cancelled",
    ]);
  });

  it("does not return malformed persisted metadata or raw internal fields", async () => {
    const userId = randomUUID();
    await new IdentityRepository(testDatabase.db).create({
      id: userId,
      displayName: "Metadata corruption test",
    });
    const repository = new ConversationRepository(testDatabase.db);
    const conversation = await repository.create({
      userId,
      title: "Malformed metadata",
      research: researchRequestSchema.parse({ disease: "ischemic stroke" }),
    });
    const turn = await repository.beginTurn({
      conversationId: conversation.id,
      userId,
      query: "Synthetic query",
    });
    const messageId = await repository.appendAssistantMessage({
      conversationId: conversation.id,
      turnId: turn.turnId,
      content: "# Safe answer",
      metadata: buildAssistantMessageMetadata({ sourceCount: 1 }),
    });
    await testDatabase.db
      .update(transcriptMessages)
      .set({
        metadataJson: JSON.stringify({
          schemaVersion: "1.0",
          sourceCount: 1,
          toolResponse: "RAW_INTERNAL_EXCERPT",
        }),
      })
      .where(eq(transcriptMessages.id, messageId));

    const detail = await repository.getDetail(conversation.id, userId);
    const assistant = detail?.messages.find(
      (message) => message.role === "assistant",
    );
    expect(assistant?.sourceMetadata).toBeUndefined();
    expect(JSON.stringify(detail)).not.toContain("RAW_INTERNAL_EXCERPT");
    expect(JSON.stringify(detail)).not.toContain("metadataJson");
  });
});
