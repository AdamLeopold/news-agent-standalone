"""ICE contract-reference import and leakage-safe same-contract labelling.

DBnomics identifies ICE as provider but does not identify the price field as
official settlement. Until independently verified these observations stay
``reference`` and must not score official settlement forecasts.
"""
from __future__ import annotations

import math
import re
import asyncio
from datetime import date, datetime, timedelta, timezone
from statistics import stdev

import httpx

from .store import Store
from .calendar import next_day, trading_day

DBNOMICS_URL = "https://api.db.nomics.world/v22/series/ICE/DUTCH_TTF_GAS_FUTURES"
ICE_DOCUMENTATION = "https://www.ice.com/products/27996665/Dutch-TTF-Gas-Futures/data"
ICE_CONTRACTS_URL = "https://www.ice.com/marketdata/api/productguide/charting/contract-data?productId=4331&hubId=7979"
ICE_HISTORY_URL = "https://www.ice.com/marketdata/api/productguide/charting/data/historical"
FETCHER_EVIDENCE = "https://git.nomics.world/dbnomics-fetchers/ice-fetcher/-/blob/3eb9e399588113f014e8341dd016f22feaf220bd/download.py"
MONTHS = {name: i + 1 for i, name in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def contract_delivery_month(label: str) -> date | None:
    match = re.fullmatch(r"([A-Za-z]{3})(\d{2})", label)
    if not match or match[1] not in MONTHS:
        return None
    return date(2000 + int(match[2]), MONTHS[match[1]], 1)


def parse_dbnomics(payload: dict, *, start_date: str = "2024-02-01", fetched_at: str | None = None) -> list[dict]:
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
    dataset = payload.get("dataset", {})
    labels = dataset.get("dimensions_values_labels", {}).get("CONTRACT", {})
    observations = []
    for series in payload.get("series", {}).get("docs", []):
        if series.get("dimensions", {}).get("FREQ") != "D":
            continue
        code = series.get("dimensions", {}).get("CONTRACT")
        label = labels.get(code, series.get("series_name", "").split("–")[-1].strip())
        delivery = contract_delivery_month(label)
        if delivery is None or delivery < date.fromisoformat(start_date).replace(day=1):
            continue  # Exclude quarterly/seasonal/calendar strips and expired old months.
        for day, value in zip(series.get("period", []), series.get("value", [])):
            if day < start_date or value in {None, "NA", "nan"}:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(number):
                continue
            observations.append({"contract": "ICE:DUTCH_TTF_GAS_FUTURES:D." + str(code),
                                 "contract_id": str(code), "contract_label": label,
                                 "delivery_month": delivery.isoformat(), "day": day, "value": number,
                                 "price_type": "reference", "verified": False,
                                 "provider": "ICE via DBnomics", "unit": "EUR/MWh",
                                 "source_url": "https://db.nomics.world/ICE/DUTCH_TTF_GAS_FUTURES/D." + str(code),
                                 "provider_documentation": dataset.get("doc_href", ICE_DOCUMENTATION),
                                 "provider_indexed_at": series.get("indexed_at"), "fetched_at": fetched_at,
                                 "price_type_note": "DBnomics metadata does not establish official settlement field; excluded from settlement scoring."})
    return observations


async def refresh_prices(store: Store, *, start_date: str = "2024-02-01") -> dict:
    state = dict(store.get_source_state("dbnomics_ice") or {})
    now = datetime.now(timezone.utc).isoformat()
    state.update({"source": "dbnomics_ice", "name": "ICE monthly contract references via DBnomics",
                  "status": "running", "last_attempt_at": now})
    store.set_source_state("dbnomics_ice", state)
    imported, series_total, offset = 0, None, 0
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True,
                                     headers={"User-Agent": "TTFNewsResearch/1.0"}) as client:
            while series_total is None or offset < series_total:
                response = await client.get(DBNOMICS_URL, params={"limit": 100, "offset": offset, "observations": 1})
                response.raise_for_status()
                payload = response.json()
                series = payload.get("series", {})
                docs = series.get("docs", [])
                series_total = int(series.get("num_found", len(docs)))
                for observation in parse_dbnomics(payload, start_date=start_date, fetched_at=now):
                    store.upsert_price(observation)
                    imported += 1
                if not docs:
                    break
                offset += len(docs)
        state.update({"status": "ok", "last_success_at": datetime.now(timezone.utc).isoformat(),
                      "observations_imported": imported, "series_scanned": offset,
                      "price_type": "reference", "settlement_verified": False, "last_error": None})
    except Exception as exc:
        state.update({"status": "failed", "last_error": str(exc)[:1000], "observations_imported": imported})
    store.set_source_state("dbnomics_ice", state)
    direct = await refresh_ice_chart_references(store, start_date=start_date)
    state["direct_ice_chart"] = {k: direct.get(k) for k in ["status", "observations_imported", "contracts", "last_error"]}
    store.set_source_state("dbnomics_ice", state)
    return state


def parse_ice_chart(payload: dict, *, contract_id: str, label: str,
                    fetched_at: str, start_date: str = "2024-02-01") -> list[dict]:
    if str(payload.get("marketId")) != str(contract_id):
        raise ValueError("ICE chart marketId does not match requested contract")
    delivery = contract_delivery_month(label)
    if not delivery:
        return []
    records = []
    for bar in payload.get("bars", []):
        if not isinstance(bar, list) or len(bar) != 2:
            continue
        day = datetime.strptime(bar[0], "%a %b %d %H:%M:%S %Y").date().isoformat()
        value = float(bar[1])
        if day < start_date or not math.isfinite(value):
            continue
        records.append({"contract": "ICE:DUTCH_TTF_GAS_FUTURES:D." + str(contract_id),
                        "contract_id": str(contract_id), "contract_label": label,
                        "delivery_month": delivery.isoformat(), "day": day, "value": value,
                        "price_type": "reference", "verified": False, "provider": "ICE public chart",
                        "unit": "EUR/MWh", "fetched_at": fetched_at,
                        "source_url": ICE_HISTORY_URL + "?marketId=" + str(contract_id) + "&historicalSpan=3",
                        "source_field": "bars[][1]", "provider_documentation": ICE_DOCUMENTATION,
                        "fetcher_evidence": FETCHER_EVIDENCE,
                        "price_type_note": "Official ICE chart bar field is not explicitly named settlement; remains reference and excluded from settlement scoring."})
    return records


async def refresh_ice_chart_references(store: Store, *, start_date: str = "2024-02-01") -> dict:
    """Refresh only three nearby monthly chart contracts, never a whole ICE crawl."""
    now = datetime.now(timezone.utc).isoformat()
    state = {"source": "ice_chart", "name": "ICE public chart (reference prices)", "status": "running",
             "last_attempt_at": now, "settlement_verified": False}
    store.set_source_state("ice_chart", state)
    imported, selected = 0, []
    try:
        async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
            response = await client.get(ICE_CONTRACTS_URL)
            response.raise_for_status()
            metadata = response.json()
            floor = datetime.now(timezone.utc).date().replace(day=1)
            eligible = [(contract_delivery_month(row.get("marketStrip", "")), row) for row in metadata]
            eligible = sorted([(month, row) for month, row in eligible if month and month > floor], key=lambda x: x[0])[:3]
            for _, row in eligible:
                contract_id, label = str(row["marketId"]), row["marketStrip"]
                await asyncio.sleep(1.05)
                response = await client.get(ICE_HISTORY_URL, params={"marketId": contract_id, "historicalSpan": 3})
                response.raise_for_status()
                records = parse_ice_chart(response.json(), contract_id=contract_id, label=label, fetched_at=now, start_date=start_date)
                for record in records:
                    store.upsert_price(record)
                    imported += 1
                selected.append({"contract_id": contract_id, "label": label, "latest_day": max((r["day"] for r in records), default=None)})
        state.update(status="ok", last_success_at=datetime.now(timezone.utc).isoformat(),
                     observations_imported=imported, contracts=selected, last_error=None)
    except Exception as exc:
        state.update(status="failed", last_error=(str(exc) or type(exc).__name__)[:1000], observations_imported=imported, contracts=selected)
    store.set_source_state("ice_chart", state)
    return state


def same_contract_sigma60(observations: list[dict], *, contract: str, prediction_day: str,
                          price_type: str = "settlement", require_verified: bool = True) -> dict:
    """Freeze 60 differences from 61 completed observations before prediction.

    Contract selection is explicit. Daily dates must precede prediction_day;
    future reference observations and a different roll contract cannot enter.
    """
    unique = {}
    for item in observations:
        if item.get("contract") != contract or item.get("day", "") >= prediction_day:
            continue
        if item.get("price_type") != price_type or (require_verified and not item.get("verified", False)):
            continue
        try:
            observed_day = date.fromisoformat(item["day"])
        except (ValueError, TypeError, KeyError):
            continue
        if not trading_day(observed_day):
            continue
        number = item.get("value")
        if isinstance(number, (int, float)) and math.isfinite(number):
            unique[item["day"]] = float(number)
    history = sorted(unique.items())[-61:]
    if len(history) < 61:
        return {"sigma60": None, "abnormal_threshold": None, "history_count": len(history),
                "status": "insufficient_same_contract_history", "contract": contract, "prediction_day": prediction_day}
    if not all(next_day(date.fromisoformat(previous[0])).isoformat() == current[0]
               for previous, current in zip(history, history[1:])):
        return {"sigma60": None, "abnormal_threshold": None, "history_count": len(history),
                "status": "nonconsecutive_same_contract_history", "contract": contract,
                "prediction_day": prediction_day, "history_start": history[0][0], "history_end": history[-1][0]}
    differences = [history[i][1] - history[i-1][1] for i in range(1, len(history))]
    sigma = stdev(differences)
    return {"sigma60": sigma, "abnormal_threshold": max(0.30, 2 * sigma),
            "history_count": len(history), "status": "ok", "contract": contract,
            "prediction_day": prediction_day, "history_start": history[0][0], "history_end": history[-1][0]}


def actual_direction(delta: float, sigma60: float | None) -> str | None:
    if not math.isfinite(delta):
        return None
    if abs(delta) <= 0.30 + 1e-9:
        return "flat"
    if sigma60 is None or not math.isfinite(sigma60):
        return None  # Cannot distinguish ordinary from abnormal moves.
    threshold = max(0.30, 2 * sigma60)
    if delta >= threshold:
        return "extreme_up"
    if delta <= -threshold:
        return "extreme_down"
    return "up" if delta > 0 else "down"
