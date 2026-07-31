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
import {
  parseAssistantMessageMetadata,
  serializeAssistantMessageMetadata,
  type AssistantMessageMetadata,
} from "./message-metadata";

async function withSqliteBusyRetry<T>(operation: () => Promise<T>) {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      if (attempt >= 3 || !hasErrorCode(error, "SQLITE_BUSY")) {
        throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, 5 * 2 ** attempt));
    }
  }
}

function hasErrorCode(error: unknown, expected: string) {
  let current = error;
  for (let depth = 0; depth < 5; depth += 1) {
    if (!current || typeof current !== "object") {
      return false;
    }
    if (
      "code" in current &&
      typeof current.code === "string" &&
      current.code === expected
    ) {
      return true;
    }
    current = "cause" in current ? current.cause : undefined;
  }
  return false;
}

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
    const messages = messageRows.map((row) => {
      const { metadataJson, ...message } = row.transcript_messages;
      return {
        ...message,
        sourceMetadata:
          message.role === "assistant"
            ? parseAssistantMessageMetadata(metadataJson)
            : undefined,
      };
    });
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
    metadata?: AssistantMessageMetadata;
  }) {
    const id = randomUUID();
    await this.db.insert(transcriptMessages).values({
      id,
      conversationId: input.conversationId,
      turnId: input.turnId,
      role: "assistant",
      content: input.content,
      metadataJson: input.metadata
        ? serializeAssistantMessageMetadata(input.metadata)
        : undefined,
    });
    return id;
  }

  async completeRunningTurn(input: {
    conversationId: string;
    turnId: string;
    content: string;
    metadata?: AssistantMessageMetadata;
  }) {
    return withSqliteBusyRetry(() =>
      this.db.transaction(async (transaction) => {
        const [updated] = await transaction
          .update(turns)
          .set({
            status: "completed",
            completedAt: sql`CURRENT_TIMESTAMP`,
            errorCode: null,
          })
          .where(
            and(
              eq(turns.id, input.turnId),
              eq(turns.conversationId, input.conversationId),
              eq(turns.status, "running"),
            ),
          )
          .returning({ id: turns.id });
        if (!updated) {
          return false;
        }
        await transaction.insert(transcriptMessages).values({
          id: randomUUID(),
          conversationId: input.conversationId,
          turnId: input.turnId,
          role: "assistant",
          content: input.content,
          metadataJson: input.metadata
            ? serializeAssistantMessageMetadata(input.metadata)
            : undefined,
        });
        return true;
      }),
    );
  }

  async markCompleted(turnId: string) {
    return withSqliteBusyRetry(async () => {
      const [updated] = await this.db
        .update(turns)
        .set({
          status: "completed",
          completedAt: sql`CURRENT_TIMESTAMP`,
          errorCode: null,
        })
        .where(and(eq(turns.id, turnId), eq(turns.status, "running")))
        .returning({ id: turns.id });
      return Boolean(updated);
    });
  }

  async cancelRunningTurn(turnId: string) {
    return withSqliteBusyRetry(async () => {
      const [updated] = await this.db
        .update(turns)
        .set({
          status: "cancelled",
          completedAt: sql`CURRENT_TIMESTAMP`,
          errorCode: null,
        })
        .where(and(eq(turns.id, turnId), eq(turns.status, "running")))
        .returning({ id: turns.id });
      return Boolean(updated);
    });
  }

  async markFailed(turnId: string, errorCode = "agent_unavailable") {
    return withSqliteBusyRetry(async () => {
      const [updated] = await this.db
        .update(turns)
        .set({
          status: "error",
          errorCode,
          completedAt: sql`CURRENT_TIMESTAMP`,
        })
        .where(and(eq(turns.id, turnId), eq(turns.status, "running")))
        .returning({ id: turns.id });
      return Boolean(updated);
    });
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
