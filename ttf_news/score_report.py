"""Independent five-class scores from verified settlement evaluations only.

``evaluated`` is the existing evaluator's verification gate: it is assigned
after both the frozen previous price and realised same-contract settlement are
verified. Reference/event-descriptive outcomes never enter this report.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

CLASS_ORDER = ("extreme_up", "up", "flat", "down", "extreme_down")
EXTREME_CLASSES = frozenset(("extreme_up", "extreme_down"))


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _summarize(rows: list[dict]) -> dict:
    positions = {label: position for position, label in enumerate(CLASS_ORDER)}
    matrix = [[0 for _ in CLASS_ORDER] for _ in CLASS_ORDER]
    abnormal = {"true_positive": 0, "false_positive": 0, "false_negative": 0, "true_negative": 0}
    correct = 0
    for row in rows:
        actual, predicted = row["actual_direction"], row["predicted_direction"]
        matrix[positions[actual]][positions[predicted]] += 1
        correct += int(actual == predicted)  # Recompute; do not trust a stale stored correct flag.
        actual_extreme, predicted_extreme = actual in EXTREME_CLASSES, predicted in EXTREME_CLASSES
        key = ("true_positive" if predicted_extreme else "false_negative") if actual_extreme else (
            "false_positive" if predicted_extreme else "true_negative")
        abnormal[key] += 1
    per_class = {}
    for label, index in positions.items():
        count = sum(matrix[index])
        predicted_count = sum(row[index] for row in matrix)
        tp = matrix[index][index]
        per_class[label] = {"count": count, "predicted_count": predicted_count, "true_positive": tp,
                            "precision": _ratio(tp, predicted_count), "recall": _ratio(tp, count)}
    abnormal.update({"actual_count": abnormal["true_positive"] + abnormal["false_negative"],
                     "predicted_count": abnormal["true_positive"] + abnormal["false_positive"],
                     "precision": _ratio(abnormal["true_positive"], abnormal["true_positive"] + abnormal["false_positive"]),
                     "recall": _ratio(abnormal["true_positive"], abnormal["true_positive"] + abnormal["false_negative"]),
                     "definition": "异常暴涨/暴跌合并为异常事件；异常方向对错仍计入五分类准确率。"})
    return {"status": "available" if rows else "not_available", "count": len(rows), "correct_count": correct,
            "accuracy": _ratio(correct, len(rows)), "confusion_matrix": matrix,
            "per_class": per_class, "abnormal_binary": abnormal}


def build_score_report(evaluations: Iterable[Mapping[str, Any]]) -> dict:
    """Accept rows already loaded by monitoring_report; all rates are 0..1.

    Matrix rows are actual classes and columns are predicted classes, both in
    class_order. A denominator of zero is unknown (null), never a synthetic 0%.
    """
    eligible, excluded = [], Counter()
    total = 0
    for original in evaluations:
        row = dict(original)
        total += 1
        if row.get("status") != "evaluated":
            excluded["not_verified_evaluated"] += 1
            continue
        if row.get("actual_direction") not in CLASS_ORDER or row.get("predicted_direction") not in CLASS_ORDER:
            excluded["unsupported_direction"] += 1
            continue
        data = row.get("data") or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (ValueError, TypeError):
                excluded["invalid_evaluation_metadata"] += 1
                continue
        if not isinstance(data, dict):
            excluded["invalid_evaluation_metadata"] += 1
            continue
        # Explicit contradictory metadata cannot override the verification gate.
        if row.get("price_type") == "reference" or data.get("price_type") == "reference" or data.get("verified") is False:
            excluded["reference_or_unverified"] += 1
            continue
        eligible.append(row)
    result = _summarize(eligible)
    groups = defaultdict(list)
    for row in eligible:
        month = str(row.get("trade_date") or "unknown")[:7]
        groups[month].append(row)
    result.update({"class_order": list(CLASS_ORDER), "matrix_rows": "actual", "matrix_columns": "predicted",
                   "total_records": total, "eligible_count": len(eligible), "excluded_count": total - len(eligible),
                   "exclusion_reasons": dict(excluded),
                   "monthly": [{"month": month, **_summarize(rows)} for month, rows in sorted(groups.items())],
                   "scope": "仅本独立模型的已验证同合约日结算五分类预测；不使用reference价格或multiagent准确率。"})
    if not eligible:
        result["reason"] = "没有合法且已验证的五分类结算评估记录，预测准确率与异常识别率暂不可报告。"
    return result


def score_report(store) -> dict:
    with store.connection() as connection:
        rows = [dict(row) for row in connection.execute("SELECT * FROM evaluations ORDER BY trade_date")]
    return build_score_report(rows)


classification_report = build_score_report
