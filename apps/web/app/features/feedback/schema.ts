import { z } from "zod";

import {
  FEEDBACK_REASONS,
  FEEDBACK_SYNC_STATUSES,
} from "~/shared/domain-values";

export const feedbackInputSchema = z
  .object({
    vote: z.enum(["up", "down"]),
    reason: z.enum(FEEDBACK_REASONS).optional(),
    comment: z
      .string()
      .trim()
      .max(1_000, "コメントは1,000文字以内で入力してください。")
      .optional(),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.vote === "down" && !value.reason) {
      context.addIssue({
        code: "custom",
        path: ["reason"],
        message: "改善理由を選択してください。",
      });
    }
  });

export type FeedbackInput = z.infer<typeof feedbackInputSchema>;

export const feedbackViewSchema = z
  .object({
    id: z.string().min(1),
    turnId: z.string().min(1),
    vote: z.enum(["up", "down"]),
    reason: z.enum(FEEDBACK_REASONS).nullable(),
    hasComment: z.boolean(),
    syncStatus: z.enum(FEEDBACK_SYNC_STATUSES),
    revision: z.number().int().positive(),
  })
  .strict();

export type FeedbackView = z.infer<typeof feedbackViewSchema>;
