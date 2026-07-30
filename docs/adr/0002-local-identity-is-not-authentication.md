# ADR-0002: 表示名を非認証のローカル識別として扱う

- Status: Accepted
- Date: 2026-07-30

## Context

MVPでは利用者名を入力し、誰が使ったかをtraceで区別したい。一方、表示名だけでは本人確認、session保護、認可、文書アクセス制御を提供できない。「ユーザー認証」と呼ぶと、安全性を過大評価する。

表示名をpromptやtraceへ含めると、不要な個人情報送信と生成への影響が生じる。

## Decision

表示名はローカル利用者識別にだけ使い、認証とは呼ばない。

1. Webサービスが内部UUIDを発行する。
2. 内部UUIDを署名済み・HttpOnly Cookieへ保存する。
3. 表示名はWeb DBにだけ保存する。
4. ADKには内部UUIDを `user_id` として渡す。
5. traceには環境ごとの秘密鍵でHMAC化した識別子だけを送る。
6. 表示名をLLM promptへ含めない。

全利用者は同一corpusへアクセスし、文書単位ACLはMVP対象外とする。

## Consequences

### Positive

- MVPの安全性を誤認させない。
- 表示名の外部送信を避けられる。
- turnをpseudonymous user単位で集計できる。
- 将来の本認証を別の設計判断として追加できる。

### Negative

- 同一人物の複数browserやCookie削除を統合できない。
- なりすましを防げない。
- 機密文書へのアクセス制御には使用できない。

## Alternatives considered

### 表示名をそのままuser IDにする

衝突、なりすまし、個人情報送信の問題があるため採用しない。

### Gista authをそのまま導入する

本認証はMVPの範囲外であり、必要な要件と運用が未確定のため採用しない。React Router、Drizzle、session設計の参考に限定する。

## Verification

- UIに「表示名は認証ではない」と表示する。
- privacy testで表示名がprompt、ADK requestの不要なfield、OTLP payloadにないことを確認する。
- Cookie改ざんを受け付けない。
