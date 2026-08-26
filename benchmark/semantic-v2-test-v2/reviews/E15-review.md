# E15 一致性复核记录

复核人员：Codex independent rule review，2026-08-21。未读取、运行或根据 Qwen 输出修改答案。

## 原始证据原句
- DOC_E15_JULY：生效日期：2026年7月1日；失效日期：2026年7月31日。
- DOC_E15_JULY：本条款适用于2026年7月1日至2026年7月31日期间发生投保申请并进入核保流程的产品A精选版。
- DOC_E15_JULY：在本版本有效期间，产品A精选版的最高投保年龄为64周岁。
- DOC_E15_JULY：最高投保年龄以申请进入核保流程日期为版本判断日期，后续人工评估日期不改变该日期。
- DOC_E15_AUGUST：发布日期：2026年7月15日；生效日期：2026年8月1日。
- DOC_E15_AUGUST：2026年8月1日前已经进入核保流程的申请，即使在8月以后人工评估，仍按进入核保流程当日有效版本处理。
- case_context：投保申请于2026年7月20日进入核保流程，人工评估日期为2026年8月21日。

## 从证据提取的事实
- `underwriting_entry_date = "2026-07-20"`，版本选择日期是申请进入核保流程日期。
- `manual_assessment_date = "2026-08-21"`，后续评估日不改变适用版本。
- `entered_after_expiry = false`，申请不是在7月版本失效后才进入核保流程。

## 规则复核
| rule_id | priority | 条件 | allowed_values |
|---|---:|---|---|
| E15_JULY_VERSION_SCOPE | 300 | `underwriting_entry_date on_or_after 2026-07-01`; `underwriting_entry_date on_or_before 2026-07-31`; `entered_after_expiry equals false` | `64` |
| E15_AUGUST_VERSION_SCOPE | 200 | `underwriting_entry_date on_or_after 2026-08-01` | `68` |
| E15_PRIOR_PRODUCT_LIMIT | 100 | `underwriting_entry_date on_or_before 2026-06-30` | `66` |

实际满足的规则：`E15_JULY_VERSION_SCOPE`。规则允许值：`64`。

## 候选集合
- `CAND_001`: `68`
- `CAND_002`: `66`
- `CAND_003`: `64`

Oracle值：`64`。五者是否一致：修订后文档证据、案件事实、形式规则、候选集合、Oracle 一致。

## 问题归属
问题属于规则。事实中已经写入用于版本选择的 `underwriting_entry_date`，但修订前规则使用嵌套 `when`，现有 gate 不会把其中的 `on_or_after` 和 `on_or_before` 解释为日期算子。

## 修订前内容
```json
"when": {
  "underwriting_entry_date": {
    "on_or_after": "2026-07-01",
    "on_or_before": "2026-07-31"
  },
  "entered_after_expiry": {
    "equals": false
  }
}
```

## 修订后内容
```json
"conditions": [
  {"fact": "underwriting_entry_date", "operator": "on_or_after", "value": "2026-07-01"},
  {"fact": "underwriting_entry_date", "operator": "on_or_before", "value": "2026-07-31"},
  {"fact": "entered_after_expiry", "operator": "equals", "value": false}
]
```

## 修订理由
修订后的事实和规则明确使用“申请进入核保流程日期”而非评估日、承保日或事故发生日。`allowed_values=64` 来自7月版本条款，条件来自原文日期区间和案件事实。
