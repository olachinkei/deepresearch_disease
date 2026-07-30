# 脳梗塞創薬 Deep Research Agent 実装計画

## Summary

- `instruction.md` を企画メモから実装仕様へ書き直し、表記ミス、曖昧な認証、空の参照先、検索・評価・安全要件を整理する。
- ローカルMVPは React Router SSR/BFF と Python Google ADK の2サービス構成にする。Vercel/Tursoへのデプロイは対象外。
- 疾患は `ischemic stroke` に固定し、社内論文のハイブリッド検索と Exa の公開論文検索から、引用付き日本語研究レポートを生成する。
- Weaveへのtrace exportは環境変数によるraw OTelを維持し、利用者・turn・評価指標のみ最小限のADK pluginで追加する。runtime tracingで `weave.init()` は使わず、Weave SDKはoffline eval・feedback同期・分析に限定する。
- 内部論文の外部送信許可が得られるまでは公開論文だけで動かし、社内PDF ingestionは無効化する。

## 1. `instruction.md` の修正

- `Versel`→`Vercel`、`Dizzle`→`Drizzle`、`inhibitation`→`inhibition`、`OTEl`→`OTel`、`アプり`→`アプリ`、末尾の誤記を修正する。
- 「ユーザー認証」を「ローカル利用者識別」に変更する。表示名から内部UUIDを発行して署名Cookieへ保存するが、本人確認やアクセス制御は行わない。本認証は将来課題とする。
- 表示名をプロンプトへ混ぜず、ADKの `user_id` とtrace metadataとして扱う。
- 初回入力を次に固定する。
  - target molecule: 任意、英語
  - mechanism: 任意、英語。候補は `stabilization / inhibition / degradation / activation / other`
  - disease: `ischemic stroke` 固定。`cerebral infarction` などは別名として正規化
  - research question: 任意。空なら標準の標的妥当性・エビデンス・臨床移行性調査を実行
- 空の `Search` 節へ、社内PDF ingestion、FTS/embedding検索、Exa runtime search、重複排除、引用検証を追加する。
- 存在しない「参考フォルダ」記述を削除する。
- Gista authは依存元ではなく、React Router/Drizzle/セッション設計の参考として明記する。[Gista auth starter](https://github.com/gistajs/auth/tree/dev)
- 医療判断・患者個別助言は対象外とし、「創薬仮説探索用であり臨床判断用ではない」を全回答に表示する。

## 2. アーキテクチャと公開契約

- `apps/web`: React Router v7 SSR、Zod、Drizzle、SQLite、Lucide、Noto Sans JP。UI、利用者識別、会話一覧、BFF、feedback queueを所有する。
- `services/agent`: Python 3.13、Google ADK 2.5系、Pydantic、pytest、Ruff、mypy。会話実行、検索、論文index、Weave evalを所有する。
- DBは共有しない。
  - Web DB: 利用者、会話、turn、表示用 transcript、feedback同期状態
  - ADK session DB: multi-turn event/state
  - Corpus DB: 文書、chunk、FTS5、embedding、corpus snapshot
- ADK API ServerのOpenAPIを内部契約の正とし、Web BFFが `/run_sse` を呼ぶ。ブラウザからPythonを直接呼ばない。
- 公開SSEは `research_started`、`search_progress`、`answer_delta`、`completed`、`cancelled`、`error` に限定し、toolの生レスポンスや社内本文はブラウザへ流さない。
- feedbackは `up/down`、任意コメント、理由 `irrelevant_sources / unsupported_claim / incomplete / citation_error / too_slow / other` を受け付ける。
- DDDの境界を Identity、Conversation、Corpus、Research、Observability/Evaluation に分け、各feature内へUI・schema・repository・testをcolocateする。
- `AGENTS.md`、`ARCHITECTURE.md`、最小限のproduct spec、実行計画、ADRを作成する。空の文書ツリーは量産しない。
- 初期依存バージョンとlockfileを固定し、ADK 2.xの破壊的変更を不用意に取り込まない。[ADK releases](https://github.com/google/adk-python/releases/tag/v2.5.0)

## 3. 論文indexとagent workflow

- 公開seed corpusとして最低200件の脳梗塞・創薬関連文献メタデータをCodexのWeb調査、PubMed/Europe PMC、Crossref、Unpaywallから収集する。OA許諾のある本文だけを保存し、paywall本文は保存しない。
- 社内論文は承認済みPDFフォルダとCSV/JSON manifestから取り込む。DOI、PMID、タイトル、社内ID、アクセス区分、ライセンスを保持する。
- PyMuPDFでpage/sectionを維持して抽出し、350–700 token、1文overlapでchunk化する。OCRが必要なPDFはMVPでは処理せず、理由付きレポートへ出す。
- `corpus.sqlite` にFTS5/BM25と768次元embeddingを保存し、RRFで統合する。外部送信許可後は `gemini-embedding-2` を使い、model/version/dimensionをsnapshotへ記録する。
- 内部・外部結果を共通の `Document`、`Evidence` 型へ正規化し、DOI→PMID→canonical URL→正規化タイトルの順で重複排除する。
- Exaは `POST /search`、`type=auto`、`category=publication`、`numResults=10`、extractive `highlights` を使用する。deprecatedな `context` やExa生成summaryを根拠に使わない。[Exa Search API](https://exa.ai/docs/reference/search)
- 1 turnの上限をinternal search 2回、Exa search 2回、contents取得1回、metadata検証1 batchの計6 tool calls、180秒とする。同一引数の3回目、または2回連続で新規sourceが0なら停止する。
- evidence packは最大12 excerpts、1 excerpt最大1,200文字、1論文最大2 excerpts、約10k input tokensに制限する。
- ADK workflowは Query Normalizer → Research Planner → internal/Exa parallel retrieval → Evidence Deduper → Synthesis → Citation Verifier とする。引用検証失敗時は1回だけ修復し、なお失敗する主張を回答から除外する。
- tool結果全文は会話履歴へ残さず、evidence IDと短いexcerptだけを返す。target・mechanism・diseaseはsession stateから毎turn再注入し、4 turnごとにcontext compactionする。
- 内部的な回答は `answer_markdown`、claimとevidenceの対応、support level、sources、limitations、run manifestを持つ。画面には日本語で「結論、mechanistic rationale、evidence table、臨床移行段階、矛盾・negative evidence、限界、references」を表示する。

## 4. Weave trace・feedback・評価

- 最初に合成データによる互換性spikeを行い、ADKのturn・LLM・tool span、conversation grouping、token usage、custom属性、Signals、feedback紐付けを実W&B projectで確認する。
- exporter、認証、project routingは標準環境変数のみで設定し、Agents endpoint `https://trace.wandb.ai/agents/otel/v1/traces` を使う。[Weave Agents OTel](https://docs.wandb.ai/weave/guides/tracking/trace-agents-otel)
- ADKのraw message/tool captureは明示的に無効化する。最小pluginでHMAC化したuser ID、turn ID、agent/prompt/corpus version、tool回数、重複query数、context比率、finish reason、citation/source数をroot spanへ付与する。
- 質問と最終回答だけを `input.value` / `output.value` として送る機能をfeature flag化する。公開・合成データでは有効、社内利用ではデータ管理者承認まで無効にする。
- raw OTel互換性が不足した場合は自動で `weave.init()` tracingへ切り替えず、spikeを失敗として報告する。
- Web feedbackはまずローカル保存し、`app.turn_id` でWeave Callを後から検索してService APIへ同期する。未着時はpendingのまま再試行し、W&B API keyをブラウザへ出さない。[Weave feedback](https://docs.wandb.ai/weave/guides/tracking/feedback)
- online evalはlegacy MonitorではなくAgents Signalsを使用する。
  - User Frustration、User Satisfaction: controlled pilotで100%
  - Low Quality Response: 20%
  - custom medical overclaim / unsupported citation: 10–20%
  - Signalsはpost-hoc監視であり、安全guardrailには使わない。[Weave Signals](https://docs.wandb.ai/weave/guides/tracking/view-agent-signals)
- tool loop、no progress、6回超の検索、context比80%以上、truncation、取得失敗、架空citation、retracted source利用はアプリ側で決定的にflag化する。
- offline evalはversioned Weave DatasetとEvaluationで構築する。
  - retrieval: 30–40件
  - synthesis: 20–25件
  - multi-turn/behavior: 15–20件
  - frustration: positive 50件、hard negative 50件
- scorerはschema、disease scope、tool policy、Recall@10/nDCG@10、citation解決性・coverage、tool/context budget、truncation、groundedness、claim-evidence entailment、evidence stage calibration、矛盾処理、multi-turn retentionを分離する。[Weave Evaluations](https://docs.wandb.ai/weave/guides/core-types/evaluations)
- release gateは架空citation・retracted sourceの肯定利用・scope違反・tool loop・truncationを0件、citation解決率100%、claim citation coverage 95%以上、entailment 90%以上、Recall@10 80%以上、nDCG@10 0.75以上、context比p95 0.80未満、frustration precision 0.80以上・recall 0.85以上とする。
- flag済みtraceはserver-side filterで絞って表形式に集計し、生trace全件をLLMへ渡さない。`wandb-primary` の運用方針に沿った分析script/runbookを用意し、SME確認後だけchallenge datasetへ昇格する。

## 5. 実装順とテスト

1. `instruction.md`、product spec、ADRを更新し、ローカル2サービス構成とデータ取扱いを確定する。
2. OTel/Weave互換性spikeを実施する。
3. monorepo、SQLite migration、OpenAPI/SSE契約、品質ツールを構築する。
4. public corpus収集、PDF ingestion、hybrid retrieval、Exa adapterを実装する。
5. ADK workflow、tool/context budget、構造化回答、citation verifierを実装する。
6. 利用者識別、会話UI、streaming/cancel、multi-turn、feedbackを実装する。
7. offline dataset/scorer、Signals、flag分析runbookを実装する。
8. 全テストと合成データでの実W&B smokeを通す。

テストは以下を必須とする。

- Vitest: Zod、Cookie session、Drizzle repository、agent client、SSE parser、feedback queue
- pytest: normalization、PDF/chunk、hybrid retrieval、Exa adapter、dedupe、budget、citation、eval scorer
- contract test: OpenAPI、SSE union、Exa response schema
- privacy test: 表示名、社内excerpt、tool response、secretがOTLP payloadに含まれない
- integration: SQLite migration/FTS5、mock Gemini/Exa、OTLP exporter、feedback retry
- Playwright: 利用者入力→初回調査→streaming→引用表示→follow-up→再読込→feedback→cancel/error
- 通常テストではExa/Geminiをsnapshot/mock化し、live canaryは明示実行に分離する
- TypeScriptはOxLint、knip、jscpd、import/no-cycle、PythonはRuff・mypyを品質gateにする

## Assumptions and safety gates

- 未回答事項は「ユーザー名による非認証の識別」「2サービス」「日本語回答」「安定版Gemini優先」「社内PDFは後置き」を既定値とする。
- 全利用者が同一corpusへアクセスする。文書単位ACLはMVP対象外。
- 社内論文断片のGemini送信、質問・回答のW&B送信、標的仮説のExa送信は、データ管理者の明示承認が得られるまで無効とする。
- gold evidence、禁止claim、frustration labelは脳卒中／創薬SMEがレビューする。未レビューのLLM判定を科学的正解や安全gateとして扱わない。
- Vercel、Turso、production hosting、SSO、本認証、OCR、患者個別助言はMVP対象外とする。
