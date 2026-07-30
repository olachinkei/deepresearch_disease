import {
  createHmac,
  timingSafeEqual,
} from "node:crypto";

const COOKIE_NAME = "deepresearch_identity";
const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365;

function signature(value: string, secret: string) {
  return createHmac("sha256", secret).update(value).digest("base64url");
}

export function serializeIdentityCookie(
  identityId: string,
  secret: string,
  secure = process.env.NODE_ENV === "production",
) {
  const token = `${identityId}.${signature(identityId, secret)}`;
  const attributes = [
    `${COOKIE_NAME}=${encodeURIComponent(token)}`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
    `Max-Age=${COOKIE_MAX_AGE_SECONDS}`,
  ];
  if (secure) {
    attributes.push("Secure");
  }
  return attributes.join("; ");
}

export function parseIdentityCookie(
  cookieHeader: string | null,
  secret: string,
) {
  if (!cookieHeader) {
    return undefined;
  }

  const cookies = new Map(
    cookieHeader.split(";").map((part) => {
      const separator = part.indexOf("=");
      const name = separator === -1 ? part.trim() : part.slice(0, separator).trim();
      const value = separator === -1 ? "" : part.slice(separator + 1).trim();
      return [name, value] as const;
    }),
  );
  const encoded = cookies.get(COOKIE_NAME);
  if (!encoded) {
    return undefined;
  }

  let token: string;
  try {
    token = decodeURIComponent(encoded);
  } catch {
    return undefined;
  }
  const separator = token.lastIndexOf(".");
  if (separator < 1) {
    return undefined;
  }
  const identityId = token.slice(0, separator);
  const actualSignature = token.slice(separator + 1);
  const expectedSignature = signature(identityId, secret);
  const actualBuffer = Buffer.from(actualSignature);
  const expectedBuffer = Buffer.from(expectedSignature);

  if (
    actualBuffer.length !== expectedBuffer.length ||
    !timingSafeEqual(actualBuffer, expectedBuffer)
  ) {
    return undefined;
  }

  return identityId;
}

export function getSessionSecret() {
  const configured = process.env.SESSION_SECRET;
  if (configured && configured.length >= 32) {
    return configured;
  }
  if (process.env.NODE_ENV === "production") {
    throw new Error("SESSION_SECRET must contain at least 32 characters.");
  }
  return "local-development-only-secret-change-me";
}
