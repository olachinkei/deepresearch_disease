# ADR-0009: Structured source summaryをversioned metadataとして表示する

- Status: Accepted
- Date: 2026-08-01

## Context

生成Markdown内のReferencesだけをsource表示の信頼境界にすると、BFFで検証済みの
source type、canonical URL、verification statusを保証できない。従来はcompleted
eventのsource summaryをWeb DBへ保存していたが、versionがなく、reload時のview modelへ
渡していなかった。DB内のmalformed metadataや将来追加された生tool fieldをそのまま
browserへ返すことも避ける必要がある。

## Decision

- assistant message metadata schema `1.0` を定義し、`sourceCount` と最大12件の
  `sourceSummary`だけを保存・復元する。
- source summaryは `id`、`title`、任意のcanonical URL、`internal` / `web` の
  source type、verification statusだけを持つ。excerpt、tool response、provenance生値は
  含めない。
- canonical URLはcredentialを含まないHTTP(S)だけを許可し、fragmentを除去する。
  `javascript:`等のscheme、credential入りURL、unknown source typeはsummary全体を拒否
  する。
- internal sourceはtitleと分類だけを表示し、URLはBFFで除去してbrowserへ送らない。
  `web`はUI上で「公開」、`internal`は「内部」と表示する。
- RepositoryはDBの `metadata_json` をZodでparseし、成功したversioned viewだけをloaderへ
  返す。malformed metadataは回答本文を失敗させず、source summaryだけを非表示にする。
- streaming完了時も同じschema builderを使い、reload前後の表示を一致させる。

## Consequences

### Positive

- generated Markdownと独立した検証済みsource表示を提供できる。
- DB corruptionやschema driftからraw/internal fieldがbrowserへ流れることを防げる。
- 公開/内部とverification statusをreload後も一貫して表示できる。

### Negative

- malformed itemが1件でもあるsummaryは安全側で全件非表示になる。
- internal sourceのURLは利用者が直接開けない。
- metadata schema変更時はversion追加とmigration/互換parserが必要になる。

## Verification

- safe URL normalization、dangerous scheme、credential、unknown type、duplicate ID、
  malformed persisted JSONをunit testする。
- internal URLとraw fieldがrepository viewへ出ないことをintegration testする。
- source count、公開badge、verification status、canonical linkがstream直後とreload後で
  同じことをPlaywrightで確認する。
