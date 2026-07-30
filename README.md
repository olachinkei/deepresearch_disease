# 脳梗塞創薬 Deep Research Agent

脳梗塞（`ischemic stroke`）の創薬仮説を、ローカル論文 index と公開論文検索から調査し、引用付きの日本語レポートとして返す研究支援MVPです。患者個別の診断・治療判断には使用しません。

## 構成

- `apps/web`: React Router SSR/BFF、非認証の利用者識別、会話UI、feedback queue
- `services/agent`: Google ADK、検索・論文index、引用検証、OTel、Weave評価
- `data/public_seed`: 公開文献の再現可能なseed metadataと収集manifest
- `docs`: product spec、実行計画、ADR、安全・運用文書

Web、ADK session、corpusは別々のSQLite DBを所有します。ブラウザはPythonサービスへ直接接続せず、Web BFFの公開SSE契約だけを利用します。

## ローカル起動

必要条件は Node.js 24以降、pnpm 10.32.1、Python 3.13、uv、SQLite 3（FTS5有効）です。

```bash
cp .env.example .env
make install

# terminal 1
make agent

# terminal 2
make web
```

外部キーを設定しない場合も、テストと合成データのデモは実行できます。実検索を許可するには `EXA_API_KEY` に加えて `AGENT_RUNTIME_MODE=live` と `AGENT_ALLOW_TARGET_TO_EXA=true` が必要です。社内PDF ingestionとtrace本文送信は、それぞれ独立したfeature flagで既定無効です。

## 検証

```bash
make quality
make test
pnpm test:e2e
```

通常のテストはGemini、Exa、Weaveをmock化しています。資格情報を伴うlive canaryと実W&B smokeは明示コマンドでのみ実行され、通常のCIからは呼ばれません。

GitHub Actionsの `Live canary` は手動実行専用です。固定された合成queryだけを使い、
`live-canary` environmentで承認されたsecretがある場合に限って実行します。任意の質問や
社内データをworkflow inputとして渡すことはできません。

初回公開と以後のcommitでは、次の監査を実行します。

```bash
git add <公開対象>
./scripts/audit-tracked-files.sh
```

この監査は `.env`、SQLite/WAL、内部文書、cache/build、秘密鍵形式などの追跡を拒否し、
機密keyへの高entropy値の代入も値を表示せず検査します。CIではさらにGitleaksでGit履歴全体を
検査します。

## データ境界

- 表示名はUI表示専用です。プロンプトやtraceへ送信しません。
- traceにはHMAC化した内部利用者IDと運用メタデータだけを付与します。
- `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false` と `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT` を必須にします。
- 社内PDF断片、標的仮説、質問・回答の外部送信は、データ管理者の明示承認まで無効です。

詳細は [instruction.md](./instruction.md)、[ARCHITECTURE.md](./ARCHITECTURE.md)、[docs/SECURITY.md](./docs/SECURITY.md) を参照してください。
