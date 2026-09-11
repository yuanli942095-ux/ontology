import csv
import json
import re
from collections import defaultdict
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

GOLD_RULE_DIR = (
        ROOT
        / "benchmark"
        / "external-real-v1"
        / "rules"
)

EVENT_CSV = (
        ROOT
        / "benchmark"
        / "external-real-v1"
        / "input"
        / "external-real-event-template.csv"
)

OUTPUT_DIR = (
        ROOT
        / "output"
        / "auto-policy-v2"
)

DETAILS_FILE = (
        OUTPUT_DIR
        / "auto-policy-v2-evaluation-details.csv"
)

BY_EVENT_FILE = (
        OUTPUT_DIR
        / "auto-policy-v2-evaluation-by-event.csv"
)

SUMMARY_FILE = (
        OUTPUT_DIR
        / "auto-policy-v2-evaluation-summary.json"
)

RUNS = 5


# ============================================================
# 2. 通用工具
# ============================================================

def load_json(path: Path):

    if not path.exists():
        raise FileNotFoundError(path)

    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def normalize_text(value):

    if value is None:
        return ""

    text = str(value).strip().lower()

    text = re.sub(
        r"\s+",
        "",
        text
    )

    text = text.replace(
        "：",
        ":"
    )

    text = text.replace(
        "＝",
        "="
    )

    text = text.replace(
        "×",
        "*"
    )

    return text


def normalize_operator(value):

    if value is None:
        return ""

    text = str(value).strip().lower()

    mapping = {
        "==": "equals",
        "=": "equals",
        "eq": "equals",

        ">=": "greater_than_or_equal",
        "gte": "greater_than_or_equal",

        "<=": "less_than_or_equal",
        "lte": "less_than_or_equal",

        ">": "greater_than",
        "<": "less_than",
    }

    return mapping.get(
        text,
        text
    )


# ============================================================
# 3. 读取事件类型
# ============================================================

def load_event_types():

    result = {}

    with EVENT_CSV.open(
            "r",
            encoding="utf-8-sig",
            newline=""
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            event_id = (
                row.get(
                    "event_id",
                    ""
                )
                .strip()
            )

            semantic_type = (
                row.get(
                    "semantic_type",
                    ""
                )
                .strip()
            )

            if (
                    event_id.startswith(
                        "EXT_E"
                    )
            ):
                result[
                    event_id
                ] = semantic_type

    return result


# ============================================================
# 4. 提取 Gold 最高优先级规则
# ============================================================

def get_top_gold_rule(
        gold_policy
):

    rules = gold_policy.get(
        "rules",
        []
    )

    if not rules:
        return None

    return max(
        rules,
        key=lambda r:
        r.get(
            "priority",
            0
        )
    )


# ============================================================
# 5. Gold semantic result
# ============================================================

def get_gold_semantic_result(
        gold_policy
):

    rule = get_top_gold_rule(
        gold_policy
    )

    if not rule:
        return ""

    allowed_values = (
        rule.get(
            "allowed_values",
            []
        )
    )

    if not allowed_values:
        return ""

    # 当前 benchmark 是单核心目标
    return str(
        allowed_values[0]
    ).strip()


# ============================================================
# 6. Auto semantic result
# ============================================================

def get_auto_semantic_result(
        auto_policy
):

    rules = auto_policy.get(
        "rules",
        []
    )

    if not rules:
        return ""

    rule = max(
        rules,
        key=lambda r:
        r.get(
            "priority",
            0
        )
    )

    return str(
        rule.get(
            "semantic_result",
            ""
        )
    ).strip()


# ============================================================
# 7. Semantic Result 轻量规范化
# ============================================================

def normalize_semantic_result(
        value
):

    text = normalize_text(
        value
    )

    # formula=xxx
    if text.startswith(
            "formula="
    ):
        text = text.split(
            "=",
            1
        )[1]

    # 常见包装字段去掉，只比较核心语义
    known_prefixes = [
        "status=",
        "value=",
        "result=",
    ]

    for prefix in known_prefixes:

        if text.startswith(
                prefix
        ):
            text = text.split(
                "=",
                1
            )[1]

    return text


# ============================================================
# 8. Gold facts
# ============================================================

def get_gold_facts(
        gold_policy
):

    facts = gold_policy.get(
        "facts",
        {}
    )

    if not isinstance(
            facts,
            dict
    ):
        return {}

    return facts


# ============================================================
# 9. Auto facts
# ============================================================

def get_auto_facts(
        auto_policy
):

    facts = auto_policy.get(
        "facts",
        {}
    )

    if not isinstance(
            facts,
            dict
    ):
        return {}

    return facts


# ============================================================
# 10. Facts 比较
# ============================================================

def compare_facts(
        gold_facts,
        auto_facts
):

    results = {}

    correct = 0

    total = 0

    for field_name, gold_value \
            in gold_facts.items():

        # 不评 provenance / audit 类字段
        if field_name in {
            "oracle_used",
            "annotation_status",
        }:
            continue

        total += 1

        auto_value = (
            auto_facts.get(
                field_name,
                None
            )
        )

        gold_norm = normalize_text(
            gold_value
        )

        auto_norm = normalize_text(
            auto_value
        )

        is_correct = (
                gold_norm
                ==
                auto_norm
        )

        if is_correct:
            correct += 1

        results[
            field_name
        ] = {
            "gold":
                gold_value,

            "auto":
                auto_value,

            "correct":
                is_correct,
        }

    accuracy = (
        correct / total
        if total
        else 1.0
    )

    return (
        results,
        correct,
        total,
        accuracy,
    )


# ============================================================
# 11. Rule condition 比较
# ============================================================

def canonical_condition_set(
        conditions
):

    result = set()

    if not isinstance(
            conditions,
            list
    ):
        return result

    for condition in conditions:

        if not isinstance(
                condition,
                dict
        ):
            continue

        fact = normalize_text(
            condition.get(
                "fact",
                ""
            )
        )

        operator = normalize_operator(
            condition.get(
                "operator",
                ""
            )
        )

        value = normalize_text(
            condition.get(
                "value",
                ""
            )
        )

        result.add(
            (
                fact,
                operator,
                value,
            )
        )

    return result


def compare_conditions(
        gold_policy,
        auto_policy
):

    gold_rule = (
        get_top_gold_rule(
            gold_policy
        )
    )

    if not gold_rule:
        return {
            "gold_count": 0,
            "auto_count": 0,
            "intersection": 0,
            "precision": 1.0,
            "recall": 1.0,
            "exact": True,
        }

    auto_rules = auto_policy.get(
        "rules",
        []
    )

    if not auto_rules:

        return {
            "gold_count":
                len(
                    gold_rule.get(
                        "conditions",
                        []
                    )
                ),

            "auto_count":
                0,

            "intersection":
                0,

            "precision":
                0.0,

            "recall":
                0.0,

            "exact":
                False,
        }

    auto_rule = max(
        auto_rules,
        key=lambda r:
        r.get(
            "priority",
            0
        )
    )

    gold_set = (
        canonical_condition_set(
            gold_rule.get(
                "conditions",
                []
            )
        )
    )

    auto_set = (
        canonical_condition_set(
            auto_rule.get(
                "conditions",
                []
            )
        )
    )

    intersection = (
            gold_set
            &
            auto_set
    )

    precision = (
        len(intersection)
        /
        len(auto_set)
        if auto_set
        else 0.0
    )

    recall = (
        len(intersection)
        /
        len(gold_set)
        if gold_set
        else 1.0
    )

    exact = (
            gold_set
            ==
            auto_set
    )

    return {
        "gold_count":
            len(gold_set),

        "auto_count":
            len(auto_set),

        "intersection":
            len(intersection),

        "precision":
            precision,

        "recall":
            recall,

        "exact":
            exact,
    }


# ============================================================
# 12. 单次 Policy 评测
# ============================================================

def evaluate_one(
        event_id,
        run_id,
        semantic_type,
        auto_record,
        gold_policy,
):

    status = (
        auto_record.get(
            "status",
            ""
        )
    )

    auto_policy = (
        auto_record.get(
            "response",
            {}
        )
    )

    if (
            status != "GENERATED"
            or
            not isinstance(
                auto_policy,
                dict
            )
    ):

        return {
            "event_id":
                event_id,

            "semantic_type":
                semantic_type,

            "run":
                run_id,

            "generation_status":
                status,

            "fact_accuracy":
                0.0,

            "condition_precision":
                0.0,

            "condition_recall":
                0.0,

            "condition_exact":
                False,

            "semantic_correct":
                False,

            "policy_pass":
                False,
        }

    gold_facts = (
        get_gold_facts(
            gold_policy
        )
    )

    auto_facts = (
        get_auto_facts(
            auto_policy
        )
    )

    (
        fact_results,
        fact_correct,
        fact_total,
        fact_accuracy,
    ) = compare_facts(
        gold_facts,
        auto_facts
    )

    condition_result = (
        compare_conditions(
            gold_policy,
            auto_policy
        )
    )

    gold_semantic = (
        get_gold_semantic_result(
            gold_policy
        )
    )

    auto_semantic = (
        get_auto_semantic_result(
            auto_policy
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

    # ========================================================
    # Policy Pass 定义
    #
    # 这里先用严格条件：
    #
    # 1. 所有 Gold facts 全匹配
    # 2. Gold rule conditions 全部被覆盖
    # 3. semantic_result 正确
    #
    # 自动多抽额外 condition 不直接判死刑，
    # 所以 condition 使用 recall == 1.0
    # ========================================================

    policy_pass = (
            fact_accuracy == 1.0
            and
            condition_result[
                "recall"
            ] == 1.0
            and
            semantic_correct
    )

    return {
        "event_id":
            event_id,

        "semantic_type":
            semantic_type,

        "run":
            run_id,

        "generation_status":
            status,

        "fact_correct":
            fact_correct,

        "fact_total":
            fact_total,

        "fact_accuracy":
            fact_accuracy,

        "condition_gold_count":
            condition_result[
                "gold_count"
            ],

        "condition_auto_count":
            condition_result[
                "auto_count"
            ],

        "condition_intersection":
            condition_result[
                "intersection"
            ],

        "condition_precision":
            condition_result[
                "precision"
            ],

        "condition_recall":
            condition_result[
                "recall"
            ],

        "condition_exact":
            condition_result[
                "exact"
            ],

        "gold_semantic":
            gold_semantic,

        "auto_semantic":
            auto_semantic,

        "gold_semantic_normalized":
            gold_semantic_norm,

        "auto_semantic_normalized":
            auto_semantic_norm,

        "semantic_correct":
            semantic_correct,

        "policy_pass":
            policy_pass,

        "fact_results_json":
            json.dumps(
                fact_results,
                ensure_ascii=False
            ),
    }


# ============================================================
# 13. 主程序
# ============================================================

def main():

    event_types = (
        load_event_types()
    )

    details = []

    expected_events = [
        f"EXT_E{i:03d}"
        for i in range(
            1,
            31
        )
    ]

    print("=" * 80)
    print(
        "AUTO FORMAL POLICY V2 GOLD EVALUATION"
    )
    print("=" * 80)

    # ========================================================
    # 30 × 5
    # ========================================================

    for event_id in expected_events:

        semantic_type = (
            event_types.get(
                event_id,
                ""
            )
        )

        gold_file = (
                GOLD_RULE_DIR
                / (
                    f"{event_id}"
                    f"-formal-policy.json"
                )
        )

        gold_policy = (
            load_json(
                gold_file
            )
        )

        for run_id in range(
                1,
                RUNS + 1
        ):

            seed = (
                    20260820
                    + run_id
                    - 1
            )

            auto_file = (
                    RAW_DIR
                    / (
                        f"{event_id}"
                        f"-run{run_id}"
                        f"-seed{seed}.json"
                    )
            )

            auto_record = (
                load_json(
                    auto_file
                )
            )

            result = evaluate_one(
                event_id=
                event_id,

                run_id=
                run_id,

                semantic_type=
                semantic_type,

                auto_record=
                auto_record,

                gold_policy=
                gold_policy,
            )

            details.append(
                result
            )

            print(
                f"{event_id} "
                f"run={run_id} "
                f"| fact="
                f"{result['fact_accuracy']:.2f} "
                f"| condR="
                f"{result['condition_recall']:.2f} "
                f"| semantic="
                f"{result['semantic_correct']} "
                f"| pass="
                f"{result['policy_pass']}"
            )

    # ========================================================
    # 14. 保存 details
    # ========================================================

    detail_fields = [
        "event_id",
        "semantic_type",
        "run",
        "generation_status",

        "fact_correct",
        "fact_total",
        "fact_accuracy",

        "condition_gold_count",
        "condition_auto_count",
        "condition_intersection",
        "condition_precision",
        "condition_recall",
        "condition_exact",

        "gold_semantic",
        "auto_semantic",
        "gold_semantic_normalized",
        "auto_semantic_normalized",

        "semantic_correct",
        "policy_pass",

        "fact_results_json",
    ]

    with DETAILS_FILE.open(
            "w",
            encoding="utf-8-sig",
            newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=detail_fields
        )

        writer.writeheader()

        writer.writerows(
            details
        )

    # ========================================================
    # 15. By-event
    # ========================================================

    grouped = defaultdict(
        list
    )

    for row in details:

        grouped[
            row["event_id"]
        ].append(
            row
        )

    by_event_rows = []

    for event_id in expected_events:

        rows = grouped[
            event_id
        ]

        attempts = len(
            rows
        )

        passes = sum(
            1
            for row in rows
            if row[
                "policy_pass"
            ]
        )

        semantic_correct = sum(
            1
            for row in rows
            if row[
                "semantic_correct"
            ]
        )

        avg_fact_accuracy = (
                sum(
                    row[
                        "fact_accuracy"
                    ]
                    for row in rows
                )
                / attempts
        )

        avg_condition_recall = (
                sum(
                    row[
                        "condition_recall"
                    ]
                    for row in rows
                )
                / attempts
        )

        by_event_rows.append({
            "event_id":
                event_id,

            "semantic_type":
                event_types.get(
                    event_id,
                    ""
                ),

            "attempts":
                attempts,

            "policy_passes":
                passes,

            "policy_accuracy":
                (
                        passes
                        / attempts
                ),

            "all_runs_correct":
                (
                        passes
                        == attempts
                ),

            "semantic_correct_runs":
                semantic_correct,

            "semantic_accuracy":
                (
                        semantic_correct
                        / attempts
                ),

            "average_fact_accuracy":
                avg_fact_accuracy,

            "average_condition_recall":
                avg_condition_recall,
        })

    event_fields = [
        "event_id",
        "semantic_type",
        "attempts",
        "policy_passes",
        "policy_accuracy",
        "all_runs_correct",
        "semantic_correct_runs",
        "semantic_accuracy",
        "average_fact_accuracy",
        "average_condition_recall",
    ]

    with BY_EVENT_FILE.open(
            "w",
            encoding="utf-8-sig",
            newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=event_fields
        )

        writer.writeheader()

        writer.writerows(
            by_event_rows
        )

    # ========================================================
    # 16. Overall
    # ========================================================

    total_attempts = len(
        details
    )

    total_passes = sum(
        1
        for row in details
        if row[
            "policy_pass"
        ]
    )

    semantic_correct_total = sum(
        1
        for row in details
        if row[
            "semantic_correct"
        ]
    )

    avg_fact_accuracy = (
            sum(
                row[
                    "fact_accuracy"
                ]
                for row in details
            )
            / total_attempts
    )

    avg_condition_recall = (
            sum(
                row[
                    "condition_recall"
                ]
                for row in details
            )
            / total_attempts
    )

    strict_events = sum(
        1
        for row
        in by_event_rows
        if row[
            "all_runs_correct"
        ]
    )

    # ========================================================
    # 17. 按类型
    # ========================================================

    by_type = {}

    semantic_types = sorted(
        set(
            row[
                "semantic_type"
            ]
            for row in details
        )
    )

    for semantic_type in (
            semantic_types
    ):

        rows = [
            row
            for row in details
            if row[
                   "semantic_type"
               ]
               == semantic_type
        ]

        attempts = len(
            rows
        )

        passes = sum(
            1
            for row in rows
            if row[
                "policy_pass"
            ]
        )

        sem_ok = sum(
            1
            for row in rows
            if row[
                "semantic_correct"
            ]
        )

        type_events = [
            row
            for row in by_event_rows
            if row[
                   "semantic_type"
               ]
               == semantic_type
        ]

        strict_type_events = sum(
            1
            for row in type_events
            if row[
                "all_runs_correct"
            ]
        )

        by_type[
            semantic_type
        ] = {
            "attempts":
                attempts,

            "policy_passes":
                passes,

            "policy_accuracy":
                (
                    passes
                    / attempts
                    if attempts
                    else 0
                ),

            "semantic_accuracy":
                (
                    sem_ok
                    / attempts
                    if attempts
                    else 0
                ),

            "average_fact_accuracy":
                (
                    sum(
                        row[
                            "fact_accuracy"
                        ]
                        for row in rows
                    )
                    / attempts
                    if attempts
                    else 0
                ),

            "average_condition_recall":
                (
                    sum(
                        row[
                            "condition_recall"
                        ]
                        for row in rows
                    )
                    / attempts
                    if attempts
                    else 0
                ),

            "strict_events":
                strict_type_events,

            "event_count":
                len(
                    type_events
                ),

            "strict_event_accuracy":
                (
                    strict_type_events
                    / len(
                        type_events
                    )
                    if type_events
                    else 0
                ),
        }

    summary = {
        "experiment":
            "AUTO_FORMAL_POLICY_V2_GOLD_EVALUATION",

        "events":
            30,

        "runs":
            RUNS,

        "attempts":
            total_attempts,

        "policy_passes":
            total_passes,

        "policy_accuracy":
            (
                    total_passes
                    / total_attempts
            ),

        "semantic_result_accuracy":
            (
                    semantic_correct_total
                    / total_attempts
            ),

        "average_fact_accuracy":
            avg_fact_accuracy,

        "average_condition_recall":
            avg_condition_recall,

        "strict_all_runs_correct_events":
            strict_events,

        "strict_event_accuracy":
            (
                    strict_events
                    / 30
            ),

        "by_semantic_type":
            by_type,

        "important_note":
            (
                "V2 was developed after V1 "
                "error analysis on the same "
                "30-event benchmark; this "
                "evaluation is therefore a "
                "development/ablation result, "
                "not a fully blind hold-out result."
            ),
    }

    SUMMARY_FILE.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    # ========================================================
    # 18. 最终输出
    # ========================================================

    print("\n" + "=" * 80)

    print(
        "AUTO FORMAL POLICY V2 EVALUATION SUMMARY"
    )

    print("=" * 80)

    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2
        )
    )

    print("\nDetails:")
    print(DETAILS_FILE)

    print("\nBy-event:")
    print(BY_EVENT_FILE)

    print("\nSummary:")
    print(SUMMARY_FILE)


# ============================================================
# 19. 入口
# ============================================================

if __name__ == "__main__":
    main()