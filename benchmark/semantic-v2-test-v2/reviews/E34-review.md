# E34 一致性复核记录

复核人员：Codex independent rule review，2026-08-21。未读取、运行或根据 Qwen 输出修改答案。

## 原始证据原句
- DOC_E34_TERMS：仅投保产品A基础保障的，保险期限为12个月。
- DOC_E34_TERMS：投保方案选择长期照护附加责任时，保险期限调整为24个月。
- DOC_E34_TERMS：该调整不适用于同时选择费用垫付责任的方案；同时选择费用垫付责任的，保险期限按18个月处理。
- DOC_E34_TERMS：申请人在附加责任页勾选了长期照护附加责任。
- DOC_E34_TERMS：核保人员确认本次申请未选择费用垫付责任。
- DOC_E34_TERMS：保险期限月数应同时读取附加责任选择和否定确认后确定，不能只依据单一条款句子判断。

## 从证据提取的事实
- `long_term_care_addon_selected = true`，已选择长期照护附加责任。
- `expense_advance_selected = false`，明确未选择费用垫付责任。

## 规则复核
| rule_id | priority | 条件 | allowed_values |
|---|---:|---|---|
| E34_EXPENSE_ADVANCE_LIMIT | 300 | `long_term_care_addon_selected equals true`; `expense_advance_selected equals true` | `18` |
| E34_LONG_TERM_CARE_EXTENSION | 250 | `long_term_care_addon_selected equals true`; `expense_advance_selected equals false` | `24` |
| E34_BASIC_TERM | 100 | `long_term_care_addon_selected equals false` | `12` |

实际满足的规则：`E34_LONG_TERM_CARE_EXTENSION`。规则允许值：`24`。

## 候选集合
- `CAND_001`: `12`
- `CAND_002`: `18`
- `CAND_003`: `24`

Oracle值：`24`。五者是否一致：修订后文档证据、案件事实、形式规则、候选集合、Oracle 一致。

## 问题归属
问题属于规则。跨句肯定事实和否定事实都已写入 facts，但修订前嵌套 `when` 让 gate 无法按 `equals false` 解释否定条件，因此没有适用规则。

## 修订前内容
```json
"when": {
  "long_term_care_addon_selected": {"equals": true},
  "expense_advance_selected": {"equals": false}
}
```

## 修订后内容
```json
"conditions": [
  {"fact": "long_term_care_addon_selected", "operator": "equals", "value": true},
  {"fact": "expense_advance_selected", "operator": "equals", "value": false}
]
```

## 修订理由
修订后的条件分别来自投保记录和核保确认，保留了跨句作用域中的否定条件。`allowed_values=24` 来自长期照护附加责任条款，且费用垫付限制不满足。
