from __future__ import annotations

import math

from analyze_schema_contract_repair_ablation import binom_two_sided_p, paired_mcnemar


def test_mcnemar_all_b_only():
    a_rows = {(f"E{i}", "1"): {"full_closure_success": "False"} for i in range(9)}
    b_rows = {(f"E{i}", "1"): {"full_closure_success": "True"} for i in range(9)}
    result = paired_mcnemar(a_rows, b_rows, "full_closure_success")
    assert result["a_only"] == 0
    assert result["b_only"] == 9
    assert result["discordant_pairs"] == 9
    assert math.isclose(result["exact_mcnemar_p"], 2 * (0.5**9), rel_tol=1e-9)


def test_mcnemar_symmetric():
    a_rows = {("E1", "1"): {"full_closure_success": "True"}, ("E2", "1"): {"full_closure_success": "False"}}
    b_rows = {("E1", "1"): {"full_closure_success": "False"}, ("E2", "1"): {"full_closure_success": "True"}}
    result = paired_mcnemar(a_rows, b_rows, "full_closure_success")
    assert result["discordant_pairs"] == 2
    assert math.isclose(result["exact_mcnemar_p"], 1.0, rel_tol=1e-9)
