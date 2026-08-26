# E14 一致性复核记录

复核人员：Codex independent rule review，2026-08-21。未读取、运行或根据 Qwen 输出修改答案。

## 原始证据原句
- DOC_E14_CURRENT：生效日期：2026年8月1日；失效日期：2026年8月31日。
- DOC_E14_CURRENT：本条款适用于2026年8月1日至2026年8月31日期间提交并完成核保的产品A成人医疗计划。
- DOC_E14_CURRENT：在本版本有效期间，产品A成人医疗计划的最高投保年龄为62周岁。
- DOC_E14_CURRENT：团体连续转入且原保障未中断的被保险人，可沿用原团体方案的最高投保年龄60周岁。
- DOC_E14_FUTURE：发布日期：2026年8月15日；生效日期：2026年9月1日。
- DOC_E14_FUTURE：发布日早于受理日但生效日晚于受理日的版本暂不适用。
- case_context：核保受理日为2026年8月20日，评估日期为2026年8月21日；9月修订版已经发布但尚未生效。

## 从证据提取的事实
- `underwriting_acceptance_date = "2026-08-20"`，版本选择日期为核保受理日。
- `assessment_date = "2026-08-21"`，不是版本适用的决定日期。
- `group_transfer_confirmed = false`，案件上下文未给出团体连续转入确认。
- 当前有效版本区间为 `2026-08-01` 至 `2026-08-31`；未来版本从 `2026-09-01` 生效。

## 规则复核
| rule_id | priority | 条件 | allowed_values |
|---|---:|---|---|
| E14_CURRENT_EFFECTIVE_VERSION | 300 | `underwriting_acceptance_date on_or_after 2026-08-01`; `underwriting_acceptance_date on_or_before 2026-08-31`; `group_transfer_confirmed equals false` | `62` |
| E14_GROUP_TRANSFER_EXCEPTION | 200 | `group_transfer_confirmed equals true` | `60` |
| E14_FUTURE_VERSION_SCOPE | 100 | `underwriting_acceptance_date on_or_after 2026-09-01` | `66` |

实际满足的规则：`E14_CURRENT_EFFECTIVE_VERSION`。规则允许值：`62`。

## 候选集合
- `CAND_001`: `66`
- `CAND_002`: `62`
- `CAND_003`: `60`

Oracle值：`62`。五者是否一致：修订后文档证据、案件事实、形式规则、候选集合、Oracle 一致。

## 问题归属
问题属于规则。文档、事实、候选和 Oracle 不需要修改。修订前事实已包含日期和团体转入否定事实，但规则条件使用嵌套 `when` 对象，现有 gate 会把嵌套对象作为 `equals` 值比较，导致没有适用规则。

## 修订前内容
```json
"when": {
  "underwriting_acceptance_date": {
    "on_or_after": "2026-08-01",
    "on_or_before": "2026-08-31"
  },
  "group_transfer_confirmed": {
    "equals": false
  }
}
```

## 修订后内容
```json
"conditions": [
  {"fact": "underwriting_acceptance_date", "operator": "on_or_after", "value": "2026-08-01"},
  {"fact": "underwriting_acceptance_date", "operator": "on_or_before", "value": "2026-08-31"},
  {"fact": "group_transfer_confirmed", "operator": "equals", "value": false}
]
```

## 修订理由
修订后的条件逐项对应原文中的受理日、当前版本有效区间和未满足团体转入例外。没有直接把 `allowed_values` 改为 Oracle 值；`allowed_values=62` 来自当前有效条款的一般规则。
