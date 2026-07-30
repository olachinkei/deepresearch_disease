# Model / prompt upgrade runbook

このrunbookはGemini model IDまたはsynthesis prompt契約を変更する場合に使う。
公開・合成データだけを対象とし、社内データや未公開研究仮説を移行試験へ使わない。

## 変更前

1. Google公式のmodel一覧、対象model page、deprecation scheduleを確認する。
2. stableな完全一致model IDを選ぶ。`latest` とpreview aliasは選ばない。
3. 直前に合格したcommit、model ID、prompt version/hash、offline評価summaryを
   rollback基準として記録する。

## 実装とoffline gate

1. `model_contract.py` のmodel IDまたはprompt契約を更新する。
2. prompt契約を変えた場合はSemantic Versioningを更新し、canonical contractから
   SHA-256を再計算する。
3. root/serviceの `.env.example` と手動live-canary workflowを同じ値へ更新する。
4. model拒否、stale version/hash、env重複、manifest/trace metadataのtestを通す。
5. `uv run run-offline-eval` を固定された公開・合成datasetで実行する。
6. technical smokeが `passed`、privacy/citation incidentが0であることを確認する。
   SME未レビューfixtureのscientific releaseが `ineligible` のままでも、technical
   gateの合否とは混同しない。

## Live canary

GitHub Actionsの `Live canary` を承認済みenvironmentから手動実行する。workflowは
コード所有の固定合成EvidenceだけをGeminiへ送り、任意のquestion、Exa、社内本文を
受け付けない。構造化outputが空でなく、実行logのmodel ID、prompt version、hash
prefixが期待値と一致することを確認する。

## Rollback

次のいずれかが起きたらreleaseせず、記録した直前の契約commitへ戻す。

- configuration、schema、privacy、citationのtest失敗
- offline technical gate失敗または新しいincident
- live canaryのprovider error、structured-output不整合、空output
- 合意済みのlatency/cost上限超過
- model IDの提供停止またはdeprecation scheduleとの不一致

自動fallback、`latest` aliasへの切替、prompt hash validationの迂回は行わない。
rollback後もlive経路が正常でない場合は `AGENT_RUNTIME_MODE=mock` のまま停止し、
別issueで移行を再評価する。
