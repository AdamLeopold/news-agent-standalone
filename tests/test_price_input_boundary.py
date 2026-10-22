"""Pure model-input boundary checks for all locally known price clocks."""
from datetime import datetime, timezone

import pytest

from ttf_news.analysis import _price_known


AS_OF = datetime(2026, 10, 21, 10, tzinfo=timezone.utc)
PAST = "2026-10-21T09:59:00+00:00"
NOW = "2026-10-21T10:00:00+00:00"
FUTURE = "2026-10-21T10:30:00+00:00"


def reference(**updates):
    return {"available": True, "contract": "ICE.same-month", "price_type": "reference",
            "price_date": "2026-10-20", "previous_price": 40, **updates}


@pytest.mark.parametrize("clock", ["available_at", "known_at", "version_seen_at", "first_seen_at", "discovered_at",
                                    "collected_at", "fact_excerpt_created_at", "price_known_at", "fetched_at",
                                    "revision_available_at", "revised_at", "updated_at", "threshold_frozen_as_of"])
def test_any_future_price_availability_rejects_whole_price_record(clock):
    item=reference(available_at=PAST, known_at=PAST, price_known_at=PAST)
    item[clock]=FUTURE
    notes=[]
    assert _price_known(item,AS_OF,notes) is None
    assert notes==["已排除分析时点之后的价格记录"]
    assert item["previous_price"]==40 and item[clock]==FUTURE


@pytest.mark.parametrize("clock", ["available_at", "known_at", "price_known_at", "fetched_at", "collected_at", "threshold_frozen_as_of"])
def test_equal_asof_price_availability_remains_eligible(clock):
    item=reference(**{clock:NOW})
    notes=[]
    assert _price_known(item,AS_OF,notes)==item
    assert notes==[]


def test_timezone_equivalent_cutoff_is_allowed():
    item=reference(price_known_at="2026-10-21T18:00:00+08:00",fetched_at=PAST)
    assert _price_known(item,AS_OF,[])==item


def test_child_quote_enforces_own_future_availability_without_dropping_known_parent():
    quote={"value":43,"observed_at":PAST,"available_at":PAST,"known_at":FUTURE}
    item=reference(price_known_at=PAST,intraday=quote)
    notes=[]
    result=_price_known(item,AS_OF,notes)
    assert result["previous_price"]==40 and "intraday" not in result
    assert item["intraday"]==quote
    assert len(notes)==1


def test_future_child_fetched_clock_is_not_borrowed_from_past_parent():
    item={"available_at":PAST,"observations":[{"value":40,"day":"2026-10-20","fetched_at":FUTURE},
                                               {"value":39,"day":"2026-10-19","fetched_at":PAST}]}
    result=_price_known(item,AS_OF,[])
    assert result["observations"]==[{"value":39,"day":"2026-10-19","fetched_at":PAST}]


def test_exactly_known_child_quote_is_preserved():
    quote={"value":43,"observed_at":PAST,"available_at":NOW,"known_at":NOW}
    item=reference(price_known_at=PAST,intraday=quote)
    assert _price_known(item,AS_OF,[])==item
