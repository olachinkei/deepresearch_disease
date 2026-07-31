# MVP Product Spec

## 1. 概要

脳梗塞創薬 Deep Research Agentは、創薬研究者が標的分子、作用機序、調査質問を入力し、社内・公開論文の根拠を確認しながらmulti-turnで調査するための研究支援アプリである。

MVPデモはローカル実行と公開論文・合成データだけを使用する。社内PDF、機密研究仮説、
未分類の質問・回答、feedback本文を外部サービスへ送信しない。これらは将来の
承認済み別deploymentでのみ有効化できる。

## 2. 対象利用者

主な利用者:

- 脳卒中領域の研究者
- 創薬標的を探索する研究者
- 論文調査と仮説整理を行うチームメンバー

前提:

- 論文のevidence stageと限界を判断できる。
- 出力を最終的な科学的結論や臨床判断として使用しない。
- citationを原文で確認する責任を持つ。

## 3. 解決する課題

- 社内論文と公開論文を横断して検索する作業が分断されている。
- 標的と作用機序に関する肯定・否定の根拠をまとめるのに時間がかかる。
- 長いtool結果によって会話contextが圧迫され、回答品質が下がる可能性がある。
- citationの実在性、取得履歴、claimとの対応を人手で追う必要がある。
- エージェントのtool loop、no progress、frustrationを継続的に把握しにくい。

## 4. Product goals

- 1つのフォームから脳梗塞創薬の調査を開始できる。
- 肯定的根拠だけでなく、矛盾、negative evidence、限界を提示する。
- 重要claimから取得済みsourceへたどれる。
- follow-upで調査条件を維持し、追加の観点を掘り下げられる。
- 利用者feedbackとtrace上の品質signalを同じturn単位で分析できる。
- tool、context、timeの上限を越えず、失敗を安全に表示できる。

## 5. Non-goals

- 疾患横断の汎用medical research
- 医師向け意思決定支援
- 患者向け助言
- 論文の全文再配布
- 文書ACLを伴う社内検索
- production deploymentと本認証
- 機密データpilotとその担当者指名
- SME review済みdatasetによるscientific release判定

## 6. Core user journey

### 6.1 初回利用

1. 利用者が表示名を入力する。
2. アプリが「表示名は認証ではない」と説明する。
3. 内部UUIDを署名Cookieへ保存する。
4. 調査フォームを表示する。

### 6.2 初回調査

1. 利用者が任意のtarget moleculeとmechanismを英語で入力する。
2. diseaseは `ischemic stroke` が固定表示される。
3. research questionを任意で入力する。
4. 入力を検証し、標準調査または指定質問の調査を開始する。
5. 検索段階と取得件数を表示する。
6. 検証済み回答をstreaming表示する。
7. sourcesと注意書きを表示する。

### 6.3 Follow-up

1. 利用者が同じ会話で追加質問を入力する。
2. アプリが初回のtarget、mechanism、diseaseを維持する。
3. 必要な新規検索だけを実行する。
4. 以前のclaimと矛盾する場合は、矛盾として明示する。

### 6.4 Feedback

1. 利用者がturnに対してupまたはdownを選ぶ。
2. 任意で理由とコメントを入力する。
3. アプリが即座にローカル保存完了を表示する。
4. Weaveとの同期は非同期で行い、同期遅延でfeedbackを失わない。

## 7. 入力要件

| 項目 | 必須 | validation |
| --- | --- | --- |
| 表示名 | 初回のみ | 空文字不可。認証ではない旨を併記 |
| target molecule | 任意 | 英語。安全な長さ上限を設ける |
| mechanism | 任意 | 候補または `other` |
| disease | 必須 | `ischemic stroke` 固定 |
| research question | 任意 | 空なら標準調査。安全な長さ上限を設ける |

Alias normalization:

- `cerebral infarction` → `ischemic stroke`
- `brain infarction` → `ischemic stroke`

出血性脳卒中だけを対象とする質問はscope外として説明し、通常調査を実行しない。

## 8. 検索要件

### 8.1 Public corpus

- 200件以上の関連文献メタデータを収録する。
- OAまたは保存許諾が確認できる本文だけを保存する。
- メタデータはPubMed / Europe PMC、Crossref、Unpaywallで検証する。
- publication type、公開状態、ライセンスを記録する。

### 8.2 Internal corpus

- 承認済みPDFとmanifestだけを取り込む。
- OCR必須PDFはskipし、理由をreportする。
- access classificationと外部送信可否を保持する。
- 未承認時はUIとruntimeの両方で無効にする。

### 8.3 Runtime search

- Exaのpublication searchとextractive highlightsを使う。
- Exaの生成summaryを根拠にしない。
- sourceのDOI、PMID、publisher、公開状態を可能な範囲で検証する。

### 8.4 Search budget

- 検索関連toolは1 turn最大6回
- 1 turn最大180秒
- 同じ引数の3回目で停止
- 2回連続で新規sourceが0件なら停止
- evidenceは最大12 excerpts、1件1,200文字、1論文2件

## 9. 回答要件

各回答に必要なセクション:

1. 結論
2. Mechanistic rationale
3. Evidence table
4. 臨床移行段階
5. 矛盾・negative evidence
6. 限界
7. References

Evidence tableには、少なくともclaim、support level、evidence stage、source、内部・公開の区別を表示する。

必須の注意書き:

> 本サービスは創薬仮説探索を支援する研究用ツールです。医療判断、診断、治療方針、患者個別の助言には使用できません。

## 10. UI状態

| 状態 | 必須の表示 |
| --- | --- |
| idle | 入力項目、固定疾患、データ送信範囲 |
| validating | 入力errorを項目単位で表示 |
| researching | 現在の段階、取得source数、cancel |
| streaming | 検証済み回答の増分 |
| completed | 全セクション、sources、feedback |
| cancelled | 中止済み、再実行導線 |
| recoverable error | 分類済みerror、retry |
| terminal error | 安全な説明、turn ID、復旧案 |

検索query、tool payload、社内excerpt、secret、stack traceはUIへ表示しない。
streaming本文全体をlive regionにせず、進捗だけを小さな`role=status`領域で
通知する。mobile履歴は開閉状態、Escapeでのclose、triggerへのfocus returnを
提供し、keyboard-onlyで主要フローを完了できるようにする。重大なaccessibility
regressionはaxeとPlaywrightで検知し、reduced motion設定を尊重する。
公開SSE schema 2.0はevent IDと単調sequenceを必須とする。terminal前の切断、
conversation/turn不一致、順序違反はretryable protocol errorとして表示し、同じ
event IDのduplicateは表示しない。自動再接続は行わず、retry時は新しいturnを作る。
cancelled/error turnはstatus、分類済みerror code、retryability、turn IDを再読込時も
復元する。retryは元turnの表示質問と会話に保存された研究条件から新しいturnを作り、
streaming中の部分回答は入力にも表示にも再利用しない。cancelはrunning turnだけを
原子的に遷移させ、確定済みturnへの再送はidempotentに現在statusを返す。
回答ごとのstructured source summaryはversioned metadataとして保存し、source count、
title、`公開` / `内部` badge、canonical URL、verification statusをstream完了直後と
reload後に同じ形で表示する。内部sourceのURL、excerpt、tool生結果はbrowserへ送らず、
malformed metadataや危険なURLはsummary単位で安全に非表示とする。

## 11. Feedback要件

評価:

- `up`
- `down`

理由:

- `irrelevant_sources`
- `unsupported_claim`
- `incomplete`
- `citation_error`
- `too_slow`
- `other`

コメントは任意とする。同じturn/userへの同一内容の再送は同じrecord/revisionを返す。
vote、理由、コメントの変更時だけrevisionを増やし、`record ID + revision` を
外部同期のidempotency keyとする。reload時はvote、理由、コメント有無、local/sync
statusを復元するが、コメント本文はbrowserへ再送しない。

## 12. Multi-turn要件

- target、mechanism、diseaseをturn間で維持する。
- research questionは各turnの最新意図を反映する。
- 4 turnごとにcontext compactionする。
- tool結果全文を会話履歴へ残さない。
- 会話を再読込しても表示用transcriptを復元する。
- 過去の回答を根拠sourceとして扱わず、必要なEvidence IDへたどる。

## 13. 品質と安全

- 全重要claimにcitationを付ける。
- citationは取得済みsourceへ解決できる。
- 裏付けられないclaimを回答から除外する。
- 撤回論文を肯定的根拠にしない。
- preprintとpeer-reviewed articleを区別する。
- evidence stageを区別する。
- 医療判断への転用を抑止する。
- prompt injectionを含む可能性がある本文命令を無視する。

## 14. Observability

turn単位で次を相関できる。

- pseudonymous user ID
- conversation ID
- turn ID
- agent / prompt / corpus version
- tool回数
- 重複query数
- context使用比率
- finish reason
- citation数
- source数
- deterministic flags
- feedback

表示名、秘密情報、社内excerpt、tool生レスポンスはtraceへ含めない。

## 15. Success metrics

### 15.1 Release gate

- fabricated citation: 0件
- retracted sourceの肯定利用: 0件
- scope違反: 0件
- tool loop: 0件
- truncation: 0件
- citation resolvability: 100%
- retrieved-before-cited: 100%
- claim citation coverage: 95%以上
- entailment: 90%以上
- Recall@10: 80%以上
- nDCG@10: 0.75以上
- context使用比率p95: 0.80未満
- frustration precision: 0.80以上
- frustration recall: 0.85以上

### 15.2 Product health

MVP pilotでは次をturn単位で集計する。

- completion / cancel / error率
- p50 / p95 latency
- tool call数とno-progress率
- source数とcitation coverage
- up / down率と理由
- User Frustration / Satisfaction

少数pilotの率だけで科学的品質を断定しない。定量指標とSMEレビューを併用する。

## 16. Acceptance scenarios

### Scenario A: targetあり

`target molecule` と `inhibition` を入力すると、`ischemic stroke` に正規化された調査が始まり、肯定・否定の根拠とcitationを含む日本語レポートが返る。

### Scenario B: targetなし

任意項目を空にすると、標準の標的妥当性・エビデンス・臨床移行性調査が実行される。

### Scenario C: scope外

出血性脳卒中だけを求める入力では通常調査を実行せず、MVPの対象範囲を説明する。

### Scenario D: no progress

2回連続で新規sourceが得られない場合、検索を停止し、Evidence不足を限界として回答する。

### Scenario E: citation failure

Citation Verifierで解決できないclaimは1回だけ修復し、失敗が残るclaimを回答から除外する。

### Scenario F: cancel

調査中にcancelすると、`cancelled` を表示し、tool生結果や不完全なclaimを表示しない。

### Scenario G: feedback delay

Weave Agent turn traceがまだ到着していなくてもfeedbackはローカルに残り、後続retryで同期できる。

### Scenario H: private data gate

`public_synthetic_demo` profileでは承認recordが存在しても、社内PDF ingestion、
内部excerptのGemini送信、未分類の質問・回答のW&B送信、機密標的のExa送信が拒否される。
