# Semantic-v2 实验总报告

> 方法论状态：本报告是 12-test 阶段的旧版闭环报告，且对
> `OPTION_FORMAL_POLICY_HARD_GATE` 的定位过强。30-test 扩展后请优先引用
> `output/semantic-v2-methodology-corrected-report.md`。修正版将
> `OPTION_FORMAL_POLICY` 定位为主 LLM-assisted 方法，将
> `OPTION_FORMAL_POLICY_HARD_GATE` 定位为 policy-available symbolic upper
> bound，并显式报告 formal-policy 人工结构化成本。

生成时间：2026-08-25  
项目目录：`G:\LearnAI\ontology-evolution`  
冻结测试集：`benchmark/semantic-v2/build/semantic-events.json` 的 `test` split  
测试事件数：12  
测试事件：E14, E15, E16, E17, E24, E25, E26, E27, E34, E35, E36, E37  
语义类型分布：TEMPORAL_VERSION 4 个，GENERAL_RULE_EXCEPTION 4 个，CROSS_SENTENCE_SCOPE 4 个  

本报告整理了当前 semantic-v2 benchmark 上已经完成的全部关键实验。整体结论是：`OPTION_FORMAL_POLICY_HARD_GATE` 在冻结 test set 上达到 100% Oracle accuracy、12/12 strict event success，并且通过无模型 baseline、顺序鲁棒性、描述扰动、跨语义类型、成本和错误案例分析形成闭环验证。

## 1. 实验目标与核心结论

本轮实验要回答的问题不是单纯“哪个方法分数最高”，而是要证明以下几点：

1. `OPTION_FORMAL_POLICY_HARD_GATE` 是否在冻结 test set 上稳定达到最高准确率。
2. 该结果是否不是由候选顺序、候选编号、随机命中造成。
3. 该结果是否不是依赖人工候选描述造成。
4. 该结果是否跨三类语义困难场景都成立。
5. 该结果是否比直接让 LLM 解释 formal policy 更稳定。
6. 该方法是否同时降低 Qwen 调用次数和 token 成本。
7. 失败案例是否能解释 hard gate 的必要性。

最终结论：

- `OPTION_FORMAL_POLICY_HARD_GATE`：100.00% Oracle accuracy，12/12 strict event success。
- `OPTION_FORMAL_POLICY_HARD_GATE`：候选顺序扰动下 12/12 严格鲁棒。
- `OPTION_FORMAL_POLICY_HARD_GATE`：三类语义类型均 100%。
- `OPTION_FORMAL_POLICY_HARD_GATE`：60/60 都是程序确定性选择，Qwen 调用 0 次，token 0。
- `OPTION_DESCRIPTION` 也达到 100%，但描述扰动实验显示它强依赖人工 description，因此应作为 description-aided upper bound，而不是公平 baseline。

## 2. 数据与运行设置

### 2.1 冻结测试集

最终主实验使用 `test` split，共 12 个事件：

| Semantic type | Events | Count |
|---|---|---:|
| TEMPORAL_VERSION | E14, E15, E16, E17 | 4 |
| GENERAL_RULE_EXCEPTION | E24, E25, E26, E27 | 4 |
| CROSS_SENTENCE_SCOPE | E34, E35, E36, E37 | 4 |

其中 E17、E27、E37 是后续强化出来的 code-mapping / exception / cross-sentence 边界事件，也是 Direct、Value-only、Formal-operation 失败最集中的事件。

### 2.2 统一运行参数

最终主表复现实验固定参数：

| Parameter | Value |
|---|---|
| split | test |
| events | 12 |
| runs | 5 |
| seed | 20260820 |
| model | qwen3.5:9b |
| temperature | 0.2 |
| attempts | 12 events x 5 runs x 7 methods = 420 |

无模型 baseline 使用：

| Parameter | Value |
|---|---|
| split | test |
| events | 12 |
| runs | 100 |
| seed | 20260820 |
| attempts | 12 events x 100 runs x 5 methods = 6000 |
| Qwen calls | 0 |

### 2.3 Oracle 隔离

各预测脚本都遵循同一原则：

- 预测阶段只读取公开事件、公开文档、候选信息、公开形式策略。
- 全部预测完成后才读取 private Oracle 进行离线评分。
- `OPTION_FORMAL_POLICY_HARD_GATE` 在执行阶段不读取人工候选 description，也不读取 Oracle。

这一点对论文非常重要：hard gate 的 100% 是 post-hoc Oracle scoring 得到的结果，不是预测时泄漏答案。

## 3. 方法定义

### 3.1 LLM / candidate information methods

| Method | 输入材料 | 是否调用 Qwen | 说明 |
|---|---|---:|---|
| DIRECT_FREE | 文档、case context、target；不提供候选集合 | 是 | 让模型自由生成值，生成后用候选值匹配 |
| VALUE_ENUM | 文档、case context、target；只在 JSON schema 中限定可输出值 | 是 | 不提供候选说明或候选编号 |
| OPTION_VALUE_ONLY | 文档、case context、target、随机化 option_id 和 display_value | 是 | 只给候选值 |
| OPTION_FORMAL_OPERATION | 文档、case context、target、候选真实形式操作 | 是 | 不提供人工 description，不执行策略 |
| OPTION_FORMAL_POLICY | 文档、候选形式操作、显式 facts、优先级 formal policy | 是 | 让 LLM 自己解释并应用 formal policy |
| OPTION_FORMAL_POLICY_HARD_GATE | 程序执行 facts + prioritized rules，过滤候选；唯一候选直接选 | 条件调用，本次为 0 | 本文正式方法 |
| OPTION_DESCRIPTION | 文档、候选值、formal_effect、人工候选 description | 是 | 高信息 upper bound |

### 3.2 无模型 baselines

| Method | 说明 |
|---|---|
| FIRST_POSITION | 选择打乱后的第一个候选，用于诊断顺序偏置 |
| RANDOM | 均匀随机选择候选 |
| HASH_FIRST | 对事件和候选 ID 做稳定哈希，选择哈希最小者 |
| LOWEST_ORIGINAL_ID | 选择原始 candidate_id 最小者，用于诊断编号偏置 |
| LATEST_AUTHORITY_UNIQUE | 选择最高 authority 且最新文档中唯一出现的候选值；不能唯一则 ABSTAIN |

## 4. 最终主表复现实验

### 4.1 实验目的

用冻结后的 12 个 test 事件统一重跑核心方法，固定 seed、runs、model、temperature，生成论文最终主结果表。

### 4.2 结果

| Method | Attempts | Oracle | Wrong | ABSTAIN | Strict event success | Mean runtime ms |
|---|---:|---:|---:|---:|---:|---:|
| OPTION_FORMAL_POLICY_HARD_GATE | 60 | 100.00% | 0.00% | 0.00% | 12/12 | 0.00 |
| OPTION_DESCRIPTION | 60 | 100.00% | 0.00% | 0.00% | 12/12 | 5028.08 |
| OPTION_FORMAL_POLICY | 60 | 96.67% | 3.33% | 0.00% | 10/12 | 4994.57 |
| OPTION_VALUE_ONLY | 60 | 76.67% | 15.00% | 8.33% | 9/12 | 5020.38 |
| DIRECT_FREE | 60 | 75.00% | 0.00% | 25.00% | 9/12 | 4419.95 |
| VALUE_ENUM | 60 | 73.33% | 0.00% | 26.67% | 8/12 | 4226.90 |
| OPTION_FORMAL_OPERATION | 60 | 68.33% | 16.67% | 15.00% | 7/12 | 5317.42 |

### 4.3 解释

`OPTION_FORMAL_POLICY_HARD_GATE` 与 `OPTION_DESCRIPTION` 都达到 100%，但两者性质不同：

- `OPTION_DESCRIPTION` 依赖人工候选 description，后续扰动实验证明它是 description-aided upper bound。
- `OPTION_FORMAL_POLICY_HARD_GATE` 不读取人工候选 description，不调用 Qwen，不读取 Oracle，而是通过程序执行 formal policy 得到唯一候选。

因此正式方法应强调 `OPTION_FORMAL_POLICY_HARD_GATE`，而不是把 `OPTION_DESCRIPTION` 作为公平 baseline。

主要输出：

- `output/final-main-table-test-r5-seed20260820-summary.csv`
- `output/final-main-table-test-r5-seed20260820-event-level.csv`
- `output/final-main-table-test-r5-seed20260820-by-event.csv`
- `output/final-main-table-test-r5-seed20260820-details.csv`
- `output/final-main-table-test-r5-seed20260820.json`

## 5. 无模型 baseline 实验

### 5.1 实验目的

排除候选顺序、候选编号、随机命中造成高分的可能性。

### 5.2 结果

| No-model baseline | Attempts | Oracle | Wrong | ABSTAIN |
|---|---:|---:|---:|---:|
| HASH_FIRST | 1200 | 41.67% | 58.33% | 0.00% |
| RANDOM | 1200 | 35.08% | 64.92% | 0.00% |
| FIRST_POSITION | 1200 | 34.83% | 65.17% | 0.00% |
| LOWEST_ORIGINAL_ID | 1200 | 16.67% | 83.33% | 0.00% |
| LATEST_AUTHORITY_UNIQUE | 1200 | 0.00% | 16.67% | 83.33% |

### 5.3 解释

无模型策略最高只有 41.67%，远低于 hard gate 的 100%。这说明 benchmark 没有被候选位置、候选编号或随机选择轻易破解。

`LATEST_AUTHORITY_UNIQUE` 基本失败，说明仅根据“最高 authority 最新文档中唯一出现值”这种浅层启发式不足以处理 temporal/version、exception 和 cross-sentence scope 的语义歧义。

主要输出：

- `output/final-no-model-baselines-test-r100-seed20260820-summary.csv`
- `output/final-no-model-baselines-test-r100-seed20260820-by-event.csv`
- `output/final-no-model-baselines-test-r100-seed20260820-details.csv`
- `output/final-no-model-baselines-test-r100-seed20260820-robustness-summary.csv`

## 6. 候选顺序鲁棒性实验

### 6.1 实验目的

验证模型或方法是否受候选排列顺序影响。主实验本身每个 run 都会 shuffle candidate order；此外针对敏感事件又做了 20-seed targeted 放大实验。

### 6.2 主实验 5-run 顺序鲁棒性

| Method | Strict robust events | Stable selected candidate events | Order-sensitive events | Accuracy | ABSTAIN |
|---|---:|---:|---:|---:|---:|
| OPTION_FORMAL_POLICY_HARD_GATE | 12/12 | 12/12 | 0/12 | 100.00% | 0.00% |
| OPTION_DESCRIPTION | 12/12 | 12/12 | 0/12 | 100.00% | 0.00% |
| OPTION_FORMAL_POLICY | 10/12 | 10/12 | 2/12 | 96.67% | 0.00% |
| OPTION_VALUE_ONLY | 9/12 | 9/12 | 2/12 | 76.67% | 8.33% |
| DIRECT_FREE | 9/12 | 9/12 | 0/12 | 75.00% | 25.00% |
| VALUE_ENUM | 8/12 | 9/12 | 1/12 | 73.33% | 26.67% |
| OPTION_FORMAL_OPERATION | 7/12 | 10/12 | 5/12 | 68.33% | 15.00% |

### 6.3 Targeted 20-seed 放大实验

针对上一轮中最敏感的边界事件进行 20-seed 放大。

| Method | Events | Attempts | Oracle | Wrong | ABSTAIN | Strict robust events | Order-sensitive events |
|---|---:|---:|---:|---:|---:|---:|---:|
| OPTION_FORMAL_POLICY_HARD_GATE | 2 | 40 | 100.00% | 0.00% | 0.00% | 2/2 | 0/2 |
| OPTION_FORMAL_POLICY | 2 | 40 | 72.50% | 27.50% | 0.00% | 0/2 | 2/2 |
| OPTION_FORMAL_OPERATION | 5 | 100 | 28.00% | 41.00% | 31.00% | 0/5 | 5/5 |
| OPTION_VALUE_ONLY | 2 | 40 | 30.00% | 70.00% | 0.00% | 0/2 | 2/2 |
| VALUE_ENUM | 1 | 20 | 25.00% | 0.00% | 75.00% | 0/1 | 1/1 |

### 6.4 解释

顺序鲁棒性实验的关键发现：

- hard gate 在主实验和 targeted 20-seed 中都保持 100%。
- LLM 自己解释 formal policy 在 E17/E27 上会出现错选，20-seed 下只有 72.50%。
- formal operation 输入不是足够可执行的信息，20-seed 下只有 28.00%，且错选和弃权混合出现。
- value-only 输入在 E17/E37 上会被数字表面值误导，20-seed 只有 30.00%。

这支持论文中的关键论点：将 formal policy 交给 LLM 解释仍有不稳定性；将 formal policy 转换为程序执行的 hard gate 能消除这种顺序/呈现敏感性。

主要输出：

- `output/final-main-table-test-r5-seed20260820-robustness-summary.csv`
- `output/order-robustness-targeted-20-summary.csv`
- `output/order-robustness-targeted-20-by-event.csv`
- `output/order-robustness-targeted-20.json`

## 7. 候选描述扰动实验

### 7.1 实验目的

检验 `OPTION_DESCRIPTION=100%` 是否来自人工候选 description 的帮助。如果遮蔽或错配 description 后性能大幅下降，则说明该方法不适合作为公平 baseline，只能作为 description-aided upper bound。

### 7.2 扰动方式

| Method | Description handling |
|---|---|
| OPTION_DESCRIPTION | 原始人工候选 description |
| OPTION_DESCRIPTION_MASKED | 将人工候选 description 替换为中性占位文本 |
| OPTION_DESCRIPTION_SHUFFLED | 在同一事件内错配候选 description |

### 7.3 结果

| Method | Attempts | Oracle | Wrong | ABSTAIN | Strict event success |
|---|---:|---:|---:|---:|---:|
| OPTION_DESCRIPTION | 60 | 100.00% | 0.00% | 0.00% | 12/12 |
| OPTION_DESCRIPTION_MASKED | 60 | 75.00% | 23.33% | 1.67% | 8/12 |
| OPTION_DESCRIPTION_SHUFFLED | 60 | 8.33% | 91.67% | 0.00% | 0/12 |

### 7.4 解释

描述扰动实验结论非常强：

- 原始 description 让模型 100% 命中。
- description masked 后掉到 75%，说明候选说明对困难样本很重要。
- description shuffled 后掉到 8.33%，且错选率 91.67%，说明模型会被错配说明系统性误导。

因此，`OPTION_DESCRIPTION` 应作为上界或 description-aided condition，而不能作为公平 baseline。正式比较应强调 `OPTION_FORMAL_POLICY_HARD_GATE` 在不使用人工 description 的情况下达到 100%。

主要输出：

- `output/description-perturbation-ablation-summary.csv`
- `output/description-perturbation-ablation-by-event.csv`
- `output/description-perturbation-ablation-details.csv`
- `output/description-perturbation-robustness-summary.csv`
- `output/description-perturbation-robustness-by-event.csv`

## 8. 运行成本与调用次数实验

### 8.1 实验目的

比较各方法的 Qwen 调用次数、直接决策次数、平均耗时和 token 消耗，说明 hard gate 不仅准确，而且成本最低。

### 8.2 结果

| Method | Oracle | Qwen calls | Direct decisions | Mean runtime ms | Prompt tokens | Eval tokens | Total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| OPTION_FORMAL_POLICY_HARD_GATE | 100.00% | 0/60 | 60/60 | 0.00 | 0 | 0 | 0 |
| OPTION_DESCRIPTION | 100.00% | 60/60 | 0/60 | 5028.08 | 61200 | 6874 | 68074 |
| OPTION_FORMAL_POLICY | 96.67% | 60/60 | 0/60 | 4994.57 | 103500 | 6328 | 109828 |
| OPTION_VALUE_ONLY | 76.67% | 60/60 | 0/60 | 5020.38 | 48830 | 6906 | 55736 |
| DIRECT_FREE | 75.00% | 60/60 | 0/60 | 4419.95 | 43090 | 5998 | 49088 |
| VALUE_ENUM | 73.33% | 60/60 | 0/60 | 4226.90 | 43330 | 5936 | 49266 |
| OPTION_FORMAL_OPERATION | 68.33% | 60/60 | 0/60 | 5317.42 | 77270 | 7084 | 84354 |

### 8.3 Hard gate 直接命中比例

`OPTION_FORMAL_POLICY_HARD_GATE` 的 60 条明细全部为：

```text
decision_path = DETERMINISTIC_SELECT
qwen_called = False
status = SELECTED
```

即在当前冻结 test set 中，hard gate 每次都能通过 formal policy 找到唯一候选，不需要 fallback 到 Qwen 排序。

### 8.4 解释

成本实验说明：

- `OPTION_FORMAL_POLICY` 给 LLM 完整 formal policy，token 最高：109828。
- `OPTION_DESCRIPTION` 也要 68074 tokens。
- `OPTION_FORMAL_POLICY_HARD_GATE` 达到同样 100% accuracy，但 Qwen calls 和 tokens 都为 0。

这支持论文中的效率论点：formal policy 不应仅作为提示文本交给 LLM 解释；当 policy 可执行时，应优先通过程序门禁完成确定性过滤。

主要输出：

- `output/final-main-table-test-r5-seed20260820-runtime-costs-summary.csv`
- `output/final-main-table-test-r5-seed20260820-runtime-costs.json`
- `output/final-all-methods-runtime-costs-summary.csv`

## 9. 按语义类型分组实验

### 9.1 实验目的

验证正式方法不是只对某一类事件有效，而是在三类语义困难场景上都成立。

### 9.2 结果

| Method | TEMPORAL_VERSION | GENERAL_RULE_EXCEPTION | CROSS_SENTENCE_SCOPE |
|---|---:|---:|---:|
| OPTION_FORMAL_POLICY_HARD_GATE | 100.00%, 4/4 | 100.00%, 4/4 | 100.00%, 4/4 |
| OPTION_DESCRIPTION | 100.00%, 4/4 | 100.00%, 4/4 | 100.00%, 4/4 |
| OPTION_FORMAL_POLICY | 95.00%, 3/4 | 95.00%, 3/4 | 100.00%, 4/4 |
| OPTION_VALUE_ONLY | 80.00%, 3/4 | 75.00%, 3/4 | 75.00%, 3/4 |
| DIRECT_FREE | 75.00%, 3/4 | 75.00%, 3/4 | 75.00%, 3/4 |
| VALUE_ENUM | 70.00%, 2/4 | 75.00%, 3/4 | 75.00%, 3/4 |
| OPTION_FORMAL_OPERATION | 70.00%, 2/4 | 75.00%, 3/4 | 60.00%, 2/4 |

### 9.3 解释

`OPTION_FORMAL_POLICY_HARD_GATE` 在三类事件上都是 100% 和 4/4 strict success：

- TEMPORAL_VERSION：正确处理版本时效和代码映射。
- GENERAL_RULE_EXCEPTION：正确处理一般规则与例外规则优先级。
- CROSS_SENTENCE_SCOPE：正确处理跨句作用域和条款限定。

这说明 hard gate 的收益不是局限在某一种语义类型上。

主要输出：

- `output/final-main-table-test-r5-seed20260820-semantic-type-summary.csv`
- `output/final-main-table-test-r5-seed20260820-semantic-type-pivot.csv`
- `output/final-main-table-test-r5-seed20260820-semantic-type.json`

## 10. 错误案例分析

### 10.1 实验目的

将 Direct、Value-only、Formal-operation、Formal-policy 的失败事件列出来，解释为什么 hard gate 必要。

### 10.2 重点失败事件

| Method | Event | Type | Success | Failure mode |
|---|---|---|---:|---|
| DIRECT_FREE | E17 | TEMPORAL_VERSION | 0/5 | free generation lacks candidate-level mapping; model abstains |
| DIRECT_FREE | E27 | GENERAL_RULE_EXCEPTION | 0/5 | free generation lacks candidate-level mapping; model abstains |
| DIRECT_FREE | E37 | CROSS_SENTENCE_SCOPE | 0/5 | free generation lacks candidate-level mapping; model abstains |
| OPTION_VALUE_ONLY | E17 | TEMPORAL_VERSION | 1/5 | value-only options cause unstable numeric choice |
| OPTION_VALUE_ONLY | E27 | GENERAL_RULE_EXCEPTION | 0/5 | candidate values alone do not identify applicable rule |
| OPTION_VALUE_ONLY | E37 | CROSS_SENTENCE_SCOPE | 0/5 | value-only options cause unstable numeric choice |
| OPTION_FORMAL_OPERATION | E17 | TEMPORAL_VERSION | 1/5 | formal operation is insufficient; model abstains |
| OPTION_FORMAL_OPERATION | E27 | GENERAL_RULE_EXCEPTION | 0/5 | formal operation is insufficient; mixed wrong choices and abstains |
| OPTION_FORMAL_OPERATION | E37 | CROSS_SENTENCE_SCOPE | 0/5 | formal operation is insufficient; mixed wrong choices and abstains |
| OPTION_FORMAL_POLICY | E17 | TEMPORAL_VERSION | 4/5 | LLM policy interpretation is not fully stable; hard execution is needed |
| OPTION_FORMAL_POLICY | E27 | GENERAL_RULE_EXCEPTION | 4/5 | LLM policy interpretation is not fully stable; hard execution is needed |

### 10.3 失败机制解释

Direct failure:

- 在 E17/E27/E37 中 Direct 全部 ABSTAIN。
- 这说明模型即使读到文档，也不一定能在没有候选映射信息时直接给出正确值。

Value-only failure:

- E17 中会被 58 或 73 等表面数值误导。
- E37 中会在 12/24 等候选值之间错选。
- E27 则因为仅凭候选值无法识别例外规则，稳定 ABSTAIN。

Formal-operation failure:

- 真实形式操作本身不等于可执行策略。
- E27 经常错选一般规则值 0，说明模型看到操作也不一定能应用例外优先级。
- E37 多数 ABSTAIN，说明跨句作用域需要明确策略执行，而不是只给操作字段。

Formal-policy failure:

- `OPTION_FORMAL_POLICY` 已经非常强，但在 E17/E27 仍各有一次错选。
- 说明“把 formal policy 作为文本/结构交给 LLM 解释”仍有残余不稳定。
- hard gate 将 policy 变成程序执行，消除了这类错选。

主要输出：

- `output/final-main-table-test-r5-seed20260820-failure-cases-summary.csv`
- `output/final-main-table-test-r5-seed20260820-failure-cases-failed-runs.csv`
- `output/final-main-table-test-r5-seed20260820-failure-cases.md`
- `output/final-main-table-test-r5-seed20260820-failure-cases.json`

## 11. 实验闭环关系

当前实验不是孤立结果，而是互相验证的闭环：

1. 最终主表证明 hard gate 在冻结 test set 上 100%。
2. 消融实验说明 Direct、Value enum、Value only、Formal operation、LLM formal policy 都低于 hard gate。
3. 无模型 baseline 排除候选顺序、编号和随机命中解释。
4. 顺序鲁棒性实验说明 hard gate 对候选顺序稳定，而 LLM formal policy 和 formal operation 存在顺序/呈现敏感性。
5. 描述扰动实验说明 `OPTION_DESCRIPTION=100%` 依赖人工候选说明，应作为 upper bound。
6. 成本实验说明 hard gate 不调用 Qwen，token 成本为 0。
7. 按语义类型分组说明 hard gate 不只适用于某一类事件。
8. 错误案例分析说明 hard gate 解决的是具体失败机制：候选映射、例外规则、跨句作用域和策略执行稳定性。

因此，最终论文可以主张：

> Executable formal-policy gating achieves perfect oracle accuracy on the frozen semantic-v2 test set while eliminating model calls in all evaluated cases. The gain cannot be explained by candidate order, candidate identifier bias, random choice, or human-written candidate descriptions. Failure analysis shows that direct generation, value-only options, and formal-operation prompts fail on code-mapping, exception, and cross-sentence-scope cases, while LLM interpretation of formal policy remains slightly unstable. Hard-gate execution removes this residual instability.

## 12. 论文建议表格组织

建议论文实验部分至少放 5 张表：

1. Main result table：使用 `final-main-table-test-r5-seed20260820-summary.csv`。
2. No-model baseline table：使用 `final-no-model-baselines-test-r100-seed20260820-summary.csv`。
3. Robustness table：使用 `final-main-table-test-r5-seed20260820-robustness-summary.csv` 和 `order-robustness-targeted-20-summary.csv`。
4. Description perturbation table：使用 `description-perturbation-ablation-summary.csv`。
5. Runtime/cost table：使用 `final-main-table-test-r5-seed20260820-runtime-costs-summary.csv`。

附录可放：

1. Semantic type grouped table：`final-main-table-test-r5-seed20260820-semantic-type-pivot.csv`。
2. Failure case table：`final-main-table-test-r5-seed20260820-failure-cases-summary.csv`。
3. Per-run details：各 `details.csv` 和 JSON。

## 13. 关键文件索引

### Scripts

- `src/run_candidate_information_ablation.py`
- `src/run_baseline_direct.py`
- `src/run_no_model_baselines_semantic_v2.py`
- `src/analyze_order_robustness.py`
- `src/analyze_runtime_costs.py`
- `src/analyze_semantic_type_groups.py`
- `src/analyze_failure_cases.py`
- `src/validate_formal_policy_gate.py`

### Main result outputs

- `output/final-main-table-test-r5-seed20260820-details.csv`
- `output/final-main-table-test-r5-seed20260820-summary.csv`
- `output/final-main-table-test-r5-seed20260820-by-event.csv`
- `output/final-main-table-test-r5-seed20260820-event-level.csv`
- `output/final-main-table-test-r5-seed20260820.json`

### No-model baseline outputs

- `output/final-no-model-baselines-test-r100-seed20260820-details.csv`
- `output/final-no-model-baselines-test-r100-seed20260820-summary.csv`
- `output/final-no-model-baselines-test-r100-seed20260820-by-event.csv`
- `output/final-no-model-baselines-test-r100-seed20260820.json`

### Robustness outputs

- `output/final-main-table-test-r5-seed20260820-robustness-summary.csv`
- `output/final-main-table-test-r5-seed20260820-robustness-by-event.csv`
- `output/order-robustness-targeted-20-summary.csv`
- `output/order-robustness-targeted-20-by-event.csv`
- `output/order-robustness-targeted-20.json`

### Description perturbation outputs

- `output/description-perturbation-ablation-details.csv`
- `output/description-perturbation-ablation-summary.csv`
- `output/description-perturbation-ablation-by-event.csv`
- `output/description-perturbation-ablation-event-level.csv`
- `output/description-perturbation-ablation.json`
- `output/description-perturbation-robustness-summary.csv`
- `output/description-perturbation-robustness-by-event.csv`

### Runtime and cost outputs

- `output/final-main-table-test-r5-seed20260820-runtime-costs-summary.csv`
- `output/final-main-table-test-r5-seed20260820-runtime-costs.json`
- `output/final-all-methods-runtime-costs-summary.csv`
- `output/final-all-methods-runtime-costs.json`

### Semantic type outputs

- `output/final-main-table-test-r5-seed20260820-semantic-type-summary.csv`
- `output/final-main-table-test-r5-seed20260820-semantic-type-pivot.csv`
- `output/final-main-table-test-r5-seed20260820-semantic-type.json`

### Failure case outputs

- `output/final-main-table-test-r5-seed20260820-failure-cases-summary.csv`
- `output/final-main-table-test-r5-seed20260820-failure-cases-failed-runs.csv`
- `output/final-main-table-test-r5-seed20260820-failure-cases.md`
- `output/final-main-table-test-r5-seed20260820-failure-cases.json`

## 14. 最终可写入论文的精简结论

可以直接写成如下实验结论：

> On the frozen semantic-v2 test set of 12 events spanning temporal-version, general-rule exception, and cross-sentence-scope ambiguities, executable formal-policy hard gating achieves 100% oracle accuracy and 12/12 strict event success. Unlike description-aided candidate selection, its performance does not rely on human-written candidate descriptions; masking descriptions reduces the description-aided method to 75%, and shuffling descriptions reduces it to 8.33%. Unlike direct generation and value-only baselines, it succeeds on code-mapping and exception cases where models abstain or select surface values. Unlike LLM interpretation of formal policy, it remains order-robust on targeted 20-seed perturbations. Finally, hard gating reduces Qwen calls from 60/60 to 0/60 and token usage to zero on the evaluated test set.
