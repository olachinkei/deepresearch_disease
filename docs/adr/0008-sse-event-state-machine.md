# ADR-0008: SSEはID付き単調sequenceと単一terminalで検証する

- Status: Accepted
- Date: 2026-07-31

## Context

従来の公開SSEはevent IDとsequenceを持たず、browser consumerはZodで解釈できない
frameを黙って破棄していた。terminal eventがないEOF、別turnのevent、順序違反、
proxyによるduplicateも正常完了と区別できず、Web DBと画面状態が不一致になり得た。

内部ADK eventにはIDがあったが、conversation IDとsequenceがなく、BFFは別turn eventや
out-of-order eventを検出できなかった。

## Decision

- 公開SSE schemaを `2.0` とし、全eventにtop-level `eventId` と0始まりの `sequence`
  を必須化する。SSE `id:` fieldとpayloadの `eventId` は完全一致させる。
- Python AgentはADK event IDに加え、`customMetadata`へ `conversation_id`、
  `turn_id`、`event_sequence` を付ける。
- Agent clientは最初のeventがsequence 0の `research_started` であること、IDの
  一意性、sequenceの連続性、request contextとのconversation/turn一致、単一terminal
  を検証する。
- 同じevent IDの再送は同一frameのretryとして破棄する。別IDのout-of-order event、
  malformed event、ID mismatch、terminal前EOFは `AgentProtocolError` とする。
- BFFはAgentのprotocol errorを本文やraw frameを含まないretryable
  `agent_protocol_error` terminalへ変換し、turnを `error` として保存する。
- browser consumerも同じstart-to-terminal state machineを実行する。公開SSEの
  malformed frame、ID/sequence/context mismatch、terminal前EOFは、固定文言の
  `stream_protocol_error` として扱う。
- terminal後のframeはDBの確定状態を変えず、安全に無視する。terminalは画面へ1件だけ
  渡す。
- MVPでは自動reconnectを行わない。利用者にはretryable errorを表示し、再実行は新しい
  turn IDで行う。元turnのdeltaやevent IDは再利用しない。

## Consequences

### Positive

- network切断、duplicate、別turn混入、順序違反をsilent successにしない。
- BFFとbrowserが同じevent境界を検証し、terminal状態を一意に保てる。
- event IDによりproxy retryを重複表示せず処理できる。

### Negative

- schema 1.0 consumerは2.0 eventを読めないため、BFFとbrowserを同時deployする必要が
  ある。
- 自動reconnectを行わないため、一時切断時は利用者によるretryが必要になる。
- terminal後に到着したframeは診断用に表示せず破棄する。

## Verification

- fragmented frame、duplicate ID、out-of-order sequence、conversation/turn mismatch、
  malformed frame、terminal前EOF、terminal後deltaをVitestで検証する。
- mock Agentでduplicateは1回だけ表示され、truncation、out-of-order、turn mismatchが
  sanitized errorになることをPlaywrightで検証する。
- Python contract testでevent IDが一意、sequenceが0から連続し、conversation IDが
  全eventへ付くことを確認する。
