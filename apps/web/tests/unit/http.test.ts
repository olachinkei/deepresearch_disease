import { describe, expect, it } from "vitest";

import { requirePostTurnId } from "~/shared/http";

describe("requirePostTurnId", () => {
  it("accepts a POST with a turn ID", () => {
    const result = requirePostTurnId(
      new Request("http://localhost/api/turns/turn-1", { method: "POST" }),
      "turn-1",
    );

    expect(result).toEqual({ turnId: "turn-1" });
  });

  it("rejects other methods before handling the turn", async () => {
    const result = requirePostTurnId(
      new Request("http://localhost/api/turns/turn-1", { method: "GET" }),
      "turn-1",
    );

    expect(result).toBeInstanceOf(Response);
    expect((result as Response).status).toBe(405);
    await expect((result as Response).json()).resolves.toEqual({
      error: "Method not allowed.",
    });
  });

  it("rejects a missing turn ID", async () => {
    const result = requirePostTurnId(
      new Request("http://localhost/api/turns", { method: "POST" }),
      undefined,
    );

    expect(result).toBeInstanceOf(Response);
    expect((result as Response).status).toBe(400);
    await expect((result as Response).json()).resolves.toEqual({
      error: "Turn ID is required.",
    });
  });
});
