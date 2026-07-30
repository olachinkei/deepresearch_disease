# MVP Execution Plan

## 1. Status

- 状態: Active
- 対象: ローカルMVP
- 正本: [instruction.md](../../../instruction.md)
- アーキテクチャ: [ARCHITECTURE.md](../../../ARCHITECTURE.md)
- セキュリティ: [SECURITY.md](../../SECURITY.md)

## 2. 完了の定義

次をすべて満たした時点でMVPを完了とする。

- 公開corpusだけで初回調査、follow-up、再読込、feedbackを実行できる。
- 引用付き日本語レポートが構造化結果から表示される。
- tool、time、evidence、contextのbudgetがコードで強制される。
- raw OTelによるWeave trace、Signals、feedback対応を合成データで確認できる。
- offline evalのrelease gateを満たす。
- 必須のunit、contract、privacy、integration、E2Eが合格する。
- 社内データ向け機能が承認なしで有効にならない。

## 3. Milestone 0: 仕様と判断の固定

### 作業

- [x] `instruction.md` とMVP product specをレビューする。
- [x] 4件のADRを採択または修正する。
- [ ] データ管理者、脳卒中SME、創薬SMEのreview ownerを決める。
- [x] 社内PDF、Gemini、W&B、Exaそれぞれの送信可否を記録する。
- [ ] Gemini modelとprompt versionのpin方針を確定する。

### Exit criteria

- 要件の未解決な矛盾がない。
- 外部送信flagの既定値がすべて無効になっている。
- 承認が得られていない機能がMVPのcritical pathに含まれていない。

## 4. Milestone 1: OTel / Weave compatibility spike

### 作業

- [x] 公開・合成データ用のW&B projectを準備する。
- [x] Agents OTel endpointと標準環境変数だけでexportする。
- [x] ADKのturn、LLM、tool spanを実W&Bで確認する。
- [x] conversation groupingとtoken usageを実W&Bで確認する。
- [x] 最小pluginでcustom属性をroot spanへ追加する。
- [x] raw message / tool content captureが無効であることをpayload testで確認する。
- [x] `input.value` / `output.value` flagのon / offを確認する。
- [x] `app.turn_id` から実Agent turn traceを検索し、feedbackを同期する。
- [ ] Agents Signalsの対象条件と出力を確認する。
- [x] spike結果をADRまたは実行記録へ残す。

### Exit criteria

- 必要なspan、grouping、token、custom属性を取得できる。
- 表示名、secret、tool payloadがOTLPへ混入しない。
- turn IDによる非同期feedback対応を確認できる。
- 互換性不足の場合はspikeを失敗として停止し、自動fallbackを追加しない。

## 5. Milestone 2: Workspaceと契約

### 作業

- [x] `apps/web` と `services/agent` の境界を作る。
- [x] 初期依存versionとlockfileを固定する。
- [x] Web DB、ADK session DB、Corpus DBのmigrationを分離する。
- [x] Google ADK API Server生成OpenAPIを契約の正として統合する
- [x] 公開SSE unionとschema versionを定義する。
- [x] agent clientとSSE parserのcontract testを作る。
- [x] 外部adapterのinterfaceとtest doubleを定義する。
- [x] TypeScriptとPythonの品質gateを設定する。
- [x] public repository向けtracked-file audit、secret scan、mock-only CIを設定する。
- [x] live canaryを手動実行かつ固定の公開・合成payloadに分離する。

### Exit criteria

- ブラウザからPythonへの直接接続がない。
- 各DBのmigration ownerが一意である。
- OpenAPIとSSE schemaの破壊的変更をtestで検知できる。
- unit testが外部APIなしで実行できる。

## 6. Milestone 3: Public corpusとretrieval

### 作業

- [ ] 脳梗塞・創薬の検索語と選定基準をSMEと作る。
- [x] PubMed / Europe PMC、Crossref、Unpaywall adapterを作る。
- [x] 200件以上の文献メタデータを収集する。
- [x] DOI、PMID、canonical URL、正規化タイトルで重複排除する。
- [ ] OA / license判定を行い、保存可能な本文だけを取得する。
- [x] PyMuPDFでpage / sectionを維持して抽出する。
- [x] OCR必須文書を理由付きでskipする。
- [x] 350〜700 token、1文overlapでchunk化する。
- [x] FTS5 / BM25 indexを作る。
- [x] 768次元embeddingとsnapshot metadataを保存する。
- [x] RRFによるhybrid retrievalを実装する。
- [x] `Document` / `Evidence` 型へ正規化する。
- [x] Recall@10 / nDCG@10のsynthetic retrieval datasetを作る。

### Exit criteria

- 200件以上の検証済みメタデータを検索できる。
- 保存本文ごとにlicenseと取得元を確認できる。
- snapshotからモデル、dimension、取得条件を再現できる。
- retrieval evalでRecall@10 80%以上、nDCG@10 0.75以上を満たす。

## 7. Milestone 4: Exa adapter

### 作業

- [x] `/search`、`type=auto`、`category=publication`、`numResults=10` を実装する。
- [x] extractive highlightsだけをEvidence候補に使う。
- [x] deprecated `context` と生成summaryを拒否する。
- [ ] timeout、rate limit、schema driftを分類する。
- [ ] metadata検証をbatch化する。
- [x] 標的仮説送信のapproval flagを実装する。
- [x] request / response contract testを作る。

### Exit criteria

- 未承認の機密入力をExaへ送らない。
- Exa summaryをcitation根拠として採択しない。
- 外部error時も内部retrievalだけで安全に終了できる。

## 8. Milestone 5: ADK research workflow

### 作業

- [x] disease alias normalizationとscope validationを実装する。
- [x] Research Plannerに固定budgetを渡す。
- [x] internal / Exa retrievalを並列化する。
- [x] Evidence Deduperとrankingを実装する。
- [x] Evidence pack上限を強制する。
- [x] 構造化synthesis結果を定義する。
- [x] Citation Verifierと1回だけのrepairを実装する。
- [x] unsupported claimを削除する。
- [x] tool loopとno-progress停止条件を実装する。
- [x] context使用比率とtruncationを検出する。
- [x] 4 turnごとのcompactionを実装する。

### Exit criteria

- 検索toolが6回、180秒を超えない。
- evidence packが件数、文字数、論文別上限を超えない。
- citationが取得済みsourceだけを参照する。
- fabricated citation、scope違反、tool loop、truncationが0件である。

## 9. Milestone 6: Web experience

### 作業

- [x] 表示名入力と非認証の説明を実装する。
- [x] 内部UUIDと署名済みHttpOnly Cookieを実装する。
- [x] 初回調査フォームとvalidationを実装する。
- [x] 検索進捗と回答streamingを実装する。
- [x] evidence tableとsource表示を実装する。
- [x] cancel / retry / error状態を実装する。
- [x] multi-turnと再読込を実装する。
- [x] feedback UIとローカルqueueを実装する。
- [ ] Noto Sans JPとLucideを用いた最低限のaccessibilityを確認する。

### Exit criteria

- Playwrightで初回調査からfeedbackまで通る。
- cancel時に不完全なtool結果を表示しない。
- 再読込後も会話とfeedback状態を復元できる。
- 全回答に研究用の注意書きがある。

## 10. Milestone 7: Evaluationと運用分析

### 作業

- [x] retrieval datasetを30〜40件用意する。
- [x] synthesis datasetを20〜25件用意する。
- [x] multi-turn / behavior datasetを15〜20件用意する。
- [x] frustration positive 50件、hard negative 50件を用意する。
- [x] 決定的scorerとLLM / human scorerを分離する。
- [ ] Weave Evaluationをversioned datasetで実W&B上で実行する。
- [ ] User Frustration / Satisfactionをpilotで100%適用する。
- [ ] Low Quality Responseを20%適用する。
- [ ] custom medical overclaim / unsupported citationを10〜20%適用する。
- [x] server-side filterと表形式集計の分析script / runbookを作る。
- [ ] SME review済み事例だけをchallenge datasetへ昇格する。

### Exit criteria

- 全release gateを自動集計できる。
- frustrationでprecision 0.80以上、recall 0.85以上を満たす。
- 生trace全件をLLMへ渡さず、傾向分析できる。
- Signalsが安全guardrailではないことを運用手順で明記する。

## 11. Milestone 8: Final verification

### 必須test

- [x] Vitest
- [x] pytest
- [x] TypeScript typecheck
- [x] Ruff
- [x] mypy
- [x] OxLint
- [x] knip
- [x] jscpd
- [x] import cycle check
- [x] OpenAPI contract
- [x] SSE contract
- [x] Exa contract
- [x] privacy test
- [x] SQLite / FTS5 integration
- [x] mock Gemini / Exa integration
- [x] OTLP exporter integration
- [x] feedback retry integration
- [x] Playwright
- [x] opt-in live canary
- [x] 合成データによる実W&B smoke

### Exit criteria

- 必須testと品質gateがすべて合格する。
- release gateがすべて合格する。
- 残る既知リスクとMVP外の項目を文書化する。
- active planをcompletedへ移す判断ができる。

## 12. リスクと対策

| リスク | 兆候 | 対策 |
| --- | --- | --- |
| raw OTelとWeaveの互換性不足 | spanやCall相関が欠落 | 最初にspikeし、自動fallbackせず判断をADR化 |
| 社内情報の外部送信 | privacy test失敗、payload混入 | deny-by-default flag、content capture無効、payload test |
| 検索品質不足 | Recall / nDCG低下 | SME gold、query改善、hybrid ranking調整 |
| citation捏造 | 未取得source ID | structured source registry、決定的verifier |
| context圧迫 | p95上昇、truncation | evidence上限、外部store、4 turn compaction |
| tool loop | 重複query、no progress | hard budgetと決定的停止 |
| metadata / license誤判定 | 保存根拠不明 | source provenanceとlicense必須化 |
| Signalの過信 | false positive / negative | post-hoc限定、SME review、hard rule併用 |

## 13. 承認記録

次の項目は未承認を既定とする。承認者、対象環境、データ分類、日付、有効期限を [SECURITY.md](../../SECURITY.md) に記録してから有効化する。

- [ ] 社内PDF ingestion
- [ ] 社内excerptのGemini送信
- [ ] 質問・回答のW&B送信
- [ ] 機密標的仮説のExa送信
