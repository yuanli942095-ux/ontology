from __future__ import annotations

"""Event-grouped confidence calibration helpers (no sklearn).

Folds are grouped by event_id so run1/run2/run3 of the same event never appear
in both train and validation. Oracle labels are post-hoc only.
"""

import math
from collections import defaultdict
from typing import Any


def event_grouped_folds(rows: list[dict[str, Any]], folds: int = 5) -> list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    events = sorted({str(row.get("event_id") or "") for row in rows})
    fold_of = {event: index % folds for index, event in enumerate(events)}
    out: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
    for fold in range(folds):
        train = [row for row in rows if fold_of[str(row.get("event_id") or "")] != fold]
        val = [row for row in rows if fold_of[str(row.get("event_id") or "")] == fold]
        if val:
            out.append((train, val))
    return out


def fit_platt(scores: list[float], labels: list[int]) -> tuple[float, float]:
    """Fit P = 1 / (1 + exp(a * score + b)) by Newton on Bernoulli NLL."""
    a, b = 0.0, 0.0
    n = max(1, len(scores))
    for _ in range(40):
        g_a = 0.0
        g_b = 0.0
        h_aa = 0.0
        h_ab = 0.0
        h_bb = 0.0
        for score, label in zip(scores, labels):
            z = a * score + b
            p = _sigmoid(z)
            err = p - label
            g_a += err * score
            g_b += err
            w = p * (1.0 - p)
            h_aa += w * score * score
            h_ab += w * score
            h_bb += w
        g_a /= n
        g_b /= n
        h_aa = h_aa / n + 1e-6
        h_ab /= n
        h_bb = h_bb / n + 1e-6
        det = h_aa * h_bb - h_ab * h_ab
        if abs(det) < 1e-12:
            break
        da = (h_bb * g_a - h_ab * g_b) / det
        db = (h_aa * g_b - h_ab * g_a) / det
        a -= da
        b -= db
        if abs(da) + abs(db) < 1e-8:
            break
    return a, b


def predict_platt(model: tuple[float, float], score: float) -> float:
    a, b = model
    return _sigmoid(a * score + b)


def fit_isotonic(scores: list[float], labels: list[int]) -> list[tuple[float, float]]:
    """PAV isotonic regression; returns (score, predicted_p) knots, nondecreasing in score."""
    pairs = sorted(zip(scores, labels), key=lambda item: item[0])
    if not pairs:
        return [(0.0, 0.0), (1.0, 1.0)]
    blocks: list[list[float]] = []
    weights: list[int] = []
    starts: list[float] = []
    ends: list[float] = []
    for score, label in pairs:
        blocks.append([float(label)])
        weights.append(1)
        starts.append(float(score))
        ends.append(float(score))
        while len(blocks) >= 2 and _mean(blocks[-2]) > _mean(blocks[-1]):
            merged = blocks[-2] + blocks[-1]
            w = weights[-2] + weights[-1]
            start = starts[-2]
            end = ends[-1]
            blocks[-2:] = [merged]
            weights[-2:] = [w]
            starts[-2:] = [start]
            ends[-2:] = [end]
    knots = []
    for values, start, end in zip(blocks, starts, ends):
        pred = _mean(values)
        knots.append((start, pred))
        if end != start:
            knots.append((end, pred))
    return knots


def predict_isotonic(knots: list[tuple[float, float]], score: float) -> float:
    if not knots:
        return 0.0
    if score <= knots[0][0]:
        return knots[0][1]
    if score >= knots[-1][0]:
        return knots[-1][1]
    for left, right in zip(knots, knots[1:]):
        if left[0] <= score <= right[0]:
            span = right[0] - left[0]
            if span <= 0:
                return right[1]
            t = (score - left[0]) / span
            return left[1] + t * (right[1] - left[1])
    return knots[-1][1]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _sigmoid(z: float) -> float:
    if z < -30:
        return 0.0
    if z > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def ece(pairs: list[tuple[float, int]], bins: int = 10) -> float:
    if not pairs:
        return 0.0
    buckets: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for pred, label in pairs:
        index = min(bins - 1, max(0, int(pred * bins)))
        buckets[index].append((pred, label))
    total = len(pairs)
    error = 0.0
    for items in buckets.values():
        conf = sum(pred for pred, _label in items) / len(items)
        acc = sum(label for _pred, label in items) / len(items)
        error += (len(items) / total) * abs(acc - conf)
    return error
