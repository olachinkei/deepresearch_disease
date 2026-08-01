# Architecture

> 実装状況（2026-07-30）: 本番入口はGoogle ADK 2.5の
> `get_fast_api_app()` が生成する `/run_sse` とOpenAPIである。既存の
> ResearchWorkflowはカスタム `BaseAgent` から実行し、状態更新はADK Eventの
> `state_delta` を通してADK session DBへ一元化する。

## 1. 概要

脳梗塞創薬 Deep Research Agent は、ローカルで動作する2サービス構成を採用する。

```text
Browser
  │ HTTPS / SSE
  ▼
React Router SSR / BFF (`apps/web`)
  │ ADK OpenAPI / `/run_sse`
  ▼
Google ADK API Server (`services/agent`)
  ├── Gemini
  ├── Exa
  ├── Corpus retrieval
  └── OTel exporter ──► Weave

Web service      ──► Web DB
ADK runtime      ──► ADK session DB
Corpus subsystem ──► Corpus DB
```

ブラウザはBFF以外の外部接続先を持たない。BFFはADKの内部eventを、限定された公開SSE eventへ変換する。

## 2. 設計原則

- サービスとDBの所有権を明確にし、暗黙の共有を避ける。
- 外部APIとLLMをadapterの後ろへ隔離する。
- 検索結果を信頼できない入力として扱う。
- 証拠とcitationの対応を構造化し、生成後に検証する。
- 観測可能性を確保しつつ、本文や識別情報を既定で送信しない。
- budgetをアプリケーションコードで決定的に強制する。
- DDDの境界とcolocationを用い、変更の影響範囲を局所化する。

## 3. サービス責務

### 3.1 Webサービス

`apps/web` が所有する責務:

- React Router v7 SSR UI
- 表示名入力と署名Cookie
- 内部user UUIDの発行
- 会話一覧、turn、表示用transcript
- ADK client
- 公開SSEへの変換
- cancelとerrorのUI表現
- feedbackのローカル保存と同期状態

Webサービスは検索、LLM呼び出し、corpus DB、ADK sessionを直接操作しない。

### 3.2 Agentサービス

`services/agent` が所有する責務:

- ADK API Server
- session stateとcontext compaction
- Query Normalizer
- Research Planner
- internal / external retrieval
- Evidence Deduper
- Synthesis
- Citation Verifier
- tool / time / context budget
- corpus ingestionとsnapshot
- OTel trace属性
- offline eval、feedback同期、trace分析

Agentサービスは表示名とWebのCookieを受け取らない。

ADK runtimeは各turnの実行taskをregistryで追跡する。`/run_sse` invocation全体を
最大180秒のdeadline scopeで囲み、cancel endpointは対応するtaskとchild provider
taskを直接中断する。terminal stateは送信前にregistryで確定し、complete/cancel race
でも`completed`、`cancelled`、`error`のいずれか1件だけを送る。
Webはturnのterminal遷移を `status = running` の条件付き更新に限定し、回答保存と
`completed` 遷移を同一transactionで行う。一時的なSQLite write contentionだけを
有限retryし、確定済みturnへのcancelは状態を変えない。Agent cancel HTTP requestは
短いtimeoutで打ち切るが、ローカルのcancel状態は維持する。

## 4. DB所有権

### 4.1 Web DB

概念上の主要record:

- `local_users`
- `conversations`
- `turns`
- `transcript_entries`
- `feedback_queue`
- `feedback_revisions`

feedbackはWeaveへの同期前に必ずローカルへcommitする。現行feedbackはturn/userで
一意とし、変更前revisionを履歴へ保存する。外部同期はrecord IDとrevisionから作る
immutable idempotency keyで行う。

### 4.2 ADK session DB

ADKがmulti-turn eventとstateを管理する。Web表示用transcriptの正本にはしない。初回条件の正規化値、直近の調査意図、context compactionに必要なstateを保持する。

### 4.3 Corpus DB

概念上の主要record:

- `documents`
- `document_identifiers`
- `chunks`
- `chunk_embeddings`
- `corpus_snapshots`
- `ingestion_runs`
- FTS5 virtual table

Corpus DBは文書本文とindexを所有する。他のDBから外部キーを張らない。

## 5. ドメイン境界

| Context | 責務 | 主な依存先 |
| --- | --- | --- |
| Identity | 非認証のローカル識別、Cookie、HMAC ID | Web DB |
| Conversation | 会話、turn、SSE、feedback受付 | Identity、Research API |
| Corpus | ingestion、文書、chunk、index、snapshot | Corpus DB |
| Research | query、retrieval、evidence、synthesis、citation | Corpus、外部adapter |
| Observability / Evaluation | OTel、Signals、feedback同期、offline eval | Researchの公開結果 |

Context間の依存方向を一方向に保つ。Observabilityは業務処理の成否を置き換えず、post-hoc評価が停止しても安全な調査結果の検証はアプリ内で完了させる。

## 6. 内部HTTP契約

ADK API Serverが生成するOpenAPIを内部契約の正とする。Web側では生成または検証済みclientを使用し、手書き型の漂流を防ぐ。

Web BFFはADKの `/run_sse` を呼び、次の情報を渡す。

- `user_id`: 内部UUID
- conversation / session ID
- turn ID
- 正規化前の入力
- 既定のresearch条件
- client側cancel signal

WebはADKの生eventをそのまま中継しない。

## 7. 公開SSE契約

公開eventはdiscriminated unionとする。

| event | 用途 | 本文の扱い |
| --- | --- | --- |
| `research_started` | turn IDと開始通知 | 入力本文を再送しない |
| `search_progress` | 検索段階、取得件数 | query、excerpt、tool payloadを含めない |
| `answer_delta` | 検証済み回答のstreaming | 最終回答に属する文字列のみ |
| `completed` | source summary、run manifestの安全な部分 | 社内excerptを含めない |
| `cancelled` | 利用者cancel | 部分的なtool結果を含めない |
| `error` | 回復可能な分類済みerror | stack、secret、外部payloadを含めない |

schema `2.0` の各eventはtop-level `eventId`、0始まりの `sequence` と、payload内の
`schemaVersion`、`conversationId`、`turnId`を持つ。SSE `id:` は `eventId` と一致
させる。Agent clientとbrowser consumerは `research_started` から単一terminalまでの
順序、context一致、重複、途中切断を検証する。duplicate IDは破棄し、terminal前EOFや
順序・ID不一致は本文を含まないretryable protocol errorへ変換する。MVPでは自動
reconnectせず、retryは新しいturnとして実行する。
timeout/cancelの内部eventは安全なrun manifestを持ち、本文を含まない
`finish_reason`と分類済みflagを記録する。terminal送信後のdeltaやtool結果は禁止する。

completed eventのstructured source summaryはBFFで検証し、assistant message metadata
schema `1.0` として保存する。保存・表示対象はsource count、title、credentialを含まない
HTTP(S) canonical URL、`internal` / `web`分類、verification statusに限定する。internal
sourceのURL、excerpt、tool生結果はbrowserへ渡さない。Repositoryは`metadata_json`を
Zodでparseし、malformed値はsource summaryだけを破棄する。

## 8. Research pipeline

```text
User input
  │
  ▼
Query Normalizer ── scope validation / alias normalization
  │
  ▼
Research Planner ── query plan / budget allocation
  │
  ├──────────────┐
  ▼              ▼
Internal       Exa
retrieval      retrieval
  └──────┬───────┘
         ▼
Evidence normalization / deduplication / ranking
         ▼
Synthesis
         ▼
Citation Verifier ── one repair at most
         ▼
Structured result / public answer
```

### 8.1 Evidence

すべての検索結果を共通の `Document` と `Evidence` へ変換する。Evidenceには、sourceを再解決するための識別子、短いexcerpt、位置情報、公開状態、evidence stageを持たせる。

重複排除はDOI、PMID、canonical URL、正規化タイトルの順で行う。同じ論文の複数versionを統合する場合、公開状態と取得元の来歴を失わない。

### 8.2 Hybrid retrieval

- lexical: SQLite FTS5 / BM25
- semantic: 768次元embedding
- fusion: RRF

rankingの入力、snapshot、top-kをrun manifestへ記録する。embedding model変更時はsnapshotを分離する。

### 8.3 Budget

検索toolは最大6回、180秒、evidenceは12 excerpts、約10,000 input tokensに制限する。重複query、no progress、上限到達を決定的に停止させる。LLMがbudgetを変更することはできない。

## 9. Multi-turnとcontext

初回のtarget、mechanism、disease、research questionを正規化してADK stateへ保存する。各turnで必要な条件だけを再注入する。

- tool生結果をsession履歴へ保存しない。
- Evidence storeにはstable evidence IDで参照する。
- sessionには短いexcerptとIDだけを残す。
- 4 turnごとにcontext compactionを行う。
- context使用比率80%以上をflag、95%以上をcriticalとする。

## 10. Observability

ADKのtraceをraw OTelでWeave Agents endpointへ送る。標準環境変数がexporter、認証、project routingを所有する。

最小pluginは動的な安全属性だけをroot spanへ追加する。raw message / tool captureは無効にし、質問と最終回答の送信はfeature flagで分離する。Agents Signals用のOTel GenAI message属性は、同じgateで許可された質問と最終回答だけを複製する。

`app.turn_id` をWeb DB、ADK state、root spanで共通の相関IDとして使う。OTLP exportは非同期であるため、feedback同期はAgent turnの `trace_id` を即時取得できることを前提にしない。Agents endpointのspanはAgents span APIで検索し、旧Call APIへは依存しない。

## 11. Feedback同期

```text
Browser feedback
  ▼
Web DB (`pending`)
  ▼
Sync worker
  ├─ turn IDでAgent turn traceを検索できない ─► retry
  ├─ 一時error ──────────────────────────► retry
  └─ idempotentにfeedback登録 ───────────► `synced`
```

再試行上限後もfeedback recordを削除しない。永久失敗はerror分類と最終試行時刻を保存し、運用上確認できるようにする。

## 12. セキュリティ境界

- 外部送信機能は個別のfeature flagでdeny-by-defaultにする。
- 表示名からtrace IDを直接導出しない。
- API keyはサーバー環境変数からのみ読む。
- PDF、Web、検索結果をprompt injectionの可能性があるデータとして扱う。
- 本文やtool payloadを通常ログ、SSE、OTel attributeへ入れない。
- citationの解決と取得履歴を生成後に決定的に検証する。

詳細は [SECURITY.md](docs/SECURITY.md) を参照する。

## 13. ローカル運用

MVPはローカル実行のみを対象とする。Vercel、Turso、公開URL、production identity provider、production secret managementは設計対象外とする。

外部APIを使わない通常testと、明示的に実行するlive canaryを分離する。live canaryでは公開・合成データだけを使う。

## 14. 関連ADR

- [ADR-0001: ローカルMVPを2サービス構成にする](docs/adr/0001-two-service-local-architecture.md)
- [ADR-0002: 表示名を非認証のローカル識別として扱う](docs/adr/0002-local-identity-is-not-authentication.md)
- [ADR-0003: Evidence中心のhybrid retrievalとcitation検証を採用する](docs/adr/0003-evidence-retrieval-and-citation.md)
- [ADR-0004: runtime traceはraw OTel、評価と分析はWeave SDKを使う](docs/adr/0004-weave-otel-and-data-boundary.md)
