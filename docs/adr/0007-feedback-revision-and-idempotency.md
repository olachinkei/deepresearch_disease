# ADR-0007: Feedbackはcurrent recordとimmutable revision keyで管理する

- Status: Accepted
- Date: 2026-07-31

## Context

従来のfeedback repositoryは同じturnへのPOSTごとにrandom UUIDで新しいrowを作り、
reload時にfeedbackを読み込まなかった。このため二重clickでlocal queueとWeave登録が
重複し、投票変更と単純retryを区別できず、同期状態もUIから失われていた。

Weave同期はAgent turn traceの到着待ちで再試行される。複数workerやworker停止からの
復旧でも、同じfeedback revisionを重複登録せず、後から変更されたrevisionを古い同期
responseで上書きしない必要がある。

## Decision

- `feedback_queue` は `(turn_id, user_id)` をuniqueとし、利用者ごと・turnごとに現行
  feedbackを1件だけ保持する。
- vote、reason、commentが同一のPOSTは同じrecord IDとrevisionを返し、同期状態を
  変更しない。
- 内容が変わるPOSTは旧値を `feedback_revisions` へ保存してrevisionを1増やし、
  現行recordを `pending`、attemptsを0へ戻す。
- Weave向けidempotency keyは `<feedback UUID>:r<revision>` とする。HTTP headerと
  request bodyの `feedback_id` に同じ値を使う。
- workerはrecord ID、revision、sync statusを条件に5分leaseを取得できた場合だけ
  送信する。完了・失敗更新も同じrevisionを条件にし、古いresponseは新revisionを
  変更できない。
- lease期限切れの `syncing` recordは再取得可能にする。Weave側は同じidempotency keyを
  検索してから登録するため、通信結果不明時のretryも登録を高々1回にする。
- loaderはvote、reason、comment有無、local/sync status、revisionだけを返す。
  comment本文、last error、Weave IDはbrowserへ返さない。
- migration前に既存重複がある場合は、`updated_at`、`created_at`、IDの順で最新を現行
  recordとし、それ以外を `feedback_revisions` へ退避してからunique制約を作る。

## Consequences

### Positive

- 二重click、reload、同期retryで同じfeedbackが増殖しない。
- vote変更を新revisionとして同期でき、旧revisionの遅延responseと競合しない。
- 既存重複や変更前feedbackをローカルDBから失わない。
- UIはreload後も保存済みと同期状態を復元できる。

### Negative

- revision履歴分だけWeb DBの使用量が増える。
- crashしたworkerの再取得まで最大5分待つ。
- 同じturnへの複数feedbackを用途別に保持する将来要件にはschema拡張が必要になる。

## Verification

- duplicate POST、vote/comment変更、既存重複migrationをintegration testで確認する。
- 2 workerの同時実行で1 workerだけがrevisionをclaimすることを確認する。
- 旧revisionの完了responseが新revisionを `synced` にしないことを確認する。
- feedback送信後のreload、変更、再reloadをPlaywrightで確認し、comment本文が画面へ
  復元されないことを確認する。
