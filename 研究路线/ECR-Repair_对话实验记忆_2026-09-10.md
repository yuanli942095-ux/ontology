# ECR-Repair 对话与实验记忆

更新时间：2026-09-10  
项目目录：`G:\LearnAI\ontology-evolution`

## 1. 研究边界

研究目标是面向版本化规范文档完成本体语义漂移识别、证据约束决策和 OWL 修复闭环。完整路线为：

```text
规范文档变化
→ 候选无关证据定位
→ 候选盲语义抽取
→ 适用性与冲突判断
→ REPAIR / NO_CHANGE / ABSTAIN
→ 有限候选构造与决策
→ Repair IR
→ Gamma
→ OWL 原子修改
→ Reasoner
→ Semantic Diff / CQ Regression
→ 专家确认
```

Hard Gate 只能作为 policy-available symbolic upper bound 或方法组件，不能被描述为全自动主方法。人工 formal policy、人工 fact values 和人工候选描述的成本必须单独报告。

## 2. 冻结方法与基准

### V2.4

- 方法 ID：`ECR-REPAIR-V2.4-FINAL`
- 目录：`method/final-v24`
- 状态：冻结最终方法。
- 原 300×5 结果：SES 92.53%，WRR 3.40%，strict event accuracy 90.33%。
- 该方法要求 `dimension=slug` literal，和后续 80 事件自然语言 literal 契约不兼容。

### V2.5

- 方法 ID：`ECR-REPAIR-V2.5-NATURAL-LITERAL-ADAPTER`
- 目录：`method/final-v25-natural-literal`
- 状态：冻结的 post-hoc adapter，不是 confirmatory 方法。
- 作用：保留 V2.4 grounding 后的自然语言 replacement literal。

### 80 事件基准

- 目录：`benchmark/ecr-repair-post-freeze-blind-v1`
- 已冻结事件：80。
- 分区：REPAIR 56 / NO_CHANGE 8 / INSUFFICIENT_EVIDENCE 11 / CONFLICTING_EVIDENCE 5。
- 冻结 manifest SHA256：`18dcf4e00d64f977c42955ed224e352f1e17451e126bb5108d853bdff538a333`。
- E055、E056、E063 经 ANN_A、ANN_B、ADJ_C 一致裁定为 INSUFFICIENT，不得为了配额改成 CONFLICTING。
- E081-E083 自然冲突追加候选未通过质量复审，不纳入冻结 80 事件。
- V2.6 的设计使用了该 80 事件的失败信息，因此后续所有 V2.6 结果只能称为 development regression，不能称为 blind 或 confirmatory。

## 3. 80 事件 V2.5 结果

V2.5 自然 literal post-hoc 结果：

- ALL SES：85/400 = 21.25%。
- strict event：15/80 = 18.75%。
- REPAIR：51/280 = 18.21%。
- SAFETY：34/120 = 28.33%。
- WRR：215/400 = 53.75%。

自动失败归因：

- SUCCESS：85。
- REPLACEMENT_VALUE_ERROR：144。
- GROUNDING_AMBIGUOUS：85。
- SAFETY_UNSAFE_REPAIR：71。
- SAFETY_DECISION_CONFUSION：15。
- 当上游 IR 精确且 grounding 成功后，没有残余 Gamma/Reasoner/CQ 系统性故障。

144 个 replacement mismatch 经双人盲审和第三人裁决：

- strict equivalent：21/37 对，按调用加权 78/144。
- evidence-supported preferred：23/37 对，按调用加权 88/144。
- exact SES 仍为主指标 21.25%。
- strict semantic sensitivity：40.75%。
- broad preferred-supported sensitivity：43.25%。
- 这些都是 post-hoc sensitivity，不能替代 exact primary result。

## 4. V2.6 开发路线与实验

### 4.1 前端基础实现

文件：

- `src/ecr_repair_v26.py`
- `src/ecr_repair_v26_prompt.py`
- `src/run_ecr_repair_v26_development.py`
- `method/v26-development/semantic-frame.schema.json`

实现：

- candidate-independent Evidence Locator。
- candidate-blind Semantic Frame。
- 精确逐字 grounding 与字符偏移。
- 冻结来源元数据确定性注入。
- 保守 applicability/conflict Gate。
- Gate 后才允许候选构造。

80×1 第一轮结果：

- SES：17/80 = 21.25%。
- REPAIR：3/56 = 5.36%。
- SAFETY：14/24 = 58.33%。
- WRR：28/80 = 35.00%。

阶段互斥归因：

- 成功：17。
- Frame 未抽到正确规则：6。
- Target-frame alignment：5。
- 时间/条件/例外/范围比较：24。
- Frame value 与 Gold literal 不一致：22。
- Safety Gate 误修：6。
- Gamma/Reasoner/CQ 独立失败：0。

结论：问题集中在 OWL 前端，不应重写 Gamma/Reasoner/CQ。

### 4.2 独立有限候选 + LLM Evidence Entailment Ranking

文件：

- `src/ecr_repair_v26_candidate_prompt.py`
- `src/run_ecr_repair_v26_hybrid_development.py`

原则：

- 候选构造不读取 semantic frames、Gate、旧候选表或 Oracle。
- 80 个事件统一生成候选，避免候选存在性泄漏。
- 只有 Gate 输出 REPAIR 后才展示候选。
- Ranking 只能返回 candidate ID，不能自由生成新值。

结果：

- SES：26.25%。
- REPAIR：6/56。
- WRR：25.00%。
- Safety：62.50%。

候选覆盖：

- 56 个 REPAIR 中，精确包含 Gold literal：15。
- Gate 通过且候选包含 Gold：10。
- 正确候选存在但 Ranking 未选中：3。

该版本是当前 SES 最好的 V2.6 开发变体，但仍远未达到冻结条件。

### 4.3 Slot-based candidate compiler

候选槽位包括：

```text
core_value, modality, polarity, numeric_value, unit,
conditions, exceptions, enumeration_members, scope_complete_value
```

结果：

- Gold candidate coverage：20/56 = 35.71%。
- SES：23.75%。
- REPAIR：3/56。
- WRR：23.75%。
- Safety：66.67%。

结论：候选召回提高，但候选变多后 Ranking 更容易弃权或选错。

### 4.4 Canonical-safe compiler

增加逐字、canonical、scope-complete、枚举、数值单位、模态、条件和例外候选，最多 32 个。

- 候选总覆盖：23/56。
- 保守 Gate 后有效覆盖：17。
- SES：25.00%。
- REPAIR：4/56。
- WRR：22.50%。
- Safety：66.67%。

实验性 Gate v3 将 REPAIR 放行从 35/56 提高到 40/56，但安全误放行达到 11/24，因此被否决。默认 Gate 已恢复为保守 fail-closed 版本。

### 4.5 Unified Tuple

文件：

- `src/ecr_repair_v26_tuple.py`
- `src/run_ecr_repair_v26_tuple_development.py`

统一 tuple：

```text
core, modality, polarity, numeric_value, unit,
conditions, exceptions, enumeration,
valid_from, valid_to, jurisdiction
```

结果：

- SES：22.50%。
- REPAIR：2/56。
- Safety：16/24 = 66.67%。
- WRR：12.50%。
- CONFLICTING：5/5。

结论：安全性改善，但修复召回严重下降。

### 4.6 Canonical Tuple Mapper

文件：

- `src/ecr_repair_v26_canonical_mapper.py`
- `tests/test_ecr_repair_v26_canonical_mapper.py`

结果：

- SES：20.00%。
- REPAIR：2/56。
- Safety：14/24。
- WRR：21.25%。

全局词法 alias 和 predicate label 锚点不足以形成真正的 value ontology，且错误合并降低安全性。该变体被否决。

### 4.7 Generic Predicate Value Ontology / Typed Reasoner

文件：

- `method/v26-development/predicate-value-ontology-v1.json`
- `src/ecr_repair_v26_typed_reasoner.py`
- `tests/test_ecr_repair_v26_typed_reasoner.py`

通用类型：

```text
ENUMERATION, BOOLEAN, NUMERIC_THRESHOLD, TEMPORAL_VALUE,
MODAL_REQUIREMENT, CONDITIONAL_RULE, RULE_WITH_EXCEPTION,
SCOPE_RESTRICTION, COMPOSITE_PROPERTY
```

结果：

- SES：16/80 = 20.00%。
- REPAIR：1/56 = 1.79%。
- Safety：15/24 = 62.50%。
- WRR：7/80 = 8.75%。
- Coverage：8/80 = 10.00%。
- INSUFFICIENT：10/11。
- CONFLICTING：4/5。

结论：通用 schema 显著降低误修，但在自由 Semantic Frame 上追加严格必需槽位会造成过度弃权。正确顺序应是先识别 predicate type，再使用类型专属 prompt 抽取必需槽位。

## 5. 当前主要科学问题

核心问题不是 OWL 后端，而是：

> 自然语言证据无法稳定映射到 ontology predicate 所要求的标准语义值。

具体短板：

1. 缺少可迁移的 predicate value ontology。
2. Semantic Frame 与 candidate slots 不在同一稳定语义空间。
3. Gate 难以区分时间替代、一般规则/例外、不同人群、跨句互补和真实冲突。
4. 候选精确 literal 覆盖率低。
5. Ranking 面对多个近义自然语言候选时不稳定。
6. 当前 ontology literal 也没有稳定解析为相同 tuple。

## 6. 已否决的做法

- 不允许按 event ID 或 Gold literal 编写 schema/rules。
- 不允许修改冻结 80 事件 Gold 适配模型输出。
- 不允许把 V2.5 或 V2.6 post-hoc/development 结果称为 confirmatory。
- 不允许通过放松 Gate 或 Ranking 阈值掩盖安全性退化。
- 不采用实验性 Gate v3。
- 不采用仅依靠全局词法 alias 的 Canonical Mapper。
- 不继续扩大字符串候选池作为主要解决方案。

## 7. 下一步建议

下一步方法顺序应调整为：

```text
Predicate Type Detection
→ Type-specific Semantic Frame Extraction
→ Slot Completeness Validation
→ Typed Applicability/Conflict Reasoning
→ Independent Typed Candidate Construction
→ Tuple Entailment Ranking
→ Deterministic Literal Compiler
→ Repair IR → Gamma → OWL → Reasoner → CQ
```

实施要求：

1. 类型检测只使用公开 predicate label、当前 ontology schema 和文档证据。
2. 每个通用类型使用不同的 extraction schema/prompt。
3. 缺少必需槽位时允许一次受控补抽，不允许无限重试。
4. 条件、例外、时间、辖区必须使用受控 concept IDs，而不是自由字符串。
5. 当前 ontology literal 必须解析为同类 typed tuple。
6. 先在开发事件上验证 slot completeness、Gate safety、candidate coverage，再测 SES。
7. 方法定型后冻结代码、prompt、schema、阈值和 manifest。
8. 最终结论必须在一批全新、未参与开发的 holdout 上确认。

## 8. 当前最佳开发结果与论文边界

- 当前最高 V2.6 SES：独立自由候选 + Evidence Entailment Ranking，26.25%。
- 当前最低 WRR：Generic Typed Schema，8.75%，但 REPAIR 仅 1/56。
- 尚不存在同时具备可接受修复召回和安全性的可冻结 V2.6。
- 当前结果支持讨论安全性/覆盖率权衡和失败机制，不支持宣称新方法已经解决自然文档到 OWL 自动修复问题。

## 9. 安全与凭据

- 对话中曾出现 API Key；任何报告、记忆和代码中均不得记录或复述该密钥。
- 凭据只允许保存在本机 `.env`，不得提交版本控制。

