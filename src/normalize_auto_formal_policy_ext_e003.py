import json
from copy import deepcopy
from pathlib import Path


ROOT = Path(r"G:\LearnAI\ontology-evolution")

INPUT_FILE = (
        ROOT
        / "output"
        / "auto-formal-policy-ext-e003-from-public-evidence.json"
)

OUTPUT_FILE = (
        ROOT
        / "output"
        / "auto-formal-policy-ext-e003-normalized.json"
)


# ============================================================
# Ontology vocabulary
# ============================================================

VOCAB = {
    "target_product": {
        "梨种植保险": "pear_planting_insurance",
    },

    "damage_type": {
        "冻害": "freeze_damage",
        "冻害损失": "freeze_damage",
    },

    "formula_basis": {
        "出险日期亩赔偿限额":
            "accident_date_compensation_limit",

        "事故当日亩赔偿限额":
            "accident_date_compensation_limit",

        "事故当日的每亩赔偿限额":
            "accident_date_compensation_limit",
    },

    "semantic_result": {
        "赔偿金额 = 出险日期亩赔偿限额 × 损失率":
            "accident_date_limit_times_loss_rate",

        "亩赔偿金额 = 出险日期亩赔偿限额 × 损失率":
            "accident_date_limit_times_loss_rate",

        "事故当日的每亩赔偿限额乘以损失率":
            "accident_date_limit_times_loss_rate",
    }
}


# ============================================================
# Operator normalization
# ============================================================

OPERATOR_MAP = {
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


def load_json(path: Path):

    if not path.exists():
        raise FileNotFoundError(path)

    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def normalize_value(field_name, value):

    if value is None:
        return value

    mapping = VOCAB.get(
        field_name,
        {}
    )

    return mapping.get(
        str(value).strip(),
        value
    )


def normalize_operator(operator):

    if operator is None:
        return operator

    key = str(operator).strip()

    return OPERATOR_MAP.get(
        key,
        key
    )


# ============================================================
# Load generated policy
# ============================================================

data = load_json(
    INPUT_FILE
)

policy = data.get(
    "response",
    data
)

if not isinstance(
        policy,
        dict
):
    raise RuntimeError(
        "response is not a JSON object"
    )


normalized_policy = deepcopy(
    policy
)


# ============================================================
# 1. Normalize facts
# ============================================================

facts = normalized_policy.get(
    "facts",
    {}
)

for field_name, value in list(
        facts.items()
):

    facts[field_name] = normalize_value(
        field_name,
        value
    )


# ============================================================
# 2. Normalize rule conditions
# ============================================================

rules = normalized_policy.get(
    "rules",
    []
)

for rule in rules:

    conditions = rule.get(
        "conditions",
        []
    )

    for condition in conditions:

        fact_name = condition.get(
            "fact"
        )

        value = condition.get(
            "value"
        )

        operator = condition.get(
            "operator"
        )

        condition["value"] = (
            normalize_value(
                fact_name,
                value
            )
        )

        condition["operator"] = (
            normalize_operator(
                operator
            )
        )


# ============================================================
# 3. Normalize semantic_result
# ============================================================

for rule in rules:

    semantic_result = rule.get(
        "semantic_result"
    )

    if semantic_result is not None:

        rule["semantic_result"] = (
            normalize_value(
                "semantic_result",
                semantic_result
            )
        )


# ============================================================
# 4. Audit information
# ============================================================

result = {
    "event_id": "EXT_E003",

    "normalization_source":
        "ontology_vocabulary",

    "original_policy":
        policy,

    "normalized_policy":
        normalized_policy,
}


# ============================================================
# 5. Save
# ============================================================

OUTPUT_FILE.write_text(
    json.dumps(
        result,
        ensure_ascii=False,
        indent=2
    ),
    encoding="utf-8"
)


# ============================================================
# 6. Print
# ============================================================

print("=" * 80)
print(
    "NORMALIZED AUTO FORMAL POLICY - EXT_E003"
)
print("=" * 80)

print(
    json.dumps(
        normalized_policy,
        ensure_ascii=False,
        indent=2
    )
)

print("\nSaved to:")
print(OUTPUT_FILE)