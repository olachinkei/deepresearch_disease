import {
  Bot,
  CircleAlert,
  FileSearch,
  LoaderCircle,
  Send,
  Square,
  UserRound,
} from "lucide-react";
import { useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";

import { FeedbackControls } from "~/features/feedback/feedback-controls";
import type { FeedbackInput } from "~/features/feedback/schema";

import type {
  ActiveConversationView,
  TranscriptView,
} from "./view-model";

type ConversationThreadProps = {
  active: ActiveConversationView;
  messages: TranscriptView[];
  streamingAnswer: string;
  busy: boolean;
  progress?: string;
  error?: string;
  submittedFeedback: Set<string>;
  onFollowUp: (question: string) => Promise<void>;
  onCancel: () => Promise<void>;
  onFeedback: (turnId: string, input: FeedbackInput) => Promise<void>;
};

export function ConversationThread({
  active,
  messages,
  streamingAnswer,
  busy,
  progress,
  error,
  submittedFeedback,
  onFollowUp,
  onCancel,
  onFeedback,
}: ConversationThreadProps) {
  const [followUp, setFollowUp] = useState("");

  async function submitFollowUp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const question = followUp.trim();
    if (!question || busy) {
      return;
    }
    setFollowUp("");
    await onFollowUp(question);
  }

  return (
    <section className="conversation-panel" aria-labelledby="conversation-title">
      <header className="conversation-header">
        <div>
          <div className="eyebrow">RESEARCH THREAD</div>
          <h1 id="conversation-title">{active.conversation.title}</h1>
          <div className="conversation-tags">
            <span>{active.conversation.disease}</span>
            {active.conversation.targetMolecule ? (
              <span>{active.conversation.targetMolecule}</span>
            ) : null}
            {active.conversation.mechanism ? (
              <span>{active.conversation.mechanism}</span>
            ) : null}
          </div>
        </div>
        <div className="public-data-badge">
          <FileSearch aria-hidden size={17} />
          公開文献のみ
        </div>
      </header>

      <div className="transcript" aria-live="polite">
        {messages.map((message) =>
          message.role === "user" ? (
            <article className="message message-user" key={message.id}>
              <div className="message-avatar">
                <UserRound aria-hidden size={17} />
              </div>
              <div className="message-content">
                <span className="message-author">あなた</span>
                <p className="user-question">{message.content}</p>
              </div>
            </article>
          ) : (
            <article className="message message-assistant" key={message.id}>
              <div className="message-avatar">
                <Bot aria-hidden size={18} />
              </div>
              <div className="message-content">
                <span className="message-author">Research agent</span>
                <div className="markdown">
                  <ReactMarkdown
                    components={{
                      a: ({ children, ...props }) => (
                        <a
                          {...props}
                          rel="noreferrer"
                          target="_blank"
                        >
                          {children}
                        </a>
                      ),
                    }}
                  >
                    {message.content}
                  </ReactMarkdown>
                </div>
                <div className="medical-disclaimer">
                  創薬仮説探索用の情報です。臨床判断や患者個別の治療助言には使用できません。
                </div>
                <FeedbackControls
                  onSubmit={onFeedback}
                  submitted={submittedFeedback.has(message.turnId)}
                  turnId={message.turnId}
                />
              </div>
            </article>
          ),
        )}

        {busy ? (
          <article className="message message-assistant message-streaming">
            <div className="message-avatar">
              <LoaderCircle aria-hidden className="spin" size={18} />
            </div>
            <div className="message-content">
              <span className="message-author">Research agent</span>
              <div className="research-progress">
                <div className="progress-label">
                  <span>{progress ?? "調査を準備しています…"}</span>
                  <span className="pulse-dots" aria-hidden>
                    <i />
                    <i />
                    <i />
                  </span>
                </div>
                {streamingAnswer ? (
                  <div className="markdown">
                    <ReactMarkdown>{streamingAnswer}</ReactMarkdown>
                  </div>
                ) : (
                  <div className="skeleton-lines" aria-hidden>
                    <span />
                    <span />
                    <span />
                  </div>
                )}
                <button
                  className="cancel-button"
                  onClick={() => void onCancel()}
                  type="button"
                >
                  <Square aria-hidden size={13} />
                  キャンセル
                </button>
              </div>
            </div>
          </article>
        ) : null}

        {error ? (
          <div className="inline-error" role="alert">
            <CircleAlert aria-hidden size={18} />
            <span>{error}</span>
          </div>
        ) : null}
      </div>

      <form className="follow-up-form" onSubmit={submitFollowUp}>
        <label htmlFor="follow-up">追加調査</label>
        <div className="follow-up-input">
          <textarea
            disabled={busy}
            id="follow-up"
            onChange={(event) => setFollowUp(event.target.value)}
            placeholder="相反する研究だけを比較して、など"
            rows={2}
            value={followUp}
          />
          <button
            aria-label="追加調査を送信"
            disabled={busy || !followUp.trim()}
            type="submit"
          >
            <Send aria-hidden size={18} />
          </button>
        </div>
        <p>前の条件と引用を保ちながら、追加の観点を調査します。</p>
      </form>
    </section>
  );
}
