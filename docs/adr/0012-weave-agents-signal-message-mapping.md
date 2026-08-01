# ADR-0012: Agents Signals向けcontentはOTel GenAI message形式を併記する

- Status: Accepted
- Date: 2026-08-01

## Context

runtime traceはraw OTelを使い、質問と最終回答だけを独立したfeature flagと
server-owned分類で `input.value` / `output.value` に送る。実W&B Signals pilotでは、
このgeneric属性だけではAgents scorerの `input_messages` / `output_messages` が空になり、
Low Quality Responseが誤検知した。

## Decision

承認済みの質問と最終回答を送る場合、同じgateの内側で次も設定する。

- `gen_ai.input.messages`: user roleの質問1件
- `gen_ai.output.messages`: assistant roleの最終回答1件

値はOTel GenAI message schemaでJSON化する。中間message、system instruction、
reasoning、tool argument、tool resultは追加しない。runtime tracingを
`weave.init()`へ切り替えない。

Agents span queryのSignals filterはconversation単位で適用されるため、controlled pilotは1 turnを
1 synthetic conversationとしてexportする。

## Consequences

- Agents Signalsが実際の質問と最終回答を読める。
- 同じ本文がgeneric属性とGenAI属性に複製されるが、送信可否と分類は共通であり、
  送信対象の情報量は増やさない。
- privacy testは両方の属性を検査する必要がある。

## Verification

- public / syntheticだけで両形式が生成されるunit testを通す。
- disabled、research-sensitive、internal outputでは両形式が欠落することを確認する。
- 20 synthetic turnをAgents endpointへ送り、Agents viewとSignals出力を確認する。
- server-side aggregateで本文を取得しない。
