# ADR-0001: ローカルMVPを2サービス構成にする

- Status: Accepted
- Date: 2026-07-30

## Context

UIとsession管理にはReact Router、エージェント実行にはPython版Google ADKを使う。ブラウザからADKや外部APIを直接呼ぶと、API key、内部event、tool payloadが露出し、Webとagentの契約も不安定になる。

Webの表示用会話、ADKのevent / state、論文corpusは更新周期と責務が異なる。1つのSQLite schemaを複数runtimeが共有すると、migration ownershipと障害境界が曖昧になる。

## Decision

ローカルMVPを次の2サービスで構成する。

- `apps/web`: React Router SSR / BFF
- `services/agent`: Google ADK API Server

ブラウザはWeb BFFだけを呼ぶ。BFFはADKの `/run_sse` を内部契約として呼び、公開SSE eventへ変換する。

DBは共有しない。

- Web DB: 利用者、会話、表示用transcript、feedback
- ADK session DB: ADK event / state
- Corpus DB: 文書、chunk、FTS5、embedding、snapshot

## Consequences

### Positive

- ブラウザへ外部API keyと内部tool payloadを出さずに済む。
- TypeScriptとPythonをそれぞれ適切な責務に使える。
- DB migrationのownerが明確になる。
- ADKの更新をBFF contractで吸収できる。
- 将来のhosting変更をサービス単位で検討できる。

### Negative

- ローカルで2 processを起動する必要がある。
- OpenAPI、SSE、ID相関のcontract testが必要になる。
- 表示用transcriptとADK stateの二重管理を意識する必要がある。

## Alternatives considered

### 単一Node.jsサービス

Python版Google ADKを主要runtimeとして使う要件と合わないため採用しない。

### 単一Pythonサービス

React Router SSR、Cookie、BFFの責務を同じruntimeへ寄せる利点が小さく、UI境界が曖昧になるため採用しない。

### 共有SQLite

schema ownershipとmigration競合が発生しやすいため採用しない。

## Verification

- ブラウザのnetwork requestがWeb originだけを指す。
- ADK OpenAPIと公開SSE schemaのcontract testがある。
- 各DB migrationを所有serviceだけが実行する。
