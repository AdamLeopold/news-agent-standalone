"""As-of retrieval of comparable events and separately labelled ongoing risks.

Retrieval describes supply/demand mechanisms; it never assigns a forecast or
uses the eventual price direction to rank a candidate. Legacy daily bundles
remain in storage but are not treated as an individual comparable event.
"""

from __future__ import annotations

import copy
import math
import re
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Iterable

from .calendar import cutoff, parse_time, trading_day


_STOP = set("""the and for with that this news gas natural from will have has had was were
is are been into over after before its their more most than about under through between
energy company companies players new announced announcement says said today year years
message publication event technical available unavailable capacity remarks reason planned
unplanned other active unavailability process problems mcm day million billion daily
according expects expected also which while first next latest report reported""".split())

_ENTITIES = {
    "norway": r"\bnorway\b|\bnorwegian\b|挪威",
    "europe": r"\beurope\b|\beuropean\b|欧洲|歐洲",
    "gassco": r"\bgassco\b|挪威天然气(?:运输|运营)",
    "equinor": r"\bequinor\b|挪威国家石油",
    "troll": r"\btroll\b|特罗尔|特羅爾",
    "nyhamna": r"\bnyhamna\b|尼哈姆纳",
    "kollsnes": r"\bkollsnes\b|科尔斯内斯",
    "aasgard": r"\b(?:åsgard|asgard|aasgard)\b|阿斯加德",
    "kristin": r"\bkristin\b|克里斯汀",
    "freeport": r"\bfreeport\b|自由港",
    "cheniere": r"\bcheniere\b|切尼尔",
    "sabine_pass": r"\bsabine\s+pass\b|萨宾帕斯",
    "qatar": r"\bqatar(?:energy)?\b|卡塔尔",
    "russia": r"\brussia\w*\b|\bgazprom\b|俄罗斯|俄羅斯|俄气",
    "ukraine": r"\bukrain\w*\b|乌克兰",
    "nord_stream": r"\bnord\s+stream\b|北溪",
    "algeria": r"\balgeri\w*\b|阿尔及利亚",
    "libya": r"\bliby\w*\b|利比亚",
    "eni": r"\beni\b|埃尼",
    "united_states": r"\bunited\s+states\b|\bu\.?s\.?\b|美国",
    "germany": r"\bgerman\w*\b|德国",
    "united_kingdom": r"\bunited\s+kingdom\b|\buk\b|\bbritain\b|英国",
    "netherlands": r"\bnetherlands\b|\bdutch\b|荷兰",
}
_FACILITIES = {"troll", "nyhamna", "kollsnes", "aasgard", "kristin", "freeport", "sabine_pass", "nord_stream"}
_REGIONS = {"norway", "europe", "qatar", "russia", "ukraine", "algeria", "libya", "united_states", "germany", "united_kingdom", "netherlands"}

_LOSS = re.compile(r"\boutage\b|\bshutdown\b|\bdisrupt\w*\b|\bhalt\w*\b|\bforce\s+majeure\b|"
                   r"\bunavailability\b|(?:supply|flow|production|export)\w*.{0,28}(?:decreas|reduc|cut|loss)|"
                   r"(?:decreas|reduc|cut).{0,24}(?:supply|flow|production|export)|供应(?:减少|下降|中断|约束)|"
                   r"供气(?:下降|减少|中断)|流量(?:减少|下降)|停产|停供|减产|故障|检修", re.I)
_RELIEF = re.compile(r"\brestart\w*\b|\bresum\w*\b|\brestor\w*\b|\boutage.{0,15}(?:ended|over)\b|"
                     r"(?:supply|flow|production|export)\w*.{0,28}(?:increas|higher|improv)|"
                     r"(?:increas|higher).{0,24}(?:supply|flow|production|export)|"
                     r"供应(?:增加|改善|恢复)|供气(?:增加|改善|恢复)|流量(?:增加|回升|恢复)|复产|恢复供气", re.I)
_NEGATION = re.compile(r"\bnot\b|\bno\b|\bwithout\b|未|没有|并非|尚未|不曾|否认", re.I)
_RESTART_DELAY = re.compile(r"(?:restart|restoration|resumption)\s+(?:(?:is|was|has\s+been|further)\s+)?(?:delay\w*|postpon\w*|hold[- ]?up|setback)|"
                            r"(?:delayed|postponed)\s+(?:restart|restoration|resumption)|(?:复产|恢复|重启)(?:被|再度|再次)?(?:推迟|延后|受阻)", re.I)
_MECHANISMS = {
    "storage_draw": r"(?:storage|inventor\w*).{0,25}(?:draw|withdraw|decreas|declin|lower)|库存(?:下降|减少)|去库|提气",
    "storage_build": r"(?:storage|inventor\w*).{0,25}(?:build|inject|increas|high|full)|库存(?:增加|上升|充足|高)|补库|注气",
    "cold_demand": r"\bcold\w*\b|\bfreez\w*\b|cold\s+snap|寒潮|低温|严寒|天气转冷",
    "warm_demand": r"\bmild\b|\bwarm\w*\b|温和|暖冬|天气变暖|天气转暖",
    "weak_demand": r"(?:demand|consumption).{0,25}(?:weak|fall|declin|lower|decreas)|需求(?:下降|减少|走弱|疲软)",
    "strong_demand": r"(?:demand|consumption).{0,25}(?:strong|rising|higher|increas)|需求(?:上升|增加|增强|走强)",
    "sanction": r"\bsanction\w*\b|\bembargo\b|制裁|禁运",
    "lng_increase": r"(?:LNG|cargo|arrival)\w*.{0,30}(?:increas|higher|more)|(?:more|higher).{0,20}(?:LNG|cargo)|LNG到港增加|液化天然气进口增加",
    "lng_decrease": r"(?:LNG|cargo|arrival)\w*.{0,30}(?:decreas|lower|less|divert|cancel)|LNG到港减少|液化天然气进口减少",
    "project_start": r"\bcommission\w*\b|first\s+(?:gas|LNG)|bring\w*.{0,25}(?:onstream|on.stream)|投产|首次供气|首次产气",
}
_TOPICS = {
    "supply": r"outage|shutdown|unavailability|supply|flow|production|pipeline|restart|restor|disrupt|供气|供应|停产|中断|管道|检修|复产|流量",
    "lng": r"\bLNG\b|liquef|regas|cargo|液化天然气|到港|再气化",
    "storage": r"storage|inventor|库存|储气|补库",
    "weather_demand": r"weather|cold|warm|mild|demand|heating|天气|寒|暖|需求|供暖",
    "geopolitical": r"sanction|embargo|war|conflict|nord\s+stream|制裁|禁运|战争|冲突|北溪",
    "policy": r"regulat|permit|policy|commission|regulatory|政策|许可|监管",
    "project": r"discover|explorat|drilling|project|development|勘探|发现|钻探|项目|开发",
}
_UP = {"supply_loss", "storage_draw", "cold_demand", "strong_demand", "sanction", "lng_decrease"}
_DOWN = {"supply_relief", "storage_build", "warm_demand", "weak_demand", "lng_increase"}


def _text(event: dict) -> str:
    return " ".join(str(event.get(key) or "") for key in ("title", "summary", "fact_excerpt", "description"))


def _time(value) -> datetime | None:
    if not value:
        return None
    try:
        return parse_time(str(value))
    except (TypeError, ValueError):
        # Source notices often use +0200 offsets unsupported by Python 3.10's
        # fromisoformat, while other event timestamps are canonical UTC.
        try:
            from dateutil.parser import isoparse
            result=isoparse(str(value))
            return result if result.tzinfo is not None else None
        except (TypeError, ValueError):
            return None


def features(text: str) -> dict[str, set[str]]:
    """Bilingual mechanism features without depending on realized labels."""
    mechanisms=set()
    for clause in re.split(r"[。！？;；\n]|(?<=[.!?])\s+", text):
        relief=_RELIEF.search(clause)
        loss=_LOSS.search(clause)
        relief_negated=relief and (_NEGATION.search(clause[max(0,relief.start()-18):relief.start()]) or _RESTART_DELAY.search(clause) or
                                   re.match(r"\s+(?:has|have|did|will|is|was|were|can|could)\s+(?:still\s+)?not\b|\s*(?:并未|尚未|未能)",clause[relief.end():],re.I))
        loss_negated=loss and _NEGATION.search(clause[max(0,loss.start()-18):loss.start()])
        nuclear_restart=bool(relief and re.search(r"nuclear|reactor|核电|核反应",clause,re.I) and
                             not re.search(r"gas\s+(?:flows?|supply|exports?|production).{0,25}(?:restor|increas)|天然气供应(?:恢复|增加)",clause,re.I))
        linepack=bool(relief and re.search(r"restor\w*\s+(?:of\s+)?linepack|linepack.{0,15}restor",clause,re.I))
        if relief and not relief_negated and not nuclear_restart and not linepack:
            mechanisms.add("supply_relief")
        if nuclear_restart and re.search(r"displace.{0,40}(?:natural\s+gas|gas)|替代.{0,12}天然气",text,re.I):
            mechanisms.add("weak_demand")
        if loss and not loss_negated:
            # "outage ended / supply restored" is a relief event, not a new
            # outage because the earlier outage noun appears in the sentence.
            if not (relief and not relief_negated and re.search(r"ended|restor|resum|restart|恢复|复产",clause,re.I)):
                mechanisms.add("supply_loss")
        if relief_negated:
            mechanisms.add("supply_loss")
        for key,pattern in _MECHANISMS.items():
            match=re.search(pattern,clause,re.I)
            if match and not _NEGATION.search(clause[max(0,match.start()-12):match.start()]):
                mechanisms.add(key)
    entities={key for key,pattern in _ENTITIES.items() if re.search(pattern,text,re.I)}
    if entities&{"gassco","troll","nyhamna","kollsnes","aasgard","kristin"}:
        entities.add("norway")
    topics={key for key,pattern in _TOPICS.items() if re.search(pattern,text,re.I)}
    terms={word for word in re.findall(r"[a-zà-ž][a-zà-ž'-]{2,}",text.lower()) if word not in _STOP and not re.search(r"\d",word)}
    return {"mechanisms":mechanisms,"entities":entities,"topics":topics,"terms":terms}


def _pressure(mechanisms: set[str]) -> str:
    up,down=bool(mechanisms&_UP),bool(mechanisms&_DOWN)
    return "mixed" if up and down else "up" if up else "down" if down else "unknown"


def _atomic(event: dict) -> bool:
    if event.get("source")=="legacy_daily" or event.get("source_quality")=="daily_aggregate_no_original_url":
        return False
    if event.get("is_daily_aggregate") or event.get("event_granularity")=="daily_bundle":
        return False
    text=_text(event)
    if not text.strip():
        return False
    # A conference/programme is not a supply-demand shock merely because it
    # shares company names or "gas" with the query.
    if re.search(r"conference|summit|webinar|annual\s+meeting|论坛|研讨会|峰会",str(event.get("title","")),re.I):
        return False
    return bool(features(text)["mechanisms"] or event.get("event_start"))


def _unavailable(event: dict) -> float | None:
    operational=event.get("operational") if isinstance(event.get("operational"),dict) else {}
    value=event.get("unavailable_capacity",operational.get("unavailable_capacity"))
    if value is None:
        match=re.search(r"\bunavailable\s*(?:capacity)?\s*:\s*(\d+(?:\.\d+)?)",_text(event),re.I)
        value=match.group(1) if match else None
    try:
        return float(value) if value is not None else None
    except (TypeError,ValueError):
        return None


def operational_state(event: dict, as_of: datetime) -> str:
    """State of the announcement, separated from verified physical flow."""
    start,end=_time(event.get("event_start")),_time(event.get("event_end"))
    # Cancellation/inactivity describes an announcement, not a physical event
    # that has already happened. Future notices never become past analogues.
    if start and start>as_of:
        return "scheduled_future"
    text=_text(event)
    if event.get("actual_flow_recovery_verified") is True:
        return "verified_flow_restored"
    if re.search(r"actual.{0,12}flow.{0,20}(?:has|have|were|was).{0,12}(?:restored|resumed)|实际流量(?:已经|已)?恢复",text,re.I) and not re.search(r"not.{0,12}restored|未恢复|尚未恢复",text,re.I):
        return "reported_flow_restored"
    status=str(event.get("event_status","")).lower()
    if status in {"dismissed","cancelled","inactive"}:
        return "notice_dismissed"
    if event.get("source")=="gassco" and _unavailable(event)==0:
        return "no_unavailable_capacity_reported"
    if end and end<=as_of:
        return "announced_window_elapsed_unverified_flow" if event.get("source")=="gassco" else "announced_window_elapsed"
    if event.get("resolution_basis")=="announced_window_elapsed_unverified_flow":
        return "announced_window_elapsed_unverified_flow"
    if event.get("state")=="resolved":
        return "resolved_announcement"
    return "announced_active" if event.get("state")=="active" else str(event.get("state") or "unknown")


def _known_outcomes(event: dict, as_of: datetime) -> dict:
    """Return a copy with only price observations available by this instant."""
    result=copy.deepcopy(event)
    result.pop("legacy_label",None)
    result.pop("legacy_outcomes",None)
    available=_time(result.get("outcome_available_at"))
    if available is None or available>as_of:
        for field in ("label","outcome","actual_label","historical_label","price_response","price_reaction","actual_direction","realized_return","future_return"):
            result.pop(field,None)
    outcomes={}
    raw_outcomes=result.get("outcomes") if isinstance(result.get("outcomes"),dict) else {}
    for horizon,row in raw_outcomes.items():
        if not isinstance(row,dict):
            continue
        known=_time(row.get("available_at") or row.get("outcome_available_at"))
        if known is None or known>as_of or not row.get("end_date"):
            continue
        try:
            end_day=date.fromisoformat(str(row["end_date"])[:10])
            if not trading_day(end_day) or cutoff(end_day)>as_of:
                continue
        except (TypeError,ValueError):
            continue
        outcomes[horizon]=copy.deepcopy(row)
    if outcomes:
        result["outcomes"]=outcomes
    else:
        result.pop("outcomes",None)
    return result


def rank_events(events: Iterable[dict], text: str, as_of: datetime, limit: int = 6, *,
                exclude_news_ids: Iterable[str] | None = None,
                current_event_ids: Iterable[str] | None = None) -> list[dict]:
    """Rank atomic analogues and relevant ongoing risks using known versions.

    A self/current event cannot be an analogue. If it still represents an
    announced ongoing constraint it may be returned as explicitly overlapping
    background, to be counted once with the current-window evidence.
    """
    if limit<=0:
        return []
    query=features(text)
    if not (query["mechanisms"] or query["entities"]):
        return []
    candidates=[event for event in events if _atomic(event) and event.get("state")!="superseded"]
    doc_features=[features(_text(event)) for event in candidates]
    frequency=Counter(term for f in doc_features for term in f["terms"])
    terms=sorted(query["terms"],key=lambda term:(frequency[term],-len(term),term))[:128]
    exclude=set(exclude_news_ids or ())
    current=set(current_event_ids or ())
    backgrounds=[]
    analogues=[]
    for event,f in zip(candidates,doc_features):
        known=_time(event.get("known_at"))
        if known is None or known>as_of:
            continue
        shared_entities=query["entities"]&f["entities"]
        shared_mechanisms=query["mechanisms"]&f["mechanisms"]
        shared_topics=query["topics"]&f["topics"]
        # Generic corporate/region overlap alone is not a comparable shock.
        if not shared_mechanisms and not (shared_entities&_FACILITIES) and not (shared_entities and shared_topics and query["mechanisms"] and f["mechanisms"]):
            continue
        facility_score=12*len(shared_entities&_FACILITIES)
        named_score=6*len(shared_entities-_FACILITIES-_REGIONS)
        region_score=2*len(shared_entities&_REGIONS)
        mechanism_score=10*len(shared_mechanisms)
        lexical=sum(min(2.0,math.log(1+(len(candidates)+1)/(frequency[term]+1))) for term in terms if term in f["terms"])
        score=facility_score+named_score+region_score+mechanism_score+min(5,lexical)+min(3,len(shared_topics))
        if score<9:
            continue
        state=operational_state(event,as_of)
        if state=="scheduled_future":
            continue
        overlap=bool(exclude&set(event.get("news_ids") or ())) or bool(current&{str(event.get(k,"")) for k in ("id","event_id","source_event_id","source_message_id")})
        pressure=_pressure(f["mechanisms"])
        qpressure=_pressure(query["mechanisms"])
        relation="aligned" if pressure==qpressure and pressure in {"up","down"} else "opposing" if {pressure,qpressure}=={"up","down"} else "mixed_or_unknown"
        if relation=="opposing":
            score*=.72
        elif pressure=="mixed" and qpressure in {"up","down"}:
            # A mixed export-diversion/recovery article is a poor substitute
            # for an unambiguous supply-restoration analogue. Matching a named
            # facility can still make the contrasting case informative.
            score*=.55
        published=_time(event.get("published_at") or event.get("event_date")) or known
        age=max(0,(as_of-published).total_seconds()/86400)
        score+=2/(1+age/90)
        if score<9 or (qpressure in {"up","down"} and pressure=="mixed" and not shared_entities&_FACILITIES and score<12):
            continue
        result=_known_outcomes(event,as_of)
        result.update(retrieval_score=round(score,3),retrieval_role="historical_analogue",
                      retrieval_basis={"shared_entities":sorted(shared_entities),"shared_mechanisms":sorted(shared_mechanisms),
                                       "shared_topics":sorted(shared_topics),"mechanism_alignment":relation,
                                       "price_outcomes_used_for_ranking":False},
                      operational_state_at_as_of=state,
                      current_window_overlap=overlap,
                      prior_publication_is_not_priced_in_evidence=True)
        forward_looking=bool(re.search(r"\b(?:will|expects?|expected|plans?|planned|scheduled|eyes|could|might|next\s+week)\b|预计|将于|计划|拟于",_text(event),re.I))
        result["mechanism_evidence_status"]="conditional_announcement" if forward_looking else "reported_event_facts"
        if forward_looking:
            result["retrieval_note"]="该记录含未来计划或预期，类比公告机制；不能将预计恢复当作已经核实的流量恢复。"
        active=event.get("state")=="active" and state=="announced_active"
        end=_time(event.get("event_end"))
        residual=state=="announced_window_elapsed_unverified_flow" and end is not None and as_of-end<=timedelta(hours=48)
        if active or residual:
            result["retrieval_role"]="ongoing_background"
            result["counted_in_window"]=overlap
            result["flow_evidence_status"]="announced_capacity_only_actual_flow_not_verified" if event.get("source")=="gassco" else "reported_event_facts"
            if residual:
                result["retrieval_note"]="公告预计时段已过，实际流量恢复待核实；不能据此认定仍停供或已经复产。"
            elif overlap:
                result["retrieval_note"]="与本窗口新闻属于同一持续事件，只计一次；已入库或先前公告不是已计价证据。"
            backgrounds.append(result)
        elif not overlap and state not in {"no_unavailable_capacity_reported","scheduled_future"}:
            # Restore/relief reports are useful analogues, even though resolved;
            # unknown physical recovery is not relabelled as a verified restart.
            analogues.append(result)
    order=lambda e:(e["retrieval_score"],str(e.get("event_date","")),str(e.get("id","")))
    backgrounds.sort(key=order,reverse=True)
    analogues.sort(key=order,reverse=True)
    selected_background=backgrounds[:min(2,limit)]
    return analogues[:max(0,limit-len(selected_background))]+selected_background
