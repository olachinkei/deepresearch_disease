import {
  BrainCircuit,
  DatabaseZap,
  Menu,
  ShieldCheck,
  X,
} from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
} from "react";
import {
  useLoaderData,
  useNavigate,
} from "react-router";

import { ConversationRepository } from "~/features/conversation/repository.server";
import { ConversationSidebar } from "~/features/conversation/sidebar";
import { ConversationThread } from "~/features/conversation/conversation-thread";
import { buildAssistantMessageMetadata } from "~/features/conversation/message-metadata";
import type {
  ActiveConversationView,
  TranscriptView,
  TurnStatusView,
} from "~/features/conversation/view-model";
import { isRetryableTurnError } from "~/features/conversation/turn-state";
import { FeedbackRepository } from "~/features/feedback/repository.server";
import {
  feedbackViewSchema,
  type FeedbackInput,
  type FeedbackView,
} from "~/features/feedback/schema";
import { IdentityRepository } from "~/features/identity/repository.server";
import { resolveLocalIdentity } from "~/features/identity/service.server";
import type { PublicResearchEvent } from "~/features/research/public-events";
import { ResearchForm } from "~/features/research/research-form";
import {
  researchRequestSchema,
  type ResearchRequest,
} from "~/features/research/schema";
import { consumeResearchStream } from "~/features/research/stream-client";
import { getAppDatabase } from "~/shared/database/client.server";

import type { Route } from "./+types/home";

export async function loader({ request }: Route.LoaderArgs) {
  const database = await getAppDatabase();
  const identity = await resolveLocalIdentity(
    request,
    new IdentityRepository(database),
  );
  if (!identity) {
    return {
      identity: null,
      conversations: [],
      active: null,
    };
  }

  const repository = new ConversationRepository(database);
  const conversations = await repository.listForUser(identity.id);
  const selectedId = new URL(request.url).searchParams.get("conversation");
  const active = selectedId
    ? await repository.getDetail(selectedId, identity.id)
    : undefined;
  const feedback = active
    ? await new FeedbackRepository(database).listForTurns(
        active.turns.map((turn) => turn.id),
        identity.id,
      )
    : [];

  return {
    identity: { displayName: identity.displayName },
    conversations: conversations.map((conversation) => ({
      id: conversation.id,
      title: conversation.title,
      targetMolecule: conversation.targetMolecule,
      mechanism: conversation.mechanism,
      updatedAt: conversation.updatedAt,
    })),
    active: active
      ? {
          conversation: {
            id: active.conversation.id,
            title: active.conversation.title,
            disease: active.conversation.disease,
            targetMolecule: active.conversation.targetMolecule,
            mechanism: active.conversation.mechanism,
            researchQuestion: active.conversation.researchQuestion,
          },
          turns: active.turns.map((turn) => ({
            id: turn.id,
            sequence: turn.sequence,
            status: turn.status,
            errorCode: turn.errorCode,
            retryable:
              turn.status === "cancelled" ||
              (turn.status === "error" &&
                isRetryableTurnError(turn.errorCode)),
          })),
          messages: active.messages.map((message) => ({
            id: message.id,
            turnId: message.turnId,
            role: message.role,
            content: message.content,
            createdAt: message.createdAt,
            sourceMetadata: message.sourceMetadata,
          })),
          feedbackByTurn: Object.fromEntries(
            feedback.map((record) => [
              record.turnId,
              {
                id: record.id,
                turnId: record.turnId,
                vote: record.vote,
                reason: record.reason,
                hasComment: Boolean(record.comment),
                syncStatus: record.syncStatus,
                revision: record.revision,
              },
            ]),
          ),
        }
      : null,
  };
}

function displayQuestion(input: ResearchRequest) {
  return (
    input.followUp ??
    input.researchQuestion ??
    "標的妥当性、エビデンス、相反する知見、臨床移行性を標準条件で調査"
  );
}

function titleFor(input: ResearchRequest) {
  if (input.targetMolecule && input.mechanism) {
    return `${input.targetMolecule} · ${input.mechanism}`;
  }
  return input.targetMolecule
    ? `${input.targetMolecule} の標的妥当性`
    : "脳梗塞の創薬エビデンス調査";
}

export default function Home() {
  const data = useLoaderData<typeof loader>();
  const navigate = useNavigate();
  const [active, setActive] = useState<ActiveConversationView | null>(
    data.active,
  );
  const [messages, setMessages] = useState<TranscriptView[]>(
    data.active?.messages ?? [],
  );
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<string>();
  const [streamingAnswer, setStreamingAnswer] = useState("");
  const [error, setError] = useState<string>();
  const [currentTurnId, setCurrentTurnId] = useState<string>();
  const [feedbackByTurn, setFeedbackByTurn] = useState<
    Record<string, FeedbackView>
  >(
    data.active?.feedbackByTurn ?? {},
  );
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const abortController = useRef<AbortController | null>(null);

  useEffect(() => {
    setActive(data.active);
    setMessages(data.active?.messages ?? []);
    setFeedbackByTurn(data.active?.feedbackByTurn ?? {});
    setError(undefined);
  }, [data.active]);

  function updateTurn(turnId: string, update: Partial<TurnStatusView>) {
    setActive((current) =>
      current
        ? {
            ...current,
            turns: current.turns.map((turn) =>
              turn.id === turnId ? { ...turn, ...update } : turn,
            ),
          }
        : current,
    );
  }

  async function runResearch(input: ResearchRequest) {
    setBusy(true);
    setError(undefined);
    setProgress("調査リクエストを送信しています…");
    setStreamingAnswer("");
    const controller = new AbortController();
    abortController.current = controller;
    let terminalConversationId: string | undefined;
    let startedTurnId: string | undefined;

    try {
      const response = await fetch("/api/research", {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(input),
        signal: controller.signal,
      });

      if (!response.ok) {
        const body = (await response.json().catch(() => undefined)) as
          | { error?: { message?: string } }
          | undefined;
        throw new Error(body?.error?.message ?? "調査を開始できませんでした。");
      }

      await consumeResearchStream(response, (event: PublicResearchEvent) => {
        if (event.type === "research_started") {
          startedTurnId = event.data.turnId;
          setCurrentTurnId(event.data.turnId);
          const userMessage: TranscriptView = {
            id: `pending-user-${event.data.turnId}`,
            turnId: event.data.turnId,
            role: "user",
            content: displayQuestion(input),
            createdAt: new Date().toISOString(),
          };
          setMessages((current) => [...current, userMessage]);
          setActive((current) => {
            const turn: TurnStatusView = {
              id: event.data.turnId,
              sequence: (current?.turns.at(-1)?.sequence ?? 0) + 1,
              status: "running",
              errorCode: null,
              retryable: false,
            };
            if (current) {
              return { ...current, turns: [...current.turns, turn] };
            }
            return {
              conversation: {
                id: event.data.conversationId,
                title: titleFor(input),
                disease: "ischemic stroke",
                targetMolecule: input.targetMolecule ?? null,
                mechanism: input.mechanism ?? null,
                researchQuestion: input.researchQuestion ?? null,
              },
              turns: [turn],
              messages: [],
              feedbackByTurn: {},
            };
          });
          return;
        }
        if (event.type === "search_progress") {
          setProgress(event.data.message);
          return;
        }
        if (event.type === "answer_delta") {
          setProgress("取得した根拠からレポートを作成しています…");
          setStreamingAnswer((current) => current + event.data.delta);
          return;
        }
        if (event.type === "completed") {
          terminalConversationId = event.data.conversationId;
          updateTurn(event.data.turnId, {
            status: "completed",
            errorCode: null,
            retryable: false,
          });
          setMessages((current) => [
            ...current,
            {
              id: `completed-${event.data.turnId}`,
              turnId: event.data.turnId,
              role: "assistant",
              content: event.data.answerMarkdown,
              createdAt: new Date().toISOString(),
              sourceMetadata: buildAssistantMessageMetadata({
                sourceCount: event.data.sourceCount,
                sourceSummary: event.data.sourceSummary,
              }),
            },
          ]);
          setStreamingAnswer("");
          setProgress(undefined);
          return;
        }
        if (event.type === "cancelled") {
          terminalConversationId = event.data.conversationId;
          updateTurn(event.data.turnId, {
            status: "cancelled",
            errorCode: null,
            retryable: true,
          });
          setStreamingAnswer("");
          setProgress(undefined);
          return;
        }
        terminalConversationId = event.data.conversationId;
        updateTurn(event.data.turnId, {
          status: "error",
          errorCode: event.data.code,
          retryable: event.data.retryable,
        });
        setStreamingAnswer("");
        setProgress(undefined);
      });
    } catch (caught) {
      if (!controller.signal.aborted) {
        const message =
          caught instanceof Error
            ? caught.message
            : "調査中にエラーが発生しました。";
        if (startedTurnId) {
          updateTurn(startedTurnId, {
            status: "error",
            errorCode: "stream_protocol_error",
            retryable: true,
          });
          setStreamingAnswer("");
        } else {
          setError(message);
        }
      }
    } finally {
      setBusy(false);
      setCurrentTurnId(undefined);
      abortController.current = null;
      if (terminalConversationId) {
        navigate(
          `/?conversation=${encodeURIComponent(terminalConversationId)}`,
          { replace: true },
        );
      }
    }
  }

  async function followUp(question: string) {
    if (!active) {
      return;
    }
    const parsed = researchRequestSchema.parse({
      conversationId: active.conversation.id,
      displayName: data.identity?.displayName,
      disease: "ischemic stroke",
      followUp: question,
    });
    await runResearch(parsed);
  }

  async function retryTurn(turnId: string) {
    if (!active) {
      return;
    }
    const originalQuestion = messages.find(
      (message) => message.turnId === turnId && message.role === "user",
    )?.content;
    if (!originalQuestion) {
      setError("元の調査条件を復元できませんでした。");
      return;
    }
    const parsed = researchRequestSchema.parse({
      conversationId: active.conversation.id,
      displayName: data.identity?.displayName,
      disease: "ischemic stroke",
      followUp: originalQuestion,
    });
    await runResearch(parsed);
  }

  async function cancelResearch() {
    if (currentTurnId) {
      const response = await fetch(
        `/api/turns/${encodeURIComponent(currentTurnId)}/cancel`,
        {
          method: "POST",
          credentials: "same-origin",
        },
      ).catch(() => undefined);
      if (!response?.ok) {
        setError("キャンセル要求を完了できませんでした。");
        return;
      }
      const result = (await response.json()) as {
        cancelled: boolean;
        status: TurnStatusView["status"];
        conversationId: string;
      };
      if (!result.cancelled) {
        return;
      }
      updateTurn(currentTurnId, {
        status: "cancelled",
        errorCode: null,
        retryable: true,
      });
      setStreamingAnswer("");
      setProgress(undefined);
      navigate(
        `/?conversation=${encodeURIComponent(result.conversationId)}`,
        { replace: true },
      );
    }
    abortController.current?.abort();
    setBusy(false);
    setCurrentTurnId(undefined);
    abortController.current = null;
  }

  async function submitFeedback(turnId: string, input: FeedbackInput) {
    const response = await fetch(
      `/api/turns/${encodeURIComponent(turnId)}/feedback`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(input),
      },
    );
    if (!response.ok) {
      throw new Error("Feedback request failed.");
    }
    const feedback = feedbackViewSchema.parse(await response.json());
    setFeedbackByTurn((current) => ({
      ...current,
      [turnId]: feedback,
    }));
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/">
          <span className="brand-mark">
            <BrainCircuit aria-hidden size={22} />
          </span>
          <span>
            <strong>Stroke Evidence Lab</strong>
            <small>Deep Research Agent</small>
          </span>
        </a>
        <div className="topbar-actions">
          <div className="privacy-indicator">
            <ShieldCheck aria-hidden size={16} />
            社内データ送信 OFF
          </div>
          {data.identity ? (
            <div className="identity-chip">
              <span>{data.identity.displayName.slice(0, 1).toUpperCase()}</span>
              {data.identity.displayName}
            </div>
          ) : null}
          <button
            aria-label={sidebarOpen ? "履歴を閉じる" : "履歴を開く"}
            className="mobile-menu"
            onClick={() => setSidebarOpen((current) => !current)}
            type="button"
          >
            {sidebarOpen ? <X aria-hidden /> : <Menu aria-hidden />}
          </button>
        </div>
      </header>

      <div className="app-body">
        <div className={sidebarOpen ? "sidebar-wrap sidebar-wrap-open" : "sidebar-wrap"}>
          <ConversationSidebar
            activeConversationId={active?.conversation.id}
            conversations={data.conversations}
          />
        </div>
        <main className="main-content">
          {active ? (
            <ConversationThread
              active={active}
              busy={busy}
              error={error}
              messages={messages}
              onCancel={cancelResearch}
              onFeedback={submitFeedback}
              onFollowUp={followUp}
              onRetry={retryTurn}
              progress={progress}
              streamingAnswer={streamingAnswer}
              feedbackByTurn={feedbackByTurn}
            />
          ) : (
            <ResearchForm
              busy={busy}
              displayName={data.identity?.displayName}
              onStart={runResearch}
            />
          )}
        </main>
      </div>

      <footer className="site-footer">
        <span>
          <DatabaseZap aria-hidden size={15} />
          Local research workspace
        </span>
        <p>創薬仮説探索用。臨床判断・患者個別助言には使用できません。</p>
      </footer>
    </div>
  );
}
