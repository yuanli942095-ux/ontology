from method_experiment_guard import add_legacy_opt_in_arg, block_legacy_entrypoint
import json
import re
import time
import urllib.request
from pathlib import Path


# ============================================================
# 1. 基本配置
# ============================================================

ROOT = Path(r"G:\LearnAI\ontology-evolution")

MODEL = "qwen3.5:9b"

OLLAMA_URL = "http://localhost:11434/api/generate"

RUNS = 5

TEMPERATURE = 0.2

NUM_PREDICT = 800

TIMEOUT = 180

SEED_BASE = 20260820


# ============================================================
# 2. 路径
# ============================================================

EXCERPT_DIR = (
        ROOT
        / "benchmark"
        / "external-real-v1"
        / "documents"
        / "excerpts"
)

OUTPUT_DIR = (
        ROOT
        / "output"
        / "auto-policy-v1"
)

RAW_OUTPUT_DIR = (
        OUTPUT_DIR
        / "raw"
)

RAW_OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)

DETAILS_FILE = (
        OUTPUT_DIR
        / "auto-policy-v1-generation-details.csv"
)

SUMMARY_FILE = (
        OUTPUT_DIR
        / "auto-policy-v1-generation-summary.json"
)


# ============================================================
# 3. Event 列表
# ============================================================

event_ids = [
    f"EXT_E{i:03d}"
    for i in range(1, 31)
]


# ============================================================
# 4. 固定 Prompt
# ============================================================

def build_prompt(
        event_id: str,
        public_evidence: str,
) -> str:

    return f"""
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

事件编号：
{event_id}

公开证据：

===== PUBLIC EVIDENCE BEGIN =====

{public_evidence}

===== PUBLIC EVIDENCE END =====
""".strip()


# ============================================================
# 5. Ollama 调用
# ============================================================

def call_qwen(
        prompt: str,
        seed: int,
):

    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "temperature": TEMPERATURE,
            "num_predict": NUM_PREDICT,
            "seed": seed,
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

    start = time.perf_counter()

    with urllib.request.urlopen(
            req,
            timeout=TIMEOUT
    ) as response:

        raw_response = response.read().decode(
            "utf-8"
        )

    runtime_ms = int(
        (time.perf_counter() - start)
        * 1000
    )

    data = json.loads(
        raw_response
    )

    return data, runtime_ms


# ============================================================
# 6. JSON 提取
# ============================================================

def extract_json(text: str):

    text = text.strip()

    if not text:
        return None

    try:
        return json.loads(text)
    except Exception:
        pass

    cleaned = text

    if cleaned.startswith("```json"):
        cleaned = cleaned[len("```json"):]

    elif cleaned.startswith("```"):
        cleaned = cleaned[len("```"):]

    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]

    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    brace_match = re.search(
        r"\{.*\}",
        cleaned,
        flags=re.S
    )

    if brace_match:
        try:
            return json.loads(
                brace_match.group(0)
            )
        except Exception:
            pass

    return None


# ============================================================
# 7. 防泄漏检查
# ============================================================

FORBIDDEN_MARKERS = [
    "oracle",
    "gold",
    "candidate_id",
    "cand_001",
    "cand_002",
    "cand_003",
    "allowed_values",
]


def check_forbidden_markers(text: str):

    lower_text = text.lower()

    found = []

    for marker in FORBIDDEN_MARKERS:
        if marker in lower_text:
            found.append(marker)

    return found


# ============================================================
# 8. CSV 工具
# ============================================================

def csv_escape(value):

    text = str(
        "" if value is None else value
    )

    text = text.replace(
        '"',
        '""'
    )

    return f'"{text}"'


def save_details_csv(records):

    header = [
        "event_id",
        "run",
        "seed",
        "status",
        "runtime_ms",
        "done_reason",
        "prompt_eval_count",
        "eval_count",
        "json_valid",
        "abstain",
        "forbidden_markers",
        "output_file",
        "error",
    ]

    lines = [
        ",".join(header)
    ]

    for row in records:

        values = [
            row.get("event_id"),
            row.get("run"),
            row.get("seed"),
            row.get("status"),
            row.get("runtime_ms"),
            row.get("done_reason"),
            row.get("prompt_eval_count"),
            row.get("eval_count"),
            row.get("json_valid"),
            row.get("abstain"),
            row.get("forbidden_markers"),
            row.get("output_file"),
            row.get("error"),
        ]

        lines.append(
            ",".join(
                csv_escape(v)
                for v in values
            )
        )

    DETAILS_FILE.write_text(
        "\n".join(lines),
        encoding="utf-8-sig"
    )


# ============================================================
# 9. 主实验
# ============================================================

def main():

    records = []

    total_attempts = (
            len(event_ids)
            * RUNS
    )

    attempt_index = 0

    print("=" * 80)
    print("AUTO FORMAL POLICY BATCH V1")
    print("=" * 80)

    print(
        f"events={len(event_ids)}, "
        f"runs={RUNS}, "
        f"attempts={total_attempts}"
    )

    print(
        f"model={MODEL}, "
        f"temperature={TEMPERATURE}"
    )

    print("=" * 80)

    for event_id in event_ids:

        evidence_file = (
                EXCERPT_DIR
                / f"{event_id}-evidence.md"
        )

        if not evidence_file.exists():

            for run_id in range(
                    1,
                    RUNS + 1
            ):

                attempt_index += 1

                print(
                    f"[{attempt_index}/{total_attempts}] "
                    f"{event_id} run={run_id} "
                    f"MISSING_EVIDENCE"
                )

                records.append({
                    "event_id": event_id,
                    "run": run_id,
                    "seed": (
                            SEED_BASE
                            + run_id
                            - 1
                    ),
                    "status": "MISSING_EVIDENCE",
                    "runtime_ms": 0,
                    "done_reason": "",
                    "prompt_eval_count": 0,
                    "eval_count": 0,
                    "json_valid": False,
                    "abstain": False,
                    "forbidden_markers": "",
                    "output_file": "",
                    "error": (
                        f"Evidence file not found: "
                        f"{evidence_file}"
                    ),
                })

            continue

        public_evidence = (
            evidence_file.read_text(
                encoding="utf-8"
            )
        )

        prompt = build_prompt(
            event_id=event_id,
            public_evidence=public_evidence,
        )

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
                f"[{attempt_index}/{total_attempts}] "
                f"{event_id} run={run_id}"
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
                    prompt=prompt,
                    seed=seed,
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

                parsed = extract_json(
                    text
                )

                forbidden = (
                    check_forbidden_markers(
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

                if not text:

                    status = (
                        "EMPTY_RESPONSE"
                    )

                elif forbidden:

                    status = (
                        "FORBIDDEN_OUTPUT"
                    )

                elif parsed is None:

                    status = (
                        "INVALID_JSON"
                    )

                elif abstain:

                    status = "ABSTAIN"

                else:

                    status = "GENERATED"

                output_record = {
                    "event_id": event_id,
                    "run": run_id,
                    "seed": seed,
                    "model": MODEL,
                    "source_type":
                        "PUBLIC_EVIDENCE_EXCERPT",
                    "source_file": str(
                        evidence_file.relative_to(
                            ROOT
                        )
                    ),
                    "oracle_used": False,
                    "candidate_used": False,
                    "manual_formal_policy_used":
                        False,
                    "status": status,
                    "runtime_ms": runtime_ms,
                    "done_reason": done_reason,
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
                        forbidden,
                    "response":
                        (
                            parsed
                            if parsed is not None
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

                records.append({
                    "event_id": event_id,
                    "run": run_id,
                    "seed": seed,
                    "status": status,
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
                    "abstain":
                        abstain,
                    "forbidden_markers":
                        "|".join(
                            forbidden
                        ),
                    "output_file":
                        str(
                            output_file.relative_to(
                                ROOT
                            )
                        ),
                    "error": "",
                })

                print(
                    f"  status={status} "
                    f"| runtime={runtime_ms}ms"
                )

            except Exception as e:

                records.append({
                    "event_id": event_id,
                    "run": run_id,
                    "seed": seed,
                    "status": "ERROR",
                    "runtime_ms": 0,
                    "done_reason": "",
                    "prompt_eval_count": 0,
                    "eval_count": 0,
                    "json_valid": False,
                    "abstain": False,
                    "forbidden_markers": "",
                    "output_file": "",
                    "error": (
                        f"{type(e).__name__}: "
                        f"{e}"
                    ),
                })

                print(
                    f"  status=ERROR "
                    f"| {type(e).__name__}: {e}"
                )

    save_details_csv(
        records
    )

    # ========================================================
    # 10. 汇总
    # ========================================================

    total = len(
        records
    )

    generated = sum(
        1
        for r in records
        if r["status"] == "GENERATED"
    )

    abstains = sum(
        1
        for r in records
        if r["status"] == "ABSTAIN"
    )

    invalid_json = sum(
        1
        for r in records
        if r["status"] == "INVALID_JSON"
    )

    forbidden_output = sum(
        1
        for r in records
        if r["status"] == "FORBIDDEN_OUTPUT"
    )

    errors = sum(
        1
        for r in records
        if r["status"] == "ERROR"
    )

    missing_evidence = sum(
        1
        for r in records
        if r["status"] == "MISSING_EVIDENCE"
    )

    empty_response = sum(
        1
        for r in records
        if r["status"] == "EMPTY_RESPONSE"
    )

    avg_runtime_ms = 0.0

    runtime_values = [
        r["runtime_ms"]
        for r in records
        if r["runtime_ms"] > 0
    ]

    if runtime_values:
        avg_runtime_ms = (
                sum(runtime_values)
                / len(runtime_values)
        )

    summary = {
        "experiment":
            "AUTO_FORMAL_POLICY_BATCH_V1",

        "model":
            MODEL,

        "events":
            len(event_ids),

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

        "invalid_json":
            invalid_json,

        "forbidden_output":
            forbidden_output,

        "empty_response":
            empty_response,

        "errors":
            errors,

        "missing_evidence":
            missing_evidence,

        "average_runtime_ms":
            round(
                avg_runtime_ms,
                2
            ),

        "prompt_version":
            "AUTO_POLICY_V1",

        "oracle_used":
            False,

        "candidate_used":
            False,

        "manual_formal_policy_used":
            False,
    }

    SUMMARY_FILE.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print("\n" + "=" * 80)
    print("GENERATION SUMMARY")
    print("=" * 80)

    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2
        )
    )

    print(
        f"\nDetails:\n{DETAILS_FILE}"
    )

    print(
        f"\nSummary:\n{SUMMARY_FILE}"
    )

    print(
        f"\nRaw policies:\n{RAW_OUTPUT_DIR}"
    )


# ============================================================
# 11. 入口
# ============================================================

if __name__ == "__main__":
    main()