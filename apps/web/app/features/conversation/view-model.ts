export type ConversationSummary = {
  id: string;
  title: string;
  targetMolecule: string | null;
  mechanism: string | null;
  updatedAt: string;
};

export type TranscriptView = {
  id: string;
  turnId: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
};

export type TurnStatusView = {
  id: string;
  sequence: number;
  status: "running" | "completed" | "cancelled" | "error";
  errorCode: string | null;
  retryable: boolean;
};

export type ActiveConversationView = {
  conversation: {
    id: string;
    title: string;
    disease: string;
    targetMolecule: string | null;
    mechanism: string | null;
    researchQuestion: string | null;
  };
  turns: TurnStatusView[];
  messages: TranscriptView[];
  feedbackByTurn: Record<string, FeedbackView>;
};
import type { FeedbackView } from "~/features/feedback/schema";
