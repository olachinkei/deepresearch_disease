export function requirePostTurnId(
  request: Request,
  turnId: string | undefined,
): { turnId: string } | Response {
  if (request.method !== "POST") {
    return Response.json({ error: "Method not allowed." }, { status: 405 });
  }
  if (!turnId) {
    return Response.json({ error: "Turn ID is required." }, { status: 400 });
  }
  return { turnId };
}
