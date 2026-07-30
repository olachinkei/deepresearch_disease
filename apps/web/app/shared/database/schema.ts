import { relations, sql } from "drizzle-orm";
import {
  index,
  integer,
  sqliteTable,
  text,
  uniqueIndex,
} from "drizzle-orm/sqlite-core";

import {
  FEEDBACK_REASONS,
  FEEDBACK_SYNC_STATUSES,
  MECHANISMS,
  TURN_STATUSES,
} from "../domain-values";

const createdAt = text("created_at")
  .notNull()
  .default(sql`CURRENT_TIMESTAMP`);

export const localUsers = sqliteTable("local_users", {
  id: text("id").primaryKey(),
  displayName: text("display_name").notNull(),
  createdAt,
  updatedAt: text("updated_at")
    .notNull()
    .default(sql`CURRENT_TIMESTAMP`),
});

export const conversations = sqliteTable(
  "conversations",
  {
    id: text("id").primaryKey(),
    userId: text("user_id")
      .notNull()
      .references(() => localUsers.id, { onDelete: "cascade" }),
    title: text("title").notNull(),
    disease: text("disease").notNull().default("ischemic stroke"),
    targetMolecule: text("target_molecule"),
    mechanism: text("mechanism", { enum: MECHANISMS }),
    researchQuestion: text("research_question"),
    createdAt,
    updatedAt: text("updated_at")
      .notNull()
      .default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    index("conversations_user_updated_idx").on(table.userId, table.updatedAt),
  ],
);

export const turns = sqliteTable(
  "turns",
  {
    id: text("id").primaryKey(),
    conversationId: text("conversation_id")
      .notNull()
      .references(() => conversations.id, { onDelete: "cascade" }),
    sequence: integer("sequence").notNull(),
    status: text("status", { enum: TURN_STATUSES }).notNull(),
    query: text("query").notNull(),
    errorCode: text("error_code"),
    startedAt: text("started_at")
      .notNull()
      .default(sql`CURRENT_TIMESTAMP`),
    completedAt: text("completed_at"),
  },
  (table) => [
    uniqueIndex("turns_conversation_sequence_unique").on(
      table.conversationId,
      table.sequence,
    ),
    index("turns_conversation_idx").on(table.conversationId),
  ],
);

export const transcriptMessages = sqliteTable(
  "transcript_messages",
  {
    id: text("id").primaryKey(),
    conversationId: text("conversation_id")
      .notNull()
      .references(() => conversations.id, { onDelete: "cascade" }),
    turnId: text("turn_id")
      .notNull()
      .references(() => turns.id, { onDelete: "cascade" }),
    role: text("role", { enum: ["user", "assistant"] }).notNull(),
    content: text("content").notNull(),
    metadataJson: text("metadata_json"),
    createdAt,
  },
  (table) => [
    index("transcript_conversation_created_idx").on(
      table.conversationId,
      table.createdAt,
    ),
    index("transcript_turn_idx").on(table.turnId),
  ],
);

export const feedbackQueue = sqliteTable(
  "feedback_queue",
  {
    id: text("id").primaryKey(),
    turnId: text("turn_id")
      .notNull()
      .references(() => turns.id, { onDelete: "cascade" }),
    userId: text("user_id")
      .notNull()
      .references(() => localUsers.id, { onDelete: "cascade" }),
    vote: text("vote", { enum: ["up", "down"] }).notNull(),
    reason: text("reason", { enum: FEEDBACK_REASONS }),
    comment: text("comment"),
    revision: integer("revision").notNull().default(1),
    syncStatus: text("sync_status", { enum: FEEDBACK_SYNC_STATUSES })
      .notNull()
      .default("pending"),
    attempts: integer("attempts").notNull().default(0),
    nextAttemptAt: text("next_attempt_at"),
    lastError: text("last_error"),
    weaveFeedbackId: text("weave_feedback_id"),
    createdAt,
    updatedAt: text("updated_at")
      .notNull()
      .default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    index("feedback_pending_idx").on(table.syncStatus, table.nextAttemptAt),
    uniqueIndex("feedback_turn_user_unique").on(table.turnId, table.userId),
  ],
);

export const feedbackRevisions = sqliteTable(
  "feedback_revisions",
  {
    id: text("id").primaryKey(),
    feedbackId: text("feedback_id").notNull(),
    turnId: text("turn_id")
      .notNull()
      .references(() => turns.id, { onDelete: "cascade" }),
    userId: text("user_id")
      .notNull()
      .references(() => localUsers.id, { onDelete: "cascade" }),
    vote: text("vote", { enum: ["up", "down"] }).notNull(),
    reason: text("reason", { enum: FEEDBACK_REASONS }),
    comment: text("comment"),
    revision: integer("revision").notNull(),
    syncStatus: text("sync_status", { enum: FEEDBACK_SYNC_STATUSES }).notNull(),
    attempts: integer("attempts").notNull(),
    nextAttemptAt: text("next_attempt_at"),
    lastError: text("last_error"),
    weaveFeedbackId: text("weave_feedback_id"),
    createdAt: text("created_at").notNull(),
    updatedAt: text("updated_at").notNull(),
    archivedAt: text("archived_at")
      .notNull()
      .default(sql`CURRENT_TIMESTAMP`),
  },
  (table) => [
    uniqueIndex("feedback_revisions_feedback_revision_unique").on(
      table.feedbackId,
      table.revision,
    ),
    index("feedback_revisions_turn_user_idx").on(table.turnId, table.userId),
  ],
);

export const localUsersRelations = relations(localUsers, ({ many }) => ({
  conversations: many(conversations),
  feedback: many(feedbackQueue),
}));

export const conversationsRelations = relations(
  conversations,
  ({ one, many }) => ({
    user: one(localUsers, {
      fields: [conversations.userId],
      references: [localUsers.id],
    }),
    turns: many(turns),
    messages: many(transcriptMessages),
  }),
);

export const turnsRelations = relations(turns, ({ one, many }) => ({
  conversation: one(conversations, {
    fields: [turns.conversationId],
    references: [conversations.id],
  }),
  messages: many(transcriptMessages),
  feedback: many(feedbackQueue),
}));

export const transcriptMessagesRelations = relations(
  transcriptMessages,
  ({ one }) => ({
    conversation: one(conversations, {
      fields: [transcriptMessages.conversationId],
      references: [conversations.id],
    }),
    turn: one(turns, {
      fields: [transcriptMessages.turnId],
      references: [turns.id],
    }),
  }),
);

export const feedbackQueueRelations = relations(
  feedbackQueue,
  ({ one }) => ({
    turn: one(turns, {
      fields: [feedbackQueue.turnId],
      references: [turns.id],
    }),
    user: one(localUsers, {
      fields: [feedbackQueue.userId],
      references: [localUsers.id],
    }),
  }),
);
