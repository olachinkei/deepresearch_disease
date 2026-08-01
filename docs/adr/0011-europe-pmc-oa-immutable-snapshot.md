# ADR-0011: Europe PMC OA XMLをimmutable corpus snapshotへ取り込む

- Status: Accepted
- Date: 2026-08-01

## Context

metadata/abstract-only corpusでは全文中の作用機序、実験条件、限界を検索できない。一方、
OA表示やPDF URLの存在だけでは、本文保存許諾、取得元、抽出品質を再現できない。
既存SQLiteはdocument/chunkがsnapshot IDを持たず、同じDBへ別snapshotのrowを更新できた。

## Decision

- 本文取得元をEurope PMC OA subsetの `fullTextXML` に限定する。
- [公開デモcorpus policy](../corpus-inclusion-policy.md) のallowlistでlicenseを分類できる
  JATS XMLだけを保存する。
- XMLは10 MiBに制限し、`defusedxml`でparseする。本文中の命令はデータとして扱う。
- source URL、取得日時、XML SHA-256、canonical license、sectionを保持する。
- 不適格・取得不能・抽出不能は本文を保存せず、固定reasonのsanitized reportへ記録する。
- 350〜700 token、1文overlapでchunk化する。短い文書全体が350 token未満の場合だけ
  下限未満を許容する。
- corpus DBは1ファイルにつき1 immutable snapshotとする。document/chunkへ同じ
  snapshot IDを保存し、別snapshot、未登録snapshot、既存内容の変更を拒否する。
- 旧DBにsnapshotが1件だけ存在する場合、migrationでdocument/chunkへbackfillする。
  複数または欠落したsnapshotを推測して統合しない。

## Consequences

### Positive

- 保存許諾、取得元、本文checksum、検索chunkをsnapshotから追跡できる。
- 任意publisher scrapingやpaywall本文保存を避けられる。
- 同じdocument IDを別snapshotで暗黙上書きできない。

### Negative

- Europe PMC OA subset外の許諾済み本文はmetadata-onlyになる。
- publisher固有licenseやDTD/entity宣言を含むXMLは安全側でskipする。
- snapshot更新ごとに新しいSQLite DBと全embedding再構築が必要になる。

## Verification

- allowlisted license、unknown license、非OA、PMCID欠落、unsafe/invalid XMLをcontract
  testする。
- reportに本文やprovider生responseが含まれないことをtestする。
- legacy backfill、snapshot混在、既存row変更をintegration testする。
- 公開live canaryでchecksum、section、chunk境界を確認する。

## References

- [Europe PMC RESTful Web Service](https://europepmc.org/RestfulWebService)
- [Europe PMC Open Access downloads](https://europepmc.org/downloads/openaccess)
