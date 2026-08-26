import json
import re
from pathlib import Path


# ============================================================
# 1. 路径
# ============================================================

ROOT = Path(r"G:\LearnAI\ontology-evolution")

EVENT_ID = "EXT_E003"

GOLD_POLICY_FILE = (
        ROOT
        / "benchmark"
        / "external-real-v1"
        / "rules"
        / "EXT_E003-formal-policy.json"
)


AUTO_POLICY_FILE = (
        ROOT
        / "output"
        / "auto-formal-policy-ext-e003-normalized.json"
)

OUTPUT_FILE = (
        ROOT
        / "output"
        / "auto-formal-policy-ext-e003-evaluation.json"
)


# ============================================================
# 2. 读取 JSON
# ============================================================

def load_json(path: Path):
    if not path.exists():
        raise FileNotFoundError(path)

    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


gold = load_json(GOLD_POLICY_FILE)
auto_record = load_json(AUTO_POLICY_FILE)

# 自动生成文件外面有一层 response

auto = auto_record.get(
    "normalized_policy",
    auto_record
)

if not isinstance(auto, dict):
    raise RuntimeError(
        "Generated policy response is not a JSON object."
    )


# ============================================================
# 3. 基础文本规范化
# ============================================================

def normalize_text(value):
    if value is None:
        return ""

    text = str(value).strip().lower()

    # 去除多余空格
    text = re.sub(r"\s+", "", text)

    # 常见中文/英文表达统一
    replacements = {
        "事故当日": "出险日期",
        "每亩赔偿限额": "亩赔偿限额",
        "乘以": "×",
        "*": "×",
        "x": "×",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return text


# ============================================================
# 4. 语义值规范化
# ============================================================

def normalize_semantic_result(value):
    text = normalize_text(value)

    mappings = {
        "accident_date_limit_times_loss_rate":
            "accident_date_limit_times_loss_rate",

        "赔偿金额=出险日期亩赔偿限额×损失率":
            "accident_date_limit_times_loss_rate",

        "亩赔偿金额=出险日期亩赔偿限额×损失率":
            "accident_date_limit_times_loss_rate",

        "出险日期亩赔偿限额×损失率":
            "accident_date_limit_times_loss_rate",

        "出险日期亩赔偿限额乘以损失率":
            "accident_date_limit_times_loss_rate",
    }

    return mappings.get(
        text,
        text
    )


# ============================================================
# 5. 从 Gold Policy 中抽核心语义结果
# ============================================================

def get_gold_semantic_result(policy):
    rules = policy.get("rules", [])

    if not rules:
        return ""

    # 取最高优先级规则
    rule = max(
        rules,
        key=lambda r: r.get("priority", 0)
    )

    allowed_values = rule.get(
        "allowed_values",
        []
    )

    if not allowed_values:
        return ""

    value = allowed_values[0]

    # formula=xxx → xxx
    if "=" in value:
        key, result = value.split(
            "=",
            1
        )

        if key.strip().lower() == "formula":
            return result.strip()

    return value


# ============================================================
# 6. 从 Auto Policy 中抽 semantic_result
# ============================================================

def get_auto_semantic_result(policy):
    rules = policy.get("rules", [])

    if not rules:
        return ""

    rule = max(
        rules,
        key=lambda r: r.get("priority", 0)
    )

    return rule.get(
        "semantic_result",
        ""
    )


# ============================================================
# 7. Facts 对比
# ============================================================

gold_facts = gold.get(
    "facts",
    {}
)

auto_facts = auto.get(
    "facts",
    {}
)

fact_fields = [
    "source_revision_year",
    "target_product",
    "damage_type",
]

fact_results = {}

for field in fact_fields:

    gold_value = gold_facts.get(
        field,
        ""
    )

    auto_value = auto_facts.get(
        field,
        ""
    )

    correct = (
            normalize_text(gold_value)
            ==
            normalize_text(auto_value)
    )

    fact_results[field] = {
        "gold": gold_value,
        "auto": auto_value,
        "correct": correct,
    }


# ============================================================
# 8. semantic_result 对比
# ============================================================

gold_semantic = (
    get_gold_semantic_result(
        gold
    )
)

auto_semantic = (
    get_auto_semantic_result(
        auto
    )
)

gold_semantic_norm = (
    normalize_semantic_result(
        gold_semantic
    )
)

auto_semantic_norm = (
    normalize_semantic_result(
        auto_semantic
    )
)

semantic_correct = (
        gold_semantic_norm
        ==
        auto_semantic_norm
)


# ============================================================
# 9. formula_basis 语义检查
# ============================================================

gold_formula_basis = (
    gold_facts.get(
        "formula_basis",
        ""
    )
)

auto_formula_basis = (
    auto_facts.get(
        "formula_basis",
        ""
    )
)

# Gold 里是 controlled symbolic term，
# Auto 里可能是自然语言表达。
# 所以这里映射到统一语义空间。

gold_formula_basis_norm = (
    normalize_semantic_result(
        gold_formula_basis
    )
)

auto_formula_basis_norm = (
    normalize_semantic_result(
        auto_formula_basis
    )
)

formula_basis_correct = (
        gold_formula_basis_norm
        ==
        auto_formula_basis_norm
)


# ============================================================
# 10. 指标
# ============================================================

fact_correct_count = sum(
    1
    for item in fact_results.values()
    if item["correct"]
)

fact_total = len(
    fact_results
)

fact_accuracy = (
    fact_correct_count / fact_total
    if fact_total
    else 0.0
)

all_core_correct = (
        fact_accuracy == 1.0
        and formula_basis_correct
        and semantic_correct
)


# ============================================================
# 11. 结果
# ============================================================

result = {
    "event_id": EVENT_ID,

    "fact_results": fact_results,

    "fact_accuracy": fact_accuracy,

    "formula_basis": {
        "gold": gold_formula_basis,
        "auto": auto_formula_basis,
        "gold_normalized":
            gold_formula_basis_norm,
        "auto_normalized":
            auto_formula_basis_norm,
        "correct":
            formula_basis_correct,
    },

    "semantic_result": {
        "gold": gold_semantic,
        "auto": auto_semantic,
        "gold_normalized":
            gold_semantic_norm,
        "auto_normalized":
            auto_semantic_norm,
        "correct":
            semantic_correct,
    },

    "policy_extraction_pass":
        all_core_correct,
}


# ============================================================
# 12. 输出
# ============================================================

print("=" * 80)
print(
    "AUTO FORMAL POLICY EVALUATION - EXT_E003"
)
print("=" * 80)

print(
    json.dumps(
        result,
        ensure_ascii=False,
        indent=2
    )
)

OUTPUT_FILE.write_text(
    json.dumps(
        result,
        ensure_ascii=False,
        indent=2
    ),
    encoding="utf-8"
)

print("\nSaved to:")
print(OUTPUT_FILE)