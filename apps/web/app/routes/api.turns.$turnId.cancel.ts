import { ConversationRepository } from "~/features/conversation/repository.server";
import { requirePostTurnIdentityContext } from "~/features/identity/service.server";
import { HttpAgentClient } from "~/features/research/agent-client.server";
import { cancelRegisteredRun } from "~/features/research/cancellation.server";

import type { Route } from "./+types/api.turns.$turnId.cancel";

export async function action({ request, params }: Route.ActionArgs) {
  const context = await requirePostTurnIdentityContext(request, params.turnId);
  if (context instanceof Response) {
    return context;
  }
  const { database, identity, turnId } = context;

  const conversations = new ConversationRepository(database);
  const ownedTurn = await conversations.findTurnOwned(turnId, identity.id);
  if (!ownedTurn) {
    return Response.json({ error: "Turn not found." }, { status: 404 });
  }

  const transitioned = await conversations.cancelRunningTurn(turnId);
  if (transitioned) {
    cancelRegisteredRun(turnId);
    await new HttpAgentClient().cancel(turnId);
  }
  const current = transitioned
    ? { ...ownedTurn.turn, status: "cancelled" as const }
    : (await conversations.findTurnOwned(turnId, identity.id))?.turn;

  return Response.json({
    cancelled: transitioned,
    status: current?.status ?? ownedTurn.turn.status,
    conversationId: ownedTurn.conversation.id,
  });
}
