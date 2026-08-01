# 公開デモcorpus inclusion / license policy

- Version: `public-demo-1.0`
- Effective date: 2026-08-01
- Scope: ischemic stroke drug-discovery technical demo
- Scientific status: SME未レビュー。gold labelやscientific release判定には使用しない。

## Inclusion criteria

次をすべて満たす文献メタデータを候補にする。

1. titleまたはabstractがischemic stroke / cerebral infarctionを対象とする。
2. drug、therapeutic、target、inhibitor、neuroprotectionのいずれかを含む。
3. Europe PMCでDOI、PMID、PMCIDまたはcanonical recordを確認できる。
4. 撤回済み文献はmetadataとして識別しても、肯定的根拠には使わない。

OA本文保存は、さらに次をすべて満たす場合だけ許可する。

1. 有効なPMCIDがあり、Europe PMC OA subsetの `fullTextXML` から取得できる。
2. XMLまたは検証済みmetadataのlicenseがallowlistへ完全に分類できる。
3. JATS bodyから本文を抽出できる。
4. 取得サイズが10 MiB以下で、DTD/entity宣言を含まない。

条件を満たさない文献は削除せずmetadata/abstract-onlyとして保持し、skip理由をreportする。

## License allowlist

| Canonical ID | 許可する表記例 | 扱い |
| --- | --- | --- |
| `CC-BY-4.0` | CC BY、`creativecommons.org/licenses/by/` | 保存可 |
| `CC-BY-SA-4.0` | CC BY-SA、`creativecommons.org/licenses/by-sa/` | 保存可 |
| `CC0-1.0` | CC0、Creative Commons Zero URL | 保存可 |
| `Public-Domain` | public domain | 保存可 |

CC BY-NC、CC BY-ND、publisher-specific、`open access`だけの表記、license不明は、
用途制約を自動判断せず本文保存対象外とする。allowlist変更は法務・データ管理者確認後に
versionを更新し、既存snapshotを変更せず新規snapshotを作る。

## Source priority

1. Europe PMC OA `fullTextXML`（JATS XML）
2. Europe PMC core metadata
3. Crossref license/journal metadata
4. Unpaywall OA location/license metadata

任意publisher URL、HTML scraping、PDF redirect、検索結果snippetは本文保存元にしない。
source URL、取得日時、SHA-256、license、section、skip理由を保存する。

## Review boundary

このpolicyは取得・再現性・安全境界を決めるtechnical policyであり、検索語の科学的妥当性、
文献の重要度、claimの正しさを承認しない。SMEレビュー後にのみgold/challenge datasetへ
昇格できる。
