# Sensitive data pilot approval, retention, and deletion runbook

## Gate

このrunbookは社内データやresearch-sensitive dataを扱うpilot専用である。
公開・合成データだけの通常開発には有効な承認recordを作らない。

pilot開始前に、次をすべて満たす。

1. data manager、service owner、脳卒中SME、創薬SME、各store deletion ownerを
   実名または組織内で一意な担当IDで指名する。
2. vendor契約と保持条件を確認し、対象機能ごとに承認recordを作る。
3. recordのconstraintsへ対象corpus、利用者、project、用途を記録する。
4. Web DB、ADK session DB、Corpus DB、W&B/vendorについて保持日数、backup方針、
   deletion owner、削除確認方法をrecordへ記録する。
5. registryをrepository外のアクセス制御済み場所へ置き、対象processだけへ
   `AGENT_SENSITIVE_APPROVAL_REGISTRY_PATH` を渡す。
6. 必要な個別flagだけを有効化し、processを再起動する。
7. 起動ログのapproval ID、feature、destination、environment、`approved` を照合する。

未記入、期限切れ、owner未指名、vendor削除手段未確認の項目が1つでもあれば開始しない。

## Registry shape

registryは `{"schema_version":"1","approvals":[...]}` とする。各approvalには次が必要:

- `approval_id`
- `feature`
- `destination`: `local_corpus` / `gemini` / `exa` / `wandb`
- `environment`: `local` / `pilot`
- `data_class`
- `purpose`, `approved_by`, `approved_on`, `expires_on`
- 1件以上の`constraints`
- 1件以上のstore別`retention`

有効なfeature/data class値は
`services/agent/src/deepresearch_agent/governance/approvals.py` を正とする。
recordにsecret、質問、回答、内部excerpt、表示名を記録しない。

## Cross-store deletion dry-run

削除実行前に、本文を出力せず次のinventoryを作る。

| Store | Owner | Lookup key | Expected count | Backup impact | Verification |
| --- | --- | --- | ---: | --- | --- |
| Web DB | assigned Web DB owner | conversation ID / turn ID |  |  | row count 0 |
| ADK session DB | assigned session owner | app/user/session ID |  |  | session absent |
| Corpus DB | assigned corpus owner | document ID / snapshot ID |  |  | document/chunk count 0 |
| W&B | assigned W&B owner | project / turn ID / trace ID |  |  | server-side query count 0 |
| Gemini/Exa vendor | assigned vendor owner | approval ID / request window |  |  | vendor receipt or policy evidence |

dry-run outputに許可するのはID、分類、件数、statusだけである。SQL dump、本文、
tool生レスポンス、表示名を出さない。DB間joinや共有DBを作らず、store ownerが
それぞれの公開interfaceで対象を列挙する。

## Deletion order and verification

1. 対象feature flagを無効化し、関連processを停止する。
2. immutableなdry-run inventoryをincident/pilot ticketへ添付する。
3. Web DB、ADK session DB、Corpus DBを各ownerが削除する。
4. W&B/vendor側の削除を各ownerが実行し、receiptまたはpolicy evidenceを記録する。
5. backupのexpiryまたはpurge状態を確認する。
6. 同じlookup keyで再照会し、全storeの件数が0または承認済みbackup expiry待ちで
   あることを確認する。
7. approval ID、store、実行日、件数、判定だけを完了記録へ残す。

vendor削除を確認できない場合は削除完了とせず、該当機能を再開しない。

## Current repository status

有効な機密承認recordと担当者割当はrepositoryに存在しない。したがって全機密機能は
OFFのままとし、現時点で許可されるlive pilotは公開・合成データだけである。
