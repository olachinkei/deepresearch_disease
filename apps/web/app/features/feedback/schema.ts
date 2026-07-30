import { z } from "zod";

import { FEEDBACK_REASONS } from "~/shared/domain-values";

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
