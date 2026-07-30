import {
  Check,
  MessageSquareText,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";
import { useState, type FormEvent } from "react";

import { FEEDBACK_REASONS } from "~/shared/domain-values";

import {
  feedbackInputSchema,
  type FeedbackInput,
  type FeedbackView,
} from "./schema";

const reasonLabels: Record<(typeof FEEDBACK_REASONS)[number], string> = {
  irrelevant_sources: "情報源が関連していない",
  unsupported_claim: "根拠のない主張がある",
  incomplete: "調査が不十分",
  citation_error: "引用に誤りがある",
  too_slow: "時間がかかりすぎる",
  other: "その他",
};

type FeedbackControlsProps = {
  turnId: string;
  feedback?: FeedbackView;
  onSubmit: (turnId: string, input: FeedbackInput) => Promise<void>;
};

export function FeedbackControls({
  turnId,
  feedback,
  onSubmit,
}: FeedbackControlsProps) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();

  async function send(input: FeedbackInput) {
    setSending(true);
    setError(undefined);
    try {
      await onSubmit(turnId, input);
      setOpen(false);
      setEditing(false);
    } catch {
      setError("フィードバックを保存できませんでした。");
    } finally {
      setSending(false);
    }
  }

  async function submitNegative(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const result = feedbackInputSchema.safeParse({
      vote: "down",
      reason: formData.get("reason"),
      comment: String(formData.get("comment") ?? "") || undefined,
    });
    if (!result.success) {
      setError(result.error.issues[0]?.message ?? "内容を確認してください。");
      return;
    }
    await send(result.data);
  }

  if (feedback && !editing) {
    const syncLabel =
      feedback.syncStatus === "synced"
        ? "同期済み"
        : feedback.syncStatus === "syncing"
          ? "同期中"
          : "同期待ち";
    return (
      <div className="feedback-thanks">
        <Check aria-hidden size={15} />
        <span>
          フィードバックを保存しました（
          {feedback.vote === "up" ? "役に立った" : "改善が必要"}・
          {syncLabel}
          {feedback.hasComment ? "・コメントあり" : ""}）
        </span>
        <button
          className="secondary-button"
          onClick={() => setEditing(true)}
          type="button"
        >
          変更
        </button>
      </div>
    );
  }

  return (
    <div className="feedback-area">
      <div className="feedback-row">
        <span>この回答は役に立ちましたか？</span>
        <button
          aria-label="役に立った"
          className="icon-button"
          disabled={sending}
          onClick={() => void send({ vote: "up" })}
          type="button"
        >
          <ThumbsUp aria-hidden size={16} />
        </button>
        <button
          aria-label="改善が必要"
          className="icon-button"
          disabled={sending}
          onClick={() => setOpen(true)}
          type="button"
        >
          <ThumbsDown aria-hidden size={16} />
        </button>
      </div>

      {open ? (
        <form className="feedback-panel" onSubmit={submitNegative}>
          <div className="feedback-panel-title">
            <MessageSquareText aria-hidden size={17} />
            <strong>改善が必要な理由</strong>
            <button
              aria-label="閉じる"
              className="icon-button"
              onClick={() => setOpen(false)}
              type="button"
            >
              <X aria-hidden size={15} />
            </button>
          </div>
          <select aria-label="改善理由" defaultValue="" name="reason" required>
            <option disabled value="">
              理由を選択
            </option>
            {FEEDBACK_REASONS.map((reason) => (
              <option key={reason} value={reason}>
                {reasonLabels[reason]}
              </option>
            ))}
          </select>
          <textarea
            aria-label="補足コメント"
            name="comment"
            placeholder="補足があれば入力してください（任意）"
            rows={3}
          />
          {error ? <p className="form-error">{error}</p> : null}
          <button className="secondary-button" disabled={sending} type="submit">
            {sending ? "保存中…" : "送信"}
          </button>
        </form>
      ) : null}
    </div>
  );
}
