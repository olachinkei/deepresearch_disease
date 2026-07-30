import { z } from "zod";

import { DISEASE, MECHANISMS } from "~/shared/domain-values";

import { displayNameSchema } from "../identity/schema";

const blankToUndefined = (value: unknown) =>
  typeof value === "string" && value.trim() === "" ? undefined : value;

const optionalEnglishText = (label: string, maxLength: number) =>
  z.preprocess(
    blankToUndefined,
    z
      .string()
      .trim()
      .min(1)
      .max(maxLength, `${label}は${maxLength}文字以内で入力してください。`)
      .regex(
        /^[\p{Script=Latin}\p{N}\s.,+_()/'’:%-]+$/u,
        `${label}は英語で入力してください。`,
      )
      .optional(),
  );

export const researchRequestSchema = z
  .object({
    conversationId: z.uuid().optional(),
    displayName: z.preprocess(
      blankToUndefined,
      displayNameSchema.optional(),
    ),
    targetMolecule: optionalEnglishText("標的分子名", 120),
    mechanism: z.preprocess(
      blankToUndefined,
      z.enum(MECHANISMS).optional(),
    ),
    disease: z
      .enum([
        DISEASE,
        "cerebral infarction",
        "cerebral ischemic stroke",
      ])
      .default(DISEASE)
      .transform(() => DISEASE),
    researchQuestion: z.preprocess(
      blankToUndefined,
      z.string().trim().min(1).max(1_000).optional(),
    ),
    followUp: z.preprocess(
      blankToUndefined,
      z.string().trim().min(1).max(2_000).optional(),
    ),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.conversationId && !value.followUp) {
      context.addIssue({
        code: "custom",
        path: ["followUp"],
        message: "追加調査の内容を入力してください。",
      });
    }
    if (!value.conversationId && value.followUp) {
      context.addIssue({
        code: "custom",
        path: ["followUp"],
        message: "新しい調査では追加質問を指定できません。",
      });
    }
  });

export type ResearchRequest = z.infer<typeof researchRequestSchema>;

export function buildResearchPrompt(input: ResearchRequest) {
  if (input.followUp) {
    return input.followUp;
  }

  const question =
    input.researchQuestion ??
    "Assess target validity, available evidence, conflicting findings, and clinical translatability.";
  const lines = [
    `Disease: ${DISEASE}`,
    input.targetMolecule
      ? `Target molecule: ${input.targetMolecule}`
      : "Target molecule: not specified",
    input.mechanism
      ? `Mechanism: ${input.mechanism}`
      : "Mechanism: not specified",
    `Research question: ${question}`,
  ];
  return lines.join("\n");
}

export function researchQuestionForAgent(
  input: ResearchRequest,
  persistedResearchQuestion?: string | null,
) {
  return input.followUp ?? persistedResearchQuestion ?? undefined;
}
