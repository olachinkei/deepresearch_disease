import {
  AgentProtocolError,
  AgentUnavailableError,
  HttpAgentClient,
} from "~/features/research/agent-client.server";
import {
  registerRun,
  unregisterRun,
} from "~/features/research/cancellation.server";
import {
  encodePublicEvent,
  type PublicResearchEvent,
} from "~/features/research/public-events";
import {
  buildResearchPrompt,
  researchQuestionForAgent,
  researchRequestSchema,
} from "~/features/research/schema";
import {
  ConversationRepository,
  createConversationTitle,
} from "~/features/conversation/repository.server";
import { IdentityRepository } from "~/features/identity/repository.server";
import { ensureLocalIdentity } from "~/features/identity/service.server";
import { getAppDatabase } from "~/shared/database/client.server";

import type { Route } from "./+types/api.research";

const encoder = new TextEncoder();

function jsonError(message: string, status: number, details?: unknown) {
  return Response.json(
    {
      error: {
        message,
        ...(details === undefined ? {} : { details }),
      },
    },
    { status },
  );
}

function timeoutMilliseconds() {
  const parsed = Number(process.env.AGENT_REQUEST_TIMEOUT_MS ?? 180_000);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 180_000;
}

export async function action({ request }: Route.ActionArgs) {
  if (request.method !== "POST") {
    return jsonError("Method not allowed.", 405);
  }

  let raw: unknown;
  try {
    raw = await request.json();
  } catch {
    return jsonError("JSON body is required.", 400);
  }
  const parsed = researchRequestSchema.safeParse(raw);
  if (!parsed.success) {
    return jsonError(
      "入力内容を確認してください。",
      422,
      parsed.error.flatten(),
    );
  }

  const database = await getAppDatabase();
  const identityRepository = new IdentityRepository(database);
  const conversationRepository = new ConversationRepository(database);

  let identityResult: Awaited<ReturnType<typeof ensureLocalIdentity>>;
  try {
    identityResult = await ensureLocalIdentity({
      request,
      repository: identityRepository,
      displayName: parsed.data.displayName,
    });
  } catch {
    return jsonError("表示名を入力してください。", 422);
  }

  const identity = identityResult.identity;
  const research = parsed.data;
  const conversation = research.conversationId
    ? await conversationRepository.requireOwned(
        research.conversationId,
        identity.id,
      )
    : await conversationRepository.create({
        userId: identity.id,
        title: createConversationTitle(research),
        research,
      });
  const prompt = buildResearchPrompt(research);
  const turn = await conversationRepository.beginTurn({
    conversationId: conversation.id,
    userId: identity.id,
    query: prompt,
    displayQuery:
      research.followUp ??
      research.researchQuestion ??
      "標的妥当性、エビデンス、相反する知見、臨床移行性を標準条件で調査",
  });
  const agentClient = new HttpAgentClient();
  const upstreamController = new AbortController();

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      let streamOpen = true;
      let timedOut = false;
      let nextPublicSequence = 0;
      const send = (event: PublicResearchEvent) => {
        if (!streamOpen) {
          return;
        }
        if (event.sequence !== nextPublicSequence) {
          throw new AgentProtocolError();
        }
        nextPublicSequence += 1;
        try {
          controller.enqueue(encoder.encode(encodePublicEvent(event)));
        } catch {
          streamOpen = false;
        }
      };
      const close = () => {
        if (!streamOpen) {
          return;
        }
        streamOpen = false;
        try {
          controller.close();
        } catch {
          // The browser can close the stream while persistence finishes.
        }
      };
      const onBrowserAbort = () => upstreamController.abort();
      request.signal.addEventListener("abort", onBrowserAbort, { once: true });
      const timeout = setTimeout(() => {
        timedOut = true;
        upstreamController.abort();
      }, timeoutMilliseconds());
      registerRun(turn.turnId, upstreamController);

      void (async () => {
        let answerMarkdown = "";
        let sourceCount: number | undefined;
        let completed = false;
        const context = {
          schemaVersion: "2.0" as const,
          conversationId: conversation.id,
          turnId: turn.turnId,
        };
        const sendCancelledTerminal = (eventId: string, sequence: number) => {
          send({
            eventId,
            sequence,
            type: "cancelled",
            data: {
              ...context,
              message: "調査をキャンセルしました。",
            },
          });
        };
        const sendCancelledIfPersisted = async (
          eventId: string,
          sequence: number,
        ) => {
          const persisted = await conversationRepository.findTurnOwned(
            turn.turnId,
            identity.id,
          );
          if (persisted?.turn.status !== "cancelled") {
            return false;
          }
          sendCancelledTerminal(eventId, sequence);
          return true;
        };
        send({
          eventId: crypto.randomUUID(),
          sequence: 0,
          type: "research_started",
          data: context,
        });

        try {
          for await (const event of agentClient.run(
            {
              userId: identity.id,
              conversationId: conversation.id,
              turnId: turn.turnId,
              prompt,
              targetMolecule: conversation.targetMolecule ?? undefined,
              mechanism: conversation.mechanism ?? undefined,
              disease: "ischemic stroke",
              researchQuestion: researchQuestionForAgent(
                research,
                conversation.researchQuestion,
              ),
            },
            upstreamController.signal,
          )) {
            if (event.type === "research_started") {
              continue;
            }
            if (event.type === "answer_delta") {
              answerMarkdown += event.data.delta;
              send(event);
              continue;
            }
            if (event.type === "search_progress") {
              sourceCount = event.data.sourceCount ?? sourceCount;
              send(event);
              continue;
            }
            if (event.type === "completed") {
              const finalAnswer =
                event.data.answerMarkdown.trim() || answerMarkdown.trim();
              if (!finalAnswer) {
                throw new AgentProtocolError("Completed event had no answer.");
              }
              answerMarkdown = finalAnswer;
              sourceCount = event.data.sourceCount ?? sourceCount;
              const transitioned =
                await conversationRepository.completeRunningTurn({
                  conversationId: conversation.id,
                  turnId: turn.turnId,
                  content: finalAnswer,
                  metadata: {
                    sourceCount,
                    sourceSummary: event.data.sourceSummary,
                  },
              });
              if (!transitioned) {
                await sendCancelledIfPersisted(
                  event.eventId,
                  event.sequence,
                );
                completed = true;
                continue;
              }
              send({
                eventId: event.eventId,
                sequence: event.sequence,
                type: "completed",
                data: {
                  ...context,
                  answerMarkdown: finalAnswer,
                  ...(sourceCount === undefined ? {} : { sourceCount }),
                  ...(event.data.sourceSummary === undefined
                    ? {}
                    : { sourceSummary: event.data.sourceSummary }),
                },
              });
              completed = true;
              continue;
            }
            if (event.type === "cancelled") {
              const transitioned =
                await conversationRepository.cancelRunningTurn(turn.turnId);
              const persisted = transitioned
                ? undefined
                : await conversationRepository.findTurnOwned(
                    turn.turnId,
                    identity.id,
                  );
              if (!transitioned && persisted?.turn.status !== "cancelled") {
                completed = true;
                continue;
              }
              send({
                eventId: event.eventId,
                sequence: event.sequence,
                type: "cancelled",
                data: { ...context, message: event.data.message },
              });
              completed = true;
              continue;
            }
            if (event.type === "error") {
              const transitioned = await conversationRepository.markFailed(
                turn.turnId,
                event.data.code,
              );
              if (!transitioned) {
                await sendCancelledIfPersisted(
                  event.eventId,
                  event.sequence,
                );
                completed = true;
                continue;
              }
              send({
                eventId: event.eventId,
                sequence: event.sequence,
                type: "error",
                data: { ...event.data, ...context },
              });
              completed = true;
              continue;
            }
          }

          if (!completed) {
            throw new AgentProtocolError("Agent stream ended before completion.");
          }
        } catch (error) {
          if (completed) {
            return;
          }
          if (upstreamController.signal.aborted && !timedOut) {
            await conversationRepository.cancelRunningTurn(turn.turnId);
            sendCancelledTerminal(crypto.randomUUID(), nextPublicSequence);
          } else {
            const protocolError = error instanceof AgentProtocolError;
            const unavailable =
              error instanceof AgentUnavailableError || timedOut;
            const code = protocolError
              ? "agent_protocol_error"
              : unavailable
                ? "agent_unavailable"
                : "internal_error";
            const transitioned = await conversationRepository.markFailed(
              turn.turnId,
              code,
            );
            if (transitioned) {
              send({
                eventId: crypto.randomUUID(),
                sequence: nextPublicSequence,
                type: "error",
                data: {
                  ...context,
                  code,
                  message: timedOut
                    ? "調査が制限時間を超えました。もう一度お試しください。"
                    : "調査サービスに接続できませんでした。もう一度お試しください。",
                  retryable: true,
                },
              });
            } else {
              await sendCancelledIfPersisted(
                crypto.randomUUID(),
                nextPublicSequence,
              );
            }
          }
        } finally {
          clearTimeout(timeout);
          unregisterRun(turn.turnId);
          request.signal.removeEventListener("abort", onBrowserAbort);
          close();
        }
      })();
    },
    cancel() {
      upstreamController.abort();
    },
  });

  const headers = new Headers({
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-cache, no-transform",
    "x-accel-buffering": "no",
  });
  if (identityResult.setCookie) {
    headers.append("set-cookie", identityResult.setCookie);
  }
  return new Response(stream, { headers });
}
