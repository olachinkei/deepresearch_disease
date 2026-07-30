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

- 非secretのJSON承認registryをschema version `1` として管理する。
- 各recordはapproval ID、機能、送信先、環境、data class、目的、承認者、承認日、
  有効期限、制約、store別retention/deletion責任を必須とする。
- sensitive flagが1つでも有効なprocessは、
  `AGENT_SENSITIVE_APPROVAL_REGISTRY_PATH` の完全一致recordを起動時に検証する。
- record欠落、schema不正、期限切れ、未来日、環境・送信先・data class不一致は
  起動を拒否する。別機能のrecordは流用しない。
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
- 承認の期限とscopeをprocess起動時に機械検証できる。
- retentionと削除責任を承認単位で追跡できる。

### Negative

- 承認更新後はregistry更新とprocess再起動が必要になる。
- repositoryだけでは人間のowner指名やvendor側削除完了を証明できない。
- 複数機能を有効にする場合、機能ごとのrecordが必要になる。

## Verification

- missing、invalid、expired/future、environment、destination、data class mismatchを
  negative testで拒否する。
- 複数flagに1件のrecordを流用できないことをtestする。
- logにapproval IDと判定だけが入り、承認者や目的が入らないことをtestする。
- [機密データpilot runbook](../runbooks/sensitive-data-pilot.md) のdry-runと
  verification checklistをpilot開始前に完了する。
