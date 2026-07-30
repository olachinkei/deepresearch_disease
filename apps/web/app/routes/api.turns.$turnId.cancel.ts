import { ConversationRepository } from "~/features/conversation/repository.server";
import { IdentityRepository } from "~/features/identity/repository.server";
import { resolveLocalIdentity } from "~/features/identity/service.server";
import { HttpAgentClient } from "~/features/research/agent-client.server";
import { cancelRegisteredRun } from "~/features/research/cancellation.server";
import { getAppDatabase } from "~/shared/database/client.server";

import type { Route } from "./+types/api.turns.$turnId.cancel";

export async function action({ request, params }: Route.ActionArgs) {
  if (request.method !== "POST") {
    return Response.json({ error: "Method not allowed." }, { status: 405 });
  }
  if (!params.turnId) {
    return Response.json({ error: "Turn ID is required." }, { status: 400 });
  }

  const database = await getAppDatabase();
  const identity = await resolveLocalIdentity(
    request,
    new IdentityRepository(database),
  );
  if (!identity) {
    return Response.json({ error: "Local identity is required." }, { status: 401 });
  }

  const conversations = new ConversationRepository(database);
  const ownedTurn = await conversations.findTurnOwned(params.turnId, identity.id);
  if (!ownedTurn) {
    return Response.json({ error: "Turn not found." }, { status: 404 });
  }

  const localCancelled = cancelRegisteredRun(params.turnId);
  const upstreamCancelled = await new HttpAgentClient().cancel(params.turnId);
  await conversations.markCancelled(params.turnId);

  return Response.json({
    cancelled: localCancelled || upstreamCancelled,
  });
}
