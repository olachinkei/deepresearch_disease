# 脳梗塞創薬 Deep Research Agent 実装仕様

## 1. この文書の位置づけ

この文書は、脳梗塞創薬 Deep Research Agent の実装仕様の正本である。ローカルMVPの機能、技術境界、データ取扱い、検索、評価、テスト、受け入れ条件を定義する。

詳細は次の文書で補足する。

- [MVPプロダクト仕様](docs/product-specs/mvp.md)
- [アーキテクチャ](ARCHITECTURE.md)
- [実行計画](docs/exec-plans/active/mvp.md)
- [セキュリティとデータ取扱い](docs/SECURITY.md)
- [ADR](docs/adr/)

## 2. 目的

社内論文と公開論文を検索し、創薬研究者が脳梗塞に関する標的妥当性、作用機序、エビデンス、臨床移行性を調査できる会話型エージェントを作る。

MVPでは公開論文だけで一連の調査を実行できるようにする。社内論文の取り込みと外部サービスへの送信は、データ管理者の明示承認が得られるまで無効にする。

全回答には、次の注意書きを表示する。

> 本サービスは創薬仮説探索を支援する研究用ツールです。医療判断、診断、治療方針、患者個別の助言には使用できません。

## 3. 対象範囲

### 3.1 MVPに含めるもの

- 対象疾患を `ischemic stroke` に限定した調査
- 表示名によるローカル利用者識別
- React Router SSRによる会話UI
- Google ADKによるmulti-turn調査
- 公開seed corpusの取り込み、index化、検索
- 承認後に有効化できる社内PDF ingestion
- Exaによる公開論文のruntime検索
- 根拠と引用を伴う日本語研究レポート
- Weaveへのraw OTel trace export
- ローカルfeedback queueとWeave feedback同期
- Weave Dataset / Evaluationを用いたoffline eval
- Weave Agents Signalsと決定的ルールを用いたonline eval
- ローカルでの単体、契約、統合、E2E、プライバシーテスト

### 3.2 MVPに含めないもの

- Vercel、Turso、その他のproduction hosting
- SSO、本人確認、権限管理を伴う本認証
- 文書単位ACL
- OCR
- 患者個別の医療判断または治療提案
- 脳梗塞以外の疾患を対象にした本番機能

## 4. 利用者識別

MVPの表示名入力は認証ではない。本人確認やアクセス制御には使用しない。

1. 初回アクセス時に表示名を入力する。
2. Webサービスが内部UUIDを発行し、署名済み・HttpOnly Cookieへ保存する。
3. 表示名はWeb DBにのみ保存し、LLM promptやtraceへ送らない。
4. Pythonサービスには内部UUIDをADKの `user_id` として渡す。
5. traceへは、環境ごとの秘密鍵でHMAC化した識別子だけを記録する。

全利用者は同一corpusへアクセスする。アクセス制御が必要になった時点で、本認証と文書ACLを別途設計する。

## 5. 初回入力

初回調査フォームは次の項目を持つ。

| 項目 | 必須 | 形式 | 動作 |
| --- | --- | --- | --- |
| target molecule | 任意 | 英語 | 空なら標的を限定しない |
| mechanism | 任意 | 英語 | `stabilization`、`inhibition`、`degradation`、`activation`、`other` |
| disease | 必須 | 固定値 | `ischemic stroke` |
| research question | 任意 | 自由記述 | 空なら標準調査を実行 |

`cerebral infarction` などの別名は `ischemic stroke` へ正規化する。出血性脳卒中は比較対象として明示された場合を除き、調査対象から除外する。

標準調査は次の観点を含む。

- 標的妥当性
- 作用機序
- 有効性と反証を含むエビデンス
- in vitro、動物、ヒト観察、臨床試験、レビューの段階
- 臨床移行性と限界

## 6. 画面と会話

- React Router v7のSSRアプリとして実装する。
- ブラウザはWeb BFFだけを呼び、Pythonサービスを直接呼ばない。
- 調査の開始、検索進捗、回答streaming、完了、cancel、errorを表示する。
- follow-upでは初回のtarget、mechanism、diseaseをsession stateから再注入する。
- 会話を再読込しても、表示用transcriptとfeedback状態を復元できるようにする。
- toolの生レスポンス、秘密情報、社内本文はブラウザへ送らない。

公開SSE eventは次に限定する。

- `research_started`
- `search_progress`
- `answer_delta`
- `completed`
- `cancelled`
- `error`

event payloadはZodで検証し、バージョンを持たせる。

## 7. 回答仕様

内部の最終結果は、少なくとも次の情報を持つ構造化データとする。

- `answer_markdown`
- claimとevidence IDの対応
- claimごとのsupport level
- sources
- limitations
- run manifest

画面には日本語で次の順に表示する。分子名、遺伝子名、論文タイトル、引用は原表記を維持する。

1. 結論
2. Mechanistic rationale
3. Evidence table
4. 臨床移行段階
5. 矛盾・negative evidence
6. 限界
7. References

要件は次のとおり。

- 重要な検証可能claimにはcitationを付ける。
- citationは、そのturnで取得済みのsourceだけを参照する。
- 内部sourceと公開sourceを画面上で区別する。
- in vitro、動物、ヒト、臨床試験、レビューを同列に扱わない。
- preprint、撤回、訂正、出版状況を判別できる場合は明示する。
- 検索で見つからないことを「存在しない」と断定しない。
- 根拠が不足するclaimは削除するか、不確実性を明示する。
- 捏造citationと撤回論文の肯定的根拠利用を禁止する。

## 8. 技術構成

### 8.1 `apps/web`

- React Router v7 SSR
- TypeScript
- Zod
- Drizzle ORM
- SQLite
- Lucide (`lucide-react`)
- Noto Sans JP
- Vitest
- Playwright

React Router、Drizzle、session設計は [Gista auth starter](https://github.com/gistajs/auth/tree/dev) を参考にする。ただし、Gista authを依存パッケージやMVP認証機能として採用しない。

### 8.2 `services/agent`

- Python 3.13
- Google ADK 2.5系
- Pydantic
- PyMuPDF
- pytest
- Ruff
- mypy

ADKのminor versionはlockfileで固定し、2.xの更新を自動で取り込まない。[Google ADK v2.5.0](https://github.com/google/adk-python/releases/tag/v2.5.0)

### 8.3 外部サービス

- Gemini: synthesisと、許可された場合のembedding
- Exa: 公開論文検索
- Weights & Biases Weave: trace、feedback、offline eval、Agents Signals
- PubMed / Europe PMC / Crossref / Unpaywall: 公開seed corpusのメタデータ取得と検証

Geminiは利用時点のstable modelを設定値として明示し、検証済みversionを固定する。modelを変更する場合は、offline evalとlive canaryを再実行する。

モデル名、prompt version、agent version、corpus snapshotはrun manifestとtrace metadataへ記録する。

## 9. サービスとDBの境界

WebとPythonは別サービスとし、DBを共有しない。

| 所有者 | DB | 保存するもの |
| --- | --- | --- |
| Web | Web DB | 利用者、会話、turn、表示用transcript、feedback同期状態 |
| ADK | ADK session DB | multi-turn eventとstate |
| Corpus | Corpus DB | 文書、chunk、FTS5、embedding、corpus snapshot |

ADK API ServerのOpenAPIを内部HTTP契約の正とする。Web BFFがADKの `/run_sse` を呼び、公開SSE形式へ変換する。

ドメイン境界は次の5つに分ける。

- Identity
- Conversation
- Corpus
- Research
- Observability / Evaluation

UI、schema、repository、testは各feature内へcolocateする。ドメイン間は公開interface経由で連携し、循環依存を許可しない。

## 10. 公開seed corpus

公開seed corpusとして、脳梗塞と創薬に関連する文献メタデータを最低200件収集する。

- CodexによるWeb調査で候補と検索語を設計する。
- PubMed / Europe PMC、Crossref、Unpaywallでメタデータと公開状態を検証する。
- OAまたは明示的に保存可能なライセンスを持つ本文だけを保存する。
- paywall本文やライセンス不明の本文は保存しない。
- DOI、PMID、canonical URL、正規化タイトル、publication type、公開状態、ライセンス、取得日時を記録する。
- corpus snapshotには取得条件、件数、source、embedding model/version/dimensionを記録する。

メタデータだけの文献も検索結果に含められるが、本文を確認していないclaimの根拠強度を過大評価しない。

## 11. 社内PDF ingestion

社内論文の取り込みは既定で無効とする。データ管理者が保存と外部送信の範囲を明示承認した後に有効化する。

入力は承認済みPDFフォルダとCSVまたはJSON manifestとする。manifestは次を保持する。

- 社内文書ID
- title
- DOI / PMID
- access classification
- license / usage restriction
- 外部送信可否
- 取り込み日時

PyMuPDFでpageとsectionを維持して抽出する。350〜700 token、1文overlapでchunk化する。OCRが必要なPDFは処理せず、文書IDと理由をingestion reportへ記録する。

論文本文は信頼できない入力として扱い、本文中の命令、prompt、tool実行要求には従わない。

## 12. Indexと検索

`corpus.sqlite` に次を保存する。

- 文書メタデータ
- page / section付きchunk
- FTS5 index
- 768次元embedding
- corpus snapshot

検索はFTS5/BM25とembedding検索をRRFで統合する。内部文書の外部送信許可が得られるまでは、内部chunkのembedding生成を外部APIへ送信しない。

外部送信が承認された場合は `gemini-embedding-2` を用い、model、version、dimensionをcorpus snapshotへ記録する。model変更時は新しいsnapshotを作成し、異なるembeddingを混在させない。

内部・外部結果を共通の `Document` と `Evidence` 型へ正規化する。重複排除の優先順位は次のとおり。

1. DOI
2. PMID
3. canonical URL
4. 正規化タイトル

Evidenceはsource ID、文書ID、title、excerpt、pageまたはsection、URL、取得経路、publication stage、公開状態を持つ。

## 13. Exa検索

Exaは次の条件で使用する。[Exa Search API](https://exa.ai/docs/reference/search)

- `POST /search`
- `type=auto`
- `category=publication`
- `numResults=10`
- extractive `highlights`

deprecatedな `context` とExa生成summaryを根拠に使用しない。取得した候補は、可能な範囲でDOI、PMID、publisher、公開状態を検証する。

標的仮説をExaへ送信する機能は、データ管理者の承認が得られるまで無効にする。未承認環境では、公開・合成入力だけをlive searchへ送れる。

## 14. Agent workflow

1 turnの処理は次の順序とする。

1. Query Normalizer
2. Research Planner
3. internal retrievalとExa retrievalの並列実行
4. Evidence Deduper
5. 公開Evidenceのmetadata batch検証
6. Synthesis
7. Citation Verifier

Citation Verifierが失敗した場合は1回だけ修復する。修復後も裏付けられないclaimは回答から除外する。

### 14.1 1 turnのbudget

| 対象 | 上限 |
| --- | ---: |
| internal search | 2回 |
| Exa search | 2回 |
| contents取得 | 1回 |
| metadata検証 | 1 batch |
| 検索関連tool合計 | 6回 |
| wall time | 180秒 |
| evidence excerpts | 12件 |
| 1 excerpt | 1,200文字 |
| 1論文のexcerpt | 2件 |
| evidence input | 約10,000 token |

次の場合は検索を停止する。

- 同じ正規化引数のtool callが3回目に達する
- 2回連続で新しいsourceを取得できない
- 検索関連tool callが6回に達する
- 180秒に達する

180秒はtool開始前だけでなく、ADK `/run_sse` invocation全体へ適用するhard deadline
とする。deadlineまたは利用者cancel時は進行中のretrieval、metadata、synthesis taskを
cancelしてcleanupし、`timeout`または`cancelled`の分類済みflagをmanifestとtraceへ
本文なしで記録する。terminal eventは1 turnにつき1件だけとし、その後に
`answer_delta`、`completed`、tool結果を送らない。

toolの生結果全文を会話履歴へ保存しない。toolはevidence IDと短いexcerptを返し、完全なevidenceは会話外のstoreで管理する。4 turnごとにcontext compactionを実行する。

## 15. Weave trace

最初に合成・公開データだけで互換性spikeを実施する。次を実W&B projectで確認する。

- ADK turn、LLM、tool span
- conversation grouping
- token usage
- custom属性
- Agents Signals
- `app.turn_id` とWeave Agent turn traceの対応
- feedback同期

runtime tracingはraw OTelとし、exporter、認証、project routingを標準環境変数で設定する。[Weave Agents OTel](https://docs.wandb.ai/weave/guides/tracking/trace-agents-otel)

Agents endpoint:

```text
https://trace.wandb.ai/agents/otel/v1/traces
```

ADKのraw message / tool content captureは明示的に無効化する。最小限のADK pluginで、root spanへ次の属性だけを追加する。

- HMAC化したuser ID
- turn ID
- agent version
- prompt version
- corpus version
- tool回数
- 重複query数
- context使用比率
- finish reason
- citation数
- source数
- 決定的flag

質問と最終回答だけを `input.value` / `output.value` として送る機能はfeature flag化する。公開・合成データでは有効にできるが、社内利用では承認まで無効とする。

raw OTelに必要なspan、属性、Signals、feedback連携が得られない場合、互換性spikeを失敗として記録する。runtime tracingを自動で `weave.init()` へ切り替えない。Weave SDKはoffline eval、feedback同期、trace分析に限定して使用する。

## 16. Feedback

利用者はturnごとに次を送信できる。

- `up` または `down`
- 任意コメント
- 任意の理由
  - `irrelevant_sources`
  - `unsupported_claim`
  - `incomplete`
  - `citation_error`
  - `too_slow`
  - `other`

Webはfeedbackをまずローカルへ保存する。同期workerがAgents span APIをserver-side filterで検索し、`app.turn_id` からAgent turnの `trace_id` を解決して、Agent turn feedback APIへ送る。旧Weave Call APIはAgents endpointへ送ったspanの検索には使わない。

- traceが未着の場合は `pending` のまま再試行する。
- 同期はidempotentにする。
- 永続的なエラーは理由を記録し、利用者のfeedback自体は失わない。
- W&B API keyをブラウザへ渡さない。

## 17. Online eval

post-hoc品質監視には [Weave Agents Signals](https://docs.wandb.ai/weave/guides/tracking/view-agent-signals) を使用する。Signalsを安全guardrailとして扱わない。

| Signal | 適用率 |
| --- | ---: |
| User Frustration | controlled pilotで100% |
| User Satisfaction | controlled pilotで100% |
| Low Quality Response | 20% |
| custom medical overclaim | 10〜20% |
| custom unsupported citation | 10〜20% |

アプリ側では、次を決定的にflag化する。

- 同一toolと同一引数の反復
- 2 round連続のno progress
- 6回を超える検索
- context使用比率80%以上
- context使用比率95%以上のcritical
- truncation
- retrieval failure
- 解決できないcitation
- 撤回sourceの肯定的利用
- network / rate limit error

## 18. Offline eval

versioned Weave DatasetとEvaluationを用いる。[Weave Evaluations](https://docs.wandb.ai/weave/guides/core-types/evaluations)

### 18.1 Dataset

| Dataset | 件数 |
| --- | ---: |
| retrieval | 30〜40 |
| synthesis | 20〜25 |
| multi-turn / behavior | 15〜20 |
| frustration positive | 50 |
| frustration hard negative | 50 |

gold evidence、禁止claim、frustration labelは脳卒中または創薬SMEがレビューする。未レビューのLLM判定を科学的正解やrelease gateとして使用しない。

### 18.2 Scorer

- schema validity
- disease scope
- tool policy
- Recall@10
- nDCG@10
- citation resolvability
- retrieved-before-cited
- claim citation coverage
- source status
- tool / context budget
- truncation
- groundedness
- claim-evidence entailment
- evidence stage calibration
- conflict handling
- multi-turn retention
- latency、token、cost

### 18.3 Release gate

| 指標 | 合格条件 |
| --- | ---: |
| fabricated citation | 0件 |
| retracted sourceの肯定利用 | 0件 |
| disease scope違反 | 0件 |
| tool loop | 0件 |
| truncation | 0件 |
| citation resolvability | 100% |
| retrieved-before-cited | 100% |
| claim citation coverage | 95%以上 |
| claim-evidence entailment | 90%以上 |
| Recall@10 | 80%以上 |
| nDCG@10 | 0.75以上 |
| context使用比率 p95 | 0.80未満 |
| frustration precision | 0.80以上 |
| frustration recall | 0.85以上 |

required metricまたはzero-incident件数が1つでも欠落した場合はrelease gateを失敗とする。
`context使用比率 p95`は昇順に並べたnearest-rank
`ceil(0.95 * n)`番目で計算し、`0.80`ちょうどは不合格とする。technical smokeと
scientific releaseを別statusで出力し、synthetic、未SME review、human review未完了の
datasetはtechnical smokeが合格してもscientific releaseを`ineligible`とする。
deterministic scorer、LLM judge、human / SME scorerの出力元を混同せず、LLM judgeだけで
scientific releaseを許可しない。

flag済みtraceはserver-side filterで対象を絞り、表形式で集計する。生trace全件をLLMへ渡さない。集計は件数、率、version、error class、tool pattern、context帯、feedback理由を基本とする。SMEが確認した事例だけをchallenge datasetへ昇格する。

## 19. テストと品質gate

### 19.1 テスト

- Vitest: Zod、Cookie session、Drizzle repository、agent client、SSE parser、feedback queue
- pytest: normalization、PDF抽出、chunk化、hybrid retrieval、Exa adapter、重複排除、budget、citation、eval scorer
- contract test: ADK OpenAPI、公開SSE union、Exa response schema
- privacy test: 表示名、社内excerpt、tool response、secretがOTLP payloadに含まれない
- integration: SQLite migration / FTS5、mock Gemini / Exa、OTLP exporter、feedback retry
- Playwright: 利用者入力、初回調査、streaming、引用表示、follow-up、再読込、feedback、cancel、error

通常テストではGeminiとExaをmockまたはsnapshot化する。live canaryと実W&B smokeは明示実行に分離する。

### 19.2 品質gate

TypeScript:

- OxLint
- knip
- jscpd
- `import/no-cycle`
- typecheck

Python:

- Ruff
- mypy
- pytest

## 20. 実装順

1. 本仕様、product spec、ADR、セキュリティ基準を確定する。
2. raw OTel / Weave互換性spikeを実施する。
3. monorepo、SQLite migration、OpenAPI / SSE契約、品質ツールを構築する。
4. public corpus収集、PDF ingestion、hybrid retrieval、Exa adapterを実装する。
5. ADK workflow、tool / context budget、構造化回答、Citation Verifierを実装する。
6. 利用者識別、会話UI、streaming、cancel、multi-turn、feedbackを実装する。
7. offline dataset / scorer、Signals、flag分析runbookを実装する。
8. 全テストと合成データによる実W&B smokeを通す。

## 21. MVP受け入れ条件

- 公開corpusだけで、初回調査とfollow-upを完了できる。
- `ischemic stroke` 以外を通常の調査対象として受け付けない。
- 回答が指定した7セクションと注意書きを含む。
- 重要claimのcitationを解決でき、未取得sourceを参照しない。
- tool、time、evidence、contextのbudgetを超えない。
- cancel、timeout、外部API errorを安全に終了し、部分的な生tool結果を表示しない。
- 表示名、secret、社内excerpt、tool生レスポンスがOTLP payloadに含まれない。
- feedbackがローカルで失われず、Weave Agent turn trace到着後に同期される。
- offline evalのrelease gateを満たす。
- 合成・公開データによる実W&B smokeでtrace、Signals、feedbackの対応を確認できる。

## 22. 承認が必要な事項

次の機能は、データ管理者の承認内容を [セキュリティ文書](docs/SECURITY.md) に記録するまで有効化しない。

- 社内PDFの取り込み
- 社内論文断片のGemini送信
- 質問と最終回答のW&B送信
- 機密性のある標的仮説のExa送信

既定値はすべて無効とする。
