"""News collector health, distinct from an in-progress refresh or price feeds."""

import json
from datetime import datetime, timezone

from .config import ROOT, POLL_SECONDS


def _time(value):
    try:
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return value.astimezone(timezone.utc) if value.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def news_source_health(sources: list, as_of: str) -> list[dict]:
    """Return only registered news feeds with one conservative health judgement.

    A poll normally runs every POLL_SECONDS. Allow five cycles (at least five
    minutes) since success, and three cycles (at least three minutes) per run.
    These bounds describe collection freshness, never a price direction. Old
    snapshots without success clocks do not become stale merely for that reason.
    """
    registry = {row["id"]: row for row in json.loads((ROOT / "data" / "sources.json").read_text())["sources"]}
    now = _time(as_of)
    fresh_seconds = max(300, 5 * POLL_SECONDS)
    timeout_seconds = max(180, 3 * POLL_SECONDS)
    result = []
    seen = set()
    fields = ("name", "status", "state", "enabled", "last_success_at", "last_attempt_at",
              "last_finished_at", "last_error_at", "last_error", "error", "next_poll_at")
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict):
            continue
        identifier = str(source.get("id") or source.get("source") or "")
        if identifier not in registry or identifier in seen:
            continue
        seen.add(identifier)
        row = {key: source[key] for key in fields if key in source}
        row.update(id=identifier, source=identifier)
        row.setdefault("name", registry[identifier].get("name", identifier))
        status = str(source.get("status") or "unknown").casefold()
        success, attempt, finished, error_at = (_time(source.get(key)) for key in
            ("last_success_at", "last_attempt_at", "last_finished_at", "last_error_at"))
        age = (now - success).total_seconds() if now and success and success <= now else None
        running_age = (now - attempt).total_seconds() if now and attempt and attempt <= now else None
        # A source state can be read just after the context's fixed as_of while
        # another collector starts. Do not turn that later attempt into a fact
        # about the earlier analysis. Original clocks remain attributable.
        future_state = bool(now and attempt and attempt > now)
        row["state_clock_after_as_of"] = future_state
        error = source.get("last_error") or source.get("error")
        known_success = success if now and success and success <= now else None
        error_recovered = bool(now and error_at and error_at <= now and known_success
                               and error_at < known_success)
        if error_at is not None:
            # The next attempt does not erase a separately timed, already known
            # failure. Conversely, a later known success supersedes an old error
            # string left in a collector's merged state.
            known_error = bool(error and now and error_at <= now and not error_recovered)
        else:
            known_error = bool(error and not future_state)
        reasons = []
        if source.get("enabled", registry[identifier].get("enabled", True)) is False:
            health = "disabled"
        else:
            if not future_state and not error_recovered and status in {"failed", "error"}:
                reasons.append("last_poll_failed")
            if not future_state and status in {"partial", "partial_failure", "partial_failed"}:
                reasons.append("last_poll_partial")
            next_poll = _time(source.get("next_poll_at"))
            if not future_state and (status == "backoff" or known_error and now and next_poll and next_poll > now):
                reasons.append("retry_backoff")
            if known_error and "last_poll_failed" not in reasons:
                reasons.append("last_poll_error")
            if age is not None and age > fresh_seconds:
                reasons.append("last_success_stale")
            if status == "running" and not future_state and running_age is not None and running_age > timeout_seconds:
                reasons.append("poll_timed_out")
            if status == "running" and not future_state and age is None:
                reasons.append("first_success_unavailable")
            if "retry_backoff" in reasons:
                health = "backoff"
            elif "poll_timed_out" in reasons:
                health = "timeout"
            elif "last_poll_failed" in reasons or "last_poll_error" in reasons:
                health = "failed"
            elif "last_poll_partial" in reasons:
                health = "partial"
            elif "last_success_stale" in reasons:
                health = "stale"
            elif "first_success_unavailable" in reasons:
                health = "uninitialized"
            elif status == "running" and not future_state:
                health = "refreshing"
            elif age is not None or (status == "ok" and not future_state
                                     and not (now and success and success > now)):
                health = "healthy"
            else:
                health = "unknown"
        messages = {"last_poll_failed": "最近采集失败", "last_poll_error": "最近采集记录有错误",
                    "last_poll_partial": "最近采集仅部分完成", "retry_backoff": "错误后等待重试",
                    "last_success_stale": f"最近成功已超过{fresh_seconds}秒",
                    "poll_timed_out": f"本轮采集已超过{timeout_seconds}秒",
                    "first_success_unavailable": "首次成功采集尚未完成"}
        row.update(coverage_status=health, coverage_gap=bool(reasons), gap_reasons=reasons,
                   coverage_note=f"来源{identifier}：" + "；".join(messages[reason] for reason in reasons)
                   if reasons else "")
        result.append(row)
    return result


def source_coverage_gaps(coverage: dict, as_of: str) -> list[dict]:
    """Re-evaluate old or new snapshots without changing their stored bytes."""
    return [row for row in news_source_health((coverage or {}).get("sources", []), as_of)
            if row["coverage_gap"]]
