"""Local Ollama news analysis with temporal, provenance and context safeguards.

Queueing and persistence belong to the service. No network/model launch occurs
at import time and no labels are invented after a failed inference.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import ValidationError

from .prompts import PROMPT_BASELINE, PROMPT_VERSION, repair_prompt, system_prompt
from .schemas import AnalysisResult, Reference


class AnalysisError(RuntimeError):
    """Readable error shown by the task/UI without a fabricated forecast."""


class ContextError(AnalysisError):
    pass


_ENCODING = None


def count_tokens(text: str) -> int:
    """Use gpt-oss's vocabulary; UTF-8 byte count is a safe fallback upper bound."""
    global _ENCODING
    try:
        if _ENCODING is None:
            import tiktoken

            os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(Path(__file__).resolve().parents[1] / "data" / "tokenizer"))
            _ENCODING = tiktoken.get_encoding("o200k_harmony")
        return len(_ENCODING.encode(text, disallowed_special=()))
    except (ImportError, OSError, ValueError):
        return len(text.encode("utf-8"))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=str)


def _datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    # Never invent a zone for an exact-but-naive timestamp. Date-only records
    # can still be used as imprecise dated context and are marked in metadata.
    if result.tzinfo is None:
        if len(str(value)) == 10:
            return result.replace(tzinfo=timezone.utc)
        return None
    return result.astimezone(timezone.utc)


def _first_time(item: dict, names: tuple[str, ...]) -> datetime | None:
    for name in names:
        if name in item:
            value = _datetime(item[name])
            if value is not None:
                return value
    return None


_NEWS_TIMES = ("published_at", "published_date", "publication_time", "timestamp", "date", "Date", "trade_date", "session_date")
_AVAILABLE_TIMES = ("available_at", "known_at", "version_seen_at", "first_seen_at", "discovered_at", "collected_at")
_EVENT_TIMES = ("start_date", "event_date", "date", "Date", "published_at", "start_at")
_OUTCOME_KEYS = {
    "outcome", "outcomes", "label", "historical_label", "impact_label", "actual_label",
    "realized_direction", "realized_return", "actual_direction", "price_response",
    "price_reaction", "settlement_change", "future_return", "target", "true_direction",
    "next_close", "current_close", "target_close", "actual_close", "realized_close",
}


def _strip_outcomes(item: Any, permit: bool = False) -> Any:
    if isinstance(item, dict):
        return {
            key: _strip_outcomes(value, permit)
            for key, value in item.items()
            if permit or key.lower() not in _OUTCOME_KEYS
        }
    if isinstance(item, list):
        return [_strip_outcomes(row, permit) for row in item]
    return item


def _known(item: dict, as_of: datetime, event: bool = False) -> bool:
    names = _EVENT_TIMES if event else _NEWS_TIMES
    observation = None
    for name in names:
        value = item.get(name)
        if not value:
            continue
        if not event and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
            # A publication date is a local calendar fact, never an invented
            # midnight timestamp. A known EU article near local midnight must
            # not be rejected because UTC is still on the preceding date.
            source_zone = {"eia": "America/New_York", "eu_energy": "Europe/Brussels"}.get(item.get("source"), item.get("timezone") or "UTC")
            try:
                local_day = as_of.astimezone(ZoneInfo(source_zone)).date().isoformat()
            except (ZoneInfoNotFoundError, TypeError, ValueError):
                local_day = as_of.date().isoformat()
            if str(value) > local_day:
                return False
            continue
        observation = _datetime(value)
        if observation is not None:
            break
    availability = [_datetime(item.get(name)) for name in _AVAILABLE_TIMES]
    revisions = [_datetime(item.get(name)) for name in ("revision_available_at", "revised_at", "updated_at")]
    return not any(moment and moment > as_of for moment in [observation, *availability, *revisions])


def _price_known(value: Any, as_of: datetime, notes: list[str]) -> Any:
    """Exclude future or same-day undated daily closing prices recursively."""
    if isinstance(value, list):
        return [safe for row in value if (safe := _price_known(row, as_of, notes)) is not None]
    if not isinstance(value, dict):
        return value
    observed = _first_time(value, ("observed_at", "observation_at", "timestamp", "date", "Date", "price_date", "day"))
    available = _first_time(value, _AVAILABLE_TIMES)
    if any(moment and moment > as_of for moment in (observed, available)):
        notes.append("已排除分析时点之后的价格记录")
        return None
    date_value = value.get("date", value.get("Date", value.get("price_date", value.get("day"))))
    has_price = any(key in value for key in ("price", "value", "close", "settlement", "previous_price"))
    if has_price and not observed and not available:
        notes.append("缺少时间戳的价格记录未进入模型")
        return None
    if has_price and date_value and len(str(date_value)) == 10 and available is None:
        if str(date_value) >= as_of.date().isoformat():
            notes.append("同日仅有日期且无可用时间的日价未进入模型")
            return None
    result = {}
    for key, row in _strip_outcomes(value).items():
        safe = _price_known(row, as_of, notes)
        if safe is not None:
            result[key] = safe
    return result


_FACT_WORDS = re.compile(
    r"outage|disrupt|sanction|strike|restart|resum|cancel|not\b|no\b|revers|storage|inventory|"
    r"LNG|TTF|gas|supply|demand|flow|pipeline|capacity|weather|cold|mild|warm|cut|delay|"
    r"供应|中断|检修|停产|制裁|罢工|恢复|取消|否认|不|库存|液化|需求|流量|管道|天气|寒|暖|\d",
    re.IGNORECASE,
)
_CATEGORIES = {
    "supply": re.compile(r"outage|disrupt|pipeline|supply|flow|供应|中断|检修|流量|管道", re.I),
    "lng": re.compile(r"LNG|liquef|液化|shipping|cargo|船", re.I),
    "storage": re.compile(r"storage|inventory|库存|储气", re.I),
    "weather_demand": re.compile(r"weather|cold|warm|mild|demand|天气|寒|暖|需求", re.I),
    "policy": re.compile(r"sanction|war|strike|policy|制裁|战争|政策|罢工", re.I),
}


def _sentences(text: str) -> list[str]:
    # Decimal points and unit abbreviations are evidence, not sentence ends.
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+|(?<=[。！？;；])\s*|\n+", text) if sentence.strip()]


def _digest(text: Any, budget: int) -> str:
    """Extract whole fact-bearing clauses, including negations and quantities."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if count_tokens(text) <= budget:
        return text
    sentences = _sentences(text)
    ranked = sorted(enumerate(sentences), key=lambda pair: (-len(_FACT_WORDS.findall(pair[1])), pair[0]))
    selected: dict[int, str] = {}
    total = 0
    for index, sentence in ranked:
        size = count_tokens(sentence)
        if size + total <= budget:
            selected[index] = sentence
            total += size
    if selected:
        return " ".join(selected[index] for index in sorted(selected))
    # A source may provide a single enormous paragraph. Extract whole clauses,
    # then a budgeted leading span only as an explicitly marked last resort.
    clauses = re.split(r"[,，:：]", text)
    fact_clauses = [part.strip() for part in clauses if _FACT_WORDS.search(part)]
    result = ""
    for clause in fact_clauses:
        if count_tokens(result + clause) <= budget:
            result += ("; " if result else "") + clause
    if result:
        return result
    while text and count_tokens(text + " [摘要截短]") > budget:
        text = text[:max(0, len(text) - max(1, len(text) // 10))]
    return text + " [摘要截短]" if text else "[原文过长，事实未展开]"


def _source(item: dict, alias: str) -> dict:
    source = {
        "alias": alias,
        "id": str(item.get("id", item.get("news_id", item.get("event_id", alias)))),
        "url": str(item.get("url", item.get("source_url", "")) or ""),
        "title": str(item.get("title", item.get("headline", item.get("event", ""))) or ""),
    }
    for key in ("version", "source", "published_at", "published_date", "first_seen_at", "known_at", "timestamp_precision", "source_quality", "window_membership"):
        if key in item:
            source[key] = item[key]
    return source


def _text(item: dict) -> str:
    title = item.get("title", item.get("headline", item.get("event", "")))
    body = item.get("summary", item.get("description", item.get("body", item.get("content", item.get("text", "")))))
    # Manual forms often use the full body as the title. Do not duplicate it:
    # repeated instruction-bearing sentences must not displace primary facts.
    if str(body or "").startswith(str(title or "")):
        return str(body or "")
    return str(title or "") + ". " + str(body or "")


def _operational_time(item: dict, as_of: datetime) -> dict:
    info = {key: item[key] for key in ("event_start", "event_end", "event_status", "technical_capacity", "available_capacity", "unavailable_capacity", "capacity_unit") if item.get(key) is not None}
    if not info:
        return info
    def parse_notice(value):
        moment = _datetime(value)
        if moment is None and item.get("source") == "gassco" and value:
            from .ingest import parse_timestamp
            moment = _datetime(parse_timestamp(str(value), default_zone="Europe/Oslo").get("published_at"))
        return moment
    start, end = parse_notice(item.get("event_start")), parse_notice(item.get("event_end"))
    if start:
        info["event_start_utc"] = start.isoformat()
        info["scheduled_for_future"] = start > as_of
    if end:
        info["event_end_utc"] = end.isoformat()
        info["announced_end_passed"] = end <= as_of
    if item.get("source") == "gassco":
        info["capacity_is_not_lost_flow"] = True
        info["actual_flow_recovery"] = "未提供独立实际流量数据，恢复状态待核实"
    return info


def _news_card(item: dict, alias: str, budget: int, as_of: datetime | None = None) -> dict:
    card = {"id": alias, "time": next((item[k] for k in _NEWS_TIMES if item.get(k)), None), "facts": _digest(_text(item), budget), "time_precision": item.get("timestamp_precision", item.get("time_precision", "unknown"))}
    if item.get("window_membership"):
        card["window_membership"] = item["window_membership"]
    operational = _operational_time(item, as_of) if as_of else {key: item[key] for key in ("event_start", "event_end", "event_status") if item.get(key) is not None}
    if operational:
        card["operational"] = operational
    return card


def _previous_text(item: dict) -> str:
    """Daily aggregate count summaries must not erase yesterday's actual news."""
    groups = item.get("groups")
    if isinstance(groups, dict):
        seen = set()
        facts = []
        for category, rows in groups.items():
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                identity = row.get("id", _text(row))
                if identity in seen:
                    continue
                seen.add(identity)
                facts.append(f"{category}: {_text(row)}")
        if facts:
            return "\n".join(facts)
    return str(item.get("summary", item.get("news_summary", _text(item))))


def _previous_source_index(rows: list[dict]) -> list[dict]:
    index = []
    for day_index, item in enumerate(rows[-5:], 1):
        seen = set()
        sources = []
        for group in (item.get("groups") or {}).values():
            for row in group if isinstance(group, list) else []:
                if not isinstance(row, dict):
                    continue
                identifier = str(row.get("id", _text(row)))
                if identifier in seen:
                    continue
                seen.add(identifier)
                sources.append(_source(row, f"P{day_index}.{len(sources)+1}"))
        index.append({"date": item.get("session_date", item.get("date", item.get("trade_date"))),
                      "news_ids": item.get("news_ids", list(seen)), "sources": sources})
    return index


def _previous_summary(item: dict, budget: int) -> dict:
    # Previous days are summaries, not a second all-article evidence window.
    # Preserve their full article lineage separately in metadata.
    summary = {"date": item.get("session_date", item.get("date", item.get("trade_date"))),
               "article_count": item.get("article_count", len(item.get("news_ids", []))),
               "facts": _digest(_previous_text(item), budget)}
    groups = item.get("groups")
    if isinstance(groups, dict):
        summary["category_counts"] = {category: len({str(row.get("id", _text(row))) for row in rows if isinstance(row, dict)})
                                      for category, rows in groups.items() if isinstance(rows, list) and rows}
    return summary


def _coverage(value: dict, level: int) -> dict:
    result = {}
    for key in ("article_count", "observed_imprecise_count", "historical_timestamps", "missing_fields", "risk_flags"):
        if key in value:
            result[key] = value[key]
    sources = value.get("sources", [])
    if isinstance(sources, list):
        fields = ("id", "status", "state", "enabled") if level >= 2 else ("id", "source", "name", "status", "state", "last_success_at", "last_error_at", "enabled")
        result["sources"] = [
            {key: row[key] for key in fields if key in row}
            for row in sources if isinstance(row, dict)
        ]
        # Preserve a short failure reason: absence and failed collection differ.
        for compact, row in zip(result["sources"], [row for row in sources if isinstance(row, dict)]):
            if row.get("error", row.get("last_error")):
                compact["error"] = _digest(row.get("error", row.get("last_error")), 40 if level < 2 else 15)
    return result


def _known_outcomes(outcomes: Any, as_of: datetime) -> dict:
    if not isinstance(outcomes, dict):
        return {}
    from .calendar import cutoff, trading_day
    from datetime import date

    result = {}
    for horizon, outcome in outcomes.items():
        if not isinstance(outcome, dict):
            continue
        available = _first_time(outcome, ("available_at", "outcome_available_at"))
        if available is None or available > as_of:
            continue
        end_date = outcome.get("end_date")
        if not end_date:
            continue
        try:
            day = date.fromisoformat(str(end_date)[:10])
            if not trading_day(day) or cutoff(day) > as_of:
                continue
        except (TypeError, ValueError):
            continue
        result[horizon] = copy.deepcopy(outcome)
    return result


def _compact_outcomes(outcomes: dict) -> dict:
    # Longer-horizon history remains in the immutable input snapshot. One and
    # three trading-day reference moves are enough for a bounded news brief.
    keep = {}
    for horizon in ("1d", "3d"):
        value = outcomes.get(horizon)
        if isinstance(value, dict):
            keep[horizon] = {key:value[key] for key in ("change", "price_type", "contract", "end_date", "available_at") if key in value}
    return keep


def _lexicon(value: dict, level: int) -> dict:
    if level < 2:
        return _strip_outcomes(value)
    return {key: list(row) if key == "categories" and isinstance(row, dict) else row
            for key, row in _strip_outcomes(value).items()}


def _group_news(rows: list[dict], aliases: list[str], budget: int) -> list[dict]:
    groups: dict[str, dict] = {}
    for item, alias in zip(rows, aliases):
        text = _text(item)
        category = next((key for key, pattern in _CATEGORIES.items() if pattern.search(text)), "other")
        group = groups.setdefault(category, {"category": category, "ids": [], "facts": []})
        group["ids"].append(alias)
        if item.get("window_membership") == "first_observed_in_window_publication_window_unknown":
            group.setdefault("publication_window_unknown_ids", []).append(alias)
        fact = _digest(text, budget)
        if fact not in group["facts"]:
            group["facts"].append(fact)
    return list(groups.values())


def prepare_context(context: dict, detail: bool = False, reserve_tokens: int = 0) -> tuple[dict, dict]:
    """Return bounded model input and a complete, separately persisted index.

    Every eligible window news item keeps a compact evidence alias even in
    grouped mode. If that minimum index itself cannot fit, fail explicitly.
    """
    as_of = _datetime(context.get("as_of"))
    if as_of is None:
        raise ContextError("分析时点 as_of 必须是带时区的 ISO 时间戳")
    current = copy.deepcopy(context.get("current_news") or {})
    if not current or not _text(current).strip(". "):
        raise ContextError("当前新闻正文或标题为空")
    if not _known(current, as_of):
        raise ContextError("当前新闻的发布、发现或修订时间晚于分析时点")
    notes: list[str] = []
    rows = [copy.deepcopy(row) for row in context.get("window_news", []) if isinstance(row, dict) and _known(row, as_of)]
    filtered_news = len(context.get("window_news", [])) - len(rows)
    events = []
    for row in context.get("historical_events", []):
        if not isinstance(row, dict) or not _known(row, as_of, event=True):
            continue
        event = copy.deepcopy(row)
        outcome_time = _first_time(event, ("outcome_available_at", "reaction_available_at", "label_available_at"))
        permit_outcome = outcome_time is not None and outcome_time <= as_of
        known_outcomes = _known_outcomes(event.get("outcomes"), as_of)
        event = _strip_outcomes(event, permit_outcome)
        event.pop("outcomes", None)
        if known_outcomes:
            event["outcomes"] = known_outcomes
        if not permit_outcome and not known_outcomes:
            event["outcome_note"] = "事后价格反应不可用于本时点分析"
        events.append(event)
    previous = [copy.deepcopy(row) for row in context.get("previous_sessions", []) if isinstance(row, dict) and _known(row, as_of)]
    source_index = [_source(current, "N0")]
    aliases = [f"N{index}" for index in range(1, len(rows) + 1)]
    source_index.extend(_source(row, alias) for row, alias in zip(rows, aliases))
    source_index.extend(_source(row, f"E{index}") for index, row in enumerate(events, 1))
    all_urls = {row["alias"]: row for row in source_index}
    price = _price_known(copy.deepcopy(context.get("price_reference") or {}), as_of, notes)
    system = system_prompt(detail)
    selected = None
    # The triggering news always receives a protected 300-token fact budget.
    # Compress background before current facts, rather than throwing away the
    # supply shock that is the reason for the inference.
    levels = ((300, 110, 200, 100), (300, 65, 130, 90), (300, 35, 85, 80), (300, 18, 45, 70), (300, 8, 20, 60), (300, 8, 20, 60))
    # Start detail at 5000 input / 3072 output. Rich known outcomes can require
    # 5500 input; its 2560 output reserve still fits the same 8192 window. Both
    # are substantially above measured complete detail outputs (<1100 tokens).
    ceilings = [5000, 5500] if detail else [2400, 5500]
    attempts = [(ceiling, level, budgets) for ceiling in ceilings for level, budgets in enumerate(levels)]
    for ceiling, level, (current_budget, news_budget, event_budget, previous_budget) in attempts:
        limit = ceiling - reserve_tokens
        context_window = 8192 if ceiling > 2400 else 4096
        if limit <= count_tokens(system) + 100:
            continue
        compact_events = []
        for index, row in enumerate(events, 1):
            card = {"id": f"E{index}", "date": next((row[k] for k in _EVENT_TIMES if row.get(k)), None), "facts": _digest(_text(row), event_budget), "status": row.get("status", row.get("state", "unknown")), "category": row.get("category"), "source_quality": row.get("source_quality", "unknown")}
            # Exact operational dates/status appear once in the operational
            # object. Repeating them outside wasted the rich-detail budget.
            for key in ("outcome", "outcomes", "label", "price_response", "price_reaction", "outcome_available_at", "outcome_note"):
                if key in row:
                    card[key] = _compact_outcomes(row[key]) if key == "outcomes" else _digest(row[key], event_budget) if isinstance(row[key], str) else row[key]
            operational = _operational_time(row, as_of)
            if operational:
                if level >= 1:
                    # Imported event timestamps are already canonical UTC.
                    # Preserve their instant once; the immutable snapshot keeps
                    # original strings. Current triggering news is not reduced.
                    for original in ("event_start", "event_end"):
                        if original+"_utc" in operational:
                            operational.pop(original,None)
                    if "actual_flow_recovery" in operational:
                        operational.pop("actual_flow_recovery")
                        operational["actual_flow_recovery_verified"]=False
                card["operational"] = operational
            compact_events.append(card)
        previous_rows=[_previous_summary(row,previous_budget+(20 if detail and level<5 else 0)) for row in previous[-5:]]
        if level>=5:
            # Last-resort background compaction retains all five dates/counts
            # and 60-token fact summaries. Category/article lineage remains in
            # the original input snapshot and complete previous_source_index.
            for row in previous_rows:
                row.pop("category_counts",None)
        selected = {
            "as_of": context["as_of"],
            "session": _strip_outcomes(context.get("session") or {}),
            "analysis_kind": context.get("analysis_kind", "live"),
            "current_news": _news_card(current, "N0", current_budget, as_of),
            "window_news": [_news_card(row, alias, news_budget, as_of) for row, alias in zip(rows, aliases)] if level < 3 else _group_news(rows, aliases, news_budget),
            "previous_sessions": previous_rows,
            "historical_events": compact_events,
            "price_reference": price,
            "lexicon": _lexicon(context.get("lexicon") or {}, level),
            "coverage": _coverage(context.get("coverage") or {}, level),
            "source_urls": {alias: source["url"] for alias, source in all_urls.items() if source["url"]},
            "compression": {"level": level, "news_count": len(rows), "every_news_id_retained": True, "lossy": True, "context_window": context_window, "input_budget": ceiling,
                            "output_budget": 2560 if detail and ceiling > 5000 else 3072 if detail else 1024,
                            "method": "当前事实保护300tokens；全部当日ID保留；前5日为日摘要并另存来源；历史价格反应仅1d/3d紧凑字段，完整1/3/5/10d在输入快照；背景先压缩"},
        }
        if level>=5:
            selected["compression"]["method"]+="；前5日类别明细留快照，模型保留日期/数量/60token事实"
        if current.get("id"):
            matched_alias = next((alias for row, alias in zip(rows, aliases) if row.get("id") == current["id"]), None)
            if matched_alias:
                selected["current_news"]["same_as_window_news_id"] = matched_alias
        # URL/title payloads can dominate the small brief context. The final
        # schema resolves ID-only citations against the complete stored index.
        if level >= 1:
            selected.pop("source_urls")
            selected["compression"]["citation_note"] = "URL不在压缩输入；references只填id和空url，由系统恢复原来源"
        total = count_tokens(system) + count_tokens(_json(selected)) + 128
        if total <= limit:
            break
    else:
        raise ContextError(f"当日新闻证据及必要背景无法装入{limit} token预算；需要分批汇总，禁止静默丢弃新闻")
    meta = {
        "prompt_version": PROMPT_VERSION,
        "prompt_baseline": PROMPT_BASELINE,
        "as_of": context["as_of"],
        "source_index": source_index,
        "previous_source_index": _previous_source_index(previous),
        "compression": selected["compression"],
        "input_token_estimate": total,
        "uncompressed_context_token_estimate": count_tokens(_json(context)),
        "input_token_limit": limit,
        "input_token_ceiling": ceiling,
        "context_window": context_window,
        "output_token_limit": selected["compression"]["output_budget"],
        "adaptive_context_used": ceiling > (5000 if detail else 2400),
        "window_news_count": len(rows),
        "filtered_future_news_count": filtered_news,
        "filtered_future_event_count": len(context.get("historical_events", [])) - len(events),
        "outcome_compression": [{"event_id": row.get("id"), "known_horizons": list((row.get("outcomes") or {}).keys()), "included_horizons": list(_compact_outcomes(row.get("outcomes") or {}).keys())} for row in events if row.get("outcomes")],
        "temporal_notes": sorted(set(notes)),
        "context_sha256": hashlib.sha256(_json(selected).encode()).hexdigest(),
    }
    return selected, meta


def _missing_data_basis(statement: str, section: str, context: dict | None) -> str | None:
    """Allow meaningful missing-data explanations only when input proves them."""
    if context is None:
        return None
    missing = re.search(r"缺少|缺乏|不足|暂无|尚无|没有|未提供|无法|未核实|未验证|待核实|未确认|不完整", statement)
    if not missing:
        return None
    current, price = context.get("current_news") or {}, context.get("price_reference") or {}
    events = context.get("historical_events", [])
    if re.search(r"本结算窗口|当日|今日|全天", statement) and "新闻" in statement and not context.get("window_news"):
        return "window_news"
    if re.search(r"历史|类比|可比", statement) and (not events or any(event.get("source_quality") == "daily_aggregate_no_original_url" for event in events) or not any(event.get("outcomes") for event in events)):
        return "historical_events.source_quality_or_outcomes"
    if re.search(r"实时(?:价格|行情)|即时(?:价格|行情)", statement):
        return "price_reference.no_intraday_quote"
    if re.search(r"官方|结算.*(?:核实|验证)|准确率|校准", statement) and not price.get("verified"):
        return "price_reference.settlement_verification"
    if re.search(r"价格|行情", statement) and not price.get("available"):
        return "price_reference"
    if re.search(r"波动率|sigma|σ", statement, re.I) and price.get("sigma60") is None:
        return "price_reference.sigma60"
    if re.search(r"实际流量|流量恢复|真实供气|实际供气|复产", statement) and current.get("source") == "gassco":
        return "current_news.actual_flow_recovery_verification"
    if re.search(r"供应量|供气量|流量|到港量|定量|量化", statement) and not any(current.get(key) is not None for key in ("actual_flow", "flow_loss", "lng_arrival_volume", "supply_volume")):
        return "current_news.no_verified_quantitative_flow"
    if re.search(r"手动|情景|假设|测试|核实", statement) and context.get("analysis_kind") == "manual":
        return "current_news.manual_verification"
    if re.search(r"正文|摘要", statement) and not current.get("summary", current.get("body", current.get("content"))):
        return "current_news.body_summary"
    if re.search(r"时间戳|发布时间|精确时间", statement) and (not current.get("published_at") or current.get("timestamp_precision") != "exact"):
        return "current_news.published_at"
    return None


def _conditional_current_basis(statement: str, context: dict | None) -> bool:
    """Resolve an uncited countercondition to N0 only from concrete input facts.

    Invalidation conditions are hypotheses, not new factual reports. Requiring
    literal source spelling rejected otherwise reviewable correct conditions.
    Do not do this for new facts/transmission or unrelated invented scenarios.
    """
    if context is None or not re.match(r"\s*(?:若|如果|一旦|当|除非)", statement):
        return False
    source = _text(context.get("current_news") or {})
    concepts = (
        (r"停产|停供|中断|复产|重启|恢复供气", r"停产|停供|中断|复产|重启|outage|shutdown|disrupt|restart|resum"),
        (r"LNG|液化天然气|到港", r"LNG|液化天然气|到港|liquef"),
        (r"天气|寒冷|转冷|温和|暖冬", r"天气|寒冷|转冷|温和|暖冬|weather|warm|mild|cold"),
        (r"库存|储气|补库", r"库存|储气|补库|storage|inventory|injection"),
        (r"需求", r"需求|demand|天气|温和|weather|mild|warm|cold"),
        (r"战争|冲突|制裁|罢工", r"战争|冲突|制裁|罢工|war|conflict|sanction|strike"),
    )
    touched = [(output, evidence) for output, evidence in concepts if re.search(output, statement, re.I)]
    return bool(touched) and all(re.search(evidence, source, re.I) for _, evidence in touched)


def _parse_result(content: str, detail: bool, source_index: list[dict], context: dict | None = None) -> dict:
    if not content.strip():
        raise ValueError("模型未返回JSON正文（可能输出额度不足）")
    # A single optional code fence is accepted. No substring recovery or
    # coercion is performed: null, extra keys and wrong types must be repaired.
    text = content.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    elif text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
    parsed = json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"非法JSON数值 {value}")))
    result = AnalysisResult.model_validate(parsed)
    if detail and result.detail is None:
        raise ValueError("详细分析缺少 detail 对象")
    placeholders = {"区分事实、推断及缺失", "上涨条件", "基准条件", "下跌条件", "中文简报", "中文预判", "新增事实及净影响", "证据和限制"}
    instruction_fragments = ("有相应事实时填写", "按输入事实填写", "实际新事实；历史新闻需写", "出现何种事实会改变本结论", "实际缺失资料的影响", "有证据才判断已定价")
    # Reject copied schema instructions in any user-visible analysis section.
    # Reference titles are resolved independently from actual input below.
    def statements(value):
        if isinstance(value, dict):
            return [sentence for key, row in value.items() if key != "references" for sentence in statements(row)]
        if isinstance(value, list):
            return [sentence for row in value for sentence in statements(row)]
        return [value] if isinstance(value, str) else []
    if any(sentence.strip() in placeholders or any(fragment in sentence for fragment in instruction_fragments)
           for sentence in statements(result.model_dump())):
        raise ValueError("分析照抄占位句/字段说明，必须填写本次实际事实、条件或缺失说明")
    chinese_prose = [result.news_impact.summary, result.close_forecast.summary, result.close_forecast.basis,
                     result.analysis.summary, result.analysis.priced_in_comment,
                     *result.news_impact.bullish_evidence, *result.news_impact.bearish_evidence,
                     *result.news_impact.neutralizing_evidence, *result.analysis.evidence,
                     *result.analysis.negation_or_reversal, *result.analysis.historical_comparisons]
    if result.close_forecast.extreme_move_risk:
        chinese_prose.append(result.close_forecast.extreme_move_risk)
    if result.detail:
        chinese_prose.extend([*result.detail.new_facts, *result.detail.transmission, *result.detail.daily_balance,
                              *result.detail.historical_comparisons, *result.detail.invalidation_conditions,
                              *result.detail.limitations, result.detail.priced_in_assessment,
                              result.detail.scenarios.bullish, result.detail.scenarios.base, result.detail.scenarios.bearish])
    allowed = {source["alias"]: source for source in source_index}
    allowed.update({source["id"]: source for source in source_index})
    evidence_lists = [result.news_impact.bullish_evidence, result.news_impact.bearish_evidence,
                      result.news_impact.neutralizing_evidence, result.analysis.evidence]
    if not result.analysis.evidence:
        raise ValueError("analysis.evidence必须有输入事实和TTF机制")
    for evidence in (item for rows in evidence_lists for item in rows):
        facts = re.sub(r"\[?[NE]\d+\]?", "", evidence).strip(" ：:→->")
        if len(facts) < 4 or not any(term in evidence for term in ("→", "->", "TTF")):
            raise ValueError("evidence必须含事实与TTF机制，不能只有编号；格式[N0]事实 → TTF机制")
        for alias in re.findall(r"(?<!\w)[NE]\d+(?!\w)", evidence):
            if alias not in allowed:
                raise ValueError("evidence引用了输入不存在的证据ID")
    if result.detail:
        required_sections = {"new_facts":result.detail.new_facts, "transmission":result.detail.transmission, "daily_balance":result.detail.daily_balance,
                             "historical_comparisons":result.detail.historical_comparisons, "invalidation_conditions":result.detail.invalidation_conditions, "limitations":result.detail.limitations}
        for section_name, section in required_sections.items():
            for index, statement in enumerate(section):
                if not re.search(r"(?<!\w)[NE]\d+(?!\w)|current_news|window_news|historical_events|price_reference|coverage|as_of", statement):
                    if section_name == "invalidation_conditions" and _conditional_current_basis(statement, context):
                        # The condition is explicitly hypothetical and grounded
                        # in current input concepts; expose its source to readers.
                        section[index] = "[N0]" + statement
                    elif _missing_data_basis(statement, section_name, context) is None:
                        raise ValueError(f"detail.{section_name}[{index}]缺少证据引用或输入可证明的缺失说明；请以[N0]/[E#]/输入字段名标注实际依据")
                for alias in re.findall(r"(?<!\w)[NE]\d+(?!\w)", statement):
                    if alias not in allowed:
                        raise ValueError("detail引用了输入不存在的历史或新闻证据")
        detail_statements = [result.detail.priced_in_assessment, result.detail.scenarios.bullish,
                      result.detail.scenarios.base, result.detail.scenarios.bearish]
        if any(statement.strip() in placeholders for statement in detail_statements):
            raise ValueError("详细分析照抄占位句，必须填写实际证据、条件或缺失说明")
        bearish = result.detail.scenarios.bearish
        if re.search(r"需求(?:上升|增加|增强)|供应(?:减少|下降|中断)", bearish) and not re.search(r"缓解|恢复|复产|改善|下降|减弱|减少|抵消|但|回落|结束", bearish):
            raise ValueError("bearish情景必须解释TTF下跌机制，不能仅以需求上升作看跌情景")
    for sentence in chinese_prose:
        chinese_count = len(re.findall(r"[\u4e00-\u9fff]", sentence))
        latin_count = len(re.findall(r"[A-Za-z]", sentence))
        # Allow original company names, quoted titles, identifiers and units;
        # a tiny Chinese label tacked onto an English paragraph is insufficient.
        if chinese_count < 2 or chinese_count < latin_count * 0.08:
            raise ValueError("核心分析文案必须为中文句子；英文新闻也要中文分析，专有名称/单位可保留英文")
    refs = result.references + (result.detail.references if result.detail else [])
    if not any(ref.id == "N0" or ref.id == source_index[0]["id"] for ref in result.references):
        raise ValueError("references必须包含当前新闻N0")
    # Models occasionally cite E1 in prose but omit it in the references list.
    # Restore only IDs actually used and present in the complete input index.
    # This adds provenance, never supplies or invents missing factual content.
    all_used = set(re.findall(r"(?<!\w)[NE]\d+(?!\w)", "\n".join(statements(result.model_dump()))))
    detail_used = set(re.findall(r"(?<!\w)[NE]\d+(?!\w)", "\n".join(statements(result.detail.model_dump())))) if result.detail else set()
    for destination, used in ((result.references, all_used), (result.detail.references if result.detail else [], detail_used)):
        existing = {ref.id for ref in destination}
        for alias in sorted(used):
            if alias not in allowed:
                raise ValueError("分析引用了输入不存在的证据ID")
            if alias not in existing and allowed[alias]["id"] not in existing:
                destination.append(Reference(id=alias, url=""))
                existing.add(alias)
    refs = result.references + (result.detail.references if result.detail else [])
    for ref in refs:
        if ref.id not in allowed:
            raise ValueError(f"引用ID {ref.id} 不属于输入证据")
        source = allowed[ref.id]
        if ref.url not in ("", source["url"]):
            raise ValueError(f"引用 {ref.id} URL与输入来源不同")
        # Resolve only from input, so a model cannot manufacture a source title.
        ref.url = source["url"]
        ref.title = source["title"] or None
        ref.id = source["id"]
    return result.model_dump(exclude_none=True)


def _has_affirmed_supply_shock(item: dict) -> bool:
    # This gate checks omission of an explicit supplied fact; it does not assign
    # a direction or assert the fact has been externally verified.
    text = _text(item)
    pattern = re.compile(r"意外停产|发生.{0,10}(?:供应中断|停产)|unplanned outage|supply disruption|gas.{0,15}shutdown", re.I)
    for clause in re.split(r"[。！？;；\n]", text):
        match = pattern.search(clause)
        if match:
            prefix = clause[max(0, match.start()-14):match.start()]
            suffix = clause[match.end():match.end()+18]
            if not re.search(r"没有|未发生|否认|避免|no\s+$|not\s+$|without\s+$", prefix, re.I) and not re.search(r"未发生|不存在|不实|已辟谣|被否认|did not occur|was denied|false", suffix, re.I):
                return True
    return False


_WEATHER_LNG_CAUSE = re.compile(
    r"(?:天气温和|温和天气|暖冬|温暖天气|天气.{0,5}(?:暖|温和)).{0,8}"
    r"(?:导致|带来|促使|推动|使得).{0,8}LNG.{0,8}(?:到港|进口|流入|供应).{0,8}(?:增加|提升|增长)",
    re.I,
)


def _unsupported_weather_lng_causality(result: dict, context: dict) -> bool:
    input_texts = [_text(context.get("current_news") or {})] + [_text(row) for row in context.get("window_news", []) if isinstance(row, dict)]
    if any(_WEATHER_LNG_CAUSE.search(text) for text in input_texts):
        return False
    def strings(value):
        if isinstance(value, dict):
            return [sentence for key, row in value.items() if key not in ("references", "_meta") for sentence in strings(row)]
        if isinstance(value, list):
            return [sentence for row in value for sentence in strings(row)]
        return [value] if isinstance(value, str) else []
    for sentence in strings(result):
        match = _WEATHER_LNG_CAUSE.search(sentence)
        if match and not re.search(r"(?:并非|不能|不得|不表示|未证实|无法确认|没有证据).{0,8}$", sentence[:match.start()]):
            return True
    return False


def _analysis_strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [sentence for key, row in value.items() if key not in ("references", "_meta") for sentence in _analysis_strings(row)]
    if isinstance(value, list):
        return [sentence for row in value for sentence in _analysis_strings(row)]
    return [value] if isinstance(value, str) else []


def _ground_current_notice_attribute(result: dict, context: dict) -> list[dict]:
    """Correct only an explicit current Unplanned notice's Chinese attribute.

    The estimate of an event's end does not make it planned maintenance. The
    source title fixes that fact; scores/directions/probabilities stay intact.
    Exclude historical citations and retain original field text for audit.
    """
    current=context.get("current_news") or {}
    if current.get("source")!="gassco" or not re.search(r"\bunplanned\b",str(current.get("title", "")),re.I):
        return []
    corrections=[]
    property_pattern=re.compile(r"(?<![非未])计划(?:性|内)?(?:停机|停产|停供|检修|维护|中断)")
    def corrected_text(text):
        parts=re.split(r"([。；;，,])",text)
        for index,clause in enumerate(parts):
            if re.search(r"(?<!\w)E\d+(?!\w)",clause) and not re.search(r"(?<!\w)N0(?!\w)",clause):
                continue
            if re.search(r"历史|此前|过去",clause) and not re.search(r"当前|本次|N0",clause):
                continue
            def replace(match):
                term=match.group()
                if "检修" in term or "维护" in term:
                    return "非计划设施不可用"
                return "非"+term
            parts[index]=property_pattern.sub(replace,clause)
        return "".join(parts)
    def visit(value,path):
        if isinstance(value,dict):
            for key,row in list(value.items()):
                if key in ("references","historical_comparisons","priced_in_comment","priced_in_assessment","matched_terms","direction","signal_strength","_meta"):
                    continue
                if isinstance(row,str):
                    new=corrected_text(row)
                    if new!=row:
                        value[key]=new
                        corrections.append({"field":path+key,"original":row,"corrected":new,"evidence":"current_news.title:Unplanned"})
                else:
                    visit(row,path+key+".")
        elif isinstance(value,list):
            for index,row in enumerate(value):
                if isinstance(row,str):
                    new=corrected_text(row)
                    if new!=row:
                        value[index]=new
                        corrections.append({"field":path+str(index),"original":row,"corrected":new,"evidence":"current_news.title:Unplanned"})
                else:
                    visit(row,path+str(index)+".")
    for key in ("news_impact","close_forecast","analysis","detail"):
        if result.get(key):
            visit(result[key],key+".")
    return corrections


def _ground_condition_facts(result: dict, context: dict) -> list[dict]:
    """Correct two fact contradictions in conditional detail, retaining audit.

    Restoring insufficient supply does not create a surplus. Warmer European
    heating weather cannot be turned into increased demand without an input
    cooling/power mechanism. Predictions and their scalar fields stay intact.
    """
    detail=result.get("detail")
    if not isinstance(detail,dict):
        return []
    current=context.get("current_news") or {}
    source=_text(current)
    corrections=[]
    bearish=detail["scenarios"]["bearish"]
    constrained_supply=re.search(r"恢复(?:程度|水平|速度)?(?:仍|尚)?(?:不足|不充分|不完全|有限|未完成)|未(?:完全)?恢复|复产不足|供应(?:不足|短缺|缺口)",bearish)
    downside=re.search(r"下跌|下行|看跌|供应过剩|供给过剩",bearish)
    balance_explained=re.search(r"但|抵消|需求大幅下降|需求大降",bearish)
    if constrained_supply and downside and (re.search(r"供应过剩|供给过剩",bearish) or not balance_explained):
        supported=current.get("source")=="gassco" or re.search(r"outage|disrupt|shutdown|unavailab|停机|停产|停供|中断",source,re.I)
        fixed="[N0]若实际供气流量恢复且供应缺口缓解，则TTF下行压力增强；恢复不足本身不构成看跌理由" if supported else "[N0]输入未提供可验证的供气恢复条件，不能以恢复不足推断供应过剩或价格下跌"
        detail["scenarios"]["bearish"]=fixed
        corrections.append({"field":"detail.scenarios.bearish","original":bearish,"corrected":fixed,"evidence":"current_news:供应恢复条件，不足不等于过剩"})
    warm_input=re.search(r"mild|warm|温和|暖冬|转暖|温暖",source,re.I)
    # Supporting cooling-driven demand must be a supplied mechanism, not an
    # explanation invented merely to salvage a contradictory condition.
    available_text="\n".join([source]+[_text(row) for row in context.get("window_news",[]) if isinstance(row,dict)])
    cooling_mechanism=re.search(r"(?:hot|heat|warm|高温|炎热|升温).{0,90}(?:cooling|air.condition|制冷|空调|发电需求)|(?:cooling|air.condition|制冷|空调).{0,80}(?:gas|天然气|需求)",available_text,re.I)
    warm_demand_up=re.compile(r"(?:天气.{0,8}(?:变热|转暖|温暖|温和|更暖|升温)|暖冬|温和天气|炎热天气|高温).{0,24}需求.{0,6}(?:上升|增加|增强)")
    if warm_input and not cooling_mechanism:
        for index,statement in enumerate(detail["invalidation_conditions"]):
            if warm_demand_up.search(statement) and not re.search(r"未(?:必|能|导致)|没有|不(?:会|一定)|无证据",statement):
                fixed="[N0]若欧洲天气转冷、取暖需求增强，则温和天气的看跌抵消因素减弱，原结论需重新评估"
                detail["invalidation_conditions"][index]=fixed
                corrections.append({"field":f"detail.invalidation_conditions.{index}","original":statement,"corrected":fixed,"evidence":"current_news:温和天气；未提供制冷或发电需求机制"})
    return corrections


def _notice_timing_mentions(statements: list[str]) -> tuple[bool,bool]:
    text="；".join(statements)
    ended=bool(re.search(r"(?:公告|计划|预计|时段|窗口|结束时间).{0,22}(?:已(?:经过|经结束|过|结束|到)|过去|届满)|(?:已(?:过|超过|到达)).{0,12}(?:公告|计划|预计|结束)",text))
    unverified=bool(re.search(r"(?:流量|供气|恢复|复产).{0,15}(?:待验证|未确认|待核实|尚未确认|未知|未核实|待确认|需验证)|(?:尚未|未|无法|待).{0,8}(?:验证|确认|核实).{0,14}(?:流量|供气|恢复|复产)",text))
    return ended,unverified


def _ground_notice_timing(result: dict, context: dict) -> list[dict]:
    """Supply an omitted, directly observable notice cutoff fact, never a forecast."""
    current=context.get("current_news") or {}
    as_of=_datetime(context.get("as_of"))
    if current.get("source")!="gassco" or as_of is None:
        return []
    operational=_operational_time(current,as_of)
    if not operational.get("announced_end_passed"):
        return []
    fact=f"[N0]公告预计时段已结束（结束时刻{operational['event_end_utc']}），实际流量恢复待核实；公告不可用容量不等于实际流量损失。"
    corrections=[]
    brief=[result["news_impact"]["summary"],result["close_forecast"]["summary"],result["close_forecast"]["basis"],result["analysis"]["summary"],*result["analysis"]["evidence"]]
    if not all(_notice_timing_mentions(brief)):
        original=result["news_impact"]["summary"]
        result["news_impact"]["summary"]=fact+" "+original
        corrections.append({"field":"news_impact.summary","original":original,"corrected":result["news_impact"]["summary"],"evidence":"current_news.event_end<=as_of;无独立实际流量恢复数据"})
    if isinstance(result.get("detail"),dict) and not all(_notice_timing_mentions(_analysis_strings(result["detail"]))):
        original=result["detail"]["new_facts"][0]
        result["detail"]["new_facts"][0]=fact+" "+original
        corrections.append({"field":"detail.new_facts.0","original":original,"corrected":result["detail"]["new_facts"][0],"evidence":"current_news.event_end<=as_of;无独立实际流量恢复数据"})
    return corrections


def _ground_current_notice_facts(result: dict, context: dict) -> list[dict]:
    """Keep N0 source capacities/window exact, excluding historical E clauses."""
    current=context.get("current_news") or {}
    if current.get("source")!="gassco":
        return []
    source=_text(current)
    capacities={}
    for field,pattern in (("technical",r"\btechnical capacity\s*:\s*(\d+(?:\.\d+)?)"),("available",r"\bavailable\s*:\s*(\d+(?:\.\d+)?)"),("unavailable",r"\bunavailable\s*:\s*(\d+(?:\.\d+)?)")):
        match=re.search(pattern,source,re.I)
        if match:
            capacities[field]=match[1]
    published=_datetime(current.get("published_at")) if len(str(current.get("published_at") or ""))>10 else None
    start=_datetime((context.get("session") or {}).get("window_start"))
    end=_datetime((context.get("session") or {}).get("window_end"))
    outside=bool(published and start and end and not start<=published<end)
    corrections=[]
    unit_number=re.compile(r"\d+(?:\.\d+)?(?=\s*(?:mcm[/／](?:d|day)|百万立方米[/／]日))",re.I)
    def corrected(text):
        parts=re.split(r"([。；;，,])",text)
        for index,clause in enumerate(parts):
            if re.search(r"(?<!\w)E\d+(?!\w)",clause) and not re.search(r"(?<!\w)N0(?!\w)",clause):
                continue
            def number(match):
                if any(abs(float(match.group())-float(value))<1e-8 for value in capacities.values()):
                    return match.group()
                preceding=clause[max(0,match.start()-24):match.start()]
                if re.search(r"不可用|下降|减少|缺口|受限|损失|unavailable",preceding,re.I):
                    key="unavailable"
                elif re.search(r"可用|available",preceding,re.I):
                    key="available"
                elif re.search(r"技术|总容量|处理能力|technical",preceding,re.I):
                    key="technical"
                else:
                    key="unavailable"
                return capacities.get(key,match.group())
            if capacities and not re.match(r"\s*(?:\[N0\])?\s*(?:若|如果|一旦|假设)",clause):
                clause=unit_number.sub(number,clause)
            if outside:
                clause=re.sub(r"本(?:结算)?窗口(?:唯一)?新增(?:的)?(?:供应冲击|供应中断|新闻|消息|事件|停机)","此前发布的供应风险背景",clause)
            parts[index]=clause
        return "".join(parts)
    def visit(value,path):
        if isinstance(value,dict):
            for key,row in list(value.items()):
                if key in ("references","historical_comparisons","priced_in_comment","priced_in_assessment","matched_terms","direction","signal_strength","_meta"):
                    continue
                if isinstance(row,str):
                    new=corrected(row)
                    if new!=row:
                        value[key]=new;corrections.append({"field":path+key,"original":row,"corrected":new,"evidence":"current_news.summary:原始容量；published_at/session:原始窗口"})
                else:visit(row,path+key+".")
        elif isinstance(value,list):
            for index,row in enumerate(value):
                if isinstance(row,str):
                    new=corrected(row)
                    if new!=row:
                        value[index]=new;corrections.append({"field":path+str(index),"original":row,"corrected":new,"evidence":"current_news.summary:原始容量；published_at/session:原始窗口"})
                else:visit(row,path+str(index)+".")
    for key in ("news_impact","close_forecast","analysis","detail"):
        if result.get(key):visit(result[key],key+".")
    if outside:
        from .calendar import session_at
        source_day=session_at(published)["trade_date"]
        operational=_operational_time(current,_datetime(context["as_of"]))
        state="公告预计时段已结束，实际流量恢复待核实" if operational.get("announced_end_passed") else "公告预计时段尚未开始，不能作为当前停供" if operational.get("scheduled_for_future") else "公告预计时段尚未结束，实际流量状态未独立核实"
        fact=f"[N0]原发布时间{published.isoformat()}，属于{source_day}结算窗口，非本窗口新增新闻；{state}。 "
        targets=[("analysis.summary",result["analysis"],"summary")]
        if isinstance(result.get("detail"),dict):targets.append(("detail.new_facts.0",result["detail"]["new_facts"],0))
        for path,target,key in targets:
            original=target[key];target[key]=fact+original
            corrections.append({"field":path,"original":original,"corrected":target[key],"evidence":"current_news.published_at / ICE settlement calendar / event_end<=as_of"})
    return corrections


def _validate_prediction_semantics(result: dict, context: dict, detail: bool) -> None:
    if not (context.get("price_reference") or {}).get("calibrated_forecast"):
        number = r"\d+(?:\.\d+)?(?:\s*[-–—~～至到]\s*\d+(?:\.\d+)?)?"
        magnitude = re.compile(r"(?:上涨|下跌|上行|下行|涨幅|跌幅|目标价|结算价|波动).{0,14}" + number + r"\s*(?:€/MWh|EUR/MWh|欧元(?:[/／]MWh|[/／]兆瓦时)?)", re.I)
        percentage = re.compile(r"(?:TTF|气价|价格|结算价).{0,12}(?:上涨|下跌|上行|下行|涨幅|跌幅|波动).{0,10}"+number+r"\s*%", re.I)
        historical = set(result.get("analysis", {}).get("historical_comparisons", []))
        historical.update((result.get("detail") or {}).get("historical_comparisons", []))
        for statement in _analysis_strings(result):
            if statement in historical and re.search(r"(?<!\w)E\d+(?!\w)",statement):
                # Observed historical amplitudes have a separate per-event
                # source/outcome check; they are not current target forecasts.
                continue
            if (magnitude.search(statement) or percentage.search(statement)) and not re.search(r"历史|昨日|sigma|σ|阈值", statement, re.I):
                raise ValueError("没有校准价格预测输入，禁止编造上涨/下跌幅度或目标价；只能方向与条件情景")
    current = context.get("current_news") or {}
    as_of = _datetime(context.get("as_of"))
    if current.get("source") != "gassco" or as_of is None or not _operational_time(current, as_of).get("announced_end_passed"):
        return
    def check_timing(statements):
        ended,unverified=_notice_timing_mentions(statements)
        if not ended or not unverified:
            raise ValueError("Gassco公告预计结束时间已经过，必须明确公告时段已结束、实际流量恢复待核实；不能把公告截止当已验证复产或仍停供")
    check_timing([result["news_impact"]["summary"], result["close_forecast"]["summary"], result["close_forecast"]["basis"], result["analysis"]["summary"], *result["analysis"]["evidence"]])
    if detail:
        check_timing(_analysis_strings(result.get("detail") or {}))


def _validate_historical_reactions(result: dict, prepared: dict) -> None:
    """Do not turn historical supply facts into fabricated realized prices."""
    events = {row["id"]:row for row in prepared.get("historical_events", [])}
    sections = [result.get("analysis", {}).get("historical_comparisons", [])]
    if result.get("detail"):
        sections.append(result["detail"].get("historical_comparisons", []))
    actual_price = re.compile(r"(?:价格|气价|TTF).{0,12}(?:上涨|下跌|暴涨|暴跌|上行|下行|不变|未变|稳定|反应平淡|没有变化)|未(?:影响|改变|推动).{0,8}(?:价格|气价|TTF)|(?:\d+d|一日|三日|次日|同期).{0,8}(?:涨|跌)|(?:涨|跌)\d+(?:\.\d+)?\s*欧元",re.I)
    caution = re.compile(r"无法|不能|尚未|未提供|未确认|不保证|不等于|待验证|待核实|若|如果|可能|未必")
    for section_index, section in enumerate(sections):
        for index, statement in enumerate(section):
            # Evaluate individual clauses: a later caution cannot erase an
            # earlier invented observation such as '未影响价格，说明未必上涨'.
            for clause in re.split(r"[。；;，,]", statement):
                aliases = re.findall(r"(?<!\w)E\d+(?!\w)", statement)
                if re.search(r"已(?:计入|反映|定价|消化)|已计价",clause) and not re.search(r"无法|不能|未确认|未核实|没有|尚未|不确定|未提供",clause):
                    raise ValueError(f"historical_comparisons[{section_index}][{index}]无已计价证据却宣称历史事件已定价；状态及同期价差不能证明已消化")
                if re.search(r"未(?:出现|发生|存在).{0,20}(?:LNG|天气|需求|供应|到港|复产)",clause,re.I):
                    explicitly_negated = any(re.search(r"not|no |without|没有|未发生|不存在|未出现",events.get(alias, {}).get("facts", ""),re.I) for alias in aliases)
                    if not explicitly_negated:
                        raise ValueError(f"historical_comparisons[{section_index}][{index}]把历史资料未提及的事项说成没有发生；只能写输入未提供该可比信息")
                if not actual_price.search(clause) or caution.search(clause):
                    continue
                supported = False
                for alias in aliases:
                    event = events.get(alias, {})
                    if event.get("outcomes"):
                        if re.search(r"(?:导致|造成|使得|推动).{0,20}(?:价格|气价|TTF|\d+d.{0,8}(?:涨|跌)|上涨|下跌)", clause,re.I):
                            raise ValueError(f"historical_comparisons[{section_index}][{index}]把同期价差说成事件因果；只能报告同合约参考变化及相似机制，不能宣称历史因果已验证")
                        amounts = re.findall(r"(\d+(?:\.\d+)?)\s*(?:€/MWh|EUR/MWh|欧元(?:[/／]MWh|[/／]兆瓦时)?)", clause,re.I)
                        allowed_changes = [abs(float(row["change"])) for row in event["outcomes"].values() if isinstance(row,dict) and isinstance(row.get("change"),(int,float))]
                        if amounts and any(not any(abs(float(amount)-actual)<=0.011 for actual in allowed_changes) for amount in amounts):
                            raise ValueError(f"historical_comparisons[{section_index}][{index}]价差数值不在输入可用outcomes中；不能捏造历史变化幅度")
                        supported = True
                        break
                    # Only an explicit reported price fact can support a
                    # descriptive comparison without matched outcome records.
                    flat_claim = bool(re.search(r"不变|未变|稳定|反应平淡|没有变化|未(?:影响|改变|推动)",clause))
                    direction_pattern = r"(?:gas price|ttf|price).{0,25}(?:unchanged|stable|unaffected)|(?:气价|价格|TTF).{0,12}(?:不变|未变|稳定)" if flat_claim else r"(?:gas price|ttf|price).{0,25}(?:rose|fell|rising|falling|higher|lower|low)|(?:气价|价格|TTF).{0,12}(?:上涨|下跌|上行|下行)"
                    if re.search(direction_pattern,event.get("facts", ""),re.I):
                        supported = True
                        break
                if not supported:
                    raise ValueError(f"historical_comparisons[{section_index}][{index}]断言历史价格反应但无可用outcomes/明确价格来源事实；只比较供需情景并说明反应无法量化")


def _pricing_evidence_available(prepared: dict) -> bool:
    price=prepared.get("price_reference") or {}
    return bool(price.get("priced_in_evidence") or price.get("announcement_price_reaction"))


def _validate_priced_in(result: dict, prepared: dict) -> None:
    if _pricing_evidence_available(prepared):
        return
    fields={"analysis.priced_in_comment":result["analysis"]["priced_in_comment"]}
    if result.get("detail"):
        fields["detail.priced_in_assessment"]=result["detail"]["priced_in_assessment"]
    for name,statement in fields.items():
        for clause in re.split(r"[。；;，,]",statement):
            if re.search(r"已(?:计入|反映|定价|消化)|(?:充分|完全)计价|已计价|priced.in",clause,re.I) and not re.search(r"无法|不能|未确认|未核实|无.{0,12}证据|没有|尚未|不确定|待核实|缺少|缺乏|未提供",clause):
                raise ValueError(f"{name}缺少公告前后价格/已计价证据，不能宣称已计入；应明确输入没有盘中报价和事件反应资料")


def _history_options(prepared: dict) -> list[str]:
    """Evidence-only comparisons used by the one repair decoder, if needed.

    Price directions and trader scenarios remain model-generated. These bounded
    choices prevent a repeatedly invalid historical section from asserting
    event causality or inventing missing facts. All values derive from the
    already temporally filtered, provenance-indexed prompt input.
    """
    options=[]
    for event in prepared.get("historical_events", []):
        alias=event["id"]
        text=f"[{alias}]历史记录日期{event.get('date') or '未知'}"
        if event.get("category"):
            text+=f"，类别为{event['category']}"
        if event.get("operational", {}).get("capacity_is_not_lost_flow"):
            text+="；公告容量限制不等于已核实流量损失"
        known=event.get("outcomes") or {}
        for horizon,value in known.items():
            if isinstance(value,dict) and isinstance(value.get("change"),(int,float)):
                text+=f"；{horizon}同合约参考价差{value['change']:+.2f}欧元/MWh（截止{value.get('end_date','未知')}）"
        text+=("；同期价差不能归因于事件" if known else "；该时点价格反应资料未提供，无法量化")
        if event.get("source_quality")=="daily_aggregate_no_original_url":
            text+="；日聚合缺少原始单条来源，类比可信度有限"
        else:
            if not re.search(r"weather|warm|mild|cold|LNG|天气|温和|寒冷",event.get("facts", ""),re.I):
                text+="；输入未提供可验证的天气及LNG差异，需核对具体供需情景"
            else:
                text+="；按输入供需事实作条件类比，事件时点及传导规模仍需核对"
        options.append(text)
    return options or ["historical_events:本次没有可用历史事件，无法直接类比或量化价格反应"]


def response_schema(detail: bool = False, prepared: dict | None = None) -> dict:
    """Ollama's structured decoding enforces the required nested field keys."""
    schema = AnalysisResult.model_json_schema()
    if detail:
        schema["properties"]["detail"] = {"$ref": "#/$defs/DetailAnalysis"}
        schema["required"] = [*schema["required"], "detail"]
    else:
        schema["properties"].pop("detail", None)
    if prepared and prepared.get("compression", {}).get("constrain_history"):
        items={"type":"string","enum":_history_options(prepared)}
        schema["$defs"]["Analysis"]["properties"]["historical_comparisons"]["items"]=items
        if detail:
            schema["$defs"]["DetailAnalysis"]["properties"]["historical_comparisons"]["items"]=items
    if prepared and prepared.get("compression", {}).get("constrain_priced_in"):
        observed={"type":"string","enum":["price_reference:未提供盘中报价和公告前后价格反应，无法确认消息是否已计入价格"]}
        schema["$defs"]["Analysis"]["properties"]["priced_in_comment"]=observed
        if detail:
            schema["$defs"]["DetailAnalysis"]["properties"]["priced_in_assessment"]=observed
    return schema


def _materialize_bounded_evidence(content: str, prepared: dict, detail: bool) -> tuple[str, dict]:
    """Keep historical observations and absent priced-in evidence deterministic.

    Ollama gpt-oss can ignore nested schema enums. Check the original response
    structure first, then materialize only the two evidence-bearing sections.
    No direction, score, probability, scenario or other conclusion is filled.
    Original field values are retained as explicitly invalid audit material.
    """
    text=content.strip()
    if text.startswith("```json") and text.endswith("```"):
        text=text[7:-3].strip()
    elif text.startswith("```") and text.endswith("```"):
        text=text[3:-3].strip()
    parsed=json.loads(text,parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"非法JSON数值 {value}")))
    AnalysisResult.model_validate(parsed)
    if detail and not isinstance(parsed.get("detail"),dict):
        raise ValueError("详细分析缺少 detail 对象")
    audit={"evidence_grounding_used":True,"raw_generated_evidence":{},"grounded_evidence_fields":[],
           "raw_response_sha256":hashlib.sha256(content.encode("utf-8")).hexdigest()}
    if prepared["compression"].get("constrain_history") or prepared["compression"].get("ground_history"):
        original=parsed["analysis"]["historical_comparisons"]+((parsed.get("detail") or {}).get("historical_comparisons") or [])
        used=set(re.findall(r"(?<!\w)E\d+(?!\w)","\n".join(original)))
        options=_history_options(prepared)
        chosen=[option for option in options if any(f"[{alias}]" in option for alias in used)][:2] or options[:2]
        for field,target in (("analysis.historical_comparisons",parsed["analysis"]),("detail.historical_comparisons",parsed.get("detail"))):
            if isinstance(target,dict):
                audit["raw_generated_evidence"][field]=target["historical_comparisons"]
                audit["grounded_evidence_fields"].append(field)
                target["historical_comparisons"]=chosen
    if prepared["compression"].get("constrain_priced_in") or prepared["compression"].get("ground_priced_in"):
        observation="price_reference:未提供盘中报价和公告前后价格反应，无法确认消息是否已计入价格"
        for field,target,key in (("analysis.priced_in_comment",parsed["analysis"],"priced_in_comment"),("detail.priced_in_assessment",parsed.get("detail"),"priced_in_assessment")):
            if isinstance(target,dict):
                audit["raw_generated_evidence"][field]=target[key]
                audit["grounded_evidence_fields"].append(field)
                target[key]=observation
    if prepared["compression"].get("constrain_history") or prepared["compression"].get("constrain_priced_in"):
        audit["bounded_repair_materialized"]=True
        audit["bounded_repair_original_fields"]=audit["raw_generated_evidence"]
    return _json(parsed),audit


def _add_known_limitations(result: dict, context: dict, meta: dict) -> None:
    """The model cannot silently claim missing data are complete."""
    risks, missing = [], []
    current = context.get("current_news") or {}
    reference = context.get("price_reference") or {}
    if context.get("analysis_kind") == "manual":
        risks.append("手动输入仅作条件情景分析，事实尚未由本系统独立核实")
        missing.append("independent_news_verification")
    if not current.get("published_at") or current.get("timestamp_precision") != "exact":
        risks.append("当前新闻缺少精确发布时间，无法确认其原始结算窗口归属")
        missing.append("current_news.published_at")
    if not current.get("url", current.get("source_url")):
        missing.append("current_news.source_url")
    if not current.get("summary", current.get("body", current.get("content"))):
        risks.append("当前记录只有标题，未获得正文摘要；数量、持续时间和欧洲传导细节均不完整")
        missing.append("current_news.body_summary")
    if current.get("ingestion_kind") == "backfill":
        risks.append("当前新闻来自历史回补，不能直接视为当日新增冲击")
    as_of = _datetime(context.get("as_of"))
    if current.get("source") == "gassco" and as_of and _operational_time(current, as_of).get("announced_end_passed"):
        risks.append("公告所列计划时段已结束，实际流量恢复待核实；公告不可用容量不等于已验证供气损失")
        missing.append("actual_flow_recovery_verification")
    if not reference.get("available") or reference.get("previous_price") is None:
        risks.append("缺少前一交易日可用行情；收盘预判仅由新闻证据驱动")
        missing.append("previous_contract_price")
    if reference.get("sigma60") is None:
        risks.append("缺少60日同合约波动率，异常等级尚不能按行情阈值核验")
        missing.append("sigma60")
    if not reference.get("verified"):
        risks.append("行情尚未验证为ICE官方日结算，不参与官方结算准确率评分")
        missing.append("settlement_verification")
    missing.extend(reference.get("missing_fields", []))
    risks.extend(reference.get("risk_flags", []))
    risks.extend((context.get("coverage") or {}).get("risk_flags", []))
    if any(row.get("window_membership") == "first_observed_in_window_publication_window_unknown" for row in context.get("window_news", [])):
        risks.append("部分新闻只有发布日：当前使用真实发现时刻作为背景，原始发布时间和结算窗口归属未知")
        missing.append("observed_news.precise_publication_window")
    risks.append("模型把握程度未经统计校准，不代表已验证准确率")
    missing.append("confidence_calibration")
    for source in (context.get("coverage") or {}).get("sources", []):
        if isinstance(source, dict) and source.get("status") in ("failed", "error", "running"):
            name = str(source.get("id", source.get("source", "unknown")))
            risks.append(f"来源{name}采集失败或尚未完成，本窗口新闻覆盖可能不完整")
            missing.append(f"source_coverage:{name}")
    if any(event.get("source_quality") == "daily_aggregate_no_original_url" for event in context.get("historical_events", [])):
        risks.append("历史事件包含多条新闻日聚合，缺少原始单条来源，类比存在时间及来源限制")
    result["risk_flags"] = list(dict.fromkeys(result["risk_flags"] + risks))
    result["missing_fields"] = list(dict.fromkeys(result["missing_fields"] + missing))
    meta["deterministic_risk_flags"] = risks
    meta["deterministic_missing_fields"] = missing


class NewsAnalyst:
    def __init__(self, model: str = "gpt-oss:20b", base_url: str = "http://127.0.0.1:11434"):
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                models = response.json().get("models", [])
            found = next((model for model in models if model.get("name", model.get("model")) == self.model), None)
            return {"available": found is not None, "model": self.model, "digest": found.get("digest") if found else None, "error": None if found else f"Ollama中未安装 {self.model}"}
        except (httpx.HTTPError, ValueError) as exc:
            return {"available": False, "model": self.model, "digest": None, "error": f"本地Ollama不可用：{type(exc).__name__}"}

    async def _request(self, system: str, data: dict, detail: bool) -> dict:
        request = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": _json(data)}],
            "format": response_schema(detail, data),
            "stream": False,
            "think": "low",
            "keep_alive": "10m",
            "options": {"temperature": 0, "seed": 17, "num_ctx": data.get("compression", {}).get("context_window", 8192 if detail else 4096), "num_predict": data.get("compression", {}).get("output_budget", 3072 if detail else 1024)},
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10), trust_env=False) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=request)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AnalysisError(f"本地模型请求失败：{type(exc).__name__}；请检查Ollama和gpt-oss:20b状态") from exc
        if not isinstance(body, dict) or body.get("error"):
            raise AnalysisError("Ollama返回错误或非JSON对象")
        return body

    async def analyze(self, context: dict, detail: bool = False) -> dict:
        started = time.monotonic()
        data, meta = prepare_context(context, detail, reserve_tokens=32)
        last_error = ""
        calls = []
        for attempt in range(2):
            system = system_prompt(detail) if attempt == 0 else repair_prompt(last_error, detail)
            if attempt:
                reserve = count_tokens(system) - count_tokens(system_prompt(detail))
                constrain_history = "historical_comparisons" in last_error
                constrain_priced_in = (constrain_history or "priced_in" in last_error) and not _pricing_evidence_available(data)
                data, repaired_meta = prepare_context(context, detail, reserve_tokens=max(0, reserve)+64)
                bounded_fields=[]
                if constrain_history:
                    data["compression"]["constrain_history"]=True
                    repaired_meta["historical_repair_strategy"]="输入事实/可用参考价差的有界比较；不生成历史因果断言"
                    bounded_fields.extend(["analysis.historical_comparisons"]+(["detail.historical_comparisons"] if detail else []))
                if constrain_priced_in:
                    data["compression"]["constrain_priced_in"]=True
                    bounded_fields.extend(["analysis.priced_in_comment"]+(["detail.priced_in_assessment"] if detail else []))
                if bounded_fields:
                    repaired_meta["compression"]=data["compression"]
                    repaired_meta["context_sha256"]=hashlib.sha256(_json(data).encode()).hexdigest()
                    repaired_meta["bounded_repair_used"]=True
                    repaired_meta["bounded_repair_fields"]=bounded_fields
                meta.update(repaired_meta)
            # Historic dates/reactions and missing intraday evidence are data
            # observations. Preserve them exactly instead of asking a language
            # model to reconstruct them. Forecasts and current-news mechanisms
            # remain independent model outputs and pass their semantic gates.
            data["compression"]["ground_history"]=True
            data["compression"]["ground_priced_in"]=not _pricing_evidence_available(data)
            meta["compression"]=data["compression"]
            meta["context_sha256"]=hashlib.sha256(_json(data).encode()).hexdigest()
            meta["input_token_estimate"]=count_tokens(system)+count_tokens(_json(data))+128
            if meta["input_token_estimate"]>meta["input_token_ceiling"]:
                raise ContextError("聊天包装及证据标识超过模型输入预算；未生成替代方向")
            body = await self._request(system, data, detail)
            token_count = body.get("prompt_eval_count")
            system_sha = hashlib.sha256(system.encode("utf-8")).hexdigest()
            schema_sha = hashlib.sha256(_json(response_schema(detail,data)).encode("utf-8")).hexdigest()
            calls.append({"prompt_eval_count": token_count, "eval_count": body.get("eval_count"), "done_reason": body.get("done_reason"), "total_duration_ns": body.get("total_duration"),
                          "system_prompt_sha256": system_sha, "response_schema_sha256": schema_sha, "context_sha256": meta["context_sha256"]})
            if isinstance(token_count, int) and token_count > meta["input_token_ceiling"]:
                raise AnalysisError("Ollama实际输入token超过预算；已拒绝保存可能截断的分析")
            message = body.get("message") or {}
            try:
                content=str(message.get("content") or "")
                bounded_audit={}
                if data["compression"].get("ground_history") or data["compression"].get("ground_priced_in"):
                    content,bounded_audit=_materialize_bounded_evidence(content,data,detail)
                result = _parse_result(content, detail, meta["source_index"], context=context)
                attribute_corrections=_ground_current_notice_attribute(result,context)
                condition_corrections=_ground_condition_facts(result,context)
                source_fact_corrections=_ground_current_notice_facts(result,context)
                timing_corrections=_ground_notice_timing(result,context)
                if _has_affirmed_supply_shock(context.get("current_news") or {}) and not result["news_impact"]["bullish_evidence"]:
                    raise ValueError("输入明确给出供应中断/意外停产，必须先列其条件性看涨机制，再与天气/LNG反向因素净化；未核实不能改写为未发生")
                if _unsupported_weather_lng_causality(result, context):
                    raise ValueError("天气温和与LNG到港增加只是同时发生，输入未给因果证据；必须分写天气降低需求、LNG增加改善供应，不得编造天气导致LNG增加")
                _validate_prediction_semantics(result, context, detail)
                _validate_historical_reactions(result, data)
                _validate_priced_in(result,data)
            except (ValidationError, ValueError, TypeError) as exc:
                # Avoid promoting generated invalid text into a system-level
                # repair instruction. Pydantic messages omit field input values.
                last_error = _json(exc.errors(include_input=False, include_url=False)) if isinstance(exc, ValidationError) else str(exc)
                if attempt == 0:
                    continue
                raise AnalysisError(f"模型JSON两次校验失败，未生成方向：{last_error[:260]}") from exc
            meta.update({"model": self.model, "detail": detail, "schema_repair_used": bool(attempt), "model_calls": calls, "elapsed_seconds": round(time.monotonic() - started, 3),
                         "system_prompt_sha256": system_sha, "response_schema_sha256": schema_sha})
            meta.update(bounded_audit)
            if attribute_corrections:
                meta["source_attribute_corrections"]=attribute_corrections
            if condition_corrections:
                meta["condition_fact_corrections"]=condition_corrections
            if timing_corrections:
                meta["notice_timing_corrections"]=timing_corrections
            if source_fact_corrections:
                meta["current_notice_fact_corrections"]=source_fact_corrections
            _add_known_limitations(result, context, meta)
            result["_meta"] = meta
            return result
        raise AnalysisError("模型未生成有效分析")
