# E25 一致性复核记录

复核人员：Codex independent rule review，2026-08-21。未读取、运行或根据 Qwen 输出修改答案。

## 原始证据原句
- DOC_E25_TERMS：本约定适用于出生后90日内为被保险人投保产品A少儿计划的申请。
- DOC_E25_TERMS：不满足少儿新生儿例外条件的，疾病责任等待期为60天。
- DOC_E25_TERMS：出生后30日内完成投保且健康告知无异常的，疾病责任等待期按0天处理。
- DOC_E25_TERMS：出生后超过30日但不超过90日完成投保，且健康告知无异常、未有既往重大疾病记录的，疾病责任等待期按15天处理。
- DOC_E25_TERMS：本次申请在被保险人出生后第46日提交，健康告知无异常，未有既往重大疾病记录。
- DOC_E25_TERMS：等待期天数应先判断是否满足少儿新生儿例外，再判断是否满足延迟投保规则；不满足任一例外时适用一般规则。

## 从证据提取的事实
- `days_after_birth = 46`，申请在出生后第46日提交。
- `health_disclosure_abnormal = false`，健康告知无异常。
- `prior_major_disease_record = false`，无既往重大疾病记录。

## 规则复核
| rule_id | priority | 条件 | allowed_values |
|---|---:|---|---|
| E25_NEONATAL_EXCEPTION | 300 | `days_after_birth less_or_equal 30`; `health_disclosure_abnormal equals false` | `0` |
| E25_DELAYED_CHILD_EXCEPTION | 250 | `days_after_birth greater_than 30`; `days_after_birth less_or_equal 90`; `health_disclosure_abnormal equals false`; `prior_major_disease_record equals false` | `15` |
| E25_GENERAL_WAITING_PERIOD | 100 | 无条件一般规则 | `60` |

实际满足的规则：`E25_DELAYED_CHILD_EXCEPTION` 与 `E25_GENERAL_WAITING_PERIOD` 都满足；按最高优先级选择 `E25_DELAYED_CHILD_EXCEPTION`。规则允许值：`15`。

## 候选集合
- `CAND_001`: `15`
- `CAND_002`: `0`
- `CAND_003`: `60`

Oracle值：`15`。五者是否一致：修订后文档证据、案件事实、形式规则、候选集合、Oracle 一致。

## 问题归属
问题属于规则。特定人群、产品状态和例外条件事实已经由文档给出并写入 facts；修订前例外规则条件编码不符合 gate 的 `{fact, operator, value}` 格式，导致只触发一般规则。

## 修订前内容
```json
"when": {
  "days_after_birth": {
    "greater_than": 30,
    "less_or_equal": 90
  },
  "health_disclosure_abnormal": {"equals": false},
  "prior_major_disease_record": {"equals": false}
}
```

## 修订后内容
```json
"conditions": [
  {"fact": "days_after_birth", "operator": "greater_than", "value": 30},
  {"fact": "days_after_birth", "operator": "less_or_equal", "value": 90},
  {"fact": "health_disclosure_abnormal", "operator": "equals", "value": false},
  {"fact": "prior_major_disease_record", "operator": "equals", "value": false}
]
```

## 修订理由
第46日满足“超过30日但不超过90日”，同时健康告知无异常且无既往重大疾病记录；修订只改变条件表达方式，未改变文档语义或 Oracle。
