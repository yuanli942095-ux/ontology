# E24 一致性复核记录

复核人员：Codex independent rule review，2026-08-21。未读取、运行或根据 Qwen 输出修改答案。

## 原始证据原句
- DOC_E24_TERMS：本约定适用于由合作单位团体计划转入产品A个人计划的被保险人。
- DOC_E24_TERMS：首次投保产品A个人计划的等待期为90天。
- DOC_E24_TERMS：被保险人从合作单位团体计划连续转入，且原保障至新合同生效日前一日仍有效，并在30日内提交连续保障证明的，等待期按0天处理。
- DOC_E24_TERMS：连续保障证明提交超过30日但不超过60日的，等待期按30天处理；无法提交证明的，仍适用一般规则。
- DOC_E24_TERMS：本次申请属于合作单位团体计划转入，原保障截至新合同生效日前一日仍有效，连续保障证明在第20日提交。
- DOC_E24_TERMS：等待期天数应由一般规则和满足条件的例外规则共同确定，例外规则优先于一般规则。

## 从证据提取的事实
- `group_transfer = true`，案件属于合作单位团体计划转入。
- `prior_coverage_uninterrupted = true`，原保障至新合同生效日前一日仍有效。
- `proof_submitted_days = 20`，连续保障证明在30日内提交。

## 规则复核
| rule_id | priority | 条件 | allowed_values |
|---|---:|---|---|
| E24_CONTINUOUS_TRANSFER_EXCEPTION | 300 | `group_transfer equals true`; `prior_coverage_uninterrupted equals true`; `proof_submitted_days less_or_equal 30` | `0` |
| E24_LATE_PROOF_EXCEPTION | 200 | `proof_submitted_days greater_than 30`; `proof_submitted_days less_or_equal 60` | `30` |
| E24_GENERAL_WAITING_PERIOD | 100 | 无条件一般规则 | `90` |

实际满足的规则：`E24_CONTINUOUS_TRANSFER_EXCEPTION` 与 `E24_GENERAL_WAITING_PERIOD` 都满足；按最高优先级选择 `E24_CONTINUOUS_TRANSFER_EXCEPTION`。规则允许值：`0`。

## 候选集合
- `CAND_001`: `90`
- `CAND_002`: `0`
- `CAND_003`: `30`

Oracle值：`0`。五者是否一致：修订后文档证据、案件事实、形式规则、候选集合、Oracle 一致。

## 问题归属
问题属于规则。连续保障和等待期豁免事实并未缺失，类型也正确；修订前例外规则条件因嵌套 `when` 未被 gate 按算子解释，只有空条件的一般规则可触发。

## 修订前内容
```json
"when": {
  "group_transfer": {"equals": true},
  "prior_coverage_uninterrupted": {"equals": true},
  "proof_submitted_days": {"less_or_equal": 30}
}
```

## 修订后内容
```json
"conditions": [
  {"fact": "group_transfer", "operator": "equals", "value": true},
  {"fact": "prior_coverage_uninterrupted", "operator": "equals", "value": true},
  {"fact": "proof_submitted_days", "operator": "less_or_equal", "value": 30}
]
```

## 修订理由
修订后的例外事实完全来自条款第五条案件事实和第三条例外规则；优先级 300 高于一般规则 100，体现“例外规则优先于一般规则”。
