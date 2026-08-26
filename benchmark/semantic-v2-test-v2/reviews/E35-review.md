# E35 一致性复核记录

复核人员：Codex independent rule review，2026-08-21。未读取、运行或根据 Qwen 输出修改答案。

## 原始证据原句
- DOC_E35_TERMS：未选择任何门诊扩展责任的，保险期限为12个月。
- DOC_E35_TERMS：选择癌症门诊扩展责任时，保险期限调整为18个月。
- DOC_E35_TERMS：若同时选择癌症门诊扩展责任和境外就医责任，保险期限调整为24个月。
- DOC_E35_TERMS：上述延长期限仅在被保险人年龄不超过60周岁时适用；超过60周岁的，保险期限仍按基础保障处理。
- DOC_E35_TERMS：本次申请勾选了癌症门诊扩展责任。
- DOC_E35_TERMS：该被保险人年龄为58周岁，且未选择前款所称境外就医责任。
- DOC_E35_TERMS：保险期限月数应综合第三条、第四条、第五条及补充确认中的指代关系确定。

## 从证据提取的事实
- `cancer_outpatient_extension_selected = true`，已选择癌症门诊扩展责任。
- `overseas_medical_selected = false`，补充确认中的“前款所称境外就医责任”未选择。
- `insured_age = 58`，不超过60周岁，延长期限年龄条件满足。

## 规则复核
| rule_id | priority | 条件 | allowed_values |
|---|---:|---|---|
| E35_AGE_SCOPE_LIMIT | 400 | `insured_age greater_than 60` | `12` |
| E35_COMBINED_EXTENSION | 300 | `cancer_outpatient_extension_selected equals true`; `overseas_medical_selected equals true`; `insured_age less_or_equal 60` | `24` |
| E35_CANCER_OUTPATIENT_EXTENSION | 250 | `cancer_outpatient_extension_selected equals true`; `overseas_medical_selected equals false`; `insured_age less_or_equal 60` | `18` |
| E35_BASIC_TERM | 100 | `cancer_outpatient_extension_selected equals false` | `12` |

实际满足的规则：`E35_CANCER_OUTPATIENT_EXTENSION`。规则允许值：`18`。

## 候选集合
- `CAND_001`: `18`
- `CAND_002`: `12`
- `CAND_003`: `24`

Oracle值：`18`。五者是否一致：修订后文档证据、案件事实、形式规则、候选集合、Oracle 一致。

## 问题归属
问题属于规则。组合条件、附加责任状态、年龄条件和否定条件已完整形式化为 facts；修订前嵌套 `when` 未被 gate 解释为 `equals`、`less_or_equal` 等算子，因此没有适用规则。

## 修订前内容
```json
"when": {
  "cancer_outpatient_extension_selected": {"equals": true},
  "overseas_medical_selected": {"equals": false},
  "insured_age": {"less_or_equal": 60}
}
```

## 修订后内容
```json
"conditions": [
  {"fact": "cancer_outpatient_extension_selected", "operator": "equals", "value": true},
  {"fact": "overseas_medical_selected", "operator": "equals", "value": false},
  {"fact": "insured_age", "operator": "less_or_equal", "value": 60}
]
```

## 修订理由
修订后的条件分别对应门诊扩展选择、境外就医否定确认和年龄限制；不是把 `allowed_values` 改成 Oracle，而是让形式规则按原文可证事实正确触发。
