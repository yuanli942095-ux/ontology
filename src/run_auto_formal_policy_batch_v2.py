from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
import csv
import json
import re
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path


# ============================================================
# 1. 基本配置
# ============================================================

ROOT = Path(r"G:\LearnAI\ontology-evolution")

MODEL = "qwen3.5:9b"

OLLAMA_URL = "http://localhost:11434/api/generate"

RUNS = 5

TEMPERATURE = 0.2

NUM_PREDICT = 900

TIMEOUT = 180

SEED_BASE = 20260820

PROMPT_VERSION = "AUTO_POLICY_V2_TYPE_AWARE_CANDIDATE_BLIND"


# ============================================================
# 2. 输入路径
# ============================================================

BENCHMARK_DIR = (
        ROOT
        / "benchmark"
        / "external-real-v1"
)

EXCERPT_DIR = (
        BENCHMARK_DIR
        / "documents"
        / "excerpts"
)

EVENT_CSV = (
        BENCHMARK_DIR
        / "input"
        / "external-real-event-template.csv"
)


# ============================================================
# 3. 输出路径
# ============================================================

OUTPUT_DIR = (
        ROOT
        / "output"
        / "auto-policy-v2"
)

RAW_OUTPUT_DIR = (
        OUTPUT_DIR
        / "raw"
)

CLEAN_EVIDENCE_DIR = (
        OUTPUT_DIR
        / "candidate-blind-evidence"
)

RAW_OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)

CLEAN_EVIDENCE_DIR.mkdir(
    parents=True,
    exist_ok=True
)

DETAILS_FILE = (
        OUTPUT_DIR
        / "auto-policy-v2-generation-details.csv"
)

BY_EVENT_FILE = (
        OUTPUT_DIR
        / "auto-policy-v2-generation-by-event.csv"
)

SUMMARY_FILE = (
        OUTPUT_DIR
        / "auto-policy-v2-generation-summary.json"
)


# ============================================================
# 4. 支持的 semantic types
# ============================================================

SUPPORTED_TYPES = {
    "TEMPORAL_VERSION",
    "GENERAL_RULE_EXCEPTION",
    "CROSS_SENTENCE_SCOPE",
}


# ============================================================
# 5. 读取事件元数据
# ============================================================

def load_events():

    if not EVENT_CSV.exists():
        raise FileNotFoundError(
            f"Event CSV not found: {EVENT_CSV}"
        )

    events = {}

    with EVENT_CSV.open(
            "r",
            encoding="utf-8-sig",
            newline=""
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            event_id = (
                row.get("event_id", "")
                .strip()
            )

            if not event_id:
                continue

            if event_id not in {
                f"EXT_E{i:03d}"
                for i in range(1, 31)
            }:
                continue

            events[event_id] = row

    return events


# ============================================================
# 6. Candidate-blind Evidence 清洗
# ============================================================

def remove_candidate_sections(
        text: str,
) -> str:
    """
    删除 public evidence 中与 candidate 有关的段落。

    例如：

    Public candidate values:

    - CAND_001: ...
    - CAND_002: ...
    - CAND_003: ...

    Status:
    ...

    会删除 candidate values 段。
    """

    lines = text.splitlines()

    cleaned = []

    skipping_candidate_section = False

    for line in lines:

        stripped = line.strip()

        lower = stripped.lower()

        # ----------------------------
        # 开始 candidate section
        # ----------------------------

        if (
                "public candidate values" in lower
                or lower == "candidate values:"
                or lower == "candidate values"
                or lower.startswith(
            "public candidates"
        )
        ):
            skipping_candidate_section = True
            continue

        # ----------------------------
        # 遇到下一个 Markdown heading
        # 退出 candidate section
        # ----------------------------

        if skipping_candidate_section:

            if (
                    stripped.startswith("#")
                    or lower.startswith("status:")
                    or lower == "status"
                    or lower.startswith(
                "evidence summary"
            )
            ):
                skipping_candidate_section = False

            else:
                continue

        # ----------------------------
        # 即使散落在其他位置，
        # 也删除显式 candidate 行
        # ----------------------------

        if re.search(
                r"\bCAND_\d+\b",
                line,
                flags=re.I
        ):
            continue

        cleaned.append(line)

    result = "\n".join(
        cleaned
    ).strip()

    return result


# ============================================================
# 7. 检查输入中是否仍有 candidate marker
# ============================================================

def find_candidate_markers(
        text: str,
):

    return re.findall(
        r"\bCAND_\d+\b",
        text,
        flags=re.I
    )


# ============================================================
# 8. 三类 schema
# ============================================================

def schema_for_semantic_type(
        semantic_type: str,
) -> str:

    if semantic_type == "TEMPORAL_VERSION":

        return """
请输出以下结构：

{
  "semantic_type": "TEMPORAL_VERSION",
  "facts": {
    "previous_revision": "...",
    "current_revision": "...",
    "effective_condition": "...",
    "target_subject": "...",
    "changed_property": "...",
    "semantic_change": "..."
  },
  "rules": [
    {
      "priority": 300,
      "conditions": [
        {
          "fact": "...",
          "operator": "...",
          "value": "..."
        }
      ],
      "semantic_result": "..."
    }
  ]
}

字段含义：

- previous_revision：
  旧版本、旧状态或此前适用版本。
  如果公开证据无法明确判断，可以为 null。

- current_revision：
  当前应采用的新版本或当前有效版本。

- effective_condition：
  新版本生效的时间、发布日期、适用条件或版本切换条件。

- target_subject：
  发生语义变化的规范对象。

- changed_property：
  发生变化的属性、要求、状态或规范字段。

- semantic_change：
  新版本相对于旧版本产生的核心变化。

- semantic_result：
  当前版本最终应采用的规范语义结果。
"""

    if semantic_type == "GENERAL_RULE_EXCEPTION":

        return """
请输出以下结构：

{
  "semantic_type": "GENERAL_RULE_EXCEPTION",
  "facts": {
    "target_subject": "...",
    "general_rule": "...",
    "exception_condition": "...",
    "exception_effect": "...",
    "changed_property": "..."
  },
  "rules": [
    {
      "priority": 300,
      "conditions": [
        {
          "fact": "...",
          "operator": "...",
          "value": "..."
        }
      ],
      "semantic_result": "..."
    }
  ]
}

字段含义：

- target_subject：
  被规则约束的规范对象。

- general_rule：
  一般情况下适用的规则。
  如果 evidence 只明确提供例外而没有完整一般规则，
  可以为 null。

- exception_condition：
  触发例外、特殊处理或修订规则的条件。

- exception_effect：
  例外成立后产生的具体语义效果。

- changed_property：
  受到一般规则/例外影响的属性或规范字段。

- semantic_result：
  在当前证据和条件下最终应采用的语义结果。
"""

    if semantic_type == "CROSS_SENTENCE_SCOPE":

        return """
请输出以下结构：

{
  "semantic_type": "CROSS_SENTENCE_SCOPE",
  "facts": {
    "target_subject": "...",
    "primary_statement": "...",
    "qualifier_or_scope_clause": "...",
    "scope_target": "...",
    "resolved_semantics": "..."
  },
  "rules": [
    {
      "priority": 300,
      "conditions": [
        {
          "fact": "...",
          "operator": "...",
          "value": "..."
        }
      ],
      "semantic_result": "..."
    }
  ]
}

字段含义：

- target_subject：
  需要解析语义作用域的规范对象。

- primary_statement：
  主句、基础规则或主要声明。

- qualifier_or_scope_clause：
  在相邻句、后续句或其他位置出现的限定条件、
  补充说明或作用域描述。

- scope_target：
  该限定条件实际作用于哪个对象、属性或规则。

- resolved_semantics：
  跨句作用域解析后的最终含义。

- semantic_result：
  结合完整上下文后最终应采用的规范语义结果。
"""

    raise ValueError(
        f"Unsupported semantic_type: {semantic_type}"
    )


# ============================================================
# 9. 构造 Prompt
# ============================================================

def build_prompt(
        event_row: dict,
        clean_evidence: str,
) -> str:

    event_id = (
        event_row["event_id"]
        .strip()
    )

    semantic_type = (
        event_row["semantic_type"]
        .strip()
    )

    title = (
        event_row.get(
            "title",
            ""
        )
        .strip()
    )

    case_context = (
        event_row.get(
            "case_context",
            ""
        )
        .strip()
    )

    subject_label = (
        event_row.get(
            "subject_label",
            ""
        )
        .strip()
    )

    predicate_label = (
        event_row.get(
            "predicate_label",
            ""
        )
        .strip()
    )

    schema = schema_for_semantic_type(
        semantic_type
    )

    return f"""
你是一名本体工程、规范文档语义演化和神经符号推理助手。

你的任务是：

仅依据公开规范文档证据，
自动生成一个可供后续本体修复系统使用的形式化语义策略。

本阶段是 Policy Construction，
不是 Candidate Selection。

你不能：

- 使用 private Oracle；
- 使用 Gold Answer；
- 使用任何候选编号；
- 使用候选值；
- 使用人工 formal-policy.json；
- 根据候选集合反推答案。

你只能依据：

1. 当前事件公开元数据；
2. 当前事件 candidate-blind public evidence。

重要原则：

- 不要因为某个非核心字段无法确定就直接 ABSTAIN。
- 如果某个辅助字段证据不足，可以填 null。
- 只有当公开证据不足以判断“核心 semantic_result”时，
  才应该 ABSTAIN。
- 不得编造公开证据没有支持的规范事实。
- semantic_result 必须描述公开证据支持的最终语义。
- 请使用简洁、可规范化、可机器处理的表达。
- operator 优先使用：
  equals
  greater_than_or_equal
  less_than_or_equal
  greater_than
  less_than
- 只输出合法 JSON。
- 不输出 Markdown。
- 不输出解释文字。

事件元数据：

event_id:
{event_id}

semantic_type:
{semantic_type}

title:
{title}

case_context:
{case_context}

subject_label:
{subject_label}

predicate_label:
{predicate_label}


对应的类型化 Policy Schema：

{schema}


如果核心 semantic_result 确实无法根据公开证据判断，
才输出：

{{
  "abstain": true,
  "reason": "公开证据不足以确定核心语义结果"
}}


===== CANDIDATE-BLIND PUBLIC EVIDENCE BEGIN =====

{clean_evidence}

===== CANDIDATE-BLIND PUBLIC EVIDENCE END =====
""".strip()


# ============================================================
# 10. Ollama 调用
# ============================================================

def call_qwen(
        prompt: str,
        seed: int,
):

    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,

        # 抽取任务不需要长 thinking
        "think": False,

        "options": {
            "temperature":
                TEMPERATURE,

            "num_predict":
                NUM_PREDICT,

            "seed":
                seed,
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
            "Content-Type":
                "application/json"
        },
        method="POST",
    )

    start = time.perf_counter()

    with urllib.request.urlopen(
            req,
            timeout=TIMEOUT
    ) as response:

        raw_response = (
            response
            .read()
            .decode("utf-8")
        )

    runtime_ms = int(
        (
                time.perf_counter()
                - start
        )
        * 1000
    )

    data = json.loads(
        raw_response
    )

    return data, runtime_ms


# ============================================================
# 11. JSON 提取
# ============================================================

def extract_json(
        text: str,
):

    text = text.strip()

    if not text:
        return None

    # 直接 JSON
    try:
        return json.loads(text)
    except Exception:
        pass

    cleaned = text

    # Markdown fence
    if cleaned.startswith(
            "```json"
    ):
        cleaned = cleaned[
                  len("```json"):
                  ]

    elif cleaned.startswith(
            "```"
    ):
        cleaned = cleaned[
                  len("```"):
                  ]

    if cleaned.endswith(
            "```"
    ):
        cleaned = cleaned[:-3]

    cleaned = cleaned.strip()

    try:
        return json.loads(
            cleaned
        )
    except Exception:
        pass

    # 尝试抽第一个 JSON object
    match = re.search(
        r"\{.*\}",
        cleaned,
        flags=re.S
    )

    if match:

        try:
            return json.loads(
                match.group(0)
            )
        except Exception:
            pass

    return None


# ============================================================
# 12. 输出防泄漏检查
# ============================================================

def forbidden_output_markers(
        text: str,
):

    markers = []

    lower = text.lower()

    # candidate 编号
    if re.search(
            r"\bcand_\d+\b",
            lower
    ):
        markers.append(
            "candidate_marker"
        )

    # candidate_id
    if "candidate_id" in lower:
        markers.append(
            "candidate_id"
        )

    # allowed_values
    if "allowed_values" in lower:
        markers.append(
            "allowed_values"
        )

    # oracle
    if re.search(
            r"\boracle\b",
            lower
    ):
        markers.append(
            "oracle"
        )

    # gold answer
    if (
            "gold answer" in lower
            or '"gold"' in lower
    ):
        markers.append(
            "gold"
        )

    return markers


# ============================================================
# 13. 简单 schema 检查
# ============================================================

def validate_generated_policy(
        parsed,
        expected_semantic_type,
):

    if not isinstance(
            parsed,
            dict
    ):
        return False, (
            "not_json_object"
        )

    if parsed.get(
            "abstain",
            False
    ):
        return True, "abstain"

    actual_type = (
        str(
            parsed.get(
                "semantic_type",
                ""
            )
        )
        .strip()
    )

    if (
            actual_type
            and
            actual_type
            != expected_semantic_type
    ):
        return False, (
            "semantic_type_mismatch"
        )

    facts = parsed.get(
        "facts"
    )

    rules = parsed.get(
        "rules"
    )

    if not isinstance(
            facts,
            dict
    ):
        return False, (
            "facts_missing"
        )

    if (
            not isinstance(
                rules,
                list
            )
            or len(rules) == 0
    ):
        return False, (
            "rules_missing"
        )

    semantic_results = []

    for rule in rules:

        if not isinstance(
                rule,
                dict
        ):
            continue

        value = rule.get(
            "semantic_result"
        )

        if (
                value is not None
                and
                str(value).strip()
        ):
            semantic_results.append(
                str(value).strip()
            )

    if not semantic_results:
        return False, (
            "semantic_result_missing"
        )

    return True, "ok"


# ============================================================
# 14. CSV 写入
# ============================================================

def save_csv(
        path,
        rows,
        fieldnames,
):

    with path.open(
            "w",
            encoding="utf-8-sig",
            newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()

        writer.writerows(
            rows
        )


# ============================================================
# 15. 主实验
# ============================================================

def main():

    events = load_events()

    expected_event_ids = [
        f"EXT_E{i:03d}"
        for i in range(
            1,
            31
        )
    ]

    missing_events = [
        event_id
        for event_id
        in expected_event_ids
        if event_id not in events
    ]

    if missing_events:

        raise RuntimeError(
            "Missing event rows: "
            + ", ".join(
                missing_events
            )
        )

    total_attempts = (
            len(expected_event_ids)
            * RUNS
    )

    print("=" * 80)
    print(
        "AUTO FORMAL POLICY BATCH V2"
    )
    print("=" * 80)

    print(
        f"events={len(expected_event_ids)}, "
        f"runs={RUNS}, "
        f"attempts={total_attempts}"
    )

    print(
        f"model={MODEL}"
    )

    print(
        f"prompt_version="
        f"{PROMPT_VERSION}"
    )

    print("=" * 80)

    records = []

    attempt_index = 0

    # ========================================================
    # 每个 event
    # ========================================================

    for event_id in expected_event_ids:

        event_row = events[
            event_id
        ]

        semantic_type = (
            event_row[
                "semantic_type"
            ]
            .strip()
        )

        if (
                semantic_type
                not in SUPPORTED_TYPES
        ):

            raise RuntimeError(
                f"{event_id}: "
                f"unsupported semantic_type "
                f"{semantic_type}"
            )

        evidence_file = (
                EXCERPT_DIR
                / f"{event_id}-evidence.md"
        )

        if not evidence_file.exists():

            raise FileNotFoundError(
                evidence_file
            )

        original_evidence = (
            evidence_file
            .read_text(
                encoding="utf-8"
            )
        )

        clean_evidence = (
            remove_candidate_sections(
                original_evidence
            )
        )

        remaining_candidate_markers = (
            find_candidate_markers(
                clean_evidence
            )
        )

        # Candidate-blind 输入必须真正干净
        if remaining_candidate_markers:

            raise RuntimeError(
                f"{event_id}: candidate marker "
                f"remained after cleaning: "
                f"{remaining_candidate_markers}"
            )

        clean_evidence_file = (
                CLEAN_EVIDENCE_DIR
                / f"{event_id}"
                  f"-candidate-blind.md"
        )

        clean_evidence_file.write_text(
            clean_evidence,
            encoding="utf-8"
        )

        prompt = build_prompt(
            event_row,
            clean_evidence
        )

        # ====================================================
        # 每个 run
        # ====================================================

        for run_id in range(
                1,
                RUNS + 1
        ):

            attempt_index += 1

            seed = (
                    SEED_BASE
                    + run_id
                    - 1
            )

            print(
                f"[{attempt_index}/"
                f"{total_attempts}] "
                f"{event_id} "
                f"{semantic_type} "
                f"run={run_id}"
            )

            output_file = (
                    RAW_OUTPUT_DIR
                    / (
                        f"{event_id}"
                        f"-run{run_id}"
                        f"-seed{seed}.json"
                    )
            )

            try:

                (
                    data,
                    runtime_ms,
                ) = call_qwen(
                    prompt,
                    seed
                )

                text = str(
                    data.get(
                        "response",
                        ""
                    )
                ).strip()

                done_reason = str(
                    data.get(
                        "done_reason",
                        ""
                    )
                )

                parsed = (
                    extract_json(
                        text
                    )
                )

                markers = (
                    forbidden_output_markers(
                        text
                    )
                )

                abstain = False

                if isinstance(
                        parsed,
                        dict
                ):

                    abstain = bool(
                        parsed.get(
                            "abstain",
                            False
                        )
                    )

                schema_valid = False

                validation_reason = ""

                if parsed is not None:

                    (
                        schema_valid,
                        validation_reason,
                    ) = (
                        validate_generated_policy(
                            parsed,
                            semantic_type
                        )
                    )

                # ============================================
                # status
                # ============================================

                if not text:

                    status = (
                        "EMPTY_RESPONSE"
                    )

                elif markers:

                    status = (
                        "FORBIDDEN_OUTPUT"
                    )

                elif parsed is None:

                    status = (
                        "INVALID_JSON"
                    )

                elif not schema_valid:

                    status = (
                        "INVALID_SCHEMA"
                    )

                elif abstain:

                    status = (
                        "ABSTAIN"
                    )

                else:

                    status = (
                        "GENERATED"
                    )

                output_record = {
                    "event_id":
                        event_id,

                    "semantic_type":
                        semantic_type,

                    "run":
                        run_id,

                    "seed":
                        seed,

                    "model":
                        MODEL,

                    "prompt_version":
                        PROMPT_VERSION,

                    "source_type":
                        "CANDIDATE_BLIND_PUBLIC_EVIDENCE",

                    "source_file":
                        str(
                            evidence_file
                            .relative_to(
                                ROOT
                            )
                        ),

                    "candidate_blind_file":
                        str(
                            clean_evidence_file
                            .relative_to(
                                ROOT
                            )
                        ),

                    "oracle_used":
                        False,

                    "candidate_used":
                        False,

                    "manual_formal_policy_used":
                        False,

                    "status":
                        status,

                    "runtime_ms":
                        runtime_ms,

                    "done_reason":
                        done_reason,

                    "prompt_eval_count":
                        data.get(
                            "prompt_eval_count",
                            0
                        ),

                    "eval_count":
                        data.get(
                            "eval_count",
                            0
                        ),

                    "forbidden_markers":
                        markers,

                    "schema_valid":
                        schema_valid,

                    "validation_reason":
                        validation_reason,

                    "response":
                        (
                            parsed
                            if parsed
                               is not None
                            else text
                        ),
                }

                output_file.write_text(
                    json.dumps(
                        output_record,
                        ensure_ascii=False,
                        indent=2
                    ),
                    encoding="utf-8"
                )

                record = {
                    "event_id":
                        event_id,

                    "semantic_type":
                        semantic_type,

                    "run":
                        run_id,

                    "seed":
                        seed,

                    "status":
                        status,

                    "runtime_ms":
                        runtime_ms,

                    "done_reason":
                        done_reason,

                    "prompt_eval_count":
                        data.get(
                            "prompt_eval_count",
                            0
                        ),

                    "eval_count":
                        data.get(
                            "eval_count",
                            0
                        ),

                    "json_valid":
                        parsed is not None,

                    "schema_valid":
                        schema_valid,

                    "validation_reason":
                        validation_reason,

                    "abstain":
                        abstain,

                    "forbidden_markers":
                        "|".join(
                            markers
                        ),

                    "candidate_blind_input":
                        True,

                    "output_file":
                        str(
                            output_file
                            .relative_to(
                                ROOT
                            )
                        ),

                    "error":
                        "",
                }

                records.append(
                    record
                )

                print(
                    f"  status={status}"
                    f" | runtime="
                    f"{runtime_ms}ms"
                    f" | schema="
                    f"{validation_reason}"
                )

            except Exception as e:

                records.append({
                    "event_id":
                        event_id,

                    "semantic_type":
                        semantic_type,

                    "run":
                        run_id,

                    "seed":
                        seed,

                    "status":
                        "ERROR",

                    "runtime_ms":
                        0,

                    "done_reason":
                        "",

                    "prompt_eval_count":
                        0,

                    "eval_count":
                        0,

                    "json_valid":
                        False,

                    "schema_valid":
                        False,

                    "validation_reason":
                        "",

                    "abstain":
                        False,

                    "forbidden_markers":
                        "",

                    "candidate_blind_input":
                        True,

                    "output_file":
                        "",

                    "error":
                        (
                            f"{type(e).__name__}: "
                            f"{e}"
                        ),
                })

                print(
                    "  status=ERROR"
                    f" | {type(e).__name__}: "
                    f"{e}"
                )

    # ========================================================
    # 16. Details CSV
    # ========================================================

    detail_fields = [
        "event_id",
        "semantic_type",
        "run",
        "seed",
        "status",
        "runtime_ms",
        "done_reason",
        "prompt_eval_count",
        "eval_count",
        "json_valid",
        "schema_valid",
        "validation_reason",
        "abstain",
        "forbidden_markers",
        "candidate_blind_input",
        "output_file",
        "error",
    ]

    save_csv(
        DETAILS_FILE,
        records,
        detail_fields
    )

    # ========================================================
    # 17. By-event Summary
    # ========================================================

    by_event_counter = (
        defaultdict(Counter)
    )

    for row in records:

        by_event_counter[
            row["event_id"]
        ][
            row["status"]
        ] += 1

    by_event_rows = []

    for event_id in (
            expected_event_ids
    ):

        semantic_type = (
            events[event_id][
                "semantic_type"
            ]
            .strip()
        )

        counter = (
            by_event_counter[
                event_id
            ]
        )

        row = {
            "event_id":
                event_id,

            "semantic_type":
                semantic_type,

            "attempts":
                RUNS,

            "generated":
                counter[
                    "GENERATED"
                ],

            "abstains":
                counter[
                    "ABSTAIN"
                ],

            "forbidden_outputs":
                counter[
                    "FORBIDDEN_OUTPUT"
                ],

            "invalid_json":
                counter[
                    "INVALID_JSON"
                ],

            "invalid_schema":
                counter[
                    "INVALID_SCHEMA"
                ],

            "errors":
                counter[
                    "ERROR"
                ],

            "all_runs_generated":
                (
                        counter[
                            "GENERATED"
                        ]
                        == RUNS
                ),
        }

        by_event_rows.append(
            row
        )

    by_event_fields = [
        "event_id",
        "semantic_type",
        "attempts",
        "generated",
        "abstains",
        "forbidden_outputs",
        "invalid_json",
        "invalid_schema",
        "errors",
        "all_runs_generated",
    ]

    save_csv(
        BY_EVENT_FILE,
        by_event_rows,
        by_event_fields
    )

    # ========================================================
    # 18. Overall summary
    # ========================================================

    total = len(
        records
    )

    status_counter = Counter(
        row["status"]
        for row in records
    )

    generated = (
        status_counter[
            "GENERATED"
        ]
    )

    abstains = (
        status_counter[
            "ABSTAIN"
        ]
    )

    forbidden = (
        status_counter[
            "FORBIDDEN_OUTPUT"
        ]
    )

    invalid_json = (
        status_counter[
            "INVALID_JSON"
        ]
    )

    invalid_schema = (
        status_counter[
            "INVALID_SCHEMA"
        ]
    )

    empty_response = (
        status_counter[
            "EMPTY_RESPONSE"
        ]
    )

    errors = (
        status_counter[
            "ERROR"
        ]
    )

    runtimes = [
        row["runtime_ms"]
        for row in records
        if row["runtime_ms"] > 0
    ]

    average_runtime_ms = (
        sum(runtimes)
        / len(runtimes)
        if runtimes
        else 0
    )

    strict_generated_events = sum(
        1
        for row
        in by_event_rows
        if row[
            "all_runs_generated"
        ]
    )

    # ========================================================
    # 19. 按 semantic type 汇总
    # ========================================================

    type_summary = {}

    for semantic_type in sorted(
            SUPPORTED_TYPES
    ):

        subset = [
            row
            for row in records
            if row[
                   "semantic_type"
               ]
               == semantic_type
        ]

        counter = Counter(
            row["status"]
            for row in subset
        )

        attempts = len(
            subset
        )

        type_summary[
            semantic_type
        ] = {
            "attempts":
                attempts,

            "generated":
                counter[
                    "GENERATED"
                ],

            "generated_rate":
                (
                    counter[
                        "GENERATED"
                    ]
                    / attempts
                    if attempts
                    else 0
                ),

            "abstains":
                counter[
                    "ABSTAIN"
                ],

            "abstain_rate":
                (
                    counter[
                        "ABSTAIN"
                    ]
                    / attempts
                    if attempts
                    else 0
                ),

            "forbidden_outputs":
                counter[
                    "FORBIDDEN_OUTPUT"
                ],

            "invalid_schema":
                counter[
                    "INVALID_SCHEMA"
                ],
        }

    summary = {
        "experiment":
            "AUTO_FORMAL_POLICY_BATCH_V2",

        "prompt_version":
            PROMPT_VERSION,

        "model":
            MODEL,

        "events":
            len(
                expected_event_ids
            ),

        "runs":
            RUNS,

        "attempts":
            total,

        "generated":
            generated,

        "generated_rate":
            (
                generated / total
                if total
                else 0
            ),

        "abstains":
            abstains,

        "abstain_rate":
            (
                abstains / total
                if total
                else 0
            ),

        "forbidden_output":
            forbidden,

        "invalid_json":
            invalid_json,

        "invalid_schema":
            invalid_schema,

        "empty_response":
            empty_response,

        "errors":
            errors,

        "strict_all_runs_generated_events":
            strict_generated_events,

        "strict_event_generation_rate":
            (
                    strict_generated_events
                    / len(
                expected_event_ids
            )
            ),

        "average_runtime_ms":
            round(
                average_runtime_ms,
                2
            ),

        "candidate_blind":
            True,

        "semantic_type_aware":
            True,

        "oracle_used":
            False,

        "candidate_used":
            False,

        "manual_formal_policy_used":
            False,

        "by_semantic_type":
            type_summary,
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
    # 20. 打印结果
    # ========================================================

    print("\n" + "=" * 80)

    print(
        "GENERATION SUMMARY V2"
    )

    print("=" * 80)

    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2
        )
    )

    print(
        "\nDetails:"
    )

    print(
        DETAILS_FILE
    )

    print(
        "\nBy-event:"
    )

    print(
        BY_EVENT_FILE
    )

    print(
        "\nSummary:"
    )

    print(
        SUMMARY_FILE
    )

    print(
        "\nRaw policies:"
    )

    print(
        RAW_OUTPUT_DIR
    )

    print(
        "\nCandidate-blind evidence:"
    )

    print(
        CLEAN_EVIDENCE_DIR
    )


# ============================================================
# 21. 程序入口
# ============================================================

if __name__ == "__main__":
    main()