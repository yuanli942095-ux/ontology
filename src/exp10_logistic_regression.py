from __future__ import annotations

"""Minimal binary logistic regression (no sklearn dependency)."""

import math
import random
from typing import Any


def sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


class LogisticRegressionModel:
    def __init__(self, weights: list[float], bias: float) -> None:
        self.weights = weights
        self.bias = bias
        self.classes_ = [0, 1]

    def predict_proba_one(self, vector: list[float]) -> float:
        score = self.bias
        for weight, value in zip(self.weights, vector, strict=True):
            score += weight * value
        return sigmoid(score)

    def coef_(self) -> list[list[float]]:
        return [list(self.weights)]

    def intercept_(self) -> list[float]:
        return [self.bias]

    def to_dict(self) -> dict[str, Any]:
        return {"weights": self.weights, "bias": self.bias, "classes": self.classes_}


def fit_logistic_regression(
    xs: list[list[float]],
    ys: list[int],
    *,
    epochs: int = 400,
    learning_rate: float = 0.05,
    seed: int = 20260903,
) -> LogisticRegressionModel:
    if not xs:
        raise ValueError("empty training data")
    dim = len(xs[0])
    rng = random.Random(seed)
    weights = [0.0] * dim
    bias = 0.0
    n = len(xs)
    for _ in range(epochs):
        grad_w = [0.0] * dim
        grad_b = 0.0
        for vector, label in zip(xs, ys, strict=True):
            pred = sigmoid(bias + sum(w * x for w, x in zip(weights, vector, strict=True)))
            error = pred - float(label)
            grad_b += error
            for index in range(dim):
                grad_w[index] += error * vector[index]
        scale = learning_rate / n
        bias -= scale * grad_b
        for index in range(dim):
            weights[index] -= scale * grad_w[index]
    return LogisticRegressionModel(weights=weights, bias=bias)
