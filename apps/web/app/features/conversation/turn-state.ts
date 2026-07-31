import type { TurnStatusView } from "./view-model";

const RETRYABLE_ERROR_CODES = new Set([
  "agent_protocol_error",
  "agent_unavailable",
  "internal_error",
  "stream_protocol_error",
  "turn_deadline_exceeded",
]);

export function isRetryableTurnError(errorCode: string | null) {
  return errorCode !== null && RETRYABLE_ERROR_CODES.has(errorCode);
}

export function turnRecoveryMessage(turn: TurnStatusView) {
  if (turn.status === "cancelled") {
    return "この調査はキャンセルされました。部分的な回答は保存されていません。";
  }
  if (turn.status === "error" && turn.retryable) {
    return "調査を完了できませんでした。元の条件で新しいturnとして再試行できます。";
  }
  if (turn.status === "error") {
    return "調査を完了できませんでした。入力内容を確認し、問題が続く場合はturn IDを添えて管理者へ連絡してください。";
  }
  return undefined;
}
