# ADR-0006: 生成modelとpromptを単一の再現可能な契約として固定する

- Status: Accepted
- Date: 2026-07-31

## Context

model aliasやprompt本文が実行時に暗黙変更されると、同じcorpusと評価datasetでも
結果を再現できない。旧 `.env.example` には重複した `AGENT_MODEL` と異なるmodel IDが
あり、実際の設定値がファイル上の順序に依存していた。また、promptはversion文字列
だけで、versionを更新せず本文だけを変えるstale metadataを検出できなかった。

Googleのmodel documentationは `gemini-3.6-flash` をstable model codeとして公開し、
`latest` aliasは新releaseへhot-swapすると説明している。2026-07-31時点の
deprecation表では、このmodelにshutdown日は告知されていない。

## Decision

- generation modelはstableな完全一致ID `gemini-3.6-flash` に固定する。
  `latest`、preview、旧model、未知のmodelを設定validationで拒否し、fallbackしない。
- synthesis promptはSemantic Versioning `1.0.0` と、instruction・input fields・
  output schemaをcanonical JSON化したSHA-256の組で固定する。
- prompt本文または契約fieldを変える場合はversionを更新し、新hashを設定、example
  environment、test、runbookを同じ変更で更新する。hash不一致はprocess起動を拒否する。
- run manifest、evaluation summary、traceにはmodel ID、prompt version、prompt hash
  だけを記録する。prompt本文、Evidence、secret、利用者入力はversion metadataへ
  入れない。
- modelまたはprompt変更は、通常のunit/contract/privacy test、固定datasetのoffline
  evaluation、固定されたコード所有の合成Evidenceだけを使う手動live canaryを通す。
- live canaryはGemini synthesisだけを直接確認する。Exa、任意workflow input、社内
  データ、研究上の未公開仮説を受け取らない。
- rollbackは直前に合格したmodel/prompt契約を含むcommitへ戻す。providerが固定IDを
  受理しない場合もaliasや別modelへ自動fallbackせず、live経路を停止する。

## Deprecation policy

model変更の作業は、Googleの公式
[model一覧](https://ai.google.dev/gemini-api/docs/models)、
[対象model](https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash)、
[deprecation schedule](https://ai.google.dev/gemini-api/docs/deprecations)
を確認してissue化する。shutdown予定の90日前、または判明時点がそれより遅ければ
即時に候補modelの評価を開始する。移行は通常のmodel/prompt変更と同じgateを通し、
旧modelが利用不能になっても自動置換しない。

## Consequences

### Positive

- 実行結果と評価結果をmodel・promptの両方へ追跡できる。
- alias hot-swap、重複env key、stale hashによる暗黙変更を起動前に検出できる。
- canaryへ機密データや任意入力を渡さずprovider schemaを確認できる。

### Negative

- provider deprecation時はコード、設定、評価を含む明示的な移行が必要になる。
- promptの軽微な文言修正でもversion/hash更新と評価が必要になる。

## Verification

- unauthorized model、stale prompt version/hash、example env key重複をnegative testで
  拒否する。
- run manifest、trace、evaluation summaryにversion/hashが入り、prompt本文が
  metadataへ入らないことを確認する。
- [model/prompt upgrade runbook](../runbooks/model-prompt-upgrade.md) に従ってoffline
  evaluationと固定合成live canaryを実行する。
