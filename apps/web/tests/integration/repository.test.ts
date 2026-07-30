import { randomUUID } from "node:crypto";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ConversationRepository } from "~/features/conversation/repository.server";
import { IdentityRepository } from "~/features/identity/repository.server";
import { researchRequestSchema } from "~/features/research/schema";

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
      metadata: { sourceCount: 4 },
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
});
