import { randomUUID } from "node:crypto";

import {
  getSessionSecret,
  parseIdentityCookie,
  serializeIdentityCookie,
} from "./cookie.server";
import { IdentityRepository } from "./repository.server";
import { displayNameSchema } from "./schema";

export async function resolveLocalIdentity(
  request: Request,
  repository: IdentityRepository,
) {
  const id = parseIdentityCookie(
    request.headers.get("cookie"),
    getSessionSecret(),
  );
  return id ? repository.findById(id) : undefined;
}

export async function ensureLocalIdentity(input: {
  request: Request;
  repository: IdentityRepository;
  displayName?: string;
}) {
  const current = await resolveLocalIdentity(input.request, input.repository);
  if (current) {
    return { identity: current, setCookie: undefined };
  }

  const displayName = displayNameSchema.parse(input.displayName);
  const identity = await input.repository.create({
    id: randomUUID(),
    displayName,
  });
  return {
    identity,
    setCookie: serializeIdentityCookie(identity.id, getSessionSecret()),
  };
}
