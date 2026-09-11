import json
import re
from pathlib import Path


# ============================================================
# 1. 基本路径
# ============================================================

ROOT = Path(r"G:\LearnAI\ontology-evolution")

RAW_DIR = (
        ROOT
        / "output"
        / "auto-policy-v2"
        / "raw"
)

GOLD_DIR = (
        ROOT
        / "benchmark"
        / "external-real-v1"
        / "rules"
)

OUTPUT_FILE = (
        ROOT
        / "output"
        / "auto-policy-v2"
        / "semantic-smoke-evaluation.json"
)

EVENTS = [
    "EXT_E003",
    "EXT_E004",
]

RUN_ID = 1
SEED = 20260820


# ============================================================
# 2. JSON 读取
# ============================================================

def load_json(path: Path):

    if not path.exists():
        raise FileNotFoundError(path)

    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


# ============================================================
# 3. 文本规范化
# ============================================================

def normalize_text(value):

    if value is None:
        return ""

    text = str(value).strip().lower()

    replacements = {
        "×": "*",
        "＝": "=",
        "：": ":",
        "；": ";",
        "，": ",",
        "（": "(",
        "）": ")",
    }

    for old, new in replacements.items():
        text = text.replace(
            old,
            new
        )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# 4. 提取 Gold 最高优先级 allowed value
# ============================================================

def get_gold_target(
        gold_policy
):

    rules = gold_policy.get(
        "rules",
        []
    )

    if not rules:
        return ""

    top_rule = max(
        rules,
        key=lambda r:
        r.get(
            "priority",
            0
        )
    )

    allowed_values = (
        top_rule.get(
            "allowed_values",
            []
        )
    )

    if not allowed_values:
        return ""

    return str(
        allowed_values[0]
    ).strip()


# ============================================================
# 5. 提取 V2 semantic_result
# ============================================================

def get_auto_semantic_result(
        auto_record
):

    policy = auto_record.get(
        "response",
        {}
    )

    if not isinstance(
            policy,
            dict
    ):
        return ""

    rules = policy.get(
        "rules",
        []
    )

    if not rules:
        return ""

    top_rule = max(
        rules,
        key=lambda r:
        r.get(
            "priority",
            0
        )
    )

    return str(
        top_rule.get(
            "semantic_result",
            ""
        )
    ).strip()


# ============================================================
# 6. E003 专用语义检查
# ============================================================

def evaluate_e003(
        gold_target,
        auto_semantic,
):

    gold_norm = normalize_text(
        gold_target
    )

    auto_norm = normalize_text(
        auto_semantic
    )

    # ----------------------------
    # Gold 应表达：
    # formula=accident_date_limit_times_loss_rate
    # ----------------------------

    expected_gold_marker = (
        "accident_date_limit_times_loss_rate"
    )

    gold_valid = (
            expected_gold_marker
            in gold_norm
    )

    # ----------------------------
    # 自动结果可能是英文 canonical symbol
    # 或中文自然语言
    # ----------------------------

    canonical_match = (
            expected_gold_marker
            in auto_norm
    )

    chinese_formula_match = (
            (
                    "出险日期"
                    in auto_norm
                    or
                    "事故日期"
                    in auto_norm
                    or
                    "事故日"
                    in auto_norm
            )
            and
            (
                    "亩赔偿限额"
                    in auto_norm
                    or
                    "每亩赔偿限额"
                    in auto_norm
            )
            and
            (
                    "损失率"
                    in auto_norm
                    or
                    "损失"
                    in auto_norm
            )
    )

    semantic_correct = (
            gold_valid
            and
            (
                    canonical_match
                    or
                    chinese_formula_match
            )
    )

    return {
        "gold_valid":
            gold_valid,

        "canonical_match":
            canonical_match,

        "natural_language_match":
            chinese_formula_match,

        "semantic_correct":
            semantic_correct,
    }


# ============================================================
# 7. E004 专用语义检查
# ============================================================

def evaluate_e004(
        gold_target,
        auto_semantic,
):

    gold_norm = normalize_text(
        gold_target
    )

    auto_norm = normalize_text(
        auto_semantic
    )

    # Gold:
    # wcag22_added=2.4.11;level=AA

    gold_has_wcag22 = (
            "wcag22"
            in gold_norm
    )

    gold_has_2411 = (
            "2.4.11"
            in gold_norm
    )

    gold_has_aa = (
            "level=aa"
            in gold_norm
            or
            "aa"
            in gold_norm
    )

    # 自动输出需要同时表达：
    # 1. WCAG 2.2
    # 2. 2.4.11
    # 3. AA
    # 4. 是新增/生效要求

    auto_has_wcag22 = (
            "wcag 2.2"
            in auto_norm
            or
            "wcag2.2"
            in auto_norm
            or
            "wcag22"
            in auto_norm
    )

    auto_has_2411 = (
            "2.4.11"
            in auto_norm
    )

    auto_has_aa = (
            re.search(
                r"\baa\b",
                auto_norm
            )
            is not None
    )

    change_markers = [
        "addition",
        "added",
        "new",
        "active",
        "新增",
        "增加",
        "生效",
        "加入",
    ]

    auto_has_change = any(
        marker in auto_norm
        for marker in change_markers
    )

    gold_valid = (
            gold_has_wcag22
            and
            gold_has_2411
            and
            gold_has_aa
    )

    semantic_correct = (
            gold_valid
            and
            auto_has_wcag22
            and
            auto_has_2411
            and
            auto_has_aa
            and
            auto_has_change
    )

    return {
        "gold_valid":
            gold_valid,

        "auto_has_wcag22":
            auto_has_wcag22,

        "auto_has_2_4_11":
            auto_has_2411,

        "auto_has_AA":
            auto_has_aa,

        "auto_has_change_semantics":
            auto_has_change,

        "semantic_correct":
            semantic_correct,
    }


# ============================================================
# 8. 通用入口
# ============================================================

def evaluate_event(
        event_id,
        gold_target,
        auto_semantic,
):

    if event_id == "EXT_E003":

        return evaluate_e003(
            gold_target,
            auto_semantic
        )

    if event_id == "EXT_E004":

        return evaluate_e004(
            gold_target,
            auto_semantic
        )

    raise ValueError(
        f"Unsupported smoke event: {event_id}"
    )


# ============================================================
# 9. 主程序
# ============================================================

def main():

    results = []

    print("=" * 80)
    print(
        "AUTO POLICY V2 SEMANTIC TARGET SMOKE EVALUATION"
    )
    print("=" * 80)

    for event_id in EVENTS:

        gold_file = (
                GOLD_DIR
                / f"{event_id}-formal-policy.json"
        )

        auto_file = (
                RAW_DIR
                / (
                    f"{event_id}"
                    f"-run{RUN_ID}"
                    f"-seed{SEED}.json"
                )
        )

        gold_policy = load_json(
            gold_file
        )

        auto_record = load_json(
            auto_file
        )

        gold_target = (
            get_gold_target(
                gold_policy
            )
        )

        auto_semantic = (
            get_auto_semantic_result(
                auto_record
            )
        )

        evaluation = (
            evaluate_event(
                event_id,
                gold_target,
                auto_semantic
            )
        )

        result = {
            "event_id":
                event_id,

            "gold_target":
                gold_target,

            "auto_semantic_result":
                auto_semantic,

            "evaluation":
                evaluation,

            "semantic_correct":
                evaluation[
                    "semantic_correct"
                ],
        }

        results.append(
            result
        )

        print(
            f"\n{event_id}"
        )

        print(
            "-" * 80
        )

        print(
            "Gold:"
        )

        print(
            gold_target
        )

        print(
            "\nAuto:"
        )

        print(
            auto_semantic
        )

        print(
            "\nSemantic correct:"
        )

        print(
            evaluation[
                "semantic_correct"
            ]
        )

    # ========================================================
    # 10. 汇总
    # ========================================================

    correct_count = sum(
        1
        for item in results
        if item[
            "semantic_correct"
        ]
    )

    summary = {
        "experiment":
            "AUTO_POLICY_V2_SEMANTIC_SMOKE",

        "events":
            len(
                results
            ),

        "correct":
            correct_count,

        "semantic_accuracy":
            (
                correct_count
                / len(results)
                if results
                else 0
            ),

        "results":
            results,
    }

    OUTPUT_FILE.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print(
        "\n"
        + "=" * 80
    )

    print(
        "SUMMARY"
    )

    print(
        "=" * 80
    )

    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2
        )
    )

    print(
        "\nSaved to:"
    )

    print(
        OUTPUT_FILE
    )


# ============================================================
# 11. 程序入口
# ============================================================

if __name__ == "__main__":
    main()