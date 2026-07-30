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

export type ActiveConversationView = {
  conversation: {
    id: string;
    title: string;
    disease: string;
    targetMolecule: string | null;
    mechanism: string | null;
    researchQuestion: string | null;
  };
  messages: TranscriptView[];
  feedbackByTurn: Record<string, FeedbackView>;
};
import type { FeedbackView } from "~/features/feedback/schema";
