from __future__ import annotations

"""Exp10-C: logistic-regression candidate-only classifiers (C1 with ID, C2 without ID)."""

import argparse
import json
from pathlib import Path
from typing import Any

from exp10_candidate_shortcut_common import (
    DEFAULT_BENCHMARK,
    DEFAULT_SEEDS,
    EXP10_ROOT,
    FEATURE_FREEZE,
    TRAIN_BENCHMARKS,
    append_checkpoint,
    apply_closure,
    attach_oracle_fields,
    extract_features,
    feature_names,
    load_checkpoint,
    load_candidate_map,
    load_event_rows,
    load_oracles,
    shuffled_candidates,
    vectorize_row,
    write_method_outputs,
)
from exp9_baseline_common import aggregate_audit
from run_auto_policy_v4_ir_candidate_repair import configure_paths


def train_logistic_regression(xs: list[list[float]], ys: list[int], max_iter: int = 200) -> Any:
    try:
        from sklearn.linear_model import LogisticRegression
    except ImportError as exc:
        raise RuntimeError("scikit-learn is required for Exp10-C; pip install scikit-learn") from exc

    model = LogisticRegression(max_iter=max_iter, class_weight="balanced", random_state=20260903)
    model.fit(xs, ys)
    return model


def predict_best_candidate(
    model: Any,
    candidates: list[dict[str, Any]],
    names: list[str],
    include_id: bool,
) -> tuple[dict[str, Any] | None, float, str]:
    if not candidates:
        return None, 0.0, "no candidates"
    scored: list[tuple[float, dict[str, Any]]] = []
    for position, candidate in enumerate(candidates):
        feats = extract_features(candidate, position, include_id=include_id)
        vector = vectorize_row(feats, names)
        prob = float(model.predict_proba([vector])[0][1])
        scored.append((prob, candidate))
    scored.sort(key=lambda item: item[0], reverse=True)
    best_prob, best = scored[0]
    second_prob = scored[1][0] if len(scored) > 1 else 0.0
    if best_prob <= 0.5 or (best_prob - second_prob) < 0.05:
        return None, best_prob, f"low margin best={best_prob:.3f} second={second_prob:.3f}"
    return best, best_prob, f"logistic score={best_prob:.3f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=("c1-with-id", "c2-without-id"), required=True)
    parser.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--event-limit", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    if args.pilot:
        args.runs = 1

    include_id = args.model == "c1-with-id"
    condition = args.model
    method_label = "STAT_CLF_C1" if include_id else "STAT_CLF_C2"
    benchmark = args.benchmark_dir.resolve()
    output = (args.output_dir or EXP10_ROOT / "runs" / ("pilot" if args.pilot else "formal") / condition).resolve()
    if not FEATURE_FREEZE.is_file():
        raise FileNotFoundError(f"missing feature freeze: {FEATURE_FREEZE}")

    names = feature_names(include_id=include_id)

    xs_train: list[list[float]] = []
    ys_train: list[int] = []
    for train_benchmark in TRAIN_BENCHMARKS:
        events = load_event_rows(train_benchmark)
        oracles = load_oracles(train_benchmark)
        candidates_by_event = load_candidate_map(train_benchmark)
        for event in events:
            oracle_id = oracles[event["event_id"]]["oracle_candidate_id"]
            candidates = candidates_by_event.get(event["event_id"], [])
            for position, candidate in enumerate(candidates):
                feats = extract_features(candidate, position, include_id=include_id)
                xs_train.append(vectorize_row(feats, names))
                ys_train.append(1 if candidate["candidate_id"] == oracle_id else 0)
    model = train_logistic_regression(xs_train, ys_train)

    events = load_event_rows(benchmark, args.event_limit)
    oracles = load_oracles(benchmark)
    paths = configure_paths(benchmark)
    seeds = DEFAULT_SEEDS[: args.runs]
    checkpoint = output / "exp10-predictions.jsonl"
    done = load_checkpoint(checkpoint) if args.resume else {}

    graph_cache: dict[Path, Any] = {}
    reasoner_cache: dict[Path, dict[str, Any]] = {}
    owl_dir = output / "candidate-owls"
    owl_dir.mkdir(parents=True, exist_ok=True)
    rows_out: list[dict[str, Any]] = []

    for run_index, seed in enumerate(seeds, start=1):
        for event in events:
            key = (event["event_id"], run_index, condition)
            if key in done:
                rows_out.append(attach_oracle_fields(done[key], oracles[event["event_id"]]))
                continue
            candidates = shuffled_candidates(
                load_candidate_map(benchmark).get(event["event_id"], []),
                event["event_id"],
                seed,
            )
            selected, score, reason = predict_best_candidate(model, candidates, names, include_id=include_id)
            status = "SELECTED" if selected else "ABSTAIN"
            closure = apply_closure(
                event=event,
                selected=selected,
                selection_status=status,
                oracle=oracles[event["event_id"]],
                paths=paths,
                output_dir=owl_dir,
                graph_cache=graph_cache,
                reasoner_cache=reasoner_cache,
                timeout=args.timeout,
            )
            agg = aggregate_audit([])
            row = {
                "event_id": event["event_id"],
                "semantic_type": event["semantic_type"],
                "domain": event["domain"],
                "document_ids": event.get("document_ids", ""),
                "run": run_index,
                "seed": seed,
                "method": condition,
                "decision_path": method_label,
                "selection_reason": reason,
                "classifier_score": score,
                "api_error": "",
                **closure,
                **agg,
            }
            append_checkpoint(
                checkpoint,
                {k: v for k, v in row.items() if k not in {"oracle_candidate_id", "oracle_value", "selection_oracle_correct"}},
            )
            rows_out.append(row)
            print(
                f"{event['event_id']} run={run_index} {status} {row.get('selected_candidate_id', '')} "
                f"closure={row.get('full_closure_success')}",
                flush=True,
            )

    model_path = output / "logistic-model.json"
    model_payload = {
        "model": condition,
        "feature_names": names,
        "train_benchmarks": [str(path) for path in TRAIN_BENCHMARKS],
        "coefficients": model.coef_.tolist(),
        "intercept": model.intercept_.tolist(),
        "classes": model.classes_.tolist(),
    }
    model_path.write_text(json.dumps(model_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    write_method_outputs(
        condition=condition,
        method=method_label,
        rows=rows_out,
        output_dir=output,
        benchmark=benchmark,
        script_path=Path(__file__),
        prompt_file=None,
        pilot=args.pilot,
        extra={"classifier": condition, "feature_freeze": str(FEATURE_FREEZE)},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
