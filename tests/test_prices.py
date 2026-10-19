from datetime import date, timedelta

from ttf_news.prices import actual_direction, contract_delivery_month, parse_dbnomics, parse_ice_chart, same_contract_sigma60
from ttf_news.calendar import next_day


def test_reference_price_cannot_masquerade_as_settlement():
    payload = {"dataset": {"dimensions_values_labels": {"CONTRACT": {"a": "Nov26", "b": "Q4 26"}}},
               "series": {"docs": [{"dimensions": {"CONTRACT": "a", "FREQ": "D"}, "period": ["2026-10-12", "2026-10-13"], "value": [40.5, "NA"]},
                                   {"dimensions": {"CONTRACT": "b", "FREQ": "D"}, "period": ["2026-10-12"], "value": [41]}]}}
    rows = parse_dbnomics(payload)
    assert len(rows) == 1
    assert rows[0]["price_type"] == "reference"
    assert rows[0]["verified"] is False
    assert rows[0]["contract_id"] == "a"


def test_same_contract_sigma_excludes_future_and_rolls():
    observations = []
    days = [date(2026, 1, 2)]
    for _ in range(69):
        days.append(next_day(days[-1]))
    for i, value in enumerate(days):
        day = value.isoformat()
        observations.append({"contract": "A", "day": day, "value": 30 + i % 3, "price_type": "settlement", "verified": True})
        observations.append({"contract": "B", "day": day, "value": 1000 * i, "price_type": "settlement", "verified": True})
    prediction_day = days[65].isoformat()
    result = same_contract_sigma60(observations, contract="A", prediction_day=prediction_day)
    assert result["history_count"] == 61
    assert result["history_end"] < prediction_day
    assert result["sigma60"] < 3
    assert same_contract_sigma60(observations, contract="A", prediction_day="2026-02-01")["sigma60"] is None


def test_sigma_does_not_bridge_a_missing_trading_day_or_count_weekend():
    days = [date(2026, 1, 2)]
    for _ in range(69):
        days.append(next_day(days[-1]))
    rows = [{"contract": "A", "day": day.isoformat(), "value": 30 + i % 3,
             "price_type": "settlement", "verified": True} for i, day in enumerate(days)]
    prediction = days[65].isoformat()
    baseline = same_contract_sigma60(rows, contract="A", prediction_day=prediction)
    # A spurious Saturday bar does not count toward 61 trading observations.
    with_weekend = rows + [{"contract": "A", "day": "2026-01-03", "value": 100000,
                            "price_type": "settlement", "verified": True}]
    assert same_contract_sigma60(with_weekend, contract="A", prediction_day=prediction)["sigma60"] == baseline["sigma60"]
    missing = [row for row in rows if row["day"] != days[40].isoformat()]
    result = same_contract_sigma60(missing, contract="A", prediction_day=prediction)
    assert result["history_count"] == 61
    assert result["sigma60"] is None and result["abnormal_threshold"] is None
    assert result["status"] == "nonconsecutive_same_contract_history"


def test_actual_labels_and_insufficient_volatility():
    assert actual_direction(0.30, 1) == "flat"
    assert actual_direction(30.6 - 30.3, 1) == "flat"
    assert actual_direction(-0.30, 1) == "flat"
    assert actual_direction(2, 1) == "extreme_up"
    assert actual_direction(-2, 1) == "extreme_down"
    assert actual_direction(1, 1) == "up"
    assert actual_direction(-1, 1) == "down"
    assert actual_direction(1, None) is None
    assert contract_delivery_month("Nov26") == date(2026, 11, 1)
    assert contract_delivery_month("Winter26") is None


def test_official_chart_is_still_reference_and_validates_identity():
    import pytest
    payload = {"marketId": 6277389, "bars": [["Tue Oct 13 00:00:00 2026", 87.223]]}
    records = parse_ice_chart(payload, contract_id="6277389", label="Nov26", fetched_at="2026-10-14T03:00:00Z")
    assert records[0]["day"] == "2026-10-13"
    assert records[0]["price_type"] == "reference" and records[0]["verified"] is False
    with pytest.raises(ValueError):
        parse_ice_chart(payload, contract_id="different", label="Nov26", fetched_at="2026-10-14T03:00:00Z")
