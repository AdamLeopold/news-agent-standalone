"""Source-faithful fact extraction used before context compression.

These helpers do not predict a price direction. They separate structured
announcement metadata from complete narrative clauses without changing values,
negation, or operational timing.
"""

from __future__ import annotations

import re


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=[。！？;；])\s*|\n+")
_METADATA = re.compile(r"^(?:Message\s+ID|Publication|Event)\s*:", re.I)
_CAPACITY_METADATA = re.compile(r"^(?:Technical\s+capacity|available|unavailable)\s*:", re.I)
_EMPTY_REMARK = re.compile(r"^(?:Remarks?|Reason)\s*:\s*[.。]*$", re.I)
_CAUSAL = re.compile(
    r"outage|disrupt|sanction|strike|restart|resum|restor|cancel|not\b|no\b|without|"
    r"storage|inventor|withdraw|inject|LNG|TTF|supply|demand|flow|pipeline|capacity|"
    r"weather|cold|mild|warm|cut|delay|maintenance|unavailab|production|export|"
    r"permit|regulat|commission|project|discover|供|中断|检修|停产|制裁|罢工|恢复|"
    r"取消|否认|尚未|未|不|库存|液化|需求|流量|管道|天气|寒|暖|投产|延期",
    re.I,
)
_NEGATION = re.compile(r"\b(?:not|no|without|unlikely|denied|unconfirmed)\b|未|不|否认|尚无", re.I)
_QUANTITY = re.compile(r"\d+(?:\.\d+)?\s*(?:mcm|bcm|mtpa|twh|gwh|%|百万|亿|吨|天|日)", re.I)


def source_body(item: dict) -> str:
    """Keep metadata summary and a bounded publisher fact excerpt together."""
    body = next((str(item[key]).strip() for key in ("summary", "description", "body", "content", "text")
                 if isinstance(item.get(key), str) and item[key].strip()), "")
    extra = str(item.get("fact_excerpt") or "").strip()
    if not extra or extra in body:
        return body
    if not body or body in extra:
        return extra
    return body + "\n" + extra


def source_clauses(text: str) -> list[str]:
    """Return intact narrative clauses; announcement clocks live separately."""
    text = re.sub(r"[\t ]+", " ", str(text or "")).strip()
    result = []
    for clause in _SENTENCE_END.split(text):
        clause = clause.strip()
        if not clause or _METADATA.match(clause) or _CAPACITY_METADATA.match(clause) or _EMPTY_REMARK.match(clause):
            continue
        result.append(clause)
    return result


def causal_clauses(text: str) -> list[str]:
    return [clause for clause in source_clauses(text) if _CAUSAL.search(clause)]


def clause_priority(clause: str) -> int:
    """No score for digits alone: notice IDs must not outrank source facts."""
    return 2 * len(_CAUSAL.findall(clause)) + 4 * bool(_NEGATION.search(clause)) + 3 * bool(_QUANTITY.search(clause))


def exact_capacity_fields(item: dict) -> dict:
    """Read source capacity values verbatim; never calculate actual lost flow."""
    fields = {key: item[key] for key in ("technical_capacity", "available_capacity", "unavailable_capacity", "capacity_unit") if item.get(key) is not None}
    text = str(item.get("summary", item.get("body", item.get("content", ""))) or "")
    for key, label in (("technical_capacity", r"Technical\s+capacity"),
                       ("available_capacity", r"\bavailable"),
                       ("unavailable_capacity", r"\bunavailable")):
        match = re.search(label + r"\s*:\s*(-?\d+(?:\.\d+)?)", text, re.I)
        if match and key not in fields:
            fields[key] = match.group(1)
    unit = re.search(r"Technical\s+capacity\s*:\s*-?\d+(?:\.\d+)?\s+([^;。]+)", text, re.I)
    if unit and "capacity_unit" not in fields:
        fields["capacity_unit"] = unit.group(1).strip()
    return fields


def change_status(item: dict) -> str:
    explicit = item.get("change_status")
    if explicit in {"new", "revised", "ongoing", "resolved", "ended"}:
        return str(explicit)
    if item.get("_revised") or isinstance(item.get("version"), int) and item["version"] > 1:
        return "revised"
    return "new" if item.get("_created") or item.get("version") == 1 else "known"


def temporal_state(item: dict, operational: dict) -> str:
    status = str(item.get("event_status", operational.get("event_status", ""))).lower()
    if status in {"dismissed", "cancelled", "inactive"}:
        return "dismissed_notice"
    if operational.get("scheduled_for_future"):
        return "scheduled_notice"
    if operational.get("announced_end_passed"):
        return "announced_end_elapsed_unverified_flow"
    if status == "active":
        return "active_notice"
    return str(item.get("state") or "reported_fact")
