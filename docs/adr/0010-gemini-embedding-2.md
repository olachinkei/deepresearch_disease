# ADR-0010: 公開corpusのembeddingにGemini Embedding 2を使う

- Status: Accepted
- Date: 2026-08-01

## Context

従来の `local-hash-embedding-v1` は決定的なtestと検索配線の検証には使えるが、
科学文献の意味類似度を表現しない。MVP仕様は外部送信が許可されたembeddingとして
`gemini-embedding-2` と768次元を指定している。

Gemini Embedding 2は既定3072次元から縮小した768次元を自動正規化する。一方、旧modelや
Hash embeddingとは意味空間に互換性がない。また、検索用途では `task_type` ではなく、
queryとdocumentへ異なるtask instructionを付ける必要がある。

## Decision

- 公開・合成corpusのproduction相当embedding providerをstable model code
  `gemini-embedding-2`、768次元に固定する。
- queryは `task: search result | query: ...`、documentは
  `title: ... | text: ...` の形式で埋め込む。
- 外部送信は `AGENT_EMBEDDING_PROVIDER=gemini`、
  `AGENT_ALLOW_PUBLIC_CONTENT_TO_GEMINI_EMBEDDINGS=true`、`GOOGLE_API_KEY` の3条件を
  必須とする。未設定時はHash providerのままとする。
- model、version、dimensionをsnapshot manifestへ保存する。runtime providerと
  snapshotのmodel/dimensionが一致しない場合は起動を拒否する。
- corpus DBは1つのimmutable snapshotだけを保持し、document/chunkへsnapshot IDを
  保存する。旧単一snapshot DBはmigration時にbackfillする。
- Hash、旧Gemini、Gemini Embedding 2のvectorを同一snapshotへ混在させない。
- 社内文書は既存の文書単位送信許可と承認がない限りGeminiへ送らない。
- offline evaluationは再現性のためHash providerを継続使用する。

## Consequences

### Positive

- 公開科学文献を意味類似度で検索できる。
- 768次元を維持し、SQLiteの保存量と既存schemaを変更せず移行できる。
- 同じ次元数でも異なる意味空間の誤混在を起動時に検出できる。

### Negative

- corpus作成時に公開title/abstractがGoogleへ送信される。
- API key、quota、provider availabilityが再indexとlive検索に必要になる。
- model変更時は全documentの再embeddingと新snapshotが必要になる。

## Verification

- mock contract testでmodel、768次元、query/document prefix、sanitized errorを確認する。
- 固定の合成公開文だけを使うlive canaryで次元数、有限値、関連/対照類似度を確認する。
- 既存Hash snapshotをGemini providerで開くnegative testを行う。
- 新規DBの全chunkが同一dimensionで、snapshot metadataと一致することを確認する。

## References

- [Gemini Embeddings guide](https://ai.google.dev/gemini-api/docs/embeddings)
- [Gemini Embeddings API](https://ai.google.dev/api/embeddings)
