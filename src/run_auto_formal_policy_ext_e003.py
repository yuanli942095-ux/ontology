import json
import urllib.request
from pathlib import Path


# ============================================================
# 1. 基本配置
# ============================================================

ROOT = Path(r"G:\LearnAI\ontology-evolution")

MODEL = "qwen3.5:9b"

OLLAMA_URL = "http://localhost:11434/api/generate"

EVENT_ID = "EXT_E003"


# ============================================================
# 2. 公开 evidence 文件
# ============================================================

EVIDENCE_FILE = (
        ROOT
        / "benchmark"
        / "external-real-v1"
        / "documents"
        / "excerpts"
        / "EXT_E003-evidence.md"
)

if not EVIDENCE_FILE.exists():
    raise FileNotFoundError(
        f"Public evidence file not found: {EVIDENCE_FILE}"
    )

public_evidence = EVIDENCE_FILE.read_text(
    encoding="utf-8"
)


# ============================================================
# 3. 构造公开事件上下文
# ============================================================

EVENT_CONTEXT = f"""
事件编号：
{EVENT_ID}

语义类型：
GENERAL_RULE_EXCEPTION

任务：
评估北京市2026年政策性农业保险统颁参考条款生效后，
梨种植保险中冻害损失赔偿公式应如何进行语义表示。

下面内容来自 public benchmark evidence excerpt。

重要限制：

1. 只能使用下面的公开材料。
2. 不得读取或引用 private Oracle。
3. 不得读取候选答案。
4. 不得读取人工 formal-policy.json。
5. 不得使用 CAND_001、CAND_002、CAND_003 等候选编号。
6. 如果公开证据不足，应明确 abstain。

===== PUBLIC EVIDENCE BEGIN =====

{public_evidence}

===== PUBLIC EVIDENCE END =====
""".strip()


# ============================================================
# 4. Prompt
# ============================================================

PROMPT = f"""
你是一名本体工程与规范文档语义演化分析助手。

你的任务是：

根据公开规范文档证据，
自动构造一个用于后续本体修复决策的形式化语义策略。

这不是候选选择任务。

你不能：

- 使用 private Oracle
- 使用 Gold Answer
- 使用 candidate_id
- 使用 CAND_001 / CAND_002 / CAND_003
- 使用人工 formal-policy.json
- 根据候选值反推答案

你只能根据提供的公开证据进行语义抽取。

请完成以下三项工作：

1. 抽取关键 facts
2. 识别规则适用条件
3. 给出规范文档要求的 semantic_result

请严格输出 JSON。

输出格式：

{{
  "facts": {{
    "source_revision_year": "...",
    "target_product": "...",
    "damage_type": "...",
    "formula_basis": "..."
  }},
  "rules": [
    {{
      "priority": 300,
      "conditions": [
        {{
          "fact": "...",
          "operator": "...",
          "value": "..."
        }}
      ],
      "semantic_result": "..."
    }}
  ]
}}

字段定义：

- source_revision_year：
  表示当前应采用的规范版本年份。

- target_product：
  表示目标保险产品或规范对象。

- damage_type：
  表示损害类型、事故类型或适用情形。

- formula_basis：
  只表示“计算公式所依据的基准量、基准值或计算基础”，
  不表示完整计算公式。

  formula_basis 中不得包含乘法、除法、加法、减法、
  损失率、阈值或其他运算步骤。

  例如，如果公开文档规定：

  “亩赔偿金额 = 出险日期亩赔偿限额 × 损失率”

  那么：

  formula_basis 应表示：
  “出险日期亩赔偿限额”

  而不是：
  “出险日期亩赔偿限额 × 损失率”

- semantic_result：
  表示完整规则最终产生的语义结果。

  semantic_result 可以包含计算关系、条件和运算。

因此必须严格区分：

formula_basis = 计算依据

semantic_result = 完整语义规则结果

其他要求：

- 可以增加公开证据明确支持的必要条件，例如损失率阈值。
- 不得编造公开证据中没有出现的规则。
- 不得输出 allowed_values。
- 不得输出 candidate_id。
- 不得输出任何 Oracle 信息。
- 不得根据任何候选答案反推结果。

如果证据不足以完成任务，请只输出：

{{
  "abstain": true,
  "reason": "证据不足的原因"
}}

公开事件材料：

{EVENT_CONTEXT}
""".strip()






# ============================================================
# 5. Ollama 请求
# ============================================================

payload = {
    "model": MODEL,
    "prompt": PROMPT,
    "stream": False,
    "think": False,
    "options": {
        "temperature": 0.2,
        "num_predict": 800,
    },
}

request_data = json.dumps(
    payload,
    ensure_ascii=False
).encode("utf-8")

req = urllib.request.Request(
    OLLAMA_URL,
    data=request_data,
    headers={
        "Content-Type": "application/json"
    },
    method="POST",
)


# ============================================================
# 6. 调用模型
# ============================================================

try:

    with urllib.request.urlopen(
            req,
            timeout=180
    ) as response:

        raw_response = response.read().decode(
            "utf-8"
        )

        data = json.loads(
            raw_response
        )

except Exception as e:

    raise RuntimeError(
        f"Ollama request failed: {e}"
    ) from e


# ============================================================
# 7. 提取最终 response
# ============================================================

text = str(
    data.get(
        "response",
        ""
    )
).strip()

done_reason = data.get(
    "done_reason",
    ""
)

if not text:

    print("=" * 80)
    print("RAW OLLAMA RESPONSE")
    print("=" * 80)

    print(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        )
    )

    raise RuntimeError(
        "Ollama returned empty response. "
        f"done_reason={done_reason}"
    )


# ============================================================
# 8. 尝试解析 JSON
# ============================================================

parsed_json = None

try:

    parsed_json = json.loads(
        text
    )

except json.JSONDecodeError:

    # 如果模型偶尔加 ```json
    cleaned = text.strip()

    if cleaned.startswith("```json"):
        cleaned = cleaned[len("```json"):]

    elif cleaned.startswith("```"):
        cleaned = cleaned[len("```"):]

    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]

    cleaned = cleaned.strip()

    try:
        parsed_json = json.loads(
            cleaned
        )

    except json.JSONDecodeError:
        parsed_json = None


# ============================================================
# 9. 防泄漏检查
# ============================================================

forbidden_markers = [
    "oracle",
    "gold",
    "cand_001",
    "cand_002",
    "cand_003",
    "candidate_id",
    "allowed_values",
]

lower_text = text.lower()

for marker in forbidden_markers:

    if marker in lower_text:

        raise RuntimeError(
            f"Forbidden marker detected in model output: {marker}"
        )


# ============================================================
# 10. 输出结果
# ============================================================

print("=" * 80)
print(
    "AUTO FORMAL POLICY FROM PUBLIC EVIDENCE - EXT_E003"
)
print("=" * 80)

if parsed_json is not None:

    print(
        json.dumps(
            parsed_json,
            ensure_ascii=False,
            indent=2
        )
    )

else:

    print(text)


# ============================================================
# 11. 保存结果
# ============================================================

OUTPUT_DIR = ROOT / "output"

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)

OUTPUT_TXT = (
        OUTPUT_DIR
        / "auto-formal-policy-ext-e003-from-public-evidence.txt"
)

OUTPUT_JSON = (
        OUTPUT_DIR
        / "auto-formal-policy-ext-e003-from-public-evidence.json"
)

OUTPUT_TXT.write_text(
    text,
    encoding="utf-8"
)

result_record = {
    "event_id": EVENT_ID,
    "model": MODEL,
    "source_type": "PUBLIC_EVIDENCE_EXCERPT",
    "source_file": str(
        EVIDENCE_FILE.relative_to(
            ROOT
        )
    ),
    "oracle_used": False,
    "candidate_used": False,
    "manual_formal_policy_used": False,
    "done_reason": done_reason,
    "response": (
        parsed_json
        if parsed_json is not None
        else text
    ),
}

OUTPUT_JSON.write_text(
    json.dumps(
        result_record,
        ensure_ascii=False,
        indent=2
    ),
    encoding="utf-8"
)


# ============================================================
# 12. 完成信息
# ============================================================

print("\n" + "=" * 80)

print("Experiment completed.")

print(
    f"Public evidence source:\n{EVIDENCE_FILE}"
)

print(
    f"\nText output:\n{OUTPUT_TXT}"
)

print(
    f"\nJSON audit record:\n{OUTPUT_JSON}"
)