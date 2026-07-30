import { randomUUID } from "node:crypto";

import { describe, expect, it } from "vitest";

import {
  parseIdentityCookie,
  serializeIdentityCookie,
} from "~/features/identity/cookie.server";

const secret = "test-session-secret-with-at-least-32-characters";

describe("signed local identity cookie", () => {
  it("round-trips an internal UUID without exposing the display name", () => {
    const identityId = randomUUID();
    const cookie = serializeIdentityCookie(identityId, secret, false);

    expect(parseIdentityCookie(cookie, secret)).toBe(identityId);
    expect(cookie).toContain("HttpOnly");
    expect(cookie).toContain("SameSite=Lax");
    expect(cookie).not.toContain("研究者");
  });

  it("rejects tampering and a different signing key", () => {
    const identityId = randomUUID();
    const cookie = serializeIdentityCookie(identityId, secret, false);
    const tampered = cookie.replace(identityId, randomUUID());

    expect(parseIdentityCookie(tampered, secret)).toBeUndefined();
    expect(
      parseIdentityCookie(cookie, "another-secret-with-at-least-32-characters"),
    ).toBeUndefined();
  });

  it("adds Secure in production mode", () => {
    expect(serializeIdentityCookie(randomUUID(), secret, true)).toContain(
      "Secure",
    );
  });
});
