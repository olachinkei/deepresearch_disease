# ADR-0003: Evidence中心のhybrid retrievalとcitation検証を採用する

- Status: Accepted
- Date: 2026-07-30

## Context

創薬調査では語彙一致だけでなく意味的な関連性が必要である。一方、embedding検索だけでは固有名詞、分子名、遺伝子名、exact phraseの再現性が不十分になり得る。

検索結果をそのままLLMへ渡すと、context圧迫、重複、prompt injection、citation捏造のリスクが増える。公開論文と社内論文は来歴が異なるが、synthesisでは一貫したEvidence表現が必要になる。

## Decision

検索と生成の間に共通の `Document` / `Evidence` modelを置く。

- SQLite FTS5 / BM25と768次元embeddingをRRFで統合する。
- DOI、PMID、canonical URL、正規化タイトルの順で重複排除する。
- Evidenceは短いexcerpt、位置、source ID、公開状態、evidence stage、取得経路を持つ。
- 1 turnのEvidence packを12 excerpts、約10,000 input tokensに制限する。
- tool生結果を会話履歴へ保存しない。
- synthesis後にCitation Verifierを実行する。
- Markdown citation、structured claim、公開source registryは同じEvidence ID集合を
  参照し、claim本文の直後にそのclaim固有のIDを置く。
- `SourceReference`はdocument単位ではなくexcerptのEvidence ID単位で1件作り、
  同一論文の複数excerptを個別に解決可能にする。
- claimのsupport levelがEvidenceのsupport levelを超えないことと、claim本文が
  cited title/excerptへ最低限lexically groundedであることを決定的に検証する。
- 修復は1回に限定し、なお解決できないclaimを回答から除外する。
- OAまたは保存許諾が確認できる本文だけを保存する。
- 論文本文とWebコンテンツ内の命令には従わない。

## Consequences

### Positive

- lexicalとsemanticの長所を組み合わせられる。
- 公開・内部sourceを同じ検証pipelineへ流せる。
- claimから取得済みEvidenceへの対応を機械的に検証できる。
- contextとtool loopを制御しやすい。
- corpus snapshot単位で検索結果を再現しやすい。

### Negative

- ingestion、ranking、dedupe、citation verifierの実装が必要になる。
- embedding model変更時にre-indexが必要になる。
- Evidenceの切り出しにより、原文全体の文脈を失う可能性がある。

## Alternatives considered

### FTS5だけ

固有名詞には強いが、作用機序や意味的な関連語を拾いにくいため採用しない。

### Vector検索だけ

exact matchの再現性と説明可能性が不足するため採用しない。

### Exa summaryをそのまま根拠にする

原文excerptではなく生成物を証拠として扱うことになるため採用しない。

### tool結果全文をcontextへ入れる

context budgetとprompt injection面で不利なため採用しない。

## Verification

- retrieval datasetでRecall@10 80%以上、nDCG@10 0.75以上を満たす。
- citation resolvabilityとretrieved-before-citedが100%になる。
- Markdown、claim mapping、source registryのEvidence ID集合が100%一致する。
- claim citation coverageが95%以上になる。
- claim-evidence entailment/support互換性が90%以上になる。
- fabricated citationと撤回sourceの肯定利用が0件になる。
