import { randomUUID } from "node:crypto";

import {
  and,
  asc,
  desc,
  eq,
  max,
  sql,
} from "drizzle-orm";

import type { AppDatabase } from "~/shared/database/client.server";
import {
  conversations,
  transcriptMessages,
  turns,
} from "~/shared/database/schema";
import type { ResearchRequest } from "~/features/research/schema";

export class ConversationRepository {
  constructor(private readonly db: AppDatabase) {}

  async create(input: {
    userId: string;
    title: string;
    research: ResearchRequest;
  }) {
    const id = randomUUID();
    await this.db.insert(conversations).values({
      id,
      userId: input.userId,
      title: input.title,
      disease: input.research.disease,
      targetMolecule: input.research.targetMolecule,
      mechanism: input.research.mechanism,
      researchQuestion: input.research.researchQuestion,
    });
    return this.requireOwned(id, input.userId);
  }

  async listForUser(userId: string, limit = 30) {
    return this.db
      .select()
      .from(conversations)
      .where(eq(conversations.userId, userId))
      .orderBy(desc(conversations.updatedAt))
      .limit(limit);
  }

  async findOwned(id: string, userId: string) {
    const [conversation] = await this.db
      .select()
      .from(conversations)
      .where(
        and(eq(conversations.id, id), eq(conversations.userId, userId)),
      )
      .limit(1);
    return conversation;
  }

  async requireOwned(id: string, userId: string) {
    const conversation = await this.findOwned(id, userId);
    if (!conversation) {
      throw new Response("Conversation not found.", { status: 404 });
    }
    return conversation;
  }

  async getDetail(id: string, userId: string) {
    const conversation = await this.findOwned(id, userId);
    if (!conversation) {
      return undefined;
    }
    const [conversationTurns, messageRows] = await Promise.all([
      this.db
        .select()
        .from(turns)
        .where(eq(turns.conversationId, id))
        .orderBy(asc(turns.sequence)),
      this.db
        .select()
        .from(transcriptMessages)
        .innerJoin(turns, eq(transcriptMessages.turnId, turns.id))
        .where(eq(transcriptMessages.conversationId, id))
        .orderBy(
          asc(turns.sequence),
          sql`CASE WHEN ${transcriptMessages.role} = 'user' THEN 0 ELSE 1 END`,
          asc(transcriptMessages.createdAt),
        ),
    ]);
    const messages = messageRows.map((row) => row.transcript_messages);
    return { conversation, turns: conversationTurns, messages };
  }

  async beginTurn(input: {
    conversationId: string;
    userId: string;
    query: string;
    displayQuery?: string;
  }) {
    await this.requireOwned(input.conversationId, input.userId);
    const [sequenceResult] = await this.db
      .select({ value: max(turns.sequence) })
      .from(turns)
      .where(eq(turns.conversationId, input.conversationId));
    const sequence = (sequenceResult?.value ?? 0) + 1;
    const turnId = randomUUID();
    const messageId = randomUUID();

    await this.db.transaction(async (transaction) => {
      await transaction.insert(turns).values({
        id: turnId,
        conversationId: input.conversationId,
        sequence,
        status: "running",
        query: input.query,
      });
      await transaction.insert(transcriptMessages).values({
        id: messageId,
        conversationId: input.conversationId,
        turnId,
        role: "user",
        content: input.displayQuery ?? input.query,
      });
      await transaction
        .update(conversations)
        .set({ updatedAt: sql`CURRENT_TIMESTAMP` })
        .where(eq(conversations.id, input.conversationId));
    });

    return { turnId, sequence };
  }

  async appendAssistantMessage(input: {
    conversationId: string;
    turnId: string;
    content: string;
    metadata?: Record<string, unknown>;
  }) {
    const id = randomUUID();
    await this.db.insert(transcriptMessages).values({
      id,
      conversationId: input.conversationId,
      turnId: input.turnId,
      role: "assistant",
      content: input.content,
      metadataJson: input.metadata
        ? JSON.stringify(input.metadata)
        : undefined,
    });
    return id;
  }

  async markCompleted(turnId: string) {
    await this.db
      .update(turns)
      .set({
        status: "completed",
        completedAt: sql`CURRENT_TIMESTAMP`,
        errorCode: null,
      })
      .where(eq(turns.id, turnId));
  }

  async markCancelled(turnId: string) {
    await this.db
      .update(turns)
      .set({
        status: "cancelled",
        completedAt: sql`CURRENT_TIMESTAMP`,
        errorCode: null,
      })
      .where(eq(turns.id, turnId));
  }

  async markFailed(turnId: string, errorCode = "agent_unavailable") {
    await this.db
      .update(turns)
      .set({
        status: "error",
        errorCode,
        completedAt: sql`CURRENT_TIMESTAMP`,
      })
      .where(eq(turns.id, turnId));
  }

  async findTurnOwned(turnId: string, userId: string) {
    const [result] = await this.db
      .select({ turn: turns, conversation: conversations })
      .from(turns)
      .innerJoin(
        conversations,
        eq(turns.conversationId, conversations.id),
      )
      .where(and(eq(turns.id, turnId), eq(conversations.userId, userId)))
      .limit(1);
    return result;
  }
}

export function createConversationTitle(input: ResearchRequest) {
  if (input.targetMolecule && input.mechanism) {
    return `${input.targetMolecule} · ${input.mechanism}`;
  }
  if (input.targetMolecule) {
    return `${input.targetMolecule} の標的妥当性`;
  }
  if (input.researchQuestion) {
    return input.researchQuestion.slice(0, 48);
  }
  return "脳梗塞の創薬エビデンス調査";
}
