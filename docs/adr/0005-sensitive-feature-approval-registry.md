# ADR-0005: 機密機能はversioned承認registryでfail closedにする

- Status: Accepted
- Date: 2026-07-30

## Context

社内PDF ingestion、社内excerptのGemini送信、機密研究仮説のGemini/Exa送信、
W&Bへのcontent送信は、個別のboolean flagだけでは記録済み承認と結び付かない。
承認範囲には機能、送信先、環境、data class、期限があり、いずれかが異なる場合は
同じ承認を流用できない。

承認者や削除責任者の実名はdeploymentごとに決定する必要がある。repository内に
架空の担当者や有効な承認recordを置くと、運用判断を実装が代行したように見える。

## Decision

- repositoryのデモは `public_synthetic_demo` deployment profileを既定とする。この
  profileでは承認registryの有無にかかわらず、機密データ経路を起動拒否する。
  server-owned fingerprintで公開・合成と確定したtrace contentは対象外とする。
- `approved_sensitive_pilot` は将来の別deployment専用とし、デモでは選択しない。
- 非secretのJSON承認registryをschema version `2` として管理する。
- registryにはdata manager、service owner、脳卒中SME、創薬SME、全storeの
  deletion ownerを組織内で一意な担当IDとして記録する。
- 各recordはapproval ID、機能、送信先、環境、data class、目的、承認者、承認日、
  有効期限、制約、機能が触れる全storeのretention/deletion責任を必須とする。
- 各recordは、公開・合成データpilotの完了日と検証者、および全対象storeの削除
  dry-run証跡を必須とする。証跡には実施者、照合件数、backup状態、検証状態、
  repository外の証跡参照先を含める。
- `approved_sensitive_pilot` でsensitive flagが1つでも有効なprocessは、
  `AGENT_SENSITIVE_APPROVAL_REGISTRY_PATH` の完全一致recordを起動時に検証する。
- record欠落、旧schema、role未割当、承認者不一致、対象store不足、pilot/dry-run
  証跡不足、期限切れ、未来日、環境・送信先・data class不一致は起動を拒否する。
  別機能のrecordは流用しない。
- 通常ログにはapproval ID、機能、送信先、環境、判定だけを残す。承認者、目的、
  制約、本文は残さない。
- registryをfeature flagの代用にしない。flagと有効recordの両方を必要とする。
- repositoryには有効なdeployment承認recordをcommitしない。

## RACI

| 作業 | Responsible | Accountable | Consulted | Informed |
| --- | --- | --- | --- | --- |
| approval record作成 | Service owner | Named data manager | Security reviewer、対象vendor owner | 脳卒中SME、創薬SME |
| 社内corpus選定 | Corpus operator | Named data manager | 脳卒中SME、創薬SME | Service owner |
| gold/challenge昇格 | Evaluation owner | Named data manager | 脳卒中SME、創薬SME | Service owner |
| 横断削除 | Storeごとのdeletion owner | Named data manager | W&B/vendor owner、backup owner | Service owner |
| incident時の再開承認 | Service owner | Named data manager | Security reviewer | 関係SME |

`Named data manager`、各SME、store ownerはpilotごとに実名または組織内で一意な
担当IDを割り当てる。未割当のroleは承認済みとは扱わない。

## Consequences

### Positive

- flagの誤設定だけでは機密経路を開けない。
- デモ環境へ承認registryを誤配置しても機密経路を開けない。
- 承認の期限とscopeをprocess起動時に機械検証できる。
- retentionと削除責任を承認単位で追跡できる。
- 公開・合成データで削除経路を検証する前に機密経路が開くことを防げる。

### Negative

- 承認更新後はregistry更新とprocess再起動が必要になる。
- repositoryだけでは人間のowner指名やvendor側削除完了を証明できない。
- 複数機能を有効にする場合、機能ごとのrecordが必要になる。

## Verification

- missing、invalid/old schema、expired/future、environment、destination、
  data class mismatchをnegative testで拒否する。
- `public_synthetic_demo` profileでは、機密データ経路を有効なrecordがあっても拒否する。
- role、対象store、retention owner、pilot verifier、削除dry-run証跡の欠落や
  不一致をnegative testで拒否する。
- 複数flagに1件のrecordを流用できないことをtestする。
- logにapproval IDと判定だけが入り、承認者や目的が入らないことをtestする。
- [機密データpilot runbook](../runbooks/sensitive-data-pilot.md) のdry-runと
  verification checklistをpilot開始前に完了する。
