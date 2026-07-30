# Security and Data Handling

## 1. 目的

この文書は、脳梗塞創薬 Deep Research Agent のローカルMVPにおける安全境界、データ分類、外部送信、秘密情報、prompt injection、trace、feedbackの取扱いを定義する。

本サービスは研究支援用であり、医療判断、診断、治療方針、患者個別助言には使用できない。

## 2. Threat model

MVPで優先して扱う脅威:

- 表示名や研究仮説の不要な外部送信
- 社内論文本文のGemini、Exa、W&Bへの送信
- API keyやCookie secretのブラウザ、ログ、traceへの露出
- 論文やWebページに埋め込まれたprompt injection
- 架空citationまたは未取得sourceへの参照
- 撤回論文を肯定的な根拠として利用すること
- tool loopによる過剰な外部送信、cost、遅延
- context圧迫によるtruncationと不完全回答
- feedback同期失敗による利用者入力の消失
- 表示名ベースの仕組みを本認証と誤認すること

MVPで解決しない脅威:

- production環境のnetwork perimeter
- SSO、MFA、RBAC
- 文書単位ACL
- 複数組織のtenant isolation

これらが必要なデータを扱う場合は、MVPを使用しない。

## 3. データ分類

| 分類 | 例 | 既定の扱い |
| --- | --- | --- |
| Public | 公開論文メタデータ、OA本文、明示確認済みの公開query | 承認済み外部APIへ送信可 |
| Synthetic | 固定された合成query、合成fixture | 承認済み外部APIへ送信可 |
| User-local | 表示名、会話一覧、feedback | Web DB内。外部送信は最小化 |
| Research-sensitive | 未公開標的仮説、質問、最終回答 | 明示承認まで外部送信不可 |
| Internal document | 社内PDF、内部excerpt、manifest | ingestionと外部送信を既定で禁止 |
| Secret | API key、Cookie署名鍵、HMAC鍵 | server環境変数のみ。保存・表示・trace禁止 |

公開論文であっても、ライセンスが不明な本文は保存しない。

trace contentの分類はserver-owned policyで決める。正規化済みの質問、target、
mechanism、disease、research question全体からserverがSHA-256 fingerprintを作り、
server設定のPublicまたはSynthetic allowlistと完全一致した入力だけをその分類として
扱う。未登録入力はtargetが空でも `research-sensitive` とし、client metadataや本文の
自己申告で分類を緩和しない。Internal Evidenceが1件でも採択された出力は `internal`
とする。

## 4. 外部送信matrix

| データ | Gemini | Exa | W&B | 既定 |
| --- | --- | --- | --- | --- |
| 公開・合成query | 必要最小限 | 必要最小限 | flag有効時のみ | 許可 |
| 公開Evidence excerpt | synthesisに必要な範囲 | 送らない | 送らない | 許可 |
| 表示名 | 送らない | 送らない | 送らない | 禁止 |
| HMAC user ID | 不要 | 不要 | metadataのみ | 許可 |
| Research-sensitive query | 承認時のみ | 承認時のみ | 承認時のみ | 禁止 |
| 社内excerpt | 承認時のみ | 送らない | 送らない | 禁止 |
| 最終回答本文 | 不要 | 送らない | 承認時のみ | 禁止 |
| tool生レスポンス | 送らない | 該当なし | 送らない | 禁止 |
| Secret | 送らない | headerのみ | headerのみ | content送信禁止 |

外部送信は目的別のfeature flagで管理する。1つの包括的なflagで複数の承認を代用しない。

## 5. Feature flag

具体的な名称は実装時に決めるが、少なくとも次を独立して制御する。

- 社内PDF ingestion
- 社内excerptのGemini送信
- Research-sensitive queryのExa送信
- 質問のW&B `input.value` 送信
- 最終回答のW&B `output.value` 送信

W&B trace contentには次の独立flagとserver-owned allowlistを使用する。

- `AGENT_TRACE_INPUT_CONTENT_ENABLED`
- `AGENT_TRACE_OUTPUT_CONTENT_ENABLED`
- `AGENT_TRACE_PUBLIC_INPUT_FINGERPRINTS`
- `AGENT_TRACE_SYNTHETIC_INPUT_FINGERPRINTS`

fingerprint allowlistはJSON配列で指定し、各値をlowercase SHA-256とする。Publicと
Syntheticの両方に同じfingerprintを登録してはならない。旧
`AGENT_TRACE_CONTENT_ENABLED` と
`AGENT_TRACE_RESEARCH_HYPOTHESES_ENABLED` は廃止し、`true` のまま起動した場合は
fail closedとする。

要件:

- 未設定、未知の値、設定読込errorは無効として扱う。
- UI表示だけでなくserver側で拒否する。
- 起動時に有効flag名を本文なしで記録する。
- 承認範囲より広い環境で有効化しない。
- testでon / off双方を検証する。

## 6. 承認記録

承認が必要な機能を有効にする前に、次を記録する。

| 項目 | 内容 |
| --- | --- |
| 機能 | 何を有効にするか |
| データ分類 | 送信対象 |
| 送信先 | Gemini / Exa / W&B |
| 目的 | 検索、生成、trace、評価 |
| 対象環境 | local / pilot |
| 承認者 | データ管理者 |
| 承認日 | YYYY-MM-DD |
| 有効期限 | YYYY-MM-DDまたは再審査条件 |
| 制約 | corpus、利用者、project、保持期間 |

未記入の項目は未承認として扱う。秘密情報や内部本文をこの記録へ貼り付けない。

承認recordは [ADR-0005](adr/0005-sensitive-feature-approval-registry.md) の
versioned JSON schemaに従う。機密feature flagを有効にしたprocessは
`AGENT_SENSITIVE_APPROVAL_REGISTRY_PATH` を読み、機能、送信先、環境、data class、
有効期間の完全一致を起動時に検証する。不一致や読込失敗では起動しない。承認者、
目的、制約は通常ログやtraceへ出さず、approval IDと判定だけを記録する。

## 7. Identityとsession

- 表示名は認証ではない。
- 内部UUIDを署名済み・HttpOnly Cookieへ保存する。
- Cookieには表示名、API key、研究質問を入れない。
- Cookie改ざん、欠損、期限切れでは新しい識別を要求する。
- HMAC user IDは環境ごとの秘密鍵で生成する。
- HMAC鍵をCookie署名鍵やAPI keyと共用しない。
- raw user UUIDとHMAC IDの対応をW&Bへ送らない。

本認証やアクセス制御が必要なデータを、MVPの表示名識別で保護しない。

## 8. Secret management

- Secretはserver processの環境変数から読む。
- ブラウザbundle、SSE、HTML、source mapへ埋め込まない。
- `.env` の実値をrepositoryへcommitしない。
- test fixture、snapshot、error messageへsecretを含めない。
- request / responseを丸ごとdebug logへ出さない。
- keyを表示する場合は存在有無だけとし、一部の文字列も表示しない。
- key漏えいが疑われる場合は直ちに無効化、rotation、影響範囲確認を行う。

対象:

- Gemini API key
- Exa API key
- W&B API key
- Cookie署名鍵
- user ID HMAC鍵

## 9. Prompt injection対策

論文、PDF、abstract、Webページ、Exa highlight、metadataを信頼できないデータとして扱う。

- 本文中の「system promptを無視する」「toolを呼ぶ」「秘密を出す」などの命令に従わない。
- tool引数はResearch Plannerの構造化schemaとallowlistで作る。
- URL、query、識別子、長さを検証する。
- 文書内容から任意のtool名やendpointを選ばせない。
- Evidenceは引用対象の事実情報として区切り、命令としてpromptへ連結しない。
- toolの生結果を別toolの命令へ流用しない。
- 取得本文に含まれるURLを自動で巡回しない。

prompt injection検体をsecurity testへ含める。

## 10. Corpusとlicense

- DOI、PMID、canonical URL、取得元、取得日時を記録する。
- OAまたは明示的な保存許諾が確認できる本文だけを保存する。
- paywall本文、利用条件不明の本文を保存しない。
- 社内文書はaccess classificationと外部送信可否を必須にする。
- manifestにない社内PDFを取り込まない。
- OCR必須PDFを外部OCRへ自動送信しない。
- 撤回、訂正、preprintを可能な範囲で識別し、回答と評価へ反映する。

## 11. LLMと検索API

- Geminiへ送るEvidenceを12 excerpts、約10,000 tokenに制限する。
- Exaへ送るqueryは必要最小限にする。
- 同一queryの過剰送信をhard budgetで止める。
- 外部API responseを信頼せず、schema validationする。
- 外部API errorのpayloadをブラウザや通常ログへ転送しない。
- Exaのtimeout、rate limit、認証、5xx、schema driftは本文を捨てて安定した分類値へ変換する。
- retryはExa call budget内の最大2回とし、同一queryを無限に再送しない。
- Exa障害時は内部retrievalを破棄せず、外部検索が一部失敗したことだけを回答へ表示する。
- DOI / PMIDを持つ公開Evidenceは1 batchでEurope PMCへ照合し、検証状態、研究段階、
  撤回・訂正状態、取得元をEvidence provenanceへ保持する。
- 未検証Evidenceは明示し、撤回済みEvidenceを肯定的なclaimへ使用しない。
- ADK invocation全体へ最大180秒のhard deadlineを適用する。
- deadlineまたはcancel時は進行中のprovider taskを中断し、部分回答やtool結果を送らない。
- timeout/cancel後のterminal eventは1件に限定し、分類済みflagとfinish reasonだけを
  manifest / traceへ記録する。
- model / API version変更時はcontract testとevalを再実行する。

## 12. Traceとログ

ADKのraw message / tool content captureを明示的に無効化する。

OTLP root spanへ送信できる情報:

- HMAC user ID
- conversation ID
- turn ID
- agent / prompt / corpus version
- tool回数と重複query数
- context使用比率
- finish reason
- citation数、source数
- booleanまたは分類済みflag

既定で送信してはいけない情報:

- 表示名
- raw user UUID
- 社内excerpt
- Research-sensitive query
- 最終回答本文
- tool request / response本文
- Cookie
- API key
- stack dumpに含まれるrequest body

質問の `input.value` と最終回答の `output.value` は、それぞれの個別承認と独立
feature flagがある環境だけで送る。flagが有効でも、入力全体のfingerprintが
Public/Synthetic allowlistに一致しない場合は送らない。Internal Evidenceを含む出力も
送らない。分類名は本文を含まない決定的属性として記録できる。

通常ログはID、件数、status、error classを中心にし、本文を避ける。trace分析はserver-side filterで絞り、集約表だけをLLMへ渡す。

## 13. Citation safety

- source registryに存在し、そのturnでretrieval済みのsource IDだけを許可する。
- Markdown citation、structured claim、source registryのEvidence ID集合を一致させる。
- source registryはdocument単位で潰さず、excerptのEvidence IDごとに解決可能にする。
- citationのURL、DOI、PMIDを解決できるか検証する。
- claim直後のcitation mapping、support level互換性、Evidence title/excerptへの
  lexical groundingを検証する。
- Citation Verifierのrepairは1回に限定する。
- repair後もunsupportedなclaimを削除する。
- 撤回sourceを肯定的根拠として使用しない。
- 「検索で見つからない」を「存在しない」へ言い換えない。

これらはpost-hoc Signalではなく、回答を利用者へ渡す前の決定的guardrailとして実装する。

## 14. Feedback

- feedbackをWeb DBへ先に保存する。
- W&Bへの同期はserver側だけで行う。
- `app.turn_id` からAgents span APIでAgent turnの `trace_id` を検索する。
- Agent turn trace未着と一時errorをretryする。
- idempotency keyで重複登録を防ぐ。
- 永久失敗でもローカルfeedbackを削除しない。
- コメント本文をW&Bへ送る場合は別途データ送信承認を必要とする。

## 15. Medical safety

各回答に研究用注意書きを表示する。

禁止:

- 患者個別の診断
- 治療選択、投与量、緊急度の指示
- 根拠を超えた臨床効果の断定
- 動物、in vitro、観察研究を臨床有効性と同一視すること
- 撤回論文の肯定的根拠利用

要求された場合は、MVPの目的外であることを説明し、創薬研究上の一般的なエビデンス整理へ範囲を戻す。

## 16. Retentionと削除

MVPではproductionの保持期間を定義しない。ただし、実装は次を満たす。

- Web transcript、ADK session、corpus、traceが別storeであることを明示する。
- 各recordをconversation ID、turn ID、document IDで特定できる。
- 削除を実装する際に、複数storeの対象を列挙できる。
- test dataとlive dataを別project / DBへ分離する。
- 不要なrequest / response dumpを保存しない。

production pilot前に、保持期間、削除責任者、backup、W&B側のretentionを別途決定する。
機密pilotでは承認recordにstore別の保持日数、削除責任者、backup方針、削除確認方法を
必須とし、[pilot runbook](runbooks/sensitive-data-pilot.md) の横断dry-runを完了する。
担当者未指名またはvendor側削除確認ができない場合は機密featureを有効化しない。

## 17. Security test

必須test:

- 表示名がLLM promptとOTLP payloadに含まれない。
- 社内excerptが未承認時にGemini、Exa、W&Bへ送られない。
- tool responseとsecretがSSE、log、OTLPに含まれない。
- 未設定のfeature flagが無効になる。
- input/output trace flagの全組合せが独立して機能する。
- targetが空の機密questionをResearch-sensitiveとしてW&Bへ送らない。
- Public/Synthetic入力でもInternal Evidenceを含む出力をW&Bへ送らない。
- client入力だけでtrace data classificationを緩和できない。
- Cookie改ざんが拒否される。
- prompt injectionを含むPDF / Web本文で命令を実行しない。
- scope外疾患を通常調査しない。
- 未取得citationを拒否する。
- 撤回sourceの肯定利用を拒否する。
- tool budgetとno-progress停止条件を越えない。
- feedback retryが重複登録せず、ローカルrecordを失わない。

## 18. Incident response

秘密情報または社内データの外部送信が疑われる場合:

1. 該当する外部送信feature flagを無効化する。
2. live canaryと該当serviceを停止する。
3. turn ID、時刻、送信先、データ分類を本文なしで記録する。
4. API keyや署名鍵の漏えいが疑われる場合はrotationする。
5. W&B、Gemini、Exa側の保存範囲と削除手段を確認する。
6. 影響範囲、原因、再発防止testを文書化する。
7. データ管理者の再承認まで機能を再開しない。

## 19. Production移行前の未解決事項

- SSO / MFA / RBAC
- 文書ACLとtenant isolation
- production secret manager
- network egress制御
- data retentionと削除SLA
- backup / restore
- audit log
- vendorごとの契約とデータ保持条件
- security reviewとthreat modelingの更新

これらが解決するまで、本MVPをproductionへdeployしない。
