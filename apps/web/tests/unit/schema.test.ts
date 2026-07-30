import { randomUUID } from "node:crypto";

import { describe, expect, it } from "vitest";

import { feedbackInputSchema } from "~/features/feedback/schema";
import {
  buildResearchPrompt,
  researchQuestionForAgent,
  researchRequestSchema,
} from "~/features/research/schema";

describe("researchRequestSchema", () => {
  it("normalizes disease aliases and empty optional fields", () => {
    const result = researchRequestSchema.parse({
      displayName: "研究者A",
      targetMolecule: "  NLRP3 ",
      mechanism: "inhibition",
      disease: "cerebral infarction",
      researchQuestion: "",
    });

    expect(result).toMatchObject({
      displayName: "研究者A",
      targetMolecule: "NLRP3",
      mechanism: "inhibition",
      disease: "ischemic stroke",
      researchQuestion: undefined,
    });
    expect(buildResearchPrompt(result)).toContain("Target molecule: NLRP3");
  });

  it("rejects non-English target names", () => {
    const result = researchRequestSchema.safeParse({
      displayName: "研究者A",
      disease: "ischemic stroke",
      targetMolecule: "炎症標的",
    });
    expect(result.success).toBe(false);
  });

  it("requires a follow-up for an existing conversation", () => {
    const result = researchRequestSchema.safeParse({
      conversationId: randomUUID(),
      disease: "ischemic stroke",
    });
    expect(result.success).toBe(false);
  });

  it("uses the follow-up as the agent research question while retaining conditions", () => {
    const input = researchRequestSchema.parse({
      conversationId: randomUUID(),
      disease: "ischemic stroke",
      followUp: "Compare only the negative studies.",
    });
    expect(
      researchQuestionForAgent(input, "Initial target validity question"),
    ).toBe("Compare only the negative studies.");
    expect(buildResearchPrompt(input)).toBe(
      "Compare only the negative studies.",
    );
  });
});

describe("feedbackInputSchema", () => {
  it("requires a reason for negative feedback", () => {
    expect(
      feedbackInputSchema.safeParse({ vote: "down" }).success,
    ).toBe(false);
    expect(
      feedbackInputSchema.parse({
        vote: "down",
        reason: "unsupported_claim",
      }),
    ).toEqual({
      vote: "down",
      reason: "unsupported_claim",
    });
  });
});
