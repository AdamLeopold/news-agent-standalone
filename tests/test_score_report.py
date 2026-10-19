import json

from ttf_news.score_report import CLASS_ORDER, build_score_report, classification_report, score_report
from ttf_news.store import Store


def record(actual, predicted, day="2026-10-14", **fields):
    return {"trade_date": day, "status": "evaluated", "actual_direction": actual, "predicted_direction": predicted,
            "data": json.dumps({"reason": "仅已验证的同合约日结算参与正式评分"}), **fields}


def test_empty_or_pending_data_has_no_synthetic_accuracy():
    for rows in ([], [record("up", "up", status="awaiting_verified_settlement")]):
        report = build_score_report(rows)
        assert report["status"] == "not_available"
        assert report["accuracy"] is None
        assert report["abnormal_binary"]["precision"] is None
        assert report["abnormal_binary"]["recall"] is None
        assert report["confusion_matrix"] == [[0] * 5 for _ in range(5)]
        assert all(item["precision"] is None and item["recall"] is None for item in report["per_class"].values())


def test_matrix_rates_months_and_stale_correct_are_independent():
    rows = [record("up", "up", "2026-09-01", correct=0), record("up", "down", "2026-09-02", correct=1),
            record("down", "up", "2026-10-01"), record("flat", "flat", "2026-10-02")]
    report = build_score_report(rows)
    assert report["class_order"] == list(CLASS_ORDER)
    assert report["eligible_count"] == 4 and report["accuracy"] == 0.5
    up, down = CLASS_ORDER.index("up"), CLASS_ORDER.index("down")
    assert report["confusion_matrix"][up][down] == 1
    assert report["confusion_matrix"][down][up] == 1
    assert report["per_class"]["up"] == {"count": 2, "predicted_count": 2, "true_positive": 1, "precision": 0.5, "recall": 0.5}
    assert [(row["month"], row["count"], row["accuracy"]) for row in report["monthly"]] == [("2026-09", 2, 0.5), ("2026-10", 2, 0.5)]
    assert report["per_class"]["extreme_up"]["precision"] is None


def test_combined_abnormal_detection_keeps_direction_error_in_accuracy():
    rows = [record("extreme_up", "extreme_down"), record("extreme_down", "flat"),
            record("up", "extreme_up"), record("flat", "flat")]
    report = build_score_report(rows)
    binary = report["abnormal_binary"]
    assert binary["true_positive"] == binary["false_negative"] == binary["false_positive"] == binary["true_negative"] == 1
    assert binary["precision"] == binary["recall"] == 0.5
    assert report["accuracy"] == 0.25
    assert report["per_class"]["extreme_up"]["recall"] == 0


def test_reference_unverified_unsupported_and_invalid_metadata_are_excluded():
    rows = [record("up", "up", data='{"price_type":"reference"}'),
            record("up", "up", data='{"verified":false}'), record("up", "up", price_type="reference"),
            record("bullish_ttf", "up"), record("up", "up", data="broken JSON"),
            record("up", "up", status="insufficient_volatility_history")]
    report = build_score_report(rows)
    assert report["status"] == "not_available" and report["excluded_count"] == 6
    assert report["exclusion_reasons"] == {"reference_or_unverified": 3, "unsupported_direction": 1,
                                           "invalid_evaluation_metadata": 1, "not_verified_evaluated": 1}


def test_zero_denominator_is_null_but_real_zero_score_is_zero():
    report = build_score_report([record("up", "down")])
    assert report["accuracy"] == 0
    assert report["per_class"]["up"]["precision"] is None
    assert report["per_class"]["up"]["recall"] == 0
    assert report["per_class"]["down"]["precision"] == 0
    assert report["per_class"]["down"]["recall"] is None


def test_store_reader_and_monitor_alias(tmp_path):
    store = Store(tmp_path / "score.db")
    with store.connection() as connection:
        connection.execute("INSERT INTO evaluations VALUES (?,?,?,?,?,?,?,?,?,?)",
                           ("2026-10-14", "analysis", "contract", 1.0, 2.0, "up", "up", 0, "evaluated", '{}'))
    report = score_report(store)
    assert report["eligible_count"] == 1 and report["accuracy"] == 1
    assert classification_report is build_score_report
