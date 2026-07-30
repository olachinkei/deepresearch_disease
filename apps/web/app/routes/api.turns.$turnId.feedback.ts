import { ConversationRepository } from "~/features/conversation/repository.server";
import { FeedbackRepository } from "~/features/feedback/repository.server";
import { feedbackInputSchema } from "~/features/feedback/schema";
import { IdentityRepository } from "~/features/identity/repository.server";
import { resolveLocalIdentity } from "~/features/identity/service.server";
import { getAppDatabase } from "~/shared/database/client.server";

import type { Route } from "./+types/api.turns.$turnId.feedback";

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
  const ownedTurn = await new ConversationRepository(database).findTurnOwned(
    params.turnId,
    identity.id,
  );
  if (!ownedTurn || ownedTurn.turn.status !== "completed") {
    return Response.json({ error: "Completed turn not found." }, { status: 404 });
  }

  let raw: unknown;
  try {
    raw = await request.json();
  } catch {
    return Response.json({ error: "JSON body is required." }, { status: 400 });
  }
  const parsed = feedbackInputSchema.safeParse(raw);
  if (!parsed.success) {
    return Response.json(
      { error: "Invalid feedback.", details: parsed.error.flatten() },
      { status: 422 },
    );
  }

  const repository = new FeedbackRepository(database);
  const existing = await repository.findForTurn(params.turnId, identity.id);
  const feedback = await repository.upsert({
    ...parsed.data,
    turnId: params.turnId,
    userId: identity.id,
  });
  return Response.json(
    {
      id: feedback.id,
      turnId: feedback.turnId,
      vote: feedback.vote,
      reason: feedback.reason,
      hasComment: Boolean(feedback.comment),
      syncStatus: feedback.syncStatus,
      revision: feedback.revision,
    },
    { status: existing ? 200 : 201 },
  );
}
