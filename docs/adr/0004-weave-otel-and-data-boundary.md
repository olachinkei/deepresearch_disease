# ADR-0004: runtime traceはraw OTel、評価と分析はWeave SDKを使う

- Status: Accepted
- Date: 2026-07-30

## Context

ADK agentをWeaveで観測し、offline eval、online signal、feedback、flag分析へつなげたい。runtime tracingでは、標準OTel環境変数による設定を維持したい。

ADKのmessageやtool contentをそのままtraceすると、表示名、社内論文、標的仮説、secretが外部へ送信される危険がある。また、OTLP exportは非同期で、アプリがWeave Agent turnの `trace_id` を即時に解決できるとは限らない。

## Decision

runtime traceはraw OTelを使い、Weave Agents endpointへ送る。

```text
https://trace.wandb.ai/agents/otel/v1/traces
```

- exporter、認証、project routingは標準環境変数で設定する。
- ADKのraw message / tool content captureを明示的に無効化する。
- 最小pluginはpseudonymous user ID、turn ID、version、budget、finish、citation、flag属性だけをroot spanへ追加する。
- 質問と最終回答の送信は独立したfeature flagにする。
- Agents Signalsへ渡す場合、同じgateの内側で質問と最終回答を
  `gen_ai.input.messages` / `gen_ai.output.messages` にもOTel GenAI形式で複製する。
  中間message、system instruction、tool payloadは複製しない。
- 未承認の社内利用ではcontent送信を無効にする。
- `app.turn_id` でAgents span APIをserver-side filterし、Agent turnの `trace_id` を後から解決してfeedbackを同期する。Agents endpointのspan検索に旧Call APIを使わない。
- runtime tracingを自動で `weave.init()` へfallbackしない。
- Weave SDKはoffline eval、feedback同期、server-side filterを使う分析に限定する。
- Agents Signalsはpost-hoc監視に使い、安全guardrailには使わない。

実装前に公開・合成データによるcompatibility spikeを行う。

## Consequences

### Positive

- runtimeの設定を標準OTelへ寄せられる。
- content送信を最小化できる。
- turn、feedback、Signals、offline evalを同じ相関IDで分析できる。
- Weave SDKを分析用途に限定し、runtime instrumentationの責務を明確にできる。

### Negative

- raw OTelとWeaveの属性mappingをspikeで検証する必要がある。
- feedbackはeventual consistencyを前提にretryが必要になる。
- Signalsに必要なcontentとプライバシーの間で、環境別の承認が必要になる。
- 互換性不足時に自動fallbackできない。

## Alternatives considered

### runtimeで `weave.init()` を使う

公式integrationとして有用だが、今回の「環境変数によるraw OTel」という制約を変えるため、暗黙のfallbackとしては採用しない。互換性spikeが失敗した場合に、別ADRで再検討する。

### traceへ全messageとtool payloadを送る

調査内容と社内データが外部送信されるため採用しない。

### Agent turnのtrace IDを同期requestで取得してからfeedbackを許可する

OTLP exportの非同期性と合わず、利用者feedbackを失う可能性があるため採用しない。

## Verification

- ADK turn、LLM、tool span、conversation grouping、token usageを確認する。
- custom属性と `app.turn_id` をAgent span上で確認する。
- privacy testで表示名、secret、社内excerpt、tool payloadがOTLPにないことを確認する。
- feedbackのpending、retry、idempotent syncを確認する。
- flag分析でserver-side filterを使い、生trace全件をLLMへ渡さない。

## References

- [Weave Agents OTel](https://docs.wandb.ai/weave/guides/tracking/trace-agents-otel)
- [Weave feedback](https://docs.wandb.ai/weave/guides/tracking/feedback)
- [Weave Agents Signals](https://docs.wandb.ai/weave/guides/tracking/view-agent-signals)
- [Weave Evaluations](https://docs.wandb.ai/weave/guides/core-types/evaluations)
