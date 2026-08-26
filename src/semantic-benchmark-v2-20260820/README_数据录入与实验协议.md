# 语义歧义本体修复基准 V2

## 1. 研究目标

在多个修复候选都满足有限算子、真实 IRI、最小修改和 OWL 一致性的前提下，
检验大语言模型能否根据文档版本、生效时间、例外条件和跨句适用范围选择正确
候选。该基准测量的是**受形式约束的语义选择能力**，不是让模型自由生成 OWL。

## 2. 数据分层

- `input/semantic-event-template.csv`：公开事件元数据，不得出现答案。
- `input/semantic-document-template.csv`：公开文档元数据；正文单独放入 `documents/`。
- `input/semantic-candidate-template.csv`：公开有限修复候选。
- `private/semantic-oracle-template.csv`：私有标准答案、证据范围和标注信息。
- `semantic-benchmark-v2-录入模板.xlsx`：便于人工录入的工作簿；运行脚本仍读取 CSV。

Oracle 文件不得放入模型提示词、候选排序输入、检索索引或开发日志。

## 3. 事件分布

| 事件范围 | 数量 | 类型 | 开发/测试 |
|---|---:|---|---|
| E12—E21 | 10 | `TEMPORAL_VERSION` | 2/8 |
| E22—E31 | 10 | `GENERAL_RULE_EXCEPTION` | 2/8 |
| E32—E41 | 10 | `CROSS_SENTENCE_SCOPE` | 2/8 |

开发集只用于修改提示词和调试；测试集冻结后不得据其结果调整提示词。

## 4. 录入顺序

1. 为事件填写真实或受控文档正文，并记录来源、权威等级、生效时间和哈希。
2. 填写案件上下文，但不得用“正确答案是……”等措辞泄漏 Oracle。
3. 从现有本体和有限算子中构造 2—4 个候选；不得凭空创建 IRI。
4. 执行构建器，确认每个候选都能应用、仅做最小修改且 Reasoner 一致。
5. 两名标注者独立选择 Oracle，并分别标注支持证据位置。
6. 不一致时由第三人裁决；只有 `agreement_status=AGREED/ADJUDICATED` 的事件才能
   进入最终测试集。
7. 运行校验器，冻结文件哈希后再执行模型实验。

## 5. CSV 字段说明

### Events

- `event_id`：全局唯一，例如 E12。
- `split`：`dev` 或 `test`。
- `semantic_type`：三个预定义语义类型之一。
- `case_context`：决定条款适用条件的案件信息。
- `value_kind`：`literal_integer`、`literal_string`、`iri` 或 `class`。
- `document_ids`：多个文档 ID 用英文竖线 `|` 分隔。
- `source_owl`：相对项目根目录的错误/缺失本体路径。
- `status`：录入完毕并复核后改为 `READY`。

### Candidates

- `candidate_id`：事件内唯一，建议使用 CAND_001 开始的连续编号。
- `display_value`：供模型和人工查看的候选值。
- `operation_json`：`repair_operators.py` 支持的单个有限算子 JSON。
- 每个事件只能有一个最小操作；多步修复应在后续基准单独研究。

示例：

```json
{
  "operator": "ADD_PROPERTY_VALUE",
  "subject_iri": "file:///G:/LearnAI/ontology-1#产品A",
  "predicate_iri": "file:///G:/LearnAI/ontology-1#等待期天数",
  "new_value": {
    "kind": "literal",
    "lexical": "0",
    "datatype": "http://www.w3.org/2001/XMLSchema#integer"
  }
}
```

### Oracle

- `oracle_candidate_id`：正确候选 ID。
- `evidence_document_ids`：支持答案的文档 ID，用 `|` 分隔。
- `evidence_spans_json`：证据位置 JSON，不复制大段全文。
- `agreement_status`：`AGREED`、`ADJUDICATED` 或 `PENDING`。
- `status`：最终可用时填写 `READY`。

## 6. 推荐命令

模板检查（允许 DRAFT）：

```powershell
python .\src\validate_semantic_benchmark_v2.py
```

为已有文档回填 SHA-256：

```powershell
python .\src\update_semantic_document_hashes.py
```

正式校验（要求至少 30 个 READY 事件）：

```powershell
python .\src\validate_semantic_benchmark_v2.py --require-ready --min-events 30
```

构建形式安全候选（依赖项目现有的 `validate_benchmark.py`、扩展版
`repair_operators.py`、`rdflib` 和 `owlready2`）：

```powershell
python .\src\build_semantic_benchmark_v2.py --min-events 30 --timeout 120
```

冻结后复核：

```powershell
python .\src\validate_semantic_benchmark_v2.py --require-ready --min-events 30
```

先跑开发集：

```powershell
python .\src\run_semantic_benchmark_v2.py --split dev --runs 5
```

提示词冻结后跑测试集：

```powershell
python .\src\run_semantic_benchmark_v2.py --split test --runs 5
```

也可以使用统一入口：

```powershell
powershell -ExecutionPolicy Bypass -File .\src\run_semantic_v2_pipeline.ps1 -Mode validate
powershell -ExecutionPolicy Bypass -File .\src\run_semantic_v2_pipeline.ps1 -Mode build
powershell -ExecutionPolicy Bypass -File .\src\run_semantic_v2_pipeline.ps1 -Mode dev -Runs 5
powershell -ExecutionPolicy Bypass -File .\src\run_semantic_v2_pipeline.ps1 -Mode test -Runs 5
```

重要：Excel 是人工录入界面，CSV 才是脚本的运行输入。填写 Excel 后需要把四个
工作表分别另存为对应 CSV，或直接维护包内 CSV；不要把私有 Oracle 表导出到
公开目录。

## 7. 论文报告边界

- 重复运行次数用于测量稳定性，不能替代独立事件数量。
- Oracle 准确率是离线评估指标，不是在线安全门禁。
- 当多个候选都形式安全时，Reasoner 无法证明语义正确；LLM 的作用是读取文档
  进行语义排序。
- 最终应同时报告严格格式成功率、Oracle 准确率、ABSTAIN、不可执行输出率、
  运行时间和按语义类型分组结果。
- 30 个事件模板只是研究设计骨架；真实文档、候选、证据位置和双人 Oracle 必须
  由研究者录入与复核，不能由脚本伪造。
