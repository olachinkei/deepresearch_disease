import { randomUUID } from "node:crypto";

import { getAppDatabase } from "~/shared/database/client.server";
import { requirePostTurnId } from "~/shared/http";

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

async function resolveLocalIdentityContext(request: Request) {
  const database = await getAppDatabase();
  const identity = await resolveLocalIdentity(
    request,
    new IdentityRepository(database),
  );
  return identity ? { database, identity } : undefined;
}

export async function requirePostTurnIdentityContext(
  request: Request,
  turnId: string | undefined,
) {
  const validated = requirePostTurnId(request, turnId);
  if (validated instanceof Response) {
    return validated;
  }
  const context = await resolveLocalIdentityContext(request);
  if (!context) {
    return Response.json(
      { error: "Local identity is required." },
      { status: 401 },
    );
  }
  return { ...context, ...validated };
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
