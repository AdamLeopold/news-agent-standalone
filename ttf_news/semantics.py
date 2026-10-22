"""Bidirectional, evidence-linked invariants for news analyses.

These checks reject specific contradictions; they do not assign a forecast,
repair a result, or claim to establish arbitrary natural-language truth.  A
missing observation is never converted to a zero price/supply change here.
The module is independent of the model caller so input preparation can reuse
the same grounded mechanism candidates without an import cycle.
"""

from __future__ import annotations

import re
import json
import copy
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_SPECS = (
    ("supply_increase", "down", r"(?:供应|供给|供气|流量|产量|出口量|输气量|LNG到港|LNG进口|LNG流入)(?:量)?[^。；;]{0,12}?(?:增加|增长|上升|回升|改善|宽松|恢复|充足)|(?:设施|工厂|气厂|处理厂|管道|生产|运营)[^。；;]{0,8}(?:复产|重启|恢复运营|恢复生产)|(?:复产|重启|恢复)(?:了)?(?:供气|供应|生产|运营|出口|流量|输送)|(?:supply|flows?|production|output|exports?|LNG arrivals?)[^.;]{0,25}?(?:increas\w*|improv\w*|recover\w*|restor\w*|resum\w*|higher|ris\w*)|(?:restart\w*|resum\w*|restor\w*)[^.;]{0,20}(?:operations?|production|flows?|supply)"),
    ("supply_decrease", "up", r"(?:供应|供给|供气|流量|产量|出口量|输气量|LNG到港|LNG进口)(?:量)?[^。；;]{0,12}?(?:减少|下降|降低|缩减|受限|中断|损失|短缺)|(?:发生|出现)[^。；;]{0,8}(?:停产|停供|供应中断)|意外停产|突发停产|(?:supply|flows?|production|output|exports?)[^.;]{0,25}?(?:decreas\w*|reduc\w*|drop\w*|fall\w*|lower|halt\w*|disrupt\w*)|\boutage\b|\bshutdown\b|supply disruption|force majeure"),
    ("demand_decrease", "down", r"(?:取暖|采暖|天然气|燃气|用气|欧洲)?需求[^。；;]{0,10}?(?:减少|下降|降低|走弱|疲弱|减弱)|(?:天气温和|温和天气|暖冬|天气变暖|气温回升)|(?:gas |heating )?demand[^.;]{0,20}?(?:fall\w*|decreas\w*|reduc\w*|weak\w*|lower)|\bmild weather\b|\bwarm winter\b"),
    ("demand_increase", "up", r"(?:取暖|采暖|天然气|燃气|用气|欧洲)?需求[^。；;]{0,10}?(?:增加|上升|增强|增长|激增)|(?:寒潮|天气转冷|气温下降|严寒)|(?:gas |heating )?demand[^.;]{0,20}?(?:increas\w*|ris\w*|higher|surge\w*|strong\w*)|\bcold weather\b|\bcold snap\b"),
    ("inventory_build", "down", r"(?:库存|储气)[^。；;]{0,10}?(?:增加|上升|回补|高位|充足)|\b(?:storage|inventor(?:y|ies))\b[^.;]{0,22}(?:build\w*|increas\w*|high|ample)|\bstorage build\b"),
    ("inventory_draw", "up", r"(?:库存|储气)[^。；;]{0,10}?(?:下降|减少|消耗|偏低|不足)|\b(?:storage|inventor(?:y|ies))\b[^.;]{0,22}(?:draw\w*|decreas\w*|low|deplet\w*)|\bstorage draw\b"),
)
_PAST_TENSE = {
    "supply_increase": r"|(?:supply|flows?|production|output|exports?)[^.;]{0,20}(?:rose|grew|went up|up by)|(?:增加|提升|扩大|补充)(?:了)?(?:欧洲|欧盟|天然气|LNG)?(?:供应|供给|供气|产量)|(?:increas\w*|expand\w*|boost\w*)\s+(?:(?:European|EU|gas|LNG)\s+)?(?:supply|production|output)",
    "supply_decrease": r"|(?:supply|flows?|production|output|exports?)[^.;]{0,20}(?:fell|cut|lost|went down|down by)",
    "demand_increase": r"|demand[^.;]{0,20}(?:rose|grew|went up)",
    "demand_decrease": r"|demand[^.;]{0,20}(?:fell|went down)|\bwarm weather\b",
    "inventory_build": r"|(?:storage|inventor(?:y|ies))[^.;]{0,20}(?:rose|grew|went up)",
    "inventory_draw": r"|(?:storage|inventor(?:y|ies))[^.;]{0,20}(?:fell|went down)",
}
_COMPILED = [(name, pressure, re.compile(pattern + _PAST_TENSE.get(name, ""), re.I)) for name, pressure, pattern in _SPECS]
_EUROPE = re.compile(r"欧洲|挪威|荷兰|英国|德国|欧盟|TTF|Europe|Norway|Norwegian|Gassco|Dutch|Germany|Britain", re.I)
_ACTUAL_FLOW = re.compile(r"(?:实际|计量|测得|已确认|确认)[^。；;]{0,35}(?:流量|供气|供应|输气|出口)|(?:actual|metered|measured|confirmed)[^.;]{0,35}(?:flows?|deliveries|exports?|supply)", re.I)
_FLOW_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mcm\s*(?:/\s*d(?:ay)?|per\s+day)|百万立方米\s*(?:/\s*[日天]|每天|每日))", re.I)
_CONDITIONAL = re.compile(r"若|如果|假如|假设|情景|预计|预期|计划|拟|可能|潜在|尚未|待核实|未核实|未确认|\bif\b|\bcould\b|\bmight\b|\bexpected\b|\bplanned\b|\bscheduled\b|\bunconfirmed\b", re.I)
_COOLING = re.compile(r"制冷|空调|高温[^。；;]{0,10}(?:发电|电力)|air.condition|cooling|heatwave[^.;]{0,15}(?:power|electricity)", re.I)
_PRICE_UP = re.compile(r"看涨|偏涨|上涨|上行|走高|抬升|推高|推升|bullish|upward (?:price )?pressure|upside pressure|prices?[^.;]{0,15}(?:higher|ris\w*|increas\w*)", re.I)
_PRICE_DOWN = re.compile(r"看跌|偏跌|下跌|下行|走低|回落|压低|推低|bearish|downward (?:price )?pressure|downside pressure|prices?[^.;]{0,15}(?:lower|fall\w*|decreas\w*)", re.I)
_PRICE_CONTEXT = re.compile(r"TTF|气价|价格|结算|报价|price|看涨|看跌|偏涨|偏跌", re.I)
_CAUSAL = re.compile(r"→|->|导致|造成|推升|推高|压低|推低|带来|形成|意味着|产生|使得|因此|cause|push|lead|exert|result|pressure", re.I)
_DISCLAIM_CAUSAL = re.compile(r"不代表|不等于|并不意味着|不能说明|不能说|不一定|未必|并非|不是由于|不能归因|无法归因|does not (?:mean|imply)|not necessarily|cannot attribute", re.I)
_PRICED_IN = re.compile(r"(?:已经|已)(?:被市场)?(?:充分|完全|部分)?(?:计入|反映|定价|消化)|市场(?:已经|已)?(?:充分|完全|部分)?(?:消化|反映)|(?:充分|完全|部分)计价|已计价|\b(?:already|fully|partly|partially)\s+priced[ -]in\b", re.I)
_NOT_PRICED_IN = re.compile(r"(?:尚未|还未|并未|未|没有)(?:被(?:市场)?)?(?:充分|完全|部分)?(?:(?:计入|反映(?:在)?)(?:到|至|当前|当日|市场|TTF)*(?:价格|气价)|计价)|\bnot\s+(?:yet\s+)?(?:(?:fully|partly|partially)\s+)?priced[ -]in\b", re.I)
_UNCONFIRMED_PREFIX = re.compile(r"无法确认|不能确认|未确认|尚未确认|未核实|尚未核实|无法证明|不能证明|未提供[^。；;]{0,15}(?:证据|反应|行情)|缺[少乏][^。；;]{0,15}(?:证据|反应)|无[^。；;]{0,12}证据|未见|没有[^。；;]{0,12}证据|不代表|不等于|不意味着|是否|若|如果|假如|即使|不能仅凭|cannot confirm|not confirmed|no evidence|whether|\bif\b", re.I)
_INFORMATION_SCOPE = re.compile(r"输入|资料|所供|所提供|报道|新闻|证据|未确认|未核实|无法确认|未提供|缺少|缺乏|尚不清楚|待核实|not (?:provided|confirmed)|no evidence", re.I)


def _clauses(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[。！？;；\n]+|(?<=[.!?])\s+", text) if s.strip()]


def _source_text(item: dict) -> str:
    values = []
    for key in ("title", "summary", "fact_excerpt", "description", "body", "content", "text", "facts", "quoted_fact"):
        value = item.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in values:
            values.append(value.strip())
    return "\n".join(values)


def _moment(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", value.replace("Z", "+00:00")))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else None
        return parsed
    except ValueError:
        return None


def _known_source(row: dict, as_of: datetime | None) -> bool:
    if as_of is None:
        return True
    observation = None
    for key in ("published_at", "published_date", "publication_time", "timestamp", "date", "Date", "trade_date", "session_date", "time"):
        value = row.get(key)
        if not value:
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
            source_zone = {"eia": "America/New_York", "eu_energy": "Europe/Brussels"}.get(row.get("source"), row.get("timezone") or "UTC")
            try:
                local_day = as_of.astimezone(ZoneInfo(source_zone)).date().isoformat()
            except (ZoneInfoNotFoundError, TypeError, ValueError):
                local_day = as_of.date().isoformat()
            if str(value) > local_day:
                return False
            continue
        observation = _moment(value)
        if observation is not None:
            break
    clocks = [observation]
    clocks.extend(_moment(row.get(key)) for key in ("available_at", "known_at", "version_seen_at", "first_seen_at", "discovered_at", "collected_at", "fact_excerpt_created_at", "revision_available_at", "revised_at", "updated_at"))
    return not any(when is not None and when > as_of for when in clocks)


def _known_price_reference(context: dict, prepared: dict) -> dict:
    # Empty/None is a deliberate temporal filtering result, not permission to
    # recover future values from the unfiltered immutable input snapshot.
    value = prepared.get("price_reference") if "price_reference" in prepared else context.get("price_reference")
    return value if isinstance(value, dict) else {}


def _missing_information_prefix(prefix: str) -> bool:
    scope = re.split(r"[，,]|但是|然而|不过|但|\bbut\b|\bhowever\b", prefix, flags=re.I)[-1]
    return bool(re.search(r"(?:未给(?:出)?|未提供|未提及|未报告|没有给出|没有提供|缺少|缺乏|未掌握|无从确认|无法判断|未知)[^。；;，,]{0,12}$|(?:no (?:information|data|evidence|report)|does not mention|not provided|not reported)[^.;,]{0,16}$|\bno\s+(?:(?:independent|confirmed|actual|reliable)\s+)*$", scope, re.I))


def _affirmed_actual_flow(text: str) -> bool:
    for clause in _clauses(text):
        for match in _ACTUAL_FLOW.finditer(clause):
            if _missing_information_prefix(clause[max(0, match.start()-64):match.start()]):
                continue
            if re.search(r"未提供|待核实|待确认|未确认|未核实|无法确认|not confirmed|unverified", match.group(), re.I):
                continue
            return True
    return False


def _negated_match(clause: str, match: re.Match) -> bool:
    prefix = clause[max(0, match.start()-16):match.start()]
    suffix = clause[match.end():match.end()+24]
    matched = match.group()
    information_prefix = clause[max(0, match.start()-64):match.start()]
    if _missing_information_prefix(information_prefix):
        return True
    if re.match(r"(?:信息|数据|事实|证据|情况|状态)?(?:未给|未提供|未提及|未报告|不详|未知)", suffix):
        return True
    # Missing confirmation is not a confirmed opposite event.
    if re.search(r"(?:未|尚未|没有|无法|不能)(?:完全|充分|能)?(?:确认|核实|证实)|not (?:yet )?confirmed|unverified", prefix + matched + suffix, re.I):
        return True
    if re.search(r"没有|未发生|否认|避免|并非|不存在|不曾|\bno\s+$|\bnot\s+$|\bwithout\s+$", prefix, re.I):
        return True
    if re.match(r"(?:增加|提升|扩大|补充)(?:了)?(?:欧洲|欧盟|天然气|LNG)?(?:供应|供给|供气|产量)|(?:increas\w*|expand\w*|boost\w*)\s+",matched,re.I) and re.search(r"(?:未|不|不会|未能|未曾)\s*$",prefix):
        return True
    if re.search(r"(?:供应|供气|流量|生产|需求|库存|储气).{0,6}(?:未|没有|尚未|未能|不曾)(?:完全|充分)?(?:恢复|增加|上升|减少|下降)|\bnot\s+(?:yet |fully )?(?:restor\w*|resum\w*|increas\w*|decreas\w*|rise|rose|fall|fell|recover\w*|restart\w*)", matched, re.I):
        return True
    if re.search(r"停产|停供|outage|shutdown|disruption", matched, re.I):
        if re.search(r"此前|先前|原先|previous|earlier|prior", prefix, re.I) or re.search(r"已(?:经)?结束|提前结束|已取消|已经解除|(?:has |is )?(?:ended|resolved|cancelled)|was (?:ended|resolved)", suffix, re.I):
            return True
        if (re.search(r"\bafter\b|\bfollowing\b", prefix, re.I) and re.search(r"restart|resum|restor", clause, re.I)) or re.search(r"后.{0,8}(?:重启|复产|恢复)", suffix):
            return True
    if re.search(r"不存在|未发生|不实|被否认|did not occur|was denied|false", suffix, re.I):
        return True
    return False


_SUPPLY_RISK_OBJECT = re.compile(
    r"(?:供应|供给|供气)(?:中断|短缺|受限|停供|过剩)?风险|"
    r"(?:gas[ -]supply|supply)\s+(?:(?:disruption|shortage|outage|surplus)s?\s+)?risks?|"
    r"risks?\s+(?:of|to)\s+(?:(?:gas|European)\s+)?supply(?:\s+(?:disruption|shortage|outage|surplus)s?)?", re.I)


_SUPPLY_PRESSURE_OBJECT = re.compile(
    r"(?:供应|供给|供气)(?:约束|压力)(?:压力|风险)?|"
    r"(?:gas[ -]supply|supply)\s+(?:constraints?|pressure)(?:\s+(?:risks?|pressure))?",re.I)


def _matching_mechanisms(clause: str) -> list[dict]:
    # A change in supply risk/constraint pressure is not a measured change
    # in supply itself. Mask only that object, retaining exact positions so an
    # actual flow change later in the same sentence is still extracted.
    physical = list(clause)
    for object_pattern in (_SUPPLY_RISK_OBJECT,_SUPPLY_PRESSURE_OBJECT):
        for risk in object_pattern.finditer(clause):
            physical[risk.start():risk.end()] = ' ' * (risk.end()-risk.start())
    physical = ''.join(physical)
    found = []
    for name, pressure, pattern in _COMPILED:
        for match in pattern.finditer(physical if name.startswith('supply_') else clause):
            if re.search(r'[，,]|价格|气价|\b(?:TTF|prices?)\b',match.group(),re.I):
                continue  # A later price/different-clause verb is not this physical change.
            if _negated_match(clause, match):
                continue
            if name == "demand_decrease" and _COOLING.search(clause) and not re.search(r"取暖|采暖|heating", match.group(), re.I):
                continue
            found.append({"mechanism": name, "pressure": pressure, "span": match.span()})
    return found


def extract_mechanisms(item: dict) -> list[dict]:
    """Extract quoted candidate mechanisms without deciding a net forecast.

    A Gassco capacity announcement is always conditional capacity evidence,
    even when Active. Dismissal removes an announcement's risk; it does not
    establish that actual production/flows have recovered.
    """
    text = _source_text(item)
    operational = item.get("operational") or {}
    source = str(item.get("source", "")).lower()
    gassco = "gassco" in source or bool(re.search(r"\bGassco\b", str(item.get("title", "")), re.I))
    status = str(item.get("event_status", operational.get("event_status", ""))).lower()
    temporal = str(item.get("temporal_state", ""))
    result: list[dict] = []
    if gassco and not _affirmed_actual_flow(text):
        unavailable = item.get("unavailable_capacity", operational.get("unavailable_capacity"))
        if unavailable is None:
            match = re.search(r"\bunavailable\s*:\s*(\d+(?:\.\d+)?)", text, re.I)
            unavailable = float(match[1]) if match else None
        if status in {"dismissed", "cancelled", "inactive"} or temporal in {"dismissed", "cancelled"}:
            quote = next((c for c in _clauses(text) if re.search(r"Dismissed|Cancelled|Inactive|取消|撤销", c, re.I)), text[:500])
            return [{"mechanism": "capacity_risk_removed", "pressure": "down", "conditional": True,
                     "quoted_fact": quote, "evidence_kind": "capacity_only"}] if quote else []
        try:
            reduced = unavailable is not None and float(unavailable) > 0
        except (ValueError, TypeError):
            reduced = False
        if reduced:
            quote = next((c for c in _clauses(text) if re.search(r"unavailable\s*:|不可用容量", c, re.I)), text[:500])
            result.append({"mechanism": "capacity_constraint", "pressure": "up", "conditional": True,
                           "quoted_fact": quote, "evidence_kind": "capacity_only"})
        # Do not infer an actual supply cut or recovery from notice terminology.
        return result
    for clause in _clauses(text):
        if re.match(r"^(?:请|必须|禁止|不要|规则|返回JSON|输出JSON)", clause):
            continue
        for candidate in _matching_mechanisms(clause):
            item_result = {key: candidate[key] for key in ("mechanism", "pressure")}
            actual_match = _ACTUAL_FLOW.search(clause)
            prospective_actual = bool(actual_match and re.search(r"预计|预期|未来|明年|计划|将|\bexpected\b|\bforecast\b|\bwill\b|\bif\b|若", clause[max(0, actual_match.start()-18):actual_match.start()], re.I))
            actual = candidate["mechanism"].startswith("supply_") and _affirmed_actual_flow(clause) and not prospective_actual
            item_result.update(conditional=bool(_CONDITIONAL.search(clause)) or temporal in {"scheduled", "future", "announced_end_elapsed_unverified_flow"},
                               quoted_fact=clause,
                               evidence_kind="actual_flow" if actual else "reported_fact")
            amount = _FLOW_AMOUNT.search(clause)
            if actual and amount:
                item_result["magnitude_mcm_d"] = float(amount[1])
            if not any(r["mechanism"] == item_result["mechanism"] and r["quoted_fact"] == clause for r in result):
                result.append(item_result)
    return result


def prospective_supply_only(item: dict) -> bool:
    """Whether supplied resource/project facts contain no current delivery.

    A discovery, licence or memorandum can change future expectations. It is
    not itself a physical increase in today's production or European flows.
    """
    text = _source_text(item)
    if not re.search(r"勘探|油气发现|天然气发现|发现.{0,25}(?:气田|油田|油气|天然气|储量|资源|百万桶)|(?:储量|资源量).{0,12}发现|许可证|许可|谅解备忘录|合作意向|\bdiscover(?:y|ies|ed)\b|\bexploration\b|\bstruck\s+(?:gas|oil)\b|hit paydirt|\blicen[cs]e\b|\bpermit\b|\bMOU\b|memorandum of understanding",text,re.I):
        return False
    if _affirmed_actual_flow(text):
        for clause in _clauses(text):
            if _affirmed_actual_flow(clause) and not _CONDITIONAL.search(clause) and _matching_mechanisms(clause):
                return False
    delivery = re.compile(r"(?:已(?:经)?|正式)(?:投产|开始生产|开始供气|开始交付|投入运营)|首次(?:实际)?(?:天然气)?(?:交付|供气|输送)|(?:has|have)\s+(?:now\s+)?(?:commenced|started|begun)\s+(?:commercial\s+)?(?:production|deliveries|gas\s+deliveries)|\bfirst\s+gas\s+(?:deliveries|delivery|delivered|flows)\b|\b(?:began|commenced|started)\s+(?:commercial\s+)?production\b|\b(?:brought|came)\s+(?:on\s*line|on\s+stream)\b",re.I)
    for clause in _clauses(text):
        for match in delivery.finditer(clause):
            before = re.split(r"[，,]",clause[max(0,match.start()-60):match.start()])[-1]
            after = clause[match.end():match.end()+25]
            if _missing_information_prefix(before) or re.search(r"尚未|未确认|未核实|没有|未来|预计|预期|计划|拟|将|若|如果|\b(?:will|plans?|planned|expected|scheduled|potential|not|no|without|if)\b",before+match.group(),re.I):
                continue
            if re.search(r"待核实|未确认|尚未|\b(?:unconfirmed|expected|scheduled)\b",after,re.I):
                continue
            return False
    return True


def _invalidation_event_condition(path: str, clause: str, match: re.Match) -> bool | None:
    """A failure criterion is hypothetical unless it asserts an observed fact.

    This is used only at physical-flow predicates, never as a whole-field
    exemption. Other source checks, quantities, roles and pricing still run.
    None preserves the existing non-field-specific interpretation.
    """
    if not re.fullmatch(r'detail\.invalidation_conditions\[\d+\]',path):
        return None
    separators=list(re.finditer(r'[，,]|→|->|但是|但|因此|所以',clause))
    start=max((part.end() for part in separators if part.end()<=match.start()),default=0)
    end=min((part.start() for part in separators if part.start()>=match.end()),default=len(clause))
    local=clause[start:end]
    before=clause[start:match.start()]
    if re.search(r'若|如果|假如|假设|一旦|是否|无法确认|未确认|不能证明|不代表|\bif\b',before,re.I):
        return True
    if re.search(r'已经|现已|业已|已(?:经)?(?:被|获)?确认|确认了|已(?:完成|发生|造成|导致|恢复|增加|减少|停供|停产|复产)|实测(?:确认|显示|表明)|事实(?:证明|表明)|(?:目前|当前|现在)(?:已|正|确实|实际)?(?:供气|供应|流量|生产|停供|停产)|(?:目前|当前|现在)(?:已|正|实际)?(?:增加|提升|扩大|恢复|改善)(?:了)?(?:欧洲|欧盟|天然气|LNG)?(?:供应|供给|供气|流量|产量)|\b(?:has|have)\s+(?:already\s+)?(?:been|confirmed)|\b(?:is|are)\s+(?:currently|now)\b',local,re.I):
        return False
    # An unmarked field criterion does not introduce assumed quantities. The
    # original numeric/physical checks keep handling such claims unchanged.
    if re.search(r'\d+(?:\.\d+)?\s*(?:mcm\s*[/／]\s*d(?:ay)?|bcm|百万立方米|亿立方米|MW(?:h)?|%)',local,re.I):
        return None
    return True


def _check_prospective_supply_scope(result: dict, context: dict, prepared: dict) -> None:
    cards = {row.get("id"):row for row in prepared.get("window_news",[]) if isinstance(row,dict) and row.get("id")}
    cards["N0"] = prepared.get("current_news") or context.get("current_news") or {}
    for alias,row in list(cards.items()):
        if row.get("same_as_current_news_id") in cards:
            cards[alias]=cards[row["same_as_current_news_id"]]
    for path,statement in _strings(result):
        if ".scenarios." in path:
            continue
        for clause in _clauses(statement):
            aliases = re.findall(r"(?<!\w)N\d+(?!\w)",clause)
            scoped = [(alias,cards[alias]) for alias in aliases if alias in cards]
            if not aliases and prospective_supply_only(cards["N0"]):
                scoped=[("N0",cards["N0"])]
            for alias,card in scoped:
                if not prospective_supply_only(card):
                    continue
                physical_increase = re.search(r"(?:供应|供给|供气|流量|产量)[^。；;，,]{0,12}(?:增加|增长|上升|改善|恢复|宽松)|(?:增加|提升|扩大|补充)(?:了)?(?:欧洲|欧盟|天然气|LNG)?(?:供应|供给|供气|产量)|(?:供应|供给)[^。；;，,]{0,10}压力[^。；;，,]{0,6}(?:降低|减弱|缓解)|(?:降低|减弱|缓解)[^。；;，,]{0,6}(?:供应|供给)压力|(?:actual\s+)?(?:supply|production|flows?)[^.;,]{0,18}(?:increas\w*|higher|restor\w*|improv\w*)",clause,re.I)
                if not physical_increase:
                    continue
                condition=_invalidation_event_condition(path,clause,physical_increase)
                if condition is True:
                    continue
                premise=re.split(r'[，,]|→|->|但是|但|因此',clause[:physical_increase.start()])[-1]
                if condition is not False and re.search(r'若|如果|假如|假设|一旦|\bif\b',premise,re.I):
                    continue  # A sourced hypothetical startup is not a current delivery assertion.
                if condition is not False and re.search(r"长期|中长期|未来|远期|投产后|若.{0,30}投产|如果.{0,30}投产|供给预期|供应预期|资源前景|预计|预期|有望|潜在|将(?:增加|提升|扩大|补充)|long.term|future|prospective|expectation|\bif\b",clause,re.I):
                    continue
                if _DISCLAIM_CAUSAL.search(clause) or re.search(r"不能.{0,10}(?:视为|写成|认为)|尚未|未形成|未提供|未确认",clause[:physical_increase.start()]):
                    continue
                raise ValueError(f"{path}:{alias}输入仅有资源发现/勘探/许可/合作前瞻，未给已投产或实际交付事实；不能写成当前实物增供并抵消当日容量约束。可分析长期供给预期、未来投产条件及其与当前供给的时间差，不得据此补默认方向")


def _check_current_project_startup_scope(result: dict, context: dict, prepared: dict) -> None:
    """A commissioning headline does not support an invented non-startup date.

    This deliberately covers the observed current-project headline, not all
    discoveries or future projects. It neither asserts that production has
    started nor supplies a delivery volume absent from the source.
    """
    current = prepared.get("current_news") or context.get("current_news") or {}
    if not _known_source(current, _moment(context.get("as_of") or prepared.get("as_of"))):
        return
    title = str(current.get("title", ""))
    if not re.search(r"\bturn(?:ing|s|ed)?\s+on\s+(?:the\s+)?taps\b", title, re.I):
        return
    source = _source_text(current)
    # Explicit non-production/future commissioning is usable; an absent date
    # or an absent European delivery quantity is not such a fact.
    if re.search(r"(?:尚未|还未|未)(?:投产|投入运营|开始生产|开始供气)|(?:has|have|is)\s+not\s+(?:yet\s+)?(?:commenced|started|begun|in)\s+(?:commercial\s+)?(?:production|operations?)|(?:will|plans?\s+to|expected\s+to|scheduled\s+to)\s+(?:begin|start|commence)\s+(?:commercial\s+)?(?:production|operations?)|(?:预计|计划|拟|将于)[^。；;\n]{0,25}(?:投产|投入运营|开始生产)", source, re.I):
        return
    aliases = {"N0", current.get("same_as_window_news_id")}
    aliases.update(row.get("id") for row in prepared.get("window_news", [])
                   if isinstance(row, dict) and row.get("same_as_current_news_id") == "N0")
    not_started = re.compile(r"(?:项目|设施|气田|工厂)[^，,。；;]{0,10}(?:尚未|还未|未)(?:投产|投入运营|开始生产|开始供气)|(?:尚未|还未)(?:投产|投入运营|开始生产|开始供气)")
    for path, statement in _strings(result):
        if ".scenarios." in path:
            continue
        for clause in _clauses(statement):
            cited = set(re.findall(r"(?<!\w)N\d+(?!\w)", clause))
            if cited and not cited.intersection(aliases):
                continue
            if not cited and not re.search(r"(?:新|该|本|此)项目|项目尚未|项目还未", clause):
                continue
            for match in not_started.finditer(clause):
                before = re.split(r"[，,]|但是|但|然而", clause[:match.start()])[-1]
                if re.search(r"若|如果|假设|是否|不能(?:断言|确认|证明)|无法确认|未提供[^，,]{0,12}(?:证据|依据)", before):
                    continue
                raise ValueError(f"{path}:N0来源标题使用turning on the taps的启动措辞，摘要仅说明本国电网供能/减排，未提供可断言项目尚未投产的依据；缺少投产时刻或输欧数量不等于尚未投产。请保留启动/供能原文及交付范围未知，不补已投产或未投产事实")


def _check_future_well_action_not_completed(result: dict, context: dict, prepared: dict) -> None:
    """Do not promote a sourced future plug/abandonment to a completed fact."""
    cards={row.get('id'):row for row in prepared.get('window_news',[]) if isinstance(row,dict) and row.get('id')}
    cards['N0']={**(prepared.get('current_news') or {}),**(context.get('current_news') or {})}
    for alias,row in list(cards.items()):
        if row.get('same_as_current_news_id') in cards:
            cards[alias]=cards[row['same_as_current_news_id']]
    now=_moment(context.get('as_of') or prepared.get('as_of'))
    future=re.compile(r'\b(?:will\s+(?:(?:now|soon|eventually)\s+)?be|is\s+(?:scheduled|planned|set)\s+to\s+be)\s+(?:permanently\s+)?plugged(?:\s+and\s+abandoned)?|(?:计划|拟|将|预计)[^。；;，,]{0,18}(?:永久)?(?:封井|封堵|弃井)',re.I)
    completed=re.compile(r'(?:随后|继而)(?:永久)?(?:封井|封堵|弃井)(?:并弃置)?|(?:已经|现已|已)(?:被|完成)?(?:永久)?(?:封井|封堵|弃井|弃置)|(?:封井|封堵|弃井)(?:已经|已)(?:完成|结束)|\b(?:has|have)\s+(?:(?:now|already)\s+)?been\s+(?:permanently\s+)?plugged(?:\s+and\s+abandoned)?|\bwas\s+(?:permanently\s+)?plugged(?:\s+and\s+abandoned)?',re.I)
    for path,statement in _strings(result):
        if '.scenarios.' in path:
            continue
        for clause in _clauses(statement):
            for match in completed.finditer(clause):
                if _physical_claim_is_conditional(clause,match):
                    continue
                before=re.split(r'[，,]|→|->|但是|但|因此',clause[:match.start()])[-1]
                if re.search(r'(?:并非|不是|不能(?:说|断言|认定|声称)|不得(?:说|断言|认定|声称))(?:该井|此井|井|该|此)?\s*$',before):
                    continue  # A locally negated completion is not an assertion.
                if re.match(r'随后|继而',match.group()) and re.search(r'将|计划|下一步|待|拟',re.split(r'[，,]|→|->|但是|但|因此',clause[:match.start()])[-1]):
                    continue  # Explicit future sequence retains the source modal.
                aliases=re.findall(r'(?<!\w)[NE]\d+(?!\w)',clause)
                scoped=[(alias,cards[alias]) for alias in aliases if alias in cards] if aliases else [('N0',cards['N0'])]
                for alias,source in scoped:
                    text=_source_text(source)
                    if not _known_source(source,now) or not future.search(text):
                        continue
                    source_complete=False
                    for source_clause in _clauses(text):
                        for observed in completed.finditer(source_clause):
                            if not _physical_claim_is_conditional(source_clause,observed):
                                source_complete=True
                    if not source_complete:
                        raise ValueError(f'{path}:{alias}来源仅说该井将封堵/弃置，不能写已被永久封堵/井已弃置/已完成封井；请保留将/计划等未来时态，不从报道日期已过推断完成。原句will now be plugged and abandoned；实际has been/was plugged完成事实须有相应来源，不改新闻或收盘方向')


def _check_notice_facility_pipeline_type(result: dict, context: dict, prepared: dict) -> None:
    """A named Gassco production/processing notice is not a pipeline notice."""
    cards={row.get('id'):row for row in prepared.get('window_news',[]) if isinstance(row,dict) and row.get('id')}
    cards['N0']={**(prepared.get('current_news') or {}),**(context.get('current_news') or {})}
    for alias,row in list(cards.items()):
        if row.get('same_as_current_news_id') in cards:
            cards[alias]=cards[row['same_as_current_news_id']]
    now=_moment(context.get('as_of') or prepared.get('as_of'))
    def entity(row):
        match=re.search(r'\bGassco\s+([^:：]{1,45})[:：]',str(row.get('title','')),re.I)
        return match[1].strip() if match else None
    def kind(row):
        op=row.get('operational') or {}
        text=str(row.get('facility_type') or op.get('facility_type') or op.get('source_facility_type') or row.get('title') or '')
        if re.search(r'\bGas production field\b|天然气生产场所|气田',text,re.I):return 'Gas production field（气田/生产设施）'
        if re.search(r'\bGas treatment plant\b|天然气处理厂|气体处理厂',text,re.I):return 'Gas treatment plant（处理厂）'
        if re.search(r'\bpipeline\b|管道',text,re.I):return 'pipeline'
        name=entity(row)
        if name and re.search(re.escape(name)+r'\s*(?:gas\s+)?(?:pipeline|管道)',_source_text(row),re.I):return 'pipeline'
        return None  # Other does not identify a facility class.
    for path,statement in _strings(result):
        statement_aliases=set(re.findall(r'(?<!\w)N\d+(?!\w)',statement))
        for clause in _clauses(statement):
            for match in re.finditer(r'管道|\bpipeline\b',clause,re.I):
                before=clause[max(0,match.start()-32):match.start()]
                after=clause[match.end():match.end()+18]
                if re.search(r'若|如果|假设|是否|无法确认|不能确认|不代表|不等于|并非|不是|不能称为|不能写成|不能改称',before):
                    continue
                aliases=set(re.findall(r'(?<!\w)N\d+(?!\w)',clause)) or (statement_aliases if len(statement_aliases)==1 else set())
                scoped=[(alias,cards[alias]) for alias in aliases if alias in cards]
                if not aliases:
                    scoped=[(alias,row) for alias,row in cards.items()
                            if entity(row) and entity(row).casefold() in before.casefold()]
                notices=[(alias,row) for alias,row in scoped if _notice_item(row) and _known_source(row,now)]
                if not notices:
                    continue
                named=any(entity(row) and entity(row).casefold() in before.casefold() for _,row in notices)
                classified=bool(named or re.search(r'该|本|此|这条|这一',before[-4:])
                                or aliases and re.match(r'(?:未来|计划|年度|即将|的|\s){0,4}(?:维护|检修|停运|不可用|容量|公告|maintenance|outage|unavailability|capacity)',after,re.I))
                if not classified:
                    continue  # A downstream pipeline channel is not a type label.
                if not aliases and any(kind(row)=='pipeline' for _,row in notices):
                    continue  # A genuinely sourced same-name pipeline may exist.
                for alias,row in notices:
                    actual=kind(row)
                    if actual=='pipeline':continue
                    message=f'来源设施类型为{actual}' if actual else '来源仅Other/未给设施类别，不能据此补成管道'
                    raise ValueError(f'{path}:{alias}{message}，不得称Troll等该实体为pipeline/管道维护；须保留对应N的原设施类别或泛称设施公告，不能借另一N的管道合同套类型，不改方向')


def _strings(value: Any, path: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, row in value.items():
            if key not in {"_meta", "source_fact_checks", "references", "matched_terms", "direction", "signal_strength", "missing_fields"}:
                yield from _strings(row, f"{path}.{key}".strip("."))
    elif isinstance(value, list):
        for index, row in enumerate(value):
            yield from _strings(row, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def notice_settlement_timing(source: dict, session: dict, as_of: str) -> dict:
    """Keep event-versus-now and event-versus-settlement as separate facts."""
    if not _notice_item(source):
        return {}
    op=source.get('operational') or {}
    start=_moment(source.get('event_start') or op.get('event_start_utc'))
    end=_moment(source.get('event_end') or op.get('event_end_utc'))
    now=_moment(as_of);left=_moment(session.get('window_start'));right=_moment(session.get('window_end') or session.get('cutoff_utc'))
    if not start or not now or not left or not right or right<=left:
        return {}
    if start>=right:
        relation='at_or_after_target_window_end';window_note='目标结算窗口结束时或之后开始'
    elif start>=left:
        relation='starts_inside_target_window';window_note='目标结算窗口内开始'
    else:
        relation='starts_before_target_window';window_note='目标结算窗口前开始'
    result={'as_of_relation':'not_started' if start>now else 'start_time_reached',
            'start_vs_target_window':relation,
            'description':('截至分析时点尚未开始；' if start>now else '公告开始时刻已到；')+window_note+'；开始时刻不证明实际流量损失'}
    if end and end>start:
        result['notice_interval_overlaps_target_window']=max(start,left)<min(end,right)
    return result


def source_fact_projection(prepared: dict) -> dict | None:
    """Compact, mechanically copied source facts for constrained generation."""
    ledger = prepared.get('evidence_ledger') or {}
    if not ledger.get('notice_facts'):
        return None
    rows=[]
    states={'active_notice':'当前公告；流量未知',
            'announced_end_elapsed_unverified_flow':'公告已结束；当前未知',
            'scheduled_notice':'未来公告',
            'dismissed_notice':'已撤销；流量未知'}
    for source in ledger['notice_facts']:
        row={key:source[key] for key in ('id','facility','facility_type','notice_type','unavailable_capacity') if key in source}
        if row.get('facility_type'):
            row['facility_type']={'Gas production field（气田/生产设施）':'气田/生产设施',
                                  'Gas treatment plant（处理厂）':'处理厂',
                                  'Other（来源未给具体类别）':'类别未给',
                                  'Pipeline（管道）':'管道'}.get(row['facility_type'],row['facility_type'])
        if source.get('event_start_utc') or source.get('event_end_utc'):
            def compact_utc(stamp):
                value=_moment(stamp)
                if value is None:return stamp
                precision='microseconds' if value.microsecond else 'seconds' if value.second else 'minutes'
                return value.astimezone(timezone.utc).isoformat(timespec=precision).replace('+00:00','Z')
            row['period_utc']=[compact_utc(source.get('event_start_utc')),compact_utc(source.get('event_end_utc'))]
        if source.get('state') in states:
            row['state_description']=states[source['state']]
            observation=str(source.get('actual_flow_observation') or '')
            if source['state']=='dismissed_notice' and (not observation or observation.startswith('输入未提供')):
                row['state_description']='仅该公告约束撤销；实际流量与整体供应风险是否消除仍未知'
        elif source.get('state_description'):
            row['state_description']=source['state_description']
        if source.get('settlement_timing'):
            row['settlement_timing']=source['settlement_timing']
        cause=source.get('cause_original')
        if isinstance(cause,str) and len(cause.split())<=10 and len(cause)<=80:
            row['cause_original']=cause
        rows.append(row)
    pricing=ledger.get('pricing_observation') or {}
    projection={'fact_contract_version':'operator-facts-2.2','operator_notices':rows,
            'capacity_scope':'公告不可用容量，非实际流量计量',
            'pricing_observation':{
                **({'observed_delta':pricing['observed_delta']} if 'observed_delta' in pricing else {}),
                **{key:pricing[key] for key in ('observed_at','available_at','clock_roles','current_news_timing') if pricing.get(key) is not None},
                'reaction_evidence_available':bool(pricing.get('announcement_reaction_evidence_available')),
                'pricing_status':('反应资料见输入，不由单向价变认定计价' if pricing.get('announcement_reaction_evidence_available') else '无法确认是否已计价；不是尚未计价或价格无反应')}}
    relations=ledger.get('notice_interval_relations') or []
    if relations:
        projection['non_overlapping_notice_ids']=[row['ids'] for row in relations if row.get('relationship')=='non_overlapping_intervals']
    active=_active_notice_types(prepared)
    planned=[identifier for identifier,kind in active.items() if kind=='计划']
    unplanned=[identifier for identifier,kind in active.items() if kind=='非计划']
    if planned and unplanned:
        has_flow_statement=any(str(source.get('actual_flow_observation') or '').startswith('原文含')
                               for source in ledger['notice_facts'] if source.get('id') in active)
        flow_scope='实际流量核验见各N原文' if has_flow_statement else '实际流量未核实'
        projection['active_notice_summary']={'ids':list(active),'planned_count':len(planned),'unplanned_count':len(unplanned),
                                             'description':f'{len(planned)}项计划+{len(unplanned)}项非计划容量公告，{flow_scope}'}
    return projection


def _active_notice_types(prepared: dict, context: dict | None = None) -> dict[str,str]:
    """Count distinct current notice IDs, never capacity or measured flow."""
    ledger=(prepared.get('evidence_ledger') or {}).get('notice_facts') or []
    if ledger:
        return {row['id']:row['notice_type'] for row in ledger
                if row.get('id') and row.get('state')=='active_notice' and row.get('notice_type') in {'计划','非计划'}}
    # Prepared cards support validation callers without the compact ledger.
    context=context or {}
    cards={row.get('id'):row for row in prepared.get('window_news',[]) if isinstance(row,dict) and row.get('id')}
    cards['N0']=prepared.get('current_news') or context.get('current_news') or {}
    result={}
    for identifier,row in cards.items():
        if row.get('same_as_current_news_id') or not _notice_item(row):
            continue
        op=row.get('operational') or {}
        if row.get('temporal_state')!='active_notice' or str(row.get('event_status') or op.get('event_status') or 'active').casefold()!='active':
            continue
        title=str(row.get('title') or '')
        if re.search(r'\bunplanned\b',title,re.I):result[identifier]='非计划'
        elif re.search(r'\bplanned\b',title,re.I):result[identifier]='计划'
    return result


def _price_signs(text: str) -> set[str]:
    if not _PRICE_CONTEXT.search(text):
        return set()
    found = set()
    for sign, pattern in (("up", _PRICE_UP), ("down", _PRICE_DOWN)):
        for match in pattern.finditer(text):
            before = text[max(0, match.start()-16):match.start()]
            after = text[match.end():match.end()+18]
            # Less upward pressure is a bearish marginal mechanism; less
            # downward pressure is bullish. Do not count the word 涨/跌 alone.
            weakened_after = bool(re.match(r"(?:的)?(?:压力|风险|预期|空间|势头)(?:逐渐|进一步|明显|有所)?(?:减弱|缓解|消退|降低|减少|受抑|被抵消)", after))
            weakened_before = bool(re.search(r"(?:缓解|减弱|削弱|抑制|降低|抵消)(?:了)?(?:TTF|气价|价格|的)?$", before, re.I))
            if (weakened_after or weakened_before) and not re.search(r"未|不|没有|无法|不能", before[-8:]):
                found.add("down" if sign == "up" else "up")
            else:
                found.add(sign)
    return found


def _check_causal_statement(path: str, statement: str) -> None:
    for sentence in _clauses(statement):
        # A contrast may legitimately distinguish supply pressure from price
        # momentum, e.g. supply relief, BUT the observed quote remains higher.
        blocks = re.split(r"(?:但(?:是)?|不过|然而|而盘中|\bhowever\b|\bbut\b)", sentence, flags=re.I)
        for block in blocks:
            if _DISCLAIM_CAUSAL.search(block):
                continue
            signs = {row["pressure"] for row in _validation_mechanisms(block)}
            price_signs = _price_signs(block)
            if len(signs) == 1 and len(price_signs) == 1 and signs != price_signs and _CAUSAL.search(block):
                raise ValueError(f"{path}:供需传导方向相反；供应改善/需求下降通常产生下行压力，供应约束/需求上升通常产生上行压力。请按引用事实重算，不能仅改措辞")
            if re.search(r"恢复不足|未完全恢复|尚未恢复|not fully restored", block, re.I) and re.search(r"供应过剩|供给过剩|supply surplus|oversupply", block, re.I) and not _DISCLAIM_CAUSAL.search(block):
                raise ValueError(f"{path}:尚未恢复不能作为供应过剩的事实；应区分残余缺口与恢复带来的边际缓解")


_HYPOTHESIS = re.compile(r"如果|假如|假设|一旦|若|\bif\b", re.I)
_OFFSET = re.compile(r"(?:被)?(?:抵消|逆转|扭转|反转|对冲|缓解|减弱|削弱|抑制)|失效", re.I)


def _condition_mechanism_signs(text: str) -> set[str]:
    """Only explicit single-direction conditions; mixed/negated ones stay open."""
    restoration=re.compile(r'(?:实际)?(?:流量|供气|供应)[^，,。；;]{0,5}(?:尚未|仍未|未|没有|已经|已)(?:完全)?恢复')
    absent_loss=re.compile(r'(?:未|没有)(?:出现|发生|产生)(?:实际)?(?:流量|供气|供应)损失')
    if restoration.search(text) or absent_loss.search(text):
        # These supplied conditional phrases retain their own polarity. Do
        # not let a later price conclusion become a physical supply verb.
        signs=set()
        for physical in re.split(r'[，,]|→|->',text):
            if re.match(r'\s*(?:TTF|气价|价格)',physical,re.I):continue
            absent=list(absent_loss.finditer(physical))
            for row in _validation_mechanisms(physical):
                if any(row['span'][0]<m.end() and m.start()<row['span'][1] for m in absent):continue
                signs.add(row['pressure'])
            if re.search(r'(?:供应|供给)(?:约束|受限)(?:仍|继续)?(?:持续|存在)',physical):signs.add('up')
            if re.search(r'(?:供应|供给)(?:约束|受限)(?:已|已经)?(?:缓解|解除)',physical):signs.add('down')
        return signs
    if re.search(r"不及预期|未达预期|低于预期|不如预期|不足以|恢复不足|未完全恢复|尚未恢复|难以权衡|无法判断|方向不确定|已(?:被)?(?:充分|完全|部分)?(?:计价|计入|消化)|priced[ -]in", text, re.I):
        return set()
    signs = set()
    for row in _validation_mechanisms(text):
        start, end = row["span"]
        matched, after = text[start:end], text[end:end+12]
        # Ending a warm/cold regime or stopping a change reverses its usual
        # implication. This narrow gate must not guess the resulting net sign.
        if re.search(r"不再|停止|结束|消退|解除", matched):
            continue
        if row["mechanism"].startswith("demand_") and re.fullmatch(r"暖冬|寒潮|天气温和|温和天气|天气变暖|气温回升|天气转冷|气温下降|严寒|mild weather|warm winter|cold weather|cold snap", matched, re.I):
            if re.match(r"(?:将|逐渐|开始|明显)?(?:结束|消退|减弱|解除|消失|转冷)", after):
                continue
        signs.add(row["pressure"])
    return signs


def _condition_offset_parts(text: str) -> tuple[str, str] | None:
    for match in _OFFSET.finditer(text):
        before = text[:match.start()]
        local_before=re.split(r'[，,]|→|->|但是|但|因此',before)[-1]
        if (re.search(r"无法|不能|不足以|未必|不应|不(?:会|能)?$|没有$", before[-12:])
                or re.search(r'(?:未被|没有被|不会被|不被)[^，,。；;]{0,22}$',local_before)):
            continue
        after = text[match.end():]
        if re.search(r"缓解|减弱|削弱|抑制", match.group()):
            # A physical deficit being relieved is itself a supply mechanism,
            # not an operation on a price-pressure target after the arrow.
            if re.match(r"\s*(?:→|->|[，,。]|从而|导致|带来|因此)", after):
                continue
            physical = r"(?:供应|供给|供气|能源)?(?:缺口|短缺|瓶颈|约束|紧张|受限|中断)"
            if re.search(physical+r"(?:的)?$", before) or re.match(r"(?:了)?(?:当前|欧洲)?"+physical, after):
                continue
            target_near = before[-24:]+after[:32]
            if not re.search(r"(?:上行|下行|上涨|下跌|看涨|看跌|利多|利空).{0,6}(?:压力|风险|势头)|(?:价格|TTF|气价)", target_near, re.I):
                continue
        # Active form: demand rises -> offsets [downward supply pressure].
        if after.strip(" ，,:："):
            return before, after
        # Passive/ending form: if supply rises, [upward pressure] is reduced.
        pieces = re.split(r"[，,]|则|那么|将使|会使", before)
        if len(pieces) > 1:
            return "，".join(pieces[:-1]), pieces[-1]
    return None


def _check_directional_conditions(result: dict) -> None:
    """Reject reversed clear scenario mechanisms, without fixing any labels."""
    for path, statement in _strings(result):
        scenario_sign = "up" if ".scenarios.bullish" in path else "down" if ".scenarios.bearish" in path else None
        if not scenario_sign and not _HYPOTHESIS.search(statement):
            continue
        for sentence in _clauses(statement):
            if _DISCLAIM_CAUSAL.search(sentence):
                continue
            hypotheses = list(_HYPOTHESIS.finditer(sentence))
            condition = sentence[hypotheses[-1].end():] if hypotheses else sentence
            offset = _condition_offset_parts(condition)
            cause = offset[0] if offset else condition
            signs = _condition_mechanism_signs(cause)
            if scenario_sign and len(signs) == 1 and scenario_sign not in signs:
                raise ValueError(f"{path}:情景名称与明确单向供需机制相反；供应增加/需求下降/库存增加产生下行压力，供应减少/需求增加/库存下降产生上行压力。须重新分析条件，不得只改标签")
            if offset and len(signs) == 1:
                target = offset[1]
                targets = {sign for sign, pattern in (("up", _PRICE_UP), ("down", _PRICE_DOWN)) if pattern.search(target)}
                if not targets:
                    targets = _condition_mechanism_signs(target)
                if len(targets) == 1 and signs == targets:
                    raise ValueError(f"{path}:条件的供需压力与声称抵消/反转的目标同向，不能用同向利空抵消利空或同向利多抵消利多；须给出真正相反的条件或保留冲突/不确定性")


def _pricing_verification_request(before: str, after: str) -> bool:
    """A requested check of pricing status is not its observed conclusion."""
    requested=re.search(r'(?:须|需|需要|应当|应|待|有待)(?:进一步)?(?:核对|核实|查证|验证|检查|判断)(?P<object>[^。；;，,]{0,22})$',before)
    if not requested:
        return False
    if re.search(r'后(?:已|已经)?(?:确认|证实|证明|认定)|(?:已|已经)(?:确认|证实|证明|认定)|结果|表明|说明',requested['object']):
        return False
    return bool(re.match(r'[”"’\']?(?:的)?(?:情况|状态|与否|程度)',after))


def _pricing_assertion_prefix(clause: str, start: int) -> str:
    before=clause[max(0,start-48):start]
    local=re.split(r'[，,]|但是|但|然而|→|->',before)[-1]
    earlier=before[:-len(local)] if local else before
    # A check requested in an earlier subclause, or an earlier uncertainty,
    # cannot qualify a subsequent confirmed conclusion. Preserve ordinary
    # whole-sentence hypothetical premises for the separate consequence gate.
    prior_request=re.search(r'(?:须|需|需要|应当|应|待|有待)(?:进一步)?(?:核对|核实|查证|验证|检查|判断)',earlier)
    confirmed=re.search(r'(?:已|已经)(?:经)?(?:确认|证实|证明|认定)|(?:核对|核实|查证|验证)后(?:确认|证实|证明|认定)',local)
    return local if prior_request or confirmed else before


def _check_priced_in(result: dict, context: dict, prepared: dict) -> None:
    price = _known_price_reference(context, prepared)
    if price.get("priced_in_evidence") or price.get("announcement_price_reaction"):
        return
    invalid_paths: dict[str, set[str]] = {}
    for path, statement in _strings(result):
        for clause in _clauses(statement):
            for match in _PRICED_IN.finditer(clause):
                before = _pricing_assertion_prefix(clause,match.start())
                local_before=re.split(r'[，,]|但是|但|然而|→|->',before)[-1]
                after = clause[match.end():match.end()+16]
                missing_evidence = bool(re.search(r"(?:没有|缺少|缺乏|无|暂无|未提供)[^，,；;。]{0,10}$", before)
                                        and re.search(r"证据|依据|迹象", after))
                if _UNCONFIRMED_PREFIX.search(before) or missing_evidence or _pricing_verification_request(local_before,after) or re.match(r"[”\"']?(?:与否|程度|情况|是否).{0,6}(?:无法|未能|不能)确认", after):
                    continue
                invalid_paths.setdefault(path,set()).add("priced")
            for match in _NOT_PRICED_IN.finditer(clause):
                before=_pricing_assertion_prefix(clause,match.start())
                local_before=re.split(r'[，,]|但是|但|然而|→|->',before)[-1]
                after=clause[match.end():match.end()+24]
                if (after.startswith("冲击")
                        and not re.search(r"市场|投资者|交易者|交易员|market|investors?|traders?", local_before[-16:]+match.group(), re.I)):
                    # "未计入价格冲击" is the analyst's evidence accounting,
                    # not a claim about what market prices have absorbed.
                    # A separate source-bound gate checks unjustified removal
                    # of current capacity risk from that accounting.
                    continue
                if _UNCONFIRMED_PREFIX.search(before) or _pricing_verification_request(local_before,after) or re.search(r"不能(?:断言|认定|证明|确定|宣称)|不得(?:断言|认定|宣称)|无法(?:确定|判断)",before):
                    continue
                if re.search(r"(?:可能|或许|也许|不能排除)[^，,]{0,10}$|\b(?:may|might|could)\b[^,]{0,12}$",before,re.I):
                    continue
                if re.match(r"[”\"']?(?:与否|程度|情况|是否).{0,8}(?:无法|未能|不能|不确定|未知)",after):
                    continue
                if re.match(r"(?:的)?(?:风险|可能性)|\s+(?:risk|possibility)",after,re.I):
                    continue
                invalid_paths.setdefault(path,set()).add("not_priced")
    if invalid_paths:
        # A shared rule may fail in summaries *and* basis. List every affected
        # field before one concise explanation so bounded retry feedback does
        # not incorrectly imply that the copied basis was valid. Acceptance
        # conditions above remain identical; repeated clauses do not repeat IDs.
        kinds=set().union(*invalid_paths.values())
        claim="已计价/已消化" if kinds=={"priced"} else "未计入价格/尚未计价" if kinds=={"not_priced"} else "已计价/已消化或未计入价格/尚未计价"
        raise ValueError(f"{'、'.join(invalid_paths)}:缺少公告前后价格反应，不能宣称{claim}；须区分计价未知")


def _check_pricing_unknown_not_directional_evidence(result: dict) -> None:
    """Unknown absorption is a limit, not an extra signed price premise.

    A separate unknown note or an explicit concession can accompany a
    source-backed forecast. Only an explicit causal use of that unknown
    status for a directional conclusion is rejected; no label is assigned.
    """
    status=r'(?:是否)?(?:已经|已|尚未|还未|未)?(?:被(?:市场)?)?(?:充分|完全|部分)?(?:计价|计入(?:当前|市场)?价格|消化|反映(?:在)?价格)'
    unknown=re.compile(r'(?:(?:无|没有|暂无|缺少|缺乏|未提供)(?:独立|直接|可比|足够)?证据(?:表明|显示|证明|确认)[^。；;，,]{0,30}?|(?:无法|不能|未能|尚未)(?:确认|核实|判断|确定)[^。；;，,]{0,26}?)'+status+r'|'+status+r'(?:状态|情况|与否)?(?:仍)?(?:未知|不确定)',re.I)
    causal=re.compile(r'因此|因而|所以|故(?!障|事|意|乡)|由此|据此|从而|→|->')
    invalid=[]
    for path,text in _strings(result):
        if '.scenarios.' in path or 'historical_comparisons' in path:continue
        for sentence in re.split(r'[。！？\n]+',text):
            # A concession puts the unknown outside the later forecast's
            # evidential premise; do not borrow it across a contrast.
            for block in re.split(r'但是|然而|不过|但|\bbut\b|\bhowever\b',sentence,flags=re.I):
                for match in unknown.finditer(block):
                    before=re.split(r'[，,；;]',block[:match.start()])[-1]
                    if re.search(r'(?:不能|不应|不得|不可)(?:因(?:为)?|由于|仅凭|把|将|以)\s*$',before):continue
                    after=block[match.end():]
                    markers=list(causal.finditer(after))
                    if not markers:
                        # Explicitly presenting the unknown as a forecast
                        # premise is also causal even without "therefore".
                        if re.search(r'(?:基于|依据|因为|由于|且|并且|加之)\s*$',before):
                            markers=[re.match(r'[^。；;，,]*?[，,；;]?\s*(?=预测|预计|预判|判断|支持)',after)]
                            markers=[m for m in markers if m is not None]
                    for link in markers:
                        premise_tail=after[:link.start()]
                        consequence=after[link.end():]
                        if re.search(r'不能(?:据此|因此)?(?:预测|推断|认定|判断|断言|确定|确认)|不得|不应|无法(?:判断|确定|确认)|不能仅凭',consequence):continue
                        if re.search(r'并非|不是|不代表|不等于|不能|不应|不得',after[max(0,link.start()-8):link.start()]):continue
                        linked=(bool(re.search(r'(?:基于|依据|因为|由于|且|并且|加之)\s*$',before))
                            or not re.sub(r'[\s，,；;：:]|(?:这一点|这个状态|计价状态|计价情况|仍然)', '',premise_tail))
                        price_scope=path.startswith('close_forecast.') or bool(re.search(r'TTF|气价|价格|收盘|结算',consequence,re.I))
                        direction=bool(_PRICE_UP.search(consequence) or _PRICE_DOWN.search(consequence) or re.search(r'持平|平稳|横盘|broadly flat',consequence,re.I))
                        if linked and price_scope and direction:
                            if path not in invalid:invalid.append(path)
    if invalid:
        raise ValueError(f'{"、".join(invalid)}:计价未知或缺少计价证据不能作为额外看涨、看跌或持平依据；请将未知单列，方向只依据已给行情基准和来源供需机制。可保留有据的条件预测，不得由未知推未计价，不改变任何方向枚举')


def _check_unobserved_announcement_reaction(result: dict, context: dict, prepared: dict) -> None:
    reference=_known_price_reference(context,prepared)
    if reference.get('announcement_price_reaction') or reference.get('priced_in_evidence'):
        return
    event=r'(?:(?:供应|供给|供气|流量|产量)(?:增加|减少|恢复|改善|中断|下降|上升)|(?:该|此|本条)?(?:消息|公告|新闻))'
    claim=re.compile(event+r'[^。；;，,]{0,12}(?:(?:未见|未出现|没有|尚无)(?:即时|明显|显著)?(?:的)?(?:价格|市场)(?:反应|变化)|(?:未(?:能)?|没有|并未)(?:导致|引发|造成|带来|推动)(?:当前|当日|即时|明显|显著)?(?:价格|气价|TTF)(?:下跌|上涨|变化|波动)|未(?:曾|实际)?影响(?:当前|当日|现有)?(?:价格|气价|TTF价格))',re.I)
    cards={row.get('id'):row for row in prepared.get('window_news',[]) if isinstance(row,dict) and row.get('id')}
    cards['N0']=context.get('current_news') or prepared.get('current_news') or {}
    for alias,row in list(cards.items()):
        if row.get('same_as_current_news_id') in cards:
            cards[alias]=cards[row['same_as_current_news_id']]
    now=_moment(context.get('as_of') or prepared.get('as_of'))
    for path,statement in _strings(result):
        if '.scenarios.' in path or 'historical_comparisons' in path:
            continue
        for clause in _clauses(statement):
            for match in claim.finditer(clause):
                before=re.split(r'[，,]|→|->|但是|但|因此',clause[:match.start()])[-1]
                after=clause[match.end():match.end()+12]
                if re.match(r'^\s*(?:\[[NE]\d+\]\s*)?(?:若|如果|假设|假如|一旦)',clause) or re.search(r'若|如果|假设|假如|无法确认|未确认|不能确认|是否|不代表|不等于|不能证明|不得推断',before):
                    continue
                if re.match(r'(?:的)?(?:数据|证据|观测|记录|资料)',after):
                    continue
                # A source may itself report the observed reaction. Merely
                # retaining a flat reference quote does not establish it.
                supplied=False
                aliases=re.findall(r'(?<!\w)N\d+(?!\w)',clause)
                sources=[cards[alias] for alias in aliases if alias in cards] if aliases else [cards['N0']]
                for source in sources:
                    if not _known_source(source,now):
                        continue
                    for source_clause in _clauses(_source_text(source)):
                        observed=claim.search(source_clause)
                        if observed and not re.search(r'未提供|缺少|缺乏|是否|若|如果|无法确认|未确认|不能确认|不代表|不等于',source_clause[:observed.start()]):
                            supplied=True
                if supplied:
                    continue
                current=context.get('current_news') or prepared.get('current_news') or {}
                announcement=_moment(current.get('published_at') or current.get('time'))
                baseline=reference.get('market_baseline') or {}
                quote=reference.get('intraday') or {}
                observed_at=_moment(baseline.get('observed_at') or quote.get('observed_at'))
                timing='已给报价的observed_at不晚于新闻发布时间，available_at只是获知/抓取时刻，不能当作公告后报价；' if announcement and observed_at and observed_at<=announcement else ''
                raise ValueError(f'{path}:{timing}未提供该消息前后独立价格反应，不能宣称供应增加/该消息未导致价格下跌或无即时反应；可描述已观察基准持平、反应未知及有依据的条件性收盘预判，不强制复制新闻方向')


def _check_conditional_pricing_consequence(result: dict, context: dict, prepared: dict) -> None:
    reference=_known_price_reference(context,prepared)
    if reference.get('announcement_price_reaction') or reference.get('priced_in_evidence'):
        return
    condition=re.compile(r'(?:若|如果|一旦|当)[^。；;]{0,100}(?:报价|价格|气价|TTF)[^。；;]{0,45}(?:上涨|下跌|上升|下降|走高|走低|不变|持平|无反应|↑|↓)',re.I)
    for path,text in _strings(result):
        for clause in _clauses(text):
            premise=condition.search(clause)
            if not premise:
                continue
            for consequence in re.finditer(r'表明|说明|证明|证实|意味着|显示',clause[premise.end():]):
                start=premise.end()+consequence.start()
                prefix=clause[max(premise.end(),start-12):start]
                if re.search(r'(?:不能|不可|无法|未必|并不|不)(?:直接|据此|就)?\s*$',prefix):
                    continue
                tail=clause[premise.end()+consequence.end():]
                priced=_PRICED_IN.search(tail) or _NOT_PRICED_IN.search(tail)
                if priced and not _UNCONFIRMED_PREFIX.search(tail[:priced.start()]):
                    raise ValueError(f'{path}:假设公告后价格单向涨跌不能说明/证明已计价或未计价，或存在其他因素也不是计价证据；须有该消息前后独立可比反应。可把价格变化列为重新检查供需与预测的条件，保留若已计价的明确假设，不得由价变推出计价事实')


def _current_source_rows(context: dict, prepared: dict) -> list[dict]:
    rows = [context.get("current_news") or {}]
    rows.extend(row for row in context.get("window_news", []) if isinstance(row, dict))
    # This also supports callers which retain only the prepared evidence ledger.
    ledger = prepared.get("evidence_ledger") or []
    if isinstance(ledger, dict):
        ledger = ledger.get("items", ledger.get("news", []))
    if isinstance(ledger, list):
        rows.extend(row for row in ledger if isinstance(row, dict))
    if isinstance(prepared.get("current_news"), dict):
        rows.append(prepared["current_news"])
    rows.extend(row for row in prepared.get("window_news", []) if isinstance(row, dict))
    as_of = _moment(context.get("as_of") or prepared.get("as_of"))
    return [row for row in rows if _known_source(row, as_of)]


def _future_notice_not_yet_physical_claim(clause: str, match: re.Match, context: dict, prepared: dict) -> bool:
    """A known future event has not started; this says nothing about total flow."""
    # The event is the subject of a negated causal assertion. A source whose
    # actual start is still future cannot yet have caused a physical loss;
    # this is distinct from claiming that the whole network has zero losses.
    causal=re.compile(r'(?:尚未|未|没有|并未)(?:曾)?(?:造成|导致|形成)(?:(?:当前|当日|即时|实际|物理|的)){0,4}(?:天然气)?(?:供气|供应|供给|流量)(?:损失|减少|中断|冲击)')
    scoped_match=next((m for m in causal.finditer(clause) if m.start()<match.end() and m.end()>match.start()),None)
    if not scoped_match:
        return False
    full_before=clause[:scoped_match.start()]
    before=re.split(r'[，,]|→|->|但是|但',full_before)[-1]
    if re.search(r'另一|其他|别的',before):
        return False  # A new event subject cannot borrow an earlier notice's clock.
    subject=re.search(r'(?:公告|维护|检修|事件|计划)(?:目前|当前|至今|迄今|仍)?$',before)
    inherited=(before==full_before or re.fullmatch(r'(?:\s|截至(?:当前|目前|分析时点)|目前|当前|至今|迄今|仍|因此|故)*',before))
    if not subject and inherited and re.search(r'公告|维护|检修|事件|计划',full_before):
        before=full_before
        subject=True  # The named event remains the causal subject across a comma;
                      # actual source start>as_of is required below, not assumed.
    if (not subject or re.search(r'整体|总体|全部|全网|全市场|欧洲|欧盟|实际流量|实测|计量',before)):
        return False
    cards={row.get('id'):row for row in prepared.get('window_news',[]) if isinstance(row,dict) and row.get('id')}
    cards['N0']={**(prepared.get('current_news') or {}),**(context.get('current_news') or {})}
    for alias,row in list(cards.items()):
        if row.get('same_as_current_news_id') in cards:
            cards[alias]=cards[row['same_as_current_news_id']]
    aliases=re.findall(r'(?<![A-Za-z0-9_])N\d+(?![A-Za-z0-9_])',clause)
    if aliases:
        scoped=[cards[alias] for alias in aliases if alias in cards]
    else:
        scoped=[row for row in cards.values() if (m:=re.search(r'\bGassco\s+([^:：]{1,45})[:：]',str(row.get('title','')),re.I)) and m[1].strip().casefold() in before.casefold()]
        if not scoped and (re.search(r'(?:该|本次|这些|上述|两条|两项|未来).{0,8}(?:公告|维护|检修|事件|计划)',before) or re.search(r'(?:计划|维护|检修)公告(?:提示|涉及|为|属于)未来',before)):
            scoped=[row for row in cards.values() if _notice_item(row)]
    now=_moment(context.get('as_of') or prepared.get('as_of'))
    if not scoped or not now:
        return False
    for source in scoped:
        op=source.get('operational') or {}
        start=_moment(source.get('event_start') or op.get('event_start_utc'))
        if not _notice_item(source) or not _known_source(source,now) or not start or not start.tzinfo or not now.tzinfo or start<=now:
            return False
    return True


def _check_unknown_as_fact(result: dict, context: dict, prepared: dict) -> None:
    sources = "\n".join(_source_text(row) for row in _current_source_rows(context, prepared))
    # No historical weather/storage reference establishes today's state.
    support = {
        "天气": re.compile(r"天气温和|温和天气|暖冬|天气稳定|天气无变化|天气没有变化|mild weather|warm winter|weather (?:is |remains )?(?:stable|unchanged)", re.I),
        "库存": re.compile(r"(?:未伴随|没有伴随)[^。；;，,]{0,14}库存(?:变化|变动)|库存稳定|库存持平|库存未变|库存无变化|库存没有变化|(?:storage|inventor(?:y|ies))[^.;]{0,15}(?:stable|unchanged)", re.I),
        "LNG": re.compile(r"LNG[^。；;]{0,12}(?:到港|流入|供应|进口)[^。；;]{0,10}(?:稳定|持平|未变|没有变化)|LNG[^.;]{0,25}(?:stable|unchanged)", re.I),
        "实际流量": re.compile(r"(?:实际|计量)[^。；;]{0,16}(?:流量|供气|供应)[^。；;]{0,18}(?:未受影响|没有损失|未减少|保持不变|稳定)|(?:actual|metered) flows?[^.;]{0,25}(?:unaffected|unchanged|no loss)", re.I),
    }
    assertions = {
        "天气": support["天气"],
        "库存": re.compile(support["库存"].pattern+r"|(?:未伴随|没有伴随)[^。；;，,]{0,14}库存(?:变化|变动)",re.I),
        "LNG": support["LNG"],
        "实际流量": re.compile(r"(?:未(?!来|计入|计作|计为|归为|纳入)|没有)(?:形成|导致|造成|出现)?[^。；;，,]{0,8}(?:实际)?(?:流量|供气|供应)(?:损失|减少|下降|缺口)|实际流量[^。；;，,]{0,6}(?:未受影响|没有损失|保持不变)|actual flows?[^.;,]{0,15}(?:unaffected|unchanged)", re.I),
    }
    for path, statement in _strings(result):
        for clause in _clauses(statement):
            for topic, pattern in assertions.items():
                matches=pattern.finditer(clause) if topic == "实际流量" else [pattern.search(clause)]
                for match in matches:
                    if not match:
                        continue
                    if re.match(r"(?:尚)?未(?:见|确认|核实|提供|观察到)", match.group()):
                        continue  # Missing observations are epistemic, not no-loss facts.
                    physical_condition=_invalidation_event_condition(path,clause,match) if topic == "实际流量" else None
                    if physical_condition is True:
                        continue  # A local physical failure criterion is hypothetical.
                    if topic == "实际流量" and _future_notice_not_yet_physical_claim(clause,match,context,prepared):
                        continue  # This future event has not physically started.
                    before = clause[max(0, match.start()-24):match.start()]
                    if topic == "实际流量":
                        full_before=clause[:match.start()]
                        before=re.split(r'[，,]|但是|但|然而|因此|\bbut\b|\bhowever\b',before,flags=re.I)[-1]
                        # A conditional's consequent may follow a comma. A
                        # contrast starts a new assertion and cannot inherit it.
                        if (re.match(r'^\s*(?:\[[NE]\d+\]\s*)?(?:若|如果|假如|假设|一旦|if\b)',clause,re.I)
                                and not re.search(r'但是|但|然而|不过|\bbut\b|\bhowever\b',full_before,re.I)):
                            continue
                    if physical_condition is not False and re.search(r"若|如果|假如|假设|当|一旦|不能|不得|无法|未确认|未核实|是否|不代表|不等于|尚不清楚|\bif\b|whether", before, re.I):
                        continue
                    supported = False
                    for source_clause in _clauses(sources):
                        for source_match in support[topic].finditer(source_clause):
                            source_before = source_clause[max(0, source_match.start()-16):source_match.start()]
                            source_after = source_clause[source_match.end():source_match.end()+15]
                            if not re.search(r"未提供|缺少|缺乏|没有[^，,]{0,6}证据|暂无|不知|未知|无法确认|是否|未确认|not confirmed|no evidence", source_before, re.I) and not re.search(r"与否[^，,]{0,6}(?:未知|不知|无法确认)", source_after):
                                supported = True
                    if not supported:
                        raise ValueError(f"{path}:输入未提供可验证的{topic}状态，不能把缺失资料写成温和/稳定/无损失的事实；请明确缺失或使用条件情景")
            if re.search(r"(?:本窗口|欧洲|当前)(?:内)?(?:没有|无|未出现).{0,7}(?:供需冲击|供应冲击|供给冲击|供应中断|需求变化)", clause) and not _INFORMATION_SCOPE.search(clause):
                if not re.search(r"(?:无|没有|未出现).{0,7}(?:供需冲击|供应冲击|供给冲击|供应中断|需求变化)", sources):
                    raise ValueError(f"{path}:新闻覆盖不足不能证明市场没有供需变化；只能说明输入新闻未显示相应冲击")


def _check_missing_is_not_zero_impact(result: dict) -> None:
    unknown = re.compile(r"未确认|未核实|未见实际|未提供[^。；;]{0,12}(?:流量|计量|供应)|缺[少乏][^。；;]{0,12}(?:流量|计量|供应)|no[^.;]{0,15}(?:measurement|flow data)", re.I)
    zero = re.compile(r"供应(?:无|没有)(?:任何|实际|有效|显著)?冲击|(?:没有|无)(?:任何|有效|显著)?(?:供应|供给|供需)冲击|零影响|完全无影响|不存在供需变化|no supply impact|zero impact", re.I)
    for path, statement in _strings(result):
        for clause in _clauses(statement):
            match = zero.search(clause)
            if match and unknown.search(clause) and _CAUSAL.search(clause) and not re.search(r"不能说明|不能证明|不代表|不等于|不得推断", clause[:match.start()]):
                raise ValueError(f"{path}:未确认实际流量损失只是缺测，不代表供应无冲击；Dismissed仅撤销公告。请引用撤销的容量风险及未核实状态分析，不能把未知当零影响或默认方向")
            # A direct missing-flow -> neutral inference erases the conditional
            # capacity risk just as surely as a direct zero-impact assertion.
            # Keep unknown data/low confidence separate from genuine offsets.
            causal_neutral=re.search(r"(?:→|->|因此|因而|所以|从而|意味着|等于)[^。；;，,]{0,10}(?:TTF)?(?:价格|新闻)?(?:影响|压力)?(?:为|是|呈)?(?:中性(?:机制|影响|压力)?|零影响|无影响)",clause,re.I)
            if causal_neutral:
                cause=clause[:causal_neutral.start()]
                missing=unknown.search(cause)
                if missing and not re.search(r"不能说明|不能证明|不代表|不等于|不得推断|不能(?:直接)?(?:判|视|认定)|无法(?:直接)?(?:判|视|认定)",cause[missing.end():]):
                    raise ValueError(f"{path}:未确认实际流量损失只是缺测，不能作为TTF中性机制或抵消容量风险的证据；可说明流量未知、风险条件和把握程度，不能把未知当零影响或默认方向")


def _check_supply_demand_proportions(result: dict, context: dict, prepared: dict) -> None:
    ratios = [re.compile(r"占(?:到|了)?(?:(?:欧洲|欧盟|全球|当前|当日|天然气|总|TTF)\s*)*(?P<topic>供给|供应|需求|产量)(?:总量|量)?(?:的|约|大约|近|\s)*(?P<ratio>\d+(?:\.\d+)?)\s*[%％]",re.I),
              re.compile(r"(?:account\w*\s+for|represent\w*)\s+(?:about\s+|approximately\s+)?(?P<ratio>\d+(?:\.\d+)?)\s*%\s+of\s+(?:(?:the|total|European|EU|gas)\s+)*(?P<topic>supply|demand|production)",re.I)]
    sources = _current_source_rows(context,prepared)
    def topic(match):
        return 'demand' if match['topic'].lower() in {'需求','demand'} else 'supply'
    for path,text in _strings(result):
        for clause in _clauses(text):
            for pattern in ratios:
                for match in pattern.finditer(clause):
                    if re.search(r'若|如果|假设|假定|不能认定|不能证明|不代表|\bif\b|assuming',clause[:match.start()],re.I):
                        continue
                    value = float(match['ratio'])
                    supported = False
                    for row in sources:
                        for source_clause in _clauses(_source_text(row)):
                            for source_pattern in ratios:
                                for observed in source_pattern.finditer(source_clause):
                                    prefix = source_clause[max(0,observed.start()-35):observed.start()]
                                    if topic(observed)==topic(match) and abs(float(observed['ratio'])-value)<.001 and not re.search(r'若|如果|未提供|未确认|预计|未来|假设|\bif\b|expected|unconfirmed',prefix,re.I):
                                        supported=True
                        # Arithmetic is supported only by explicit, like-unit
                        # observed numerator and total fields, not a quote %.
                        denominators = (('total_demand_mcm_d','european_total_demand_mcm_d') if topic(match)=='demand' else ('total_supply_mcm_d','european_total_supply_mcm_d'))
                        numerators = (('actual_demand_change_mcm_d','actual_demand_loss_mcm_d') if topic(match)=='demand' else ('actual_flow_loss_mcm_d','actual_flow_reduction_mcm_d','actual_supply_change_mcm_d'))
                        for denominator in denominators:
                            total=row.get(denominator)
                            if not isinstance(total,(int,float)) or isinstance(total,bool) or total<=0:
                                continue
                            for numerator in numerators:
                                part=row.get(numerator)
                                if isinstance(part,(int,float)) and not isinstance(part,bool) and abs(100*abs(part)/total-value)<=.05:
                                    supported=True
                    if not supported:
                        raise ValueError(f'{path}:输入未提供这条供需事实的总供给/总需求占比{value}%或可核对的同单位分母；不能编造占比评估冲击强弱，行情涨跌百分比不是供需占比证据。请使用已给容量原值、时点和核实状态分析')


def _check_weather_demand(result: dict, context: dict, prepared: dict) -> None:
    source = "\n".join(_source_text(row) for row in _current_source_rows(context, prepared))
    wrong = re.compile(r"(?:天气温和|温和天气|暖冬|天气变热|天气变暖|气温回升|mild weather|warm weather|warm winter)[^。；;]{0,18}(?:→|->|导致|造成|使得|带来|增加|increas\w*|caus\w*|rais\w*)[^。；;]{0,18}(?:(?:采暖|取暖|天然气|燃气|用气|欧洲)?需求(?:增加|上升|增强|增长)|(?:heating |gas )?demand (?:ris\w*|increas\w*|higher))", re.I)
    for path, statement in _strings(result):
        for clause in _clauses(statement):
            if wrong.search(clause) and not _DISCLAIM_CAUSAL.search(clause):
                if not _COOLING.search(source) or re.search(r"采暖|取暖|heating", clause, re.I):
                    raise ValueError(f"{path}:温和天气通常降低采暖需求；没有输入中的制冷/发电证据，不能把温和天气写成需求上升")


def _physical_quantity_arrow_mechanisms(text: str) -> list[dict]:
    """Arrows qualify the adjacent quantity, not every TTF word nearby."""
    result=[]
    patterns=((r'(?:供应|供给|供气|流量|产量)(?:量|预期)?\s*[:：]?\s*(?P<arrow>[↑↓])',('supply_increase','down'),('supply_decrease','up')),
              (r'(?:采暖|取暖|天然气|燃气|用气|欧洲)?需求(?:量|预期)?\s*[:：]?\s*(?P<arrow>[↑↓])',('demand_increase','up'),('demand_decrease','down')),
              (r'(?:库存|储气)(?:量)?\s*[:：]?\s*(?P<arrow>[↑↓])',('inventory_build','down'),('inventory_draw','up')))
    for pattern,increase,decrease in patterns:
        for match in re.finditer(pattern,text,re.I):
            if not _negated_match(text,match):
                mechanism,pressure=increase if match['arrow']=='↑' else decrease
                result.append({'mechanism':mechanism,'pressure':pressure})
    return result


def _physical_quantity_arrow_signs(text: str) -> set[str]:
    return {row['pressure'] for row in _physical_quantity_arrow_mechanisms(text)}


def _grounded_expectation_reversal(text: str, sources: list[dict], target: str) -> bool:
    gap=r'低于(?:此前|原先)?预期|不及(?:此前|原先)?预期|未达(?:此前|原先)?预期|少于(?:此前|原先)?预期|预期[^。；;，,]{0,5}(?:下修|下调|上修|上调)|below (?:prior )?expectations'
    if not re.search(gap,text,re.I):
        return False
    if target=='up':
        supported=r'(?:发现(?:量|规模)?|资源(?:量|规模)?|储量|供应(?:量|规模)?|供给(?:量|规模)?)[^。；;，,]{0,20}(?:低于(?:此前|原先)?预期|不及(?:此前|原先)?预期|未达(?:此前|原先)?预期|below (?:prior )?expectations)|(?:供应|供给)预期[^。；;，,]{0,8}(?:下修|下调|下降)'
    else:
        supported=r'(?:损失|减供|停供|中断|缺口|停产)(?:量|规模|时长)?[^。；;，,]{0,20}(?:低于(?:此前|原先)?预期|不及(?:此前|原先)?预期|少于(?:此前|原先)?预期)|(?:供应|供给)预期[^。；;，,]{0,8}(?:上修|上调|上升)'
    for source in sources:
        for clause in _clauses(_source_text(source)):
            match=re.search(supported,clause,re.I)
            if match and not _missing_information_prefix(clause[:match.start()]) and not _UNCONFIRMED_PREFIX.search(clause[:match.start()]) and not re.search(r'未提供|缺少|缺乏|待核实|待确认|未核实|未确认|无法确认|是否|若|如果|未知|(?:没有|并非|并未|并不|不曾|未曾|未)(?:低于|不及|少于|下修|下调|上修|上调)',match.group()+clause[match.end():match.end()+20]):
                return True
    return False


def _check_quantity_arrow_scope(result: dict, context: dict, prepared: dict) -> None:
    cards={row.get('id'):row for row in prepared.get('window_news',[]) if isinstance(row,dict) and row.get('id')}
    cards['N0']=prepared.get('current_news') or context.get('current_news') or {}
    for alias,row in list(cards.items()):
        if row.get('same_as_current_news_id') in cards:cards[alias]=cards[row['same_as_current_news_id']]
    now=_moment(context.get('as_of') or prepared.get('as_of'))
    prospective=[row for row in cards.values() if _known_source(row,now) and prospective_supply_only(row)]
    for path,text in _strings(result):
        if '.scenarios.' in path:
            continue
        aliases=re.findall(r'(?<!\w)N\d+(?!\w)',text)
        sources=[cards[alias] for alias in aliases if alias in cards] if aliases else list(cards.values())
        sources=[row for row in sources if _known_source(row,now)]
        expected='up' if path.startswith('news_impact.bullish_evidence[') else 'down' if path.startswith('news_impact.bearish_evidence[') else None
        quantity=_physical_quantity_arrow_signs(text)
        signs=quantity|{row['pressure'] for row in _validation_mechanisms(text)}
        if expected and quantity and len(signs)==1 and expected not in signs and not _DISCLAIM_CAUSAL.search(text) and not _grounded_expectation_reversal(text,sources,expected):
            raise ValueError(f'{path}:物理量箭头不是TTF价格箭头；供给↑/库存↑对应下行压力，供给↓/需求↑对应上行压力。潜在供给增加也不能因资源发现而当看涨依据；须按来源机制分栏，净方向仍可权衡')
        prices=_price_signs(text)
        for match in re.finditer(r'(?:TTF(?:价格|气价)?|价格|气价)\s*(?:预期)?\s*([↑↓])',text,re.I):
            if not _negated_match(text,match):prices.add('up' if match[1]=='↑' else 'down')
        if quantity and len(signs)==1 and len(prices)==1 and signs!=prices and _CAUSAL.search(text) and not _DISCLAIM_CAUSAL.search(text) and not _grounded_expectation_reversal(text,sources,next(iter(prices))):
            raise ValueError(f'{path}:物理供给/需求/库存箭头与TTF价格传导相反；请区分供给↑的下行压力与价格↑，有来源的反向预期差或供需冲突可另行解释')
        resource_clause=next((clause for clause in _clauses(text) if re.search(r'(?:资源|油气|天然气|气田)发现[^。；;]{0,20}(?:看涨|利多|推高(?:TTF|气价|价格))',clause,re.I)),None)
        direct_resource_up=resource_clause is not None
        resource_sources=[row for row in sources if prospective_supply_only(row)]
        if (expected=='up' and resource_sources or direct_resource_up and prospective) and not _DISCLAIM_CAUSAL.search(text):
            resource_text=resource_clause or text
            claimed_up={row['mechanism'] for row in [*_validation_mechanisms(resource_text),*_physical_quantity_arrow_mechanisms(resource_text)] if row['pressure']=='up'}
            supplied_up={signal['mechanism'] for row in sources for signal in [*extract_mechanisms(row),*_physical_quantity_arrow_mechanisms(_source_text(row))] if signal['pressure']=='up'}
            capacity_offset=bool(re.search(r'供应约束|供给约束|容量受限|不可用容量',resource_text) and 'capacity_constraint' in supplied_up)
            if not (claimed_up&supplied_up or capacity_offset or _grounded_expectation_reversal(resource_text,sources,'up')):
                raise ValueError(f'{path}:资源发现仅支持潜在未来供给，不能因发现是公司利好就当作TTF看涨因素；潜在增供通常是预期下行机制。看涨须有来源的需求/供应反向事实或低于既有供给预期的证据，不强制净标签')



_SUPPLY_RISK_CHANGE = re.compile(
    r"(?P<risk>(?:供应|供给|供气)(?:(?:中断|短缺|受限|停供|过剩))?风险)"
    r"(?P<qualifier>(?:(?:已经|已|被|有所|明显|进一步|持续|暂时|未|没有|尚未|不会|无法|不能|不|确认|证实|核实|是否|预计|预期|可能|或许|将|尚|待|仍|并|的)){0,5})"
    r"(?P<change>降低|下降|减少|减弱|缓解|消退|减轻|移除|撤销|解除|消除|上升|增加|提高|加剧|增大)", re.I)

def _validation_mechanisms(clause: str) -> list[dict]:
    """Result-only risk semantics; source extraction/input stays unchanged.

    Less supply-shortage risk is marginal relief, not a measured supply cut.
    Scope a risk qualifier to its own match so a nearby actual-flow fact is
    neither removed nor relabelled.  No conclusion/direction is rewritten.
    """
    risk=[]
    scopes=[]
    for match in _SUPPLY_RISK_CHANGE.finditer(clause):
        scopes.append(match.span())
        if _negated_match(clause,match) or re.search(r'未|没有|不|无法|不能|是否|待',match['qualifier']):
            continue
        # Merely mentioning a former quotation is not a present risk change.
        # An explicit mechanism following that quotation is still checked.
        local_before=re.split(r'[，,]|→|->|但是|但|因此|TTF机制[:：]',clause[:match.start()])[-1]
        after=clause[match.end():match.end()+16]
        if re.search(r'历史|当时|曾经|此前|[\[（]E\d+',local_before):
            continue
        if re.search(r'未确认|未核实|无法确认|不能确认|尚不清楚|是否',local_before) or re.match(r'(?:是否|仍)?(?:未知|待核实|待确认|未确认|未核实|无法确认)',after):
            continue
        increasing=match['change'] in {'上升','增加','提高','加剧','增大'}
        surplus='过剩' in match['risk']
        pressure=('up' if increasing else 'down') if not surplus else ('down' if increasing else 'up')
        risk.append({'mechanism':'supply_risk_increase' if increasing else 'supply_risk_relief',
                     'pressure':pressure,'span':match.span(),'evidence_kind':'risk_only'})
    generic=[row for row in _matching_mechanisms(clause)
             if not any(row['span'][0] < span[1] and span[0] < row['span'][1]
                        for span in scopes)]
    return generic+risk

def _check_supply_risk_buckets(result: dict,context: dict,prepared: dict) -> None:
    """A cited marginal risk change must not be put in the opposite slot.

    This checks the explicit TTF-mechanism portion, preserving historical
    quotations, actually sourced opposing mechanisms, and the freedom to
    choose a net flat forecast. A cancellation is never an actual flow fact.
    """
    cards={row.get('id'):row for row in prepared.get('window_news',[]) if isinstance(row,dict) and row.get('id')}
    cards['N0']=prepared.get('current_news') or context.get('current_news') or {}
    for alias,row in list(cards.items()):
        if row.get('same_as_current_news_id') in cards:cards[alias]=cards[row['same_as_current_news_id']]
    now=_moment(context.get('as_of') or prepared.get('as_of'))
    for field,expected in (('bullish_evidence','up'),('bearish_evidence','down')):
        for index,value in enumerate((result.get('news_impact') or {}).get(field) or []):
            text=str(value)
            aliases=re.findall(r'(?<![A-Za-z0-9_])N\d+(?![A-Za-z0-9_])',text)
            sources=[cards[a] for a in aliases if a in cards] if aliases else [cards['N0']]
            sources=[row for row in sources if _known_source(row,now)]
            if not sources:continue
            parts=re.split(r'TTF(?:价格)?机制[:：]|→|->',text,maxsplit=1,flags=re.I)
            mechanism=parts[-1]
            if len(parts)==1 and re.search(r'历史|当时|曾经|此前|[\[（]E\d+',mechanism):continue
            for clause in _clauses(mechanism):
                if _DISCLAIM_CAUSAL.search(clause):continue
                signals=_validation_mechanisms(clause)
                risk=[row for row in signals if row.get('evidence_kind')=='risk_only']
                if not risk:continue
                if all(row['pressure']==expected for row in risk):continue
                # Opposite-pressure risk cannot be rescued by a generated
                # extra demand/cut claim. The cited known source must supply
                # the corresponding opposing mechanism, not just its ID.
                counter=[row for row in signals if row['pressure']==expected]
                supported=False
                for source in sources:
                    for source_clause in _clauses(_source_text(source)):
                        if re.search(r'历史|当时|曾经|此前',source_clause) and not re.search(r'当前|目前|现在|本次|本窗口',source_clause):continue
                        supplied=_validation_mechanisms(source_clause)
                        for claim in counter:
                            if claim['mechanism'].startswith('supply_') and _affirmed_actual_flow(clause) and not _affirmed_actual_flow(source_clause):continue
                            for given in supplied:
                                if given['mechanism']!=claim['mechanism'] or given['pressure']!=expected:continue
                                claim_local=re.split(r'[，,]|但是|但|因此',clause[:claim['span'][0]])[-1]+clause[claim['span'][0]:claim['span'][1]]
                                given_local=re.split(r'[，,]|但是|但|因此',source_clause[:given['span'][0]])[-1]+source_clause[given['span'][0]:given['span'][1]]
                                if (re.search(r'实际|实测|计量|已确认|已经|\bactual\b|\bmeasured\b|\bconfirmed\b',claim_local,re.I)
                                        and (_CONDITIONAL.search(given_local) or re.search(r'未来|明日|明天|将于|将(?:增加|上升|减少|下降)|\bfuture\b|\bwill\b',given_local,re.I))):continue
                                supported=True
                if supported:continue
                # Source-backed reverse expectation differences retain their
                # established exception, not an unsupported 'priced-in' guess.
                if _grounded_expectation_reversal(clause,sources,expected):continue
                raise ValueError(f'news_impact.{field}[{index}]:供应风险降低是TTF边际下行/中和机制，供应风险上升是边际上行机制；风险变化不等于实际流量恢复或损失，不能反放证据栏。须按引用事实重算/分栏，净方向仍可flat，不补实际流量。')

def _check_evidence_buckets(result: dict) -> None:
    news = result.get("news_impact") or {}
    for field, expected in (("bullish_evidence", "up"), ("bearish_evidence", "down")):
        for index, statement in enumerate(news.get(field) or []):
            for clause in _clauses(str(statement)):
                signs = {r["pressure"] for r in _validation_mechanisms(clause)}
                if len(signs) == 1 and expected not in signs and not _DISCLAIM_CAUSAL.search(clause):
                    raise ValueError(f"news_impact.{field}[{index}]:证据栏与供需机制相反；请移动/重算证据，不能把供应增加放作看涨或供应减少放作看跌")
    for path, statement in _strings({"news_impact": news, "analysis": result.get("analysis") or {}}):
        if not re.search(r"evidence\[", path):
            continue
        plain = re.sub(r"\[?(?:N|E|P)\d+(?:\.\d+)?\]?[:：]?", "", statement).strip()
        if re.match(r"(?:输入事实|事实|输入消息|按输入事实|input facts?)\s*(?:→|->|:|：)", plain, re.I) or re.search(r"\b[A-Za-z]{1,5}\.\.\.\s*(?:→|->)", plain):
            raise ValueError(f"{path}:证据照抄占位事实，必须写设施/规模/状态等本次实际事实及其机制")



def _evidence_flow_change_mechanisms(text: str) -> list[dict]:
    """Result-only flow synonyms omitted by the shared source vocabulary.

    A direct pipeline/regasification change is a physical supply channel;
    merely opening a corporate project is not.  Never relabel source cards.
    """
    result=[]
    pattern=r'(?P<object>(?:管道)?输气(?:流量|量)?|(?:LNG)?再气化(?:流量|量)?)(?:实际|计量|预计|预期|可能|将)?(?P<change>增加|增长|上升|恢复|减少|下降|降低|缩减)'
    for match in re.finditer(pattern,text,re.I):
        if not _negated_match(text,match):
            increase=match['change'] in {'增加','增长','上升','恢复'}
            result.append({'mechanism':'supply_increase' if increase else 'supply_decrease',
                           'pressure':'down' if increase else 'up','span':match.span()})
    return result


def _check_source_bound_evidence_slot_prices(result: dict, context: dict, prepared: dict) -> None:
    """TTF price signs and factual channels belong to their evidence slot.

    A source's corporate good news is not itself a gas-price mechanism.
    This is a result-only invariant; it neither adds source directions nor
    assigns a net news/close label or treats capacity as measured flow.
    """
    cards={row.get('id'):row for row in prepared.get('window_news',[]) if isinstance(row,dict) and row.get('id')}
    cards['N0']=prepared.get('current_news') or context.get('current_news') or {}
    for alias,row in list(cards.items()):
        if row.get('same_as_current_news_id') in cards:cards[alias]=cards[row['same_as_current_news_id']]
    now=_moment(context.get('as_of') or prepared.get('as_of'));errors=[]
    for field,expected in (('bullish_evidence','up'),('bearish_evidence','down')):
        for index,value in enumerate((result.get('news_impact') or {}).get(field) or []):
            text=str(value)
            aliases=re.findall(r'(?<![A-Za-z0-9_])N\d+(?![A-Za-z0-9_])',text)
            sources=[cards[a] for a in aliases if a in cards] if aliases else [cards['N0']]
            sources=[row for row in sources if _known_source(row,now)]
            if not sources:continue
            parts=re.split(r'TTF(?:价格)?机制[:：]|→|->',text,maxsplit=1,flags=re.I)
            mechanism=parts[-1]
            historical=bool(re.search(r'历史(?:背景|比较|价格)|当时|曾经',mechanism)
                and not re.search(r'当前|目前|本次|本窗口|现在|current|now',mechanism,re.I))
            if historical or _DISCLAIM_CAUSAL.search(mechanism):continue
            price_signs=_price_signs(mechanism)
            path=f'news_impact.{field}[{index}]'
            if len(price_signs)==1 and expected not in price_signs:
                errors.append(f'{path}:明写的TTF价格方向与证据栏相反，价格上行属看涨栏、价格下行属看跌栏；须按该N来源分栏/解释，不改变净方向')
                continue
            # A pure endpoint template is hollow only when the evidence has
            # supplied no physical/risk/expectation channel before it either.
            endpoint=bool(len(parts)>1 and re.fullmatch(r'\s*(?:TTF)?(?:价格)?(?:看涨|看跌|上行|下行|上涨|下跌)(?:机制|压力)?[。.]?\s*',mechanism,re.I))
            flow_channels=_evidence_flow_change_mechanisms(text)
            channels=[*_validation_mechanisms(text),*_physical_quantity_arrow_mechanisms(text),*flow_channels]
            channel_signs={row['pressure'] for row in channels}
            if (flow_channels and len(channel_signs)==1 and expected not in channel_signs
                    and not _grounded_expectation_reversal(text,sources,expected)):
                errors.append(f'{path}:输气/再气化量变的供给传导与证据栏相反；减量是上行压力、增量是下行压力。须按来源机制分栏，净方向仍可flat')
                continue
            specific_channel=re.search(r'(?:供应|供给|供气)(?:约束|短缺|缺口|扩张|增量|预期)|(?:不可用|受限)容量|容量(?:受限|约束|风险)|采暖|取暖|储气|supply\s+(?:constraint|shortage|expectation)|capacity\s+(?:constraint|risk)',text,re.I)
            if endpoint and not channels and not specific_channel and not _grounded_expectation_reversal(text,sources,expected):
                errors.append(f'{path}:项目开启或公司合同事实不能只接TTF涨跌模板；须说明该N有来源的供需/容量风险/预期差传导，不能把公司利好直接当气价利好；净方向仍可flat')
    if errors:raise ValueError('；'.join(errors))

def _check_neutral_capacity_risk_bucket(result: dict, context: dict, prepared: dict) -> None:
    """Conditional current capacity risk is not itself counterevidence."""
    cards={row.get('id'):row for row in prepared.get('window_news',[]) if isinstance(row,dict) and row.get('id')}
    cards['N0']=prepared.get('current_news') or context.get('current_news') or {}
    for alias,row in list(cards.items()):
        target=row.get('same_as_current_news_id')
        if target in cards:
            cards[alias]=cards[target]
    now=_moment(context.get('as_of') or prepared.get('as_of'))
    available=_current_source_rows(context,prepared)
    risk=re.compile(r'(?:可能|潜在|有条件|条件性)?(?:的)?(?:供应|供给|供气)(?:约束|受限|短缺|缺口|中断风险)|(?:可能|潜在|条件性)(?:的)?(?:上行|看涨|上涨)风险|potential\s+(?:supply\s+)?constraint|capacity\s+constraint',re.I)
    unchanged=re.compile(r'(?:实际|计量)[^。；;，,]{0,16}(?:流量|供气|供应)[^。；;，,]{0,14}(?:未受影响|不受影响|没有损失|未减少|保持不变|无变化|未变|稳定)|(?:actual|metered)\s+(?:gas\s+)?flows?[^.;,]{0,25}(?:unaffected|unchanged|no loss)',re.I)

    def current(row):
        op=row.get('operational') or {}
        if row.get('temporal_state') in {'scheduled_notice','scheduled','future','dismissed_notice','dismissed','cancelled','announced_end_elapsed_unverified_flow','resolved'}:
            return False
        if str(row.get('event_status') or op.get('event_status') or '').casefold() in {'dismissed','cancelled','inactive'}:
            return False
        start=_moment(row.get('event_start') or op.get('event_start_utc'))
        end=_moment(row.get('event_end') or op.get('event_end_utc'))
        return _known_source(row,now) and not (op.get('scheduled_for_future') or op.get('announced_end_passed') or start and now and start>now or end and now and end<=now)

    for index,statement in enumerate((result.get('news_impact') or {}).get('neutralizing_evidence') or []):
        for clause in _clauses(str(statement)):
            # Pure uncertainty/low-confidence notes remain evidence limits.
            # This gate addresses a fact -> directional-risk mechanism being
            # put in the counterevidence bucket without an actual counterfact.
            parts=re.split(r'→|->|因此|因而|所以',clause,maxsplit=1)
            if len(parts)!=2 or not risk.search(parts[1]) or _DISCLAIM_CAUSAL.search(parts[1]):
                continue
            aliases=re.findall(r'(?<!\w)N\d+(?!\w)',clause)
            scoped=[cards[alias] for alias in aliases if alias in cards]
            if not aliases:
                scoped=[cards['N0']]
            notices=[row for row in scoped if _notice_item(row) and current(row)
                     and any(signal['mechanism']=='capacity_constraint' for signal in extract_mechanisms(row))]
            if not notices:
                continue
            # A real opposed mechanism in this same cited evidence can
            # counter the risk; absent measurements cannot play that role.
            claimed_down={signal['mechanism'] for signal in _validation_mechanisms(clause) if signal['pressure']=='down'}
            supplied_down={signal['mechanism'] for row in scoped if current(row)
                           for signal in extract_mechanisms(row) if signal['pressure']=='down'}
            if claimed_down & supplied_down:
                continue
            entities=[match[1].strip() for notice in notices if (match:=re.search(r'\bGassco\s+([^:：]{1,45})[:：]',str(notice.get('title','')),re.I))]
            observed_unchanged=False
            for source in available:
                text=_source_text(source)
                if entities and not any(entity.casefold() in text.casefold() for entity in entities):
                    continue
                for source_clause in _clauses(text):
                    observed=unchanged.search(source_clause)
                    if observed and _affirmed_actual_flow(source_clause) and not re.search(r'未提供|未确认|未核实|缺乏|缺少|是否|若|如果|无法确认|不代表|不等于|no evidence|not confirmed|\bif\b',source_clause,re.I):
                        observed_unchanged=True
            if not observed_unchanged:
                raise ValueError(f'news_impact.neutralizing_evidence[{index}]:当前容量缺口→可能供应约束是条件性上行风险，不能以未确认流量损失作为抵消证据；须分列该风险与未知/把握程度，或引用真实反向机制/同范围独立流量未变事实。净方向仍可基于有据的弱传导或权衡判flat，不得强制up')


def _check_unverified_capacity_risk_exclusion(result: dict, context: dict, prepared: dict) -> None:
    """Absent flow confirmation cannot erase a cited current capacity risk."""
    cards={row.get('id'):row for row in prepared.get('window_news',[]) if isinstance(row,dict) and row.get('id')}
    cards['N0']=prepared.get('current_news') or context.get('current_news') or {}
    for alias,row in list(cards.items()):
        if row.get('same_as_current_news_id') in cards:
            cards[alias]=cards[row['same_as_current_news_id']]
    now=_moment(context.get('as_of') or prepared.get('as_of'))
    available=_current_source_rows(context,prepared)
    evidence_aliases={alias for path,text in _strings(result) if 'evidence' in path
                      for alias in re.findall(r'(?<!\w)N\d+(?!\w)',text)}
    missing=re.compile(r'(?:未确认|未核实|未见实际|未提供[^。；;]{0,12}(?:流量|计量)|缺[少乏][^。；;]{0,12}(?:流量|计量)|(?:实际)?流量[^。；;，,]{0,8}(?:未确认|未核实|未知))',re.I)
    exclusion=re.compile(r'(?:不|未|没有)(?:计入|纳入)(?:(?:本次|当前|当日|此次)(?:的)?)?(?:价格冲击|新闻影响|新闻压力|分析)|(?:不|未|没有)(?:纳入|考虑)(?:本次|此次|当前)(?:的)?(?:分析|风险判断)',re.I)
    unchanged=re.compile(r'(?:实际|计量)[^。；;，,]{0,16}(?:流量|供气|供应)[^。；;，,]{0,14}(?:未受影响|不受影响|没有损失|未减少|保持不变|无变化|未变|稳定)|(?:actual|metered)\s+(?:gas\s+)?flows?[^.;,]{0,25}(?:unaffected|unchanged|no loss)',re.I)
    for path,statement in _strings(result):
        if '.scenarios.' in path:
            continue
        for clause in _clauses(statement):
            excluded=exclusion.search(clause)
            if not excluded:
                continue
            before=clause[:excluded.start()]
            absent=missing.search(before)
            if not absent or not re.search(r'故|因此|因而|所以|从而|由此|于是|→|->',before[absent.end():]):
                continue
            if re.search(r'若|如果|假设|不能(?:直接)?|不得|不应|不代表|不等于',before[max(absent.end(),excluded.start()-18):]):
                continue
            if re.search(r'市场|投资者|交易者|交易员',before[-12:]):
                continue  # Market-pricing assertions retain their own gate.
            aliases=re.findall(r'(?<!\w)N\d+(?!\w)',clause)
            scoped=[cards[a] for a in aliases if a in cards]
            if not aliases:
                scoped=[cards[a] for a in evidence_aliases if a in cards]
                if _notice_item(cards['N0']):scoped.append(cards['N0'])
            notices=[]
            for row in scoped:
                op=row.get('operational') or {}
                start=_moment(row.get('event_start') or op.get('event_start_utc'))
                end=_moment(row.get('event_end') or op.get('event_end_utc'))
                if (not _notice_item(row) or not _known_source(row,now)
                        or row.get('temporal_state') in {'scheduled_notice','scheduled','future','dismissed_notice','dismissed','cancelled','announced_end_elapsed_unverified_flow','resolved'}
                        or op.get('scheduled_for_future') or op.get('announced_end_passed')
                        or start and now and start>now or end and now and end<=now):
                    continue
                if any(signal['mechanism']=='capacity_constraint' for signal in extract_mechanisms(row)):
                    notices.append(row)
            if not notices:
                continue
            entities=[match[1].strip() for notice in notices if (match:=re.search(r'\bGassco\s+([^:：]{1,45})[:：]',str(notice.get('title','')),re.I))]
            confirmed_entities=set()
            for source in available:
                text=_source_text(source)
                if entities and not any(entity.casefold() in text.casefold() for entity in entities):
                    continue
                for source_clause in _clauses(text):
                    if (unchanged.search(source_clause) and _affirmed_actual_flow(source_clause)
                            and not re.search(r'未提供|未确认|未核实|缺乏|缺少|是否|若|如果|无法确认|不代表|不等于|no evidence|not confirmed|\bif\b',source_clause,re.I)):
                        confirmed_entities.update(entity for entity in entities if entity.casefold() in source_clause.casefold())
            if not entities or not all(entity in confirmed_entities for entity in entities):
                raise ValueError(f'{path}:当前公告容量受限是条件性供应风险，缺乏实际流量确认不能因此不计入价格冲击或排除本次分析；这不是市场未计价判断。须保留容量风险与实流未知，可依据弱传导/反向事实权衡净flat及把握程度，不强制up或补实际损失')


def _check_mixed_notice_type_aggregation(result: dict, context: dict, prepared: dict) -> None:
    active=_active_notice_types(prepared,context)
    if len(active)!=2 or set(active.values())!={'计划','非计划'}:
        return
    both=re.compile(r'(?:两|二|2)(?:项|处|个|条|座)(?:当前|活跃|正在|现有)?(?:的)?(?:(?:均|都|皆)(?:为|是|属)?|全部(?:为|是))?(?P<kind>非计划|计划)(?:性)?(?:停产|停机|停供|检修|维护|容量公告|不可用公告|供应中断)|(?:两|二|2)(?:项|处|个|条|座)(?:当前|活跃|正在|现有)?(?:的)?(?:停产|停机|停供|检修|维护|容量公告|不可用公告)(?:均|都|皆)(?:为|是|属)?(?P<post_kind>非计划|计划)',re.I)
    for path,statement in _strings(result):
        if '.scenarios.' in path or 'historical_comparisons' in path:
            continue
        for clause in _clauses(statement):
            aliases=set(re.findall(r'(?<!\w)N\d+(?!\w)',clause))
            if aliases and not set(active).issubset(aliases):
                continue  # An explicit single-source claim has its own gate.
            for match in both.finditer(clause):
                before=re.split(r'[，,]|→|->|但是|但|因此',clause[:match.start()])[-1]
                if re.search(r'若|如果|假设|假如|是否|不能说|不能称|不代表|不等于|不能归为|并非|不是',before):
                    continue
                facts='、'.join(f'{identifier}={kind}' for identifier,kind in active.items())
                raise ValueError(f'{path}:当前两项容量公告类型混合（{facts}），不能统称两项均计划或非计划停产/停机；年检原因不改变Unplanned类型。请分别保留1项计划+1项非计划及容量/实际流量边界，不改写净方向')


def _check_cited_mechanism(result: dict, context: dict, prepared: dict) -> None:
    cards = {row.get("id"): row for row in prepared.get("window_news", []) if isinstance(row, dict) and row.get("id")}
    cards["N0"] = context.get("current_news") or prepared.get("current_news") or {}
    for alias, row in list(cards.items()):
        target = row.get("same_as_current_news_id")
        if target and target in cards:
            cards[alias] = cards[target]
    news = result.get("news_impact") or {}
    for field in ("bullish_evidence", "bearish_evidence", "neutralizing_evidence"):
        for index, statement in enumerate(news.get(field) or []):
            if re.search(r"若|如果|假如|假设|\bif\b", statement, re.I):
                continue
            aliases = re.findall(r"(?<!\w)N\d+(?!\w)", statement)
            sources = [cards[alias] for alias in aliases if alias in cards]
            if not sources:
                continue
            # Match the *type* of mechanism, not merely the word up/down:
            # inventory builds can coexist with supply disruption in one item.
            original = [s for row in sources for s in extract_mechanisms(row)]
            claimed = _validation_mechanisms(statement)
            for family in ("supply_", "demand_", "inventory_"):
                given = {s["pressure"] for s in original if s["mechanism"].startswith(family)}
                stated = {s["pressure"] for s in claimed if s["mechanism"].startswith(family)}
                if len(given) == 1 and len(stated) == 1 and given != stated:
                    raise ValueError(f"news_impact.{field}[{index}]:引用来源的{family.rstrip('_')}变化方向与所述事实相反；不能把来源的供应增加改写成供应减少或反向需求")


def _check_summary_direction(result: dict) -> None:
    for section in ("news_impact", "close_forecast"):
        value = result.get(section) or {}
        direction = value.get("direction")
        summary = str(value.get("summary", ""))
        signs = _price_signs(summary)
        if len(signs) != 1:
            continue
        sign = next(iter(signs))
        # News pressure may be weakly signed yet legitimately classed flat.
        if direction == "flat":
            if section == "close_forecast" and re.search(r"预计|预判|预测|将|will|expect", summary, re.I) and not re.search(r"基本维持|持平|有限|轻微|小幅|可能|若|如果|或|flat|little|minor|may|if", summary, re.I):
                raise ValueError(f"{section}.summary:文字明确预测涨跌却枚举为flat；须统一结论")
            continue
        expected = "up" if direction in {"up", "extreme_up"} else "down" if direction in {"down", "extreme_down"} else None
        if expected and sign != expected and not re.search(r"若|如果|假如|但|然而|抵消|不足以|未必|\bif\b|\bbut\b", summary, re.I):
            raise ValueError(f"{section}.summary:文字价格方向与direction相反，必须基于相同证据统一结论")


def _check_one_sided_actual_flow(result: dict, context: dict, prepared: dict) -> None:
    price = _known_price_reference(context, prepared)
    if price.get("priced_in_evidence") or price.get("announcement_price_reaction"):
        # A measured change need not be new price pressure if its reaction is
        # actually supplied. The forecast still passes causal/citation checks.
        return
    signals = []
    strong = []
    now = _moment(context.get("as_of") or prepared.get("as_of"))
    session = context.get("session") or prepared.get("session") or {}
    window_start = _moment(session.get("window_start"))
    for row in _current_source_rows(context, prepared):
        extracted = extract_mechanisms(row)
        published = _moment(row.get("published_at") or row.get("time") or row.get("first_seen_at"))
        # Old triggering articles are legitimate background, not an automatic
        # new-window price shock. Unknown publication-window membership cannot
        # establish a new same-session observation either.
        if (published and window_start and published.tzinfo and window_start.tzinfo and published < window_start) or row.get("window_membership") == "first_observed_in_window_publication_window_unknown":
            continue
        op = row.get("operational") or {}
        start = _moment(row.get("event_start") or op.get("event_start_utc") or op.get("event_start"))
        end = _moment(row.get("event_end") or op.get("event_end_utc") or op.get("event_end"))
        if (start and now and start.tzinfo and now.tzinfo and start > now) or row.get("temporal_state") in {"scheduled", "scheduled_notice", "future"}:
            continue
        for signal in extracted:
            ended = end and now and end.tzinfo and now.tzinfo and end <= now
            dismissed = str(row.get("event_status", op.get("event_status", ""))).lower() in {"dismissed", "cancelled", "inactive"}
            if signal["mechanism"] == "supply_decrease" and (ended or dismissed or row.get("temporal_state") in {"announced_end_elapsed_unverified_flow", "dismissed_notice", "dismissed", "resolved"}):
                continue
            signals.append(signal)
            if signal.get("evidence_kind") != "actual_flow" or signal.get("magnitude_mcm_d", 0) < 20 or not _EUROPE.search(signal["quoted_fact"]):
                continue
            strong.append(signal)
    signs = {s["pressure"] for s in strong}
    if len(signs) != 1:
        return
    expected = next(iter(signs))
    opposite = "down" if expected == "up" else "up"
    # Only supplied facts can offset a supplied one-sided actual flow shock.
    # Conditional capacity notices are not measured opposing flow observations.
    if any(s["pressure"] == opposite and s.get("evidence_kind") != "capacity_only" for s in signals):
        return
    direction = (result.get("news_impact") or {}).get("direction")
    if direction not in {expected, "extreme_" + expected}:
        raise ValueError("news_impact.direction:输入给出欧洲大幅实际流量单向变化，却无来源支持的反向供需事实；缺行情/历史资料不能当作抵消或直接判平。请重算净新闻压力，收盘方向可另依据盘中基准说明")


def _check_observed_baseline(result: dict, context: dict, prepared: dict) -> None:
    close = result.get("close_forecast") or {}
    direction = close.get("direction")
    if direction is None or close.get("evidence_status") == "insufficient_evidence":
        return
    reference = _known_price_reference(context, prepared)
    baseline = reference.get("market_baseline") or {}
    if not baseline.get("available"):
        return
    observed = baseline.get("observed_direction")
    observed = "up" if observed in {"up", "extreme_up"} else "down" if observed in {"down", "extreme_down"} else None
    try:
        material = abs(float(baseline.get("observed_delta", 0))) > float(baseline.get("flat_band", .30))
    except (ValueError, TypeError):
        return
    predicted = "up" if direction in {"up", "extreme_up"} else "down" if direction in {"down", "extreme_down"} else direction
    if not observed or not material or predicted == observed:
        return
    opposite = "down" if observed == "up" else "up"
    source_signals = [s for row in _current_source_rows(context, prepared) for s in extract_mechanisms(row)]
    explanation = str(close.get("summary", "")) + "。" + str(close.get("basis", ""))
    explained = {s["pressure"] for s in _validation_mechanisms(explanation)}
    supplied = any(s["pressure"] == opposite for s in source_signals)
    reversal = bool(re.search(r"回吐|反转|逆转|扭转|抵消|覆盖|回落|回升|减弱|缓解|压低|推升|上行压力|下行压力|reverse|reversal|offset|retreat|give.back|pressure", explanation, re.I))
    if not supplied or opposite not in explained or not reversal:
        raise ValueError("close_forecast:已观察同合约价格显著偏离上一日，预测flat/反向须引用输入中相反供需事实并解释回吐/反转；新闻弱或资料缺失不能把已观察变化归零。若不能形成预判，须引用真实供需冲突或价格比较不适用依据，不能仅因弱新闻、缺资料或缺少逆转证据取消有效基准；不得替填方向")


def _check_known_flow_quantity_not_claimed_missing(result: dict, context: dict, prepared: dict) -> None:
    cards={row.get('id'):row for row in prepared.get('window_news',[]) if isinstance(row,dict) and row.get('id')}
    cards['N0']=prepared.get('current_news') or context.get('current_news') or {}
    available=_current_source_rows(context,prepared)
    for path,text in _strings(result):
        if '.scenarios.' in path:
            continue
        for clause in _clauses(text):
            missing=re.search(r'(?:未提供|缺少|缺乏|没有|未知|无法量化)[^。；;，,]{0,12}(?:实际流量|实际供气|计量流量|流量变化)[^。；;，,]{0,12}(?:量化|定量|数量|数值|规模|变化量)|(?:未提供|缺少|缺乏|没有)[^。；;，,]{0,10}(?:量化|定量|数值)[^。；;，,]{0,10}(?:实际流量|实际供气|计量流量)',clause)
            if not missing or re.search(r'独立|核实|核验|验证|连续|序列|完整|长期|后续|公告前后|事件前后',missing.group()):
                continue
            before=clause[max(0,missing.start()-20):missing.start()]
            if re.search(r'若|如果|假设|不能说|不能声称|并非|不代表',before):
                continue
            aliases=re.findall(r'(?<!\w)N\d+(?!\w)',clause)
            rows=available
            if aliases:
                selected=[cards[a] for a in aliases if a in cards]
                selected.extend([context.get('current_news') or {}] if 'N0' in aliases else [])
                rows=[row for row in selected if _known_source(row,_moment(context.get('as_of') or prepared.get('as_of')))]
            observed=[signal for row in rows for signal in extract_mechanisms(row)
                      if signal.get('evidence_kind')=='actual_flow' and 'magnitude_mcm_d' in signal]
            if observed:
                raise ValueError(f'{path}:输入已明确提供实际流量变化的量化原值（如{observed[0]["magnitude_mcm_d"]:g}mcm/d），不能声称未提供实际流量量化资料；可说明独立核验、连续序列或后续恢复状态尚缺失，不得把核验不足混成数值不存在')


def _check_known_price_not_claimed_missing(result: dict, context: dict, prepared: dict) -> None:
    reference = _known_price_reference(context, prepared)
    baseline = reference.get("market_baseline") or {}
    if not baseline.get("available"):
        return
    news=result.get('news_impact') or {}
    close=result.get('close_forecast') or {}
    if close.get('evidence_status')=='insufficient_evidence' and news.get('direction') in {'up','extreme_up','down','extreme_down'}:
        explanation=str(close.get('summary') or '')+'。'+str(close.get('basis') or '')
        missing_reaction = re.search(r'(?:缺少|缺乏|没有|未提供|尚无|无)[^。；;]{0,25}(?:价格反应|市场反应|公告前后|事件前后)|(?:无法确定|无法确认|无法判断|不能确认|不能判断|未知|不知|不确定)[^。；;]{0,25}(?:是否已(?:计入|计价|定价|消化)|已计价|已定价)|(?:lack|missing|no)[^.;]{0,20}(?:price reaction|announcement reaction)|uncertain[^.;]{0,20}priced',explanation,re.I)
        if missing_reaction:
            opposite='up' if news['direction'] in {'down','extreme_down'} else 'down'
            explained={s['mechanism'] for s in _validation_mechanisms(explanation) if s['pressure']==opposite}
            supplied={s['mechanism'] for row in _current_source_rows(context,prepared)
                      if row.get('temporal_state') not in {'scheduled_notice','dismissed_notice','announced_end_elapsed_unverified_flow'}
                      for s in extract_mechanisms(row) if s['pressure']==opposite}
            conflict=bool(re.search(r'冲突|抵消|拉锯|反向|相反|分歧|难以权衡|conflict|offset|opposing',explanation,re.I))
            if not conflict or not (explained & supplied):
                raise ValueError('close_forecast:合法同合约比较锚点已给；报价若早于消息仅是基准，未知后续反应不等于零反应或未计价。请据源供需与锚点给条件收盘预判，五档自由、不自动复制新闻方向；缺少公告前后反应不能单独作为拒绝收盘预判的理由，真实冲突须引用具体反向事实')
    missing_before = re.compile(r"(?:缺乏|缺少|没有|未提供|尚无|缺失|无法获取|不可获取)[^。；;，,]{0,20}(?:市场基准|市场价格基准|当日价格基准|当日行情|盘中(?:报价|价格|基准)|同合约(?:报价|行情)|market baseline|intraday (?:quote|price|baseline))", re.I)
    missing_after = re.compile(r"(?:市场基准|市场价格基准|当日价格基准|当日行情|盘中(?:报价|价格|基准)|同合约(?:报价|行情)|market baseline|intraday (?:quote|price|baseline))\s*(?:(?:数据|记录|当前|目前|仍|尚|为|是|均|都)\s*){0,3}(?:不可用|缺失|未提供|缺乏|无法获取|unavailable|missing)", re.I)
    qualified = re.compile(r"公告前后|事件前后|消息前后|连续|逐笔|高频|实时|即时|无延迟|完整(?:的)?(?:历史|序列|时间序列)|官方|已验证|校准|历史|before.and.after|tick.by.tick|continuous|real.time|undelayed|official|calibrat", re.I)
    for path, statement in _strings(result):
        for clause in _clauses(statement):
            for match in list(missing_before.finditer(clause)) + list(missing_after.finditer(clause)):
                if qualified.search(match.group()):
                    continue
                if re.search(r"不能(?:说|声称)|并非|不是|不代表|不等于", clause[max(0, match.start()-12):match.start()]):
                    continue
                raise ValueError(f"{path}:输入已提供截至as_of可用的同合约市场基准，不能以缺乏市场基准/盘中报价为信息不足理由；可基于真实冲突或无法判断延续性明确insufficient_evidence，不得补默认方向")
    missing = result.get("missing_fields") or []
    unavailable_paths = {"price_reference", "price_reference.market_baseline", "price_reference.intraday", "price_reference.previous_price", "market_baseline", "intraday_quote"}
    if any(str(field).strip() in unavailable_paths for field in missing):
        raise ValueError("missing_fields:市场基准已提供，不能把整个行情、盘中报价或前日参考价列作缺失；请列具体未提供的连续反应/官方结算等资料")

    if (close.get('evidence_status')=='insufficient_evidence' and news.get('direction')=='flat'
            and baseline.get('observed_direction') in {'up','down','extreme_up','extreme_down'}
            and isinstance(baseline.get('observed_delta'),(int,float)) and not isinstance(baseline.get('observed_delta'),bool)
            and abs(baseline['observed_delta'])>float(baseline.get('flat_band',.3))
            and not baseline.get('stale') and not (reference.get('intraday') or {}).get('stale')):
        explanation=str(close.get('summary') or '')+'。'+str(close.get('basis') or '')
        opposite='down' if baseline.get('observed_direction') in {'up','extreme_up'} else 'up'
        explained={signal['mechanism'] for signal in _validation_mechanisms(explanation) if signal['pressure']==opposite}
        cards={row.get('id'):row for row in prepared.get('window_news',[]) if isinstance(row,dict) and row.get('id')}
        cards['N0']=prepared.get('current_news') or context.get('current_news') or {}
        for alias,row in list(cards.items()):
            if row.get('same_as_current_news_id') in cards:cards[alias]=cards[row['same_as_current_news_id']]
        cited=re.findall(r'(?<![A-Za-z0-9_])N\d+(?![A-Za-z0-9_])',explanation)
        supplied={signal['mechanism'] for alias in cited if alias in cards
                  for row in (cards[alias],) if _known_source(row,_moment(context.get('as_of') or prepared.get('as_of')))
                  and row.get('temporal_state') not in {'scheduled_notice','dismissed_notice','announced_end_elapsed_unverified_flow'}
                  for signal in extract_mechanisms(row) if signal['pressure']==opposite}
        conflict=bool(re.search(r'冲突|抵消|拉锯|反向|相反|分歧|难以权衡|conflict|offset|opposing',explanation,re.I))
        if not conflict or not (explained & supplied):
            raise ValueError('close_forecast:已有有效同合约盘中方向基准；新闻影响较弱或无法证明单新闻价格因果、未知是否计价，不能单独取消相对已给前价的条件性收盘预判。请权衡真实基准与有来源的冲突机制，保留五档选择，不得自动复制新闻方向或把参考变化当已实现收盘')


def _check_target_previous_settlement_anchor(result: dict, context: dict, prepared: dict) -> None:
    reference=_known_price_reference(context,prepared)
    anchor=reference.get('settlement_anchor')
    if anchor is None and (reference.get('market_baseline') or {}).get('reason')=='previous_trading_day_reference_missing':
        from .context import settlement_anchor
        anchor=settlement_anchor(reference,context.get('session') or prepared.get('session') or {},context.get('as_of') or prepared.get('as_of'))
    # Stored legacy inputs without the new contract retain their original
    # interpretation. Every new prepared request includes this explicit status.
    if not isinstance(anchor,dict) or anchor.get('available'):
        return
    close=result.get('close_forecast') or {}
    news=result.get('news_impact') or {}
    if close.get('evidence_status','forecast')=='forecast' and news.get('direction') in {None,'flat'}:
        raise ValueError(f'close_forecast:目标{anchor.get("target_trade_date")}缺少前交易日{anchor.get("expected_previous_trade_date")}同合约锚点；当前绝对报价或其他日期参考差不能当昨收涨跌。新闻也无方向依据时须insufficient_evidence且direction=null；有方向新闻仍可给有据条件预测，不补flat或强制复制新闻方向')
    observed_move=re.compile(r'(?:当前|参考|盘中|市场)(?:价格|报价|气价)(?:已经|已|实际)(?:明显|小幅|显著)?(?:上涨|下跌|上升|下降|走高|走低)|(?:当前|参考|盘中|市场)(?:价格|报价|气价)[^。；;，,]{0,6}(?:较|比|相对)(?:前一|上一|昨日|昨收|前日)(?:结算|收盘|价格)?[^。；;，,]{0,8}(?:上涨|下跌|上升|下降)',re.I)
    for field in ('summary','basis'):
        for clause in _clauses(str(close.get(field) or '')):
            match=observed_move.search(clause)
            if match and not _UNCONFIRMED_PREFIX.search(clause[:match.start()]):
                if ((reference.get('announcement_price_reaction') or reference.get('priced_in_evidence'))
                        and re.search(r'公告前后|消息前后|事件前后|可比反应',clause)
                        and not re.search(r'前一结算|上一结算|前一交易日|昨日|昨收',clause)):
                    continue  # Explicit event reaction has its own denominator.
                raise ValueError(f'close_forecast.{field}:无目标日前交易日同合约锚点，不能把其他日期reference、provider change/previous_close或旧报价差称为当前已相对前结算涨跌；可保留已观察绝对报价，基于新闻给条件判断或明确比较依据不足')


def _notice_item(item: dict) -> bool:
    op = item.get("operational") or {}
    return "gassco" in str(item.get("source", "")).lower() or bool(re.search(r"\bGassco\b", str(item.get("title", "")), re.I)) or bool(op.get("capacity_is_not_lost_flow"))


def _physical_claim_is_conditional(clause: str, match: re.Match) -> bool:
    before = clause[max(0, match.start()-35):match.start()]
    after = clause[match.end():match.end()+20]
    accounting_scope=re.split(r"[，,]|但是|但|然而|\bbut\b",before,flags=re.I)[-1]
    if re.search(r"未(?:计入|计作|计为|归为|纳入)[^，,]{0,12}$",accounting_scope):
        return True  # Excluding a future capacity from today's loss ledger.
    if re.search(r"若|如果|假如|假设|可能|预计|预期|有望|或将|取决于|有赖于|依赖于|需等待|需核实|待核实|未核实|未确认|未见|尚未观察到|无法确认|不能确认|是否|不代表|不等于|不意味着|不能证明|不得宣称|\bif\b|\bcould\b|\bmight\b|\bexpected\b|\bnot confirmed\b", before, re.I):
        return True
    return bool(re.match(r"(?:状态|情况|程度)?(?:仍|尚)?(?:待核实|待确认|未核实|未确认|无法确认|不确定|未知|与否)|(?:的)?(?:风险|可能性)|\s*(?:is )?(?:unconfirmed|unverified|unknown)|\s+risk", after, re.I))


def _notice_no_impact_is_observed(clause: str, match: re.Match, notices: list[dict], available: list[dict]) -> bool:
    # A preceding comma-separated "recovery unconfirmed" is not a qualifier
    # of a following declarative "there was no supply shock".
    before = re.split(r"[，,]|→|->|但是|但|因此", clause[:match.start()])[-1]
    after = clause[match.end():match.end()+15]
    if re.match(r'^\s*(?:\[[NE]\d+\]\s*)?(?:若|如果|假设|假如|一旦)',clause):
        return True
    if re.search(r"若|如果|假设|假如|是否|不能确认|未确认|未核实|无法确认|不代表|不等于|不能证明|不得推断|\bif\b|unconfirmed|cannot confirm",before,re.I):
        return True
    if re.search(r"输入|资料|所供|报道|新闻|证据",before) and re.search(r"未显示|未表明|未提供|没有|无|缺少|缺乏",before+match.group()):
        return True
    if re.match(r"(?:的)?(?:确切|独立|有效|实际)?证据|\s+evidence",after,re.I):
        return True  # "输入没有供应冲击证据" is an evidence limit.
    entities = [m[1].strip() for notice in notices if (m := re.search(r"\bGassco\s+([^:：]{1,45})[:：]",str(notice.get("title","")),re.I))]
    unchanged = re.compile(r"(?:实际|计量)[^。；;，,]{0,16}(?:流量|供气|供应)[^。；;，,]{0,14}(?:未受影响|不受影响|没有损失|未减少|保持不变|无变化|未变|稳定)|(?:actual|metered)\s+(?:gas\s+)?flows?[^.;,]{0,25}(?:unaffected|unchanged|no loss)",re.I)
    for source in available:
        text = _source_text(source)
        if entities and not any(entity.casefold() in text.casefold() for entity in entities):
            continue
        for source_clause in _clauses(text):
            supported = unchanged.search(source_clause)
            if not supported or not _affirmed_actual_flow(source_clause):
                continue
            prefix = source_clause[max(0,supported.start()-40):supported.start()]
            suffix = source_clause[supported.end():supported.end()+20]
            if not re.search(r"未提供|未确认|未核实|缺乏|缺少|是否|若|如果|无法确认|no evidence|not confirmed|\bif\b",prefix+supported.group()+suffix,re.I):
                return True
    return False


def _notice_risk_removal_is_scoped_or_observed(clause: str, match: re.Match, notices: list[dict], available: list[dict], pattern: re.Pattern) -> bool:
    # The uncertainty in an earlier comma-separated clause does not qualify a
    # following assertion that *all* supply risk has gone away.
    before=re.split(r'[，,]|→|->|但是|但|因此',clause[:match.start()])[-1]
    after=clause[match.end():match.end()+20]
    if re.match(r'^\s*(?:\[[NE]\d+\]\s*)?(?:若|如果|假设|假如|一旦)',clause):
        return True
    if re.search(r'若|如果|假设|假如|是否|可能|预计|预期|有望|不能确认|未确认|未核实|无法确认|不代表|不等于|不能证明|不得推断|不能断言|\bif\b|unconfirmed|cannot confirm',before,re.I):
        return True
    if re.match(r'(?:与否|是否)?(?:仍|尚)?(?:待核实|待确认|未确认|未知|不确定)|(?:的)?(?:证据|依据)(?:不足|缺乏|未提供)',after):
        return True
    # Explicitly bound announcement risk is the cancelled object. A notice
    # appearing in an earlier clause is not a scope for global supply risk.
    if re.search(r'(?:该|本|此)公告(?:所述|对应|所指)?(?:的)?(?:容量|约束)?(?:的)?$|(?:公告容量|所述约束)(?:的)?$',before):
        return True
    entities=[m[1].strip() for notice in notices if (m:=re.search(r'\bGassco\s+([^:：]{1,45})[:：]',str(notice.get('title','')),re.I))]
    broad_claim=bool(re.search(r'整体|总体|全部|所有|欧洲|欧盟|全欧',before+match.group()))
    for source in available:
        text=_source_text(source)
        if entities and not any(entity.casefold() in text.casefold() for entity in entities):
            continue
        for source_clause in _clauses(text):
            observed=pattern.search(source_clause)
            if not observed or not _affirmed_actual_flow(source_clause):
                continue
            source_before=re.split(r'[，,]|→|->|但是|但|因此',source_clause[:observed.start()])[-1]
            source_after=source_clause[observed.end():observed.end()+20]
            if re.search(r'未提供|未确认|未核实|缺乏|缺少|是否|若|如果|无法确认|不代表|不等于|no evidence|not confirmed|\bif\b',source_before+observed.group()+source_after,re.I):
                continue
            if broad_claim and not re.search(r'整体|总体|全部|所有|欧洲|欧盟|全欧',source_before+observed.group()):
                continue
            return True
    return False


def _check_gassco_physical_state(result: dict, context: dict, prepared: dict) -> None:
    """A notice status is not an independent observation of production/flow."""
    cards = {row.get("id"): row for row in prepared.get("window_news", []) if isinstance(row, dict) and row.get("id")}
    cards.update({row.get("id"): row for row in prepared.get("historical_events", []) if isinstance(row, dict) and row.get("id")})
    cards["N0"] = context.get("current_news") or prepared.get("current_news") or {}
    for alias, row in list(cards.items()):
        if row.get("same_as_current_news_id") in cards:
            cards[alias] = cards[row["same_as_current_news_id"]]
    now = _moment(context.get("as_of") or prepared.get("as_of"))
    available = _current_source_rows(context, prepared)
    reference = _known_price_reference(context,prepared)
    evidence_aliases = {alias for field,text in _strings(result) if "evidence" in field
                        for alias in re.findall(r"(?<!\w)N\d+(?!\w)",text)}
    evidence_notices = [cards[alias] for alias in evidence_aliases if alias in cards and _notice_item(cards[alias])]
    restoration = re.compile(r"(?:实际(?:天然气)?(?:流量|供气|供应)|(?:供气|供气流量|出口流量))\s*(?:已经|已|现已|已确认)?\s*(?:完全|全面|全部)?(?:恢复|回升)|(?:已经|已|现已)(?:全面|完全)?(?:复产|恢复生产|恢复运营|恢复供气)|(?:设施|处理厂|工厂)(?:已经|已|现已)(?:全面|完全)?(?:重启|复产|恢复)|(?:actual|metered)\s+(?:gas\s+)?flows?\s+(?:have |has |are |were )?(?:already |fully )?(?:restored|recovered|resumed)|\b(?:has|have)\s+(?:fully )?(?:restarted|resumed)\s+(?:production|operations|flows)", re.I)
    loss = re.compile(r"实际(?:天然气)?(?:流量|供气|供应)[^。；;，,]{0,12}(?:减少|下降|中断|损失|受限)|(?:造成|导致|形成)[^。；;，,]{0,12}实际(?:流量|供气|供应)(?:损失|减少|中断)|(?:当前|目前|今天|今日|正在)(?:已经|已|正在|实际)?(?:供气|供应|流量|生产|设施)(?:已经|已|正在)?(?:减少|下降|中断|停供|停产)|(?:已经|已)(?:确认|证实)(?:当前|目前|现在)?(?:实际)?(?:停供|停产)|(?:供气|出口流量|天然气供应)(?:已经|已|正在)(?:减少|下降|中断|停供)|(?:活跃(?:的)?|当前|新增|新出现(?:的)?)(?:供应|供给)中断|(?:actual|metered)\s+(?:gas\s+)?flows?[^.;]{0,16}(?:reduced|decreased|fell|lost|stopped)", re.I)
    no_impact = re.compile(r"(?:没有|无|未(?:形成|造成|导致|出现)|不存在)(?:任何|实际|有效|显著)?(?:供需|供应|供给)(?:冲击|影响|中断)|(?:无|没有)(?:任何)?实际(?:流量|供气)(?:影响|损失)|(?:供应|供给)(?:不受影响|未受影响)|实际(?:天然气)?流量[^。；;，,]{0,8}(?:未受影响|不受影响|没有损失|保持不变|无变化)|(?:actual|metered)\s+(?:gas\s+)?flows?[^.;,]{0,15}(?:unaffected|unchanged)|no\s+(?:supply|supply.and.demand)\s+(?:shock|impact)",re.I)
    risk_removed=re.compile(r'(?:供应|供给|供气|停供)(?:中断|短缺)?风险(?:已经|已|现已)?(?:完全|全面|全部|彻底)?(?:消除|解除|消失|清零)|(?:已|已经)?(?:完全|全面|全部|彻底)?(?:消除|解除)(?:了)?(?:供应|供给|供气|停供)(?:中断|短缺)?风险|(?:supply|gas.supply)\s+risk\s+(?:has |have |is |was )?(?:been )?(?:fully |completely |entirely )?(?:eliminated|removed|resolved)',re.I)
    quantified_outage=re.compile(r"(?:计划|预计|将)?(?:停产|停供|减产|减供)\s*(?:约|达|达到|为)?\s*(?P<amount>\d+(?:\.\d+)?)\s*(?:mcm\s*(?:/\s*d(?:ay)?|per\s+day)|百万立方米\s*(?:/\s*[日天]|每天|每日))",re.I)
    # A sourced future production/delivery plan is usable even though a
    # future flow cannot yet be metered. Technical/unavailable capacity alone
    # does not match these explicit physical-quantity phrases.
    source_loss_quantity=re.compile(r"(?:(?:实际(?:天然气)?)?(?:供气(?:流量)?|天然气(?:供应|产量)|流量|产量|生产量|gas\s+flows?|gas\s+supply|production|output|deliveries)[^。；;，,]{0,24}?(?:减少|下降|降低|减产|减供|decreas\w*|reduc\w*|fall\w*|curtail\w*)[^。；;，,]{0,8}?|(?:计划|预计|将)(?:实际)?(?:停产|停供|减产|减供)\s*(?:约|达|达到|为)?\s*)(?P<amount>\d+(?:\.\d+)?)\s*(?:mcm\s*(?:/\s*d(?:ay)?|per\s+day)|百万立方米\s*(?:/\s*[日天]|每天|每日))",re.I)
    unaffected_price = re.compile(r'未(?:曾|实际)?影响(?:当前|当日|现有)?(?:价格|气价|TTF价格)|(?:当前|当日)?(?:价格|气价|TTF价格)(?:未受影响|不受影响|保持不变|没有变化|未变)|对(?:当前|当日)?(?:价格|气价)(?:无|没有)(?:实际)?影响|(?:未|并未|没有)对(?:当前|当日|现有)?(?:TTF价格|价格|气价)(?:产生|造成|带来)(?:实际|显著|明显)?影响|对(?:当前|当日|现有)?(?:TTF价格|价格|气价)(?:未|并未|没有)(?:产生|造成|带来)(?:实际|显著|明显)?影响|(?:新闻|消息|公告)(?:未|没有)(?:产生|造成|引发)(?:即时|实际|显著|明显)?价格反应|(?:无|没有)(?:即时|实际|显著|明显)?价格反应',re.I)
    for path, statement in _strings(result):
        if ".scenarios." in path:
            # Scenarios are counterfactual premises, not asserted observations.
            continue
        for clause in _clauses(statement):
            aliases = re.findall(r"(?<!\w)[NE]\d+(?!\w)", clause)
            scoped = [cards[alias] for alias in aliases if alias in cards]
            if not aliases and _notice_item(cards["N0"]):
                scoped = [cards["N0"]]
            elif not aliases and (path.endswith(".summary") or path == "close_forecast.basis"):
                # A summary of N3/N7 evidence cannot silently upgrade their
                # capacity notices to measured outages merely by dropping IDs.
                scoped = evidence_notices
            notices = [row for row in scoped if _notice_item(row)]
            if not notices:
                continue
            dismissed=[row for row in notices if str(row.get('event_status') or (row.get('operational') or {}).get('event_status') or '').casefold() in {'dismissed','cancelled','inactive'} or row.get('temporal_state') in {'dismissed_notice','dismissed','cancelled'} or re.search(r'\b(?:Dismissed|Cancelled)\b',str(row.get('title','')),re.I)]
            if dismissed:
                for match in risk_removed.finditer(clause):
                    if not _notice_risk_removal_is_scoped_or_observed(clause,match,dismissed,available,risk_removed):
                        raise ValueError(f'{path}:撤销仅限该公告约束，实际流量未核实，不能宣称整体供应风险已消除/完全解除；请明确该公告容量风险撤销或缓解，整体风险清零须有同范围独立计量与风险事实')
            for match in unaffected_price.finditer(clause):
                before=re.split(r'[，,]|→|->|但是|但|因此',clause[:match.start()])[-1]
                after=clause[match.end():match.end()+18]
                if re.fullmatch(r'(?:无|没有)(?:即时|实际|显著|明显)?价格反应',match.group()) and re.match(r'(?:的)?(?:证据|数据|观测|记录)',after):
                    continue  # No evidence of a reaction is not evidence of no reaction.
                if (re.search(r'若|如果|假设|一旦|可能|预计|预期|未确认|无法确认|是否|不能证明|不代表',before)
                        or _UNCONFIRMED_PREFIX.search(before)
                        or re.match(r'(?:的)?(?:证据|观测|记录)(?:仍|尚)?(?:不足|缺失|未知|未提供)',after)):
                    continue
                if reference.get('announcement_price_reaction') or reference.get('priced_in_evidence'):
                    continue
                entities=[m[1].strip() for notice in notices if (m:=re.search(r'\bGassco\s+([^:：]{1,45})[:：]',str(notice.get('title','')),re.I))]
                supported=False
                for source in available:
                    source_text=_source_text(source)
                    if entities and not any(entity.casefold() in source_text.casefold() for entity in entities):
                        continue
                    for source_clause in _clauses(source_text):
                        observed=unaffected_price.search(source_clause)
                        if observed and not re.search(r'若|如果|预计|预期|可能|未确认|无法确认|是否|不代表|没有证据',source_clause[:observed.start()]):
                            supported=True
                if not supported:
                    raise ValueError(f'{path}:输入没有这条公告的价格反应观测，不能断言未影响当前价格或价格未受影响；可判断当前新闻压力较弱、未来公告不计作当前停供，并说明价格反应未知')
            for match in no_impact.finditer(clause):
                if _invalidation_event_condition(path,clause,match) is True:
                    continue  # Only this physical predicate, never the whole field.
                # Degree of this known future news pressure is a judgement,
                # not an assertion that measured physical supply is unchanged.
                # Keep this confined to summary/basis wording and notices
                # verified not started as_of; no active/ended/cancelled escape.
                degree_aliases=re.findall(r'(?<![A-Za-z0-9_])[NE]\d+(?![A-Za-z0-9_])',clause)
                degree_notices=[cards[a] for a in degree_aliases if a in cards and _notice_item(cards[a])] if degree_aliases else notices
                named_entities={m[1].strip().casefold() for row in cards.values()
                    if (m:=re.search(r"\bGassco\s+([^:：]{1,45})[:：]",str(row.get('title','')),re.I))
                    and m[1].strip().casefold() in clause.casefold()}
                bound_entities={m[1].strip().casefold() for row in degree_notices
                    if (m:=re.search(r"\bGassco\s+([^:：]{1,45})[:：]",str(row.get('title','')),re.I))}
                degree_judgment=(path in {"news_impact.summary","analysis.summary","close_forecast.summary","close_forecast.basis"}
                    and degree_notices and named_entities.issubset(bound_entities)
                    and re.fullmatch(r"(?:无|没有)(?:显著|明显)(?:供需|供应|供给)(?:冲击|影响|中断)",match.group())
                    and not re.search(r"整体|总体|全网|全市场|欧洲|欧盟|实际|实测|计量|可观测|流量|供气(?:量)?",clause)
                    and now is not None and all(
                        (start:=_moment(row.get('event_start') or (row.get('operational') or {}).get('event_start_utc'))) is not None
                        and start>now and _known_source(row,now)
                        and str(row.get('event_status') or (row.get('operational') or {}).get('event_status') or '').casefold() not in {'dismissed','cancelled','inactive'}
                        and row.get('temporal_state') not in {'dismissed_notice','dismissed','cancelled','announced_end_elapsed_unverified_flow'}
                        and not re.search(r"Dismissed|Cancelled",str(row.get('title','')),re.I)
                        for row in degree_notices))
                if degree_judgment:
                    continue
                if not _notice_no_impact_is_observed(clause,match,notices,available):
                    raise ValueError(f"{path}:Gassco公告撤销/结束或实际流量未确认，不能证明未形成供需冲击、供应不受影响或实际流量无变化；须有独立无变化计量事实。可写输入未显示冲击证据或恢复待核实，不能把缺测写成无冲击")
            for match in quantified_outage.finditer(clause):
                before=clause[max(0,match.start()-40):match.start()]
                if _HYPOTHESIS.search(before) or _DISCLAIM_CAUSAL.search(before) or re.search(r"不能(?:写成|视为|认定)|不得(?:写成|视为|认定)|是否|未确认|无法确认",before):
                    continue
                amount=float(match['amount'])
                entities=[m[1].strip() for notice in notices if (m:=re.search(r'\bGassco\s+([^:：]{1,45})[:：]',str(notice.get('title','')),re.I))]
                supported=False
                for source in available:
                    source_text=_source_text(source)
                    if entities and not any(entity.casefold() in source_text.casefold() for entity in entities):
                        continue
                    for source_clause in _clauses(source_text):
                        for source_match in source_loss_quantity.finditer(source_clause):
                            prefix=source_clause[max(0,source_match.start()-40):source_match.start()]
                            if _missing_information_prefix(prefix) or re.search(r"未确认|无法确认|未提供|未核实|不代表|若|如果|\bif\b",prefix,re.I):
                                continue
                            if abs(float(source_match['amount'])-amount)<1e-8:
                                supported=True
                if not supported:
                    raise ValueError(f'{path}:公告不可用容量不等于计划停产/减供的实际数量；输入未给独立实物流量或交付计划依据，不能把容量改称停产{match["amount"]}mcm/d。可列公告不可用容量、未来时段及其条件风险')
            for kind, pattern, pressure in (("恢复", restoration, "down"), ("损失", loss, "up")):
                for match in pattern.finditer(clause):
                    if (kind == "损失"
                            and re.fullmatch(r"(?:活跃(?:的)?|当前|新增|新出现(?:的)?)(?:供应|供给)中断", match.group())
                            and clause[match.end():].startswith("公告")):
                        # The immediately qualified noun describes the source
                        # notice, not a metered outage. Separate actual-loss
                        # assertions in the same clause are still checked.
                        continue
                    condition=_invalidation_event_condition(path,clause,match)
                    if condition is True or (condition is None and _physical_claim_is_conditional(clause, match)):
                        continue
                    if kind == "损失" and _future_notice_not_yet_physical_claim(clause,match,context,prepared):
                        continue
                    if kind == "损失" and re.search(r"不可用容量|可用容量|容量风险|capacity", clause[max(0, match.start()-12):match.start()], re.I) and not re.search(r"实际|actual|metered", match.group(), re.I):
                        continue
                    future_notice = False
                    if kind == "损失":
                        for notice in notices:
                            op = notice.get("operational") or {}
                            start = _moment(notice.get("event_start") or op.get("event_start_utc") or op.get("event_start"))
                            future_notice |= bool(op.get("scheduled_for_future") or notice.get("temporal_state") in {"scheduled", "scheduled_notice", "future"} or (start and now and start > now))
                    if future_notice:
                        raise ValueError(f"{path}:Gassco未来维护公告不能写成当前实际停供或流量损失；应写未来容量风险并保留起止时间")
                    supported = False
                    for source in available:
                        # Facility-specific confirmation cannot be borrowed
                        # from an unrelated notice or another country's flows.
                        entities = [m[1].strip() for notice in notices if (m := re.search(r"\bGassco\s+([^:：]{1,45})[:：]", str(notice.get("title", "")), re.I))]
                        source_text = _source_text(source)
                        if entities and not any(entity.casefold() in source_text.casefold() for entity in entities):
                            continue
                        for source_clause in _clauses(source_text):
                            if re.search(r"未提供|缺少|缺乏|待核实|待确认|未核实|未确认|无法确认|是否|不代表|不等于|not confirmed|unverified|no (?:actual |flow )?data", source_clause, re.I):
                                continue
                            if _affirmed_actual_flow(source_clause) and any(s["pressure"] == pressure for s in _matching_mechanisms(source_clause)):
                                supported = True
                    if not supported:
                        raise ValueError(f"{path}:Gassco公告容量/Active/Dismissed/Cancelled或预计结束均不能证明实际流量{kind}；须有输入中的独立计量/实际流量事实，否则写容量风险或恢复待核实")



def _check_notice_entity_production_stop(result: dict, context: dict, prepared: dict) -> None:
    """Bare named production-stop assertions need that entity's real source.

    Unavailability is an announcement about capacity; it does not alone
    establish that the named facility stopped producing. Qualified notice
    descriptions and conditional/future premises retain their stated scope.
    """
    cards=[prepared.get('current_news') or context.get('current_news') or {}]
    cards.extend(row for row in prepared.get('window_news',[]) if isinstance(row,dict))
    now=_moment(context.get('as_of') or prepared.get('as_of'));entities={}
    for row in cards:
        if not _notice_item(row) or not _known_source(row,now):continue
        m=re.search(r'\bGassco\s+([^:：]{1,45})[:：]',str(row.get('title','')),re.I)
        if m:entities.setdefault(m[1].strip(),[]).append(row)
    available=_current_source_rows(context,prepared)
    for entity,notices in entities.items():
        predicate=re.compile(re.escape(entity)+r'\s*(?:已经|现已|已|正在)?(?:停产|停供)',re.I)
        confirmation=re.compile(re.escape(entity)+r'[^。；;，,]{0,20}(?:已确认(?:实际)?(?:停产|停供)|实际(?:生产|产量)[^。；;，,]{0,7}(?:停止|停产|停供)|(?:actual|confirmed)\s+(?:production|flows?)\s+(?:has |have |is |are )?(?:stopped|halted)|(?:has|have)\s+(?:stopped|halted)\s+(?:production|supply))',re.I)
        for path,text in _strings(result):
            if '.scenarios.' in path or 'historical_comparisons' in path:continue
            for clause in _clauses(text):
                for claim in predicate.finditer(clause):
                    before=re.split(r'[，,]|→|->|但是|但|然而',clause[:claim.start()])[-1]
                    after=clause[claim.end():claim.end()+12]
                    if re.match(r'(?:的)?(?:公告|消息|预告|风险|可能性)',after):continue
                    condition=_invalidation_event_condition(path,clause,claim)
                    if condition is True:continue
                    if condition is not False and (re.search(r'若|如果|假设|假如|一旦|未来|预计|预期|计划|将|可能|不能证明|不代表|不等于|是否',before)
                            or _physical_claim_is_conditional(clause,claim)):
                        continue
                    supported=False
                    for source in available:
                        if not _known_source(source,now):continue
                        for source_clause in _clauses(_source_text(source)):
                            for observed in confirmation.finditer(source_clause):
                                local=re.split(r'[，,]|但是|但|然而',source_clause[:observed.start()])[-1]+observed.group()
                                if re.search(r'若|如果|预计|预期|计划|未来|将|可能|未确认|未核实|无法确认|是否|并非|不是|(?:未|没有|并未|不曾|从未)(?:实际|完全)?(?:停止|停产|停供)|\bif\b|unconfirmed|expected|planned|will|not\s+(?:actually\s+)?(?:stopped|halted)',local,re.I):continue
                                supported=True
                    if not supported:
                        raise ValueError(f'{path}:{entity}输入仅有该设施不可用容量公告且实流未知，不能裸称设施停产/停供已发生；可写停产公告/公告容量风险、若/未来条件，实际停产须该实体截至as_of已知的独立生产/流量确认，不改变净方向')

def _check_notice_settlement_timing(result: dict, context: dict, prepared: dict, source_index: list[dict] | None = None) -> None:
    cards={row.get('id'):row for row in prepared.get('window_news',[]) if isinstance(row,dict) and row.get('id')}
    cards['N0']=prepared.get('current_news') or context.get('current_news') or {}
    for alias,row in list(cards.items()):
        if row.get('same_as_current_news_id') in cards:cards[alias]=cards[row['same_as_current_news_id']]
    session=prepared.get('session') or context.get('session') or {}
    now=prepared.get('as_of') or context.get('as_of')
    known_at=_moment(now);window_end=_moment(session.get('window_end') or session.get('cutoff_utc'))
    future_knowledge=re.compile(r'截至(?:(?:本|当前|目标)(?:次|交易|结算)?)?窗口(?:结束|截止)(?:时|之时)?[^，,。；;]{0,8}?(?:(?:尚未|仍未|未|无法)(?:能)?(?:确认|核实|验证)|(?:状态|情况)?(?:仍|尚)?未知)|截至(?:(?:本|当前|目标|当日)(?:日)?)?(?:收盘|结算)(?:时点|时间|时)?[^，,。；;]{0,8}?(?:(?:尚未|仍未|未|无法)(?:能)?(?:确认|核实|验证)|(?:状态|情况)?(?:仍|尚)?未知)')
    current_window=re.compile(r'(?:本|当前)(?:次|交易|结算)?窗口(?:内|中)?')
    capacity_change=re.compile(r'(?:可用(?:容量|产能)|容量|产能)(?:分别)?(?:已经|已|现已)?(?:下降|减少|降低|损失|受限)')
    evidence_aliases={alias for path,text in _strings(result) if 'evidence' in path
        for alias in re.findall(r'(?<![A-Za-z0-9_])N\d+(?![A-Za-z0-9_])',text)}
    patterns=[('after',re.compile(r'(?:本|目标|当前)(?:次|交易|结算)?窗口(?:之后|结束后)|(?:本|当日|目标)(?:日)?(?:收盘|结算)(?:时点)?之后')),
              ('inside',re.compile(r'(?:在|于)(?:本|目标|当前)(?:次|交易|结算)?窗口(?:内|中)(?:开始|生效)')),
              ('started',re.compile(r'(?:已经|现已|已)(?:正式)?(?:开始|生效|启动)(?:维护|检修)?'))]
    connective=re.compile(r'(?:[\s\[\]、，,与和及或&/]|and|or)*',re.I)
    def sources_before(clause,claim):
        groups=[];previous=None
        for alias in re.finditer(r'(?<![A-Za-z0-9_])N\d+(?![A-Za-z0-9_])',clause[:claim.start()]):
            if previous and connective.fullmatch(clause[previous.end():alias.start()]):groups[-1][1].append(alias.group())
            else:groups.append((alias.start(),[alias.group()]))
            previous=alias
        prefix=clause[:claim.start()];named=[]
        local_start=max(prefix.rfind('，'),prefix.rfind(','),prefix.rfind('；'),prefix.rfind(';'))+1
        collective=re.search(r'两项|两条|两处|两份|均|都|全部|所有',prefix[local_start:])
        if groups:
            selected_groups=[group for position,group in groups if position>=local_start] if collective else [groups[-1][1]]
            aliases=[alias for group in selected_groups for alias in group]
            return [(alias,cards[alias]) for alias in aliases if alias in cards and _notice_item(cards[alias])]
        for alias,source in cards.items():
            entity=re.search(r'\bGassco\s+([^:：]{1,45})[:：]',str(source.get('title','')),re.I)
            if not entity:continue
            name=entity[1].strip();position=prefix.casefold().rfind(name.casefold())
            if position>=0:named.append((position,name,alias,source))
        if named:
            latest=max(position for position,_,_,_ in named)
            rows=[(alias,source) for position,_,alias,source in named
                  if position==latest or collective and position>=local_start]
            # A same-facility single notice without an ID is ambiguous. An
            # explicit two/all-notices claim does bind the known pair.
            if len(rows)>1 and not re.search(r'两项|两条|两处|两份|均|都|全部|所有',prefix[latest:]):return []
            return rows
        return [('N0',cards['N0'])] if _notice_item(cards['N0']) else []
    def original_source(alias):
        # Alias order and same-facility names are not source identities.
        indexed=[row for row in (source_index or []) if row.get('alias')==alias and row.get('id')]
        if len(indexed)!=1:return None
        entry=indexed[0]
        originals=[row for row in [context.get('current_news') or {},*(context.get('window_news') or [])]
            if isinstance(row,dict) and row.get('id')==entry['id']
            and (entry.get('version') is None or row.get('version')==entry['version'])
            and _known_source(row,known_at)]
        return originals[0] if len(originals)==1 else None
    def complete_notice_identity(row):
        if not isinstance(row,dict) or not str(row.get('title') or '').strip():return None
        text=next((row.get(k) for k in ('summary','body','fact_excerpt','content','facts')
                   if isinstance(row.get(k),str) and row[k].strip()),None)
        # A shared reason/remark digest is not a complete notice identity.
        if not text or not re.search(r'\bMessage ID:\s*\S+',text,re.I) or not re.search(r'\bEvent:\s*\d{4}-\d{2}-\d{2}T',text,re.I):
            return None
        return row['title'],text
    for path,text in _strings(result):
        if '.scenarios.' in path:continue
        for clause in _clauses(text):
            if known_at is not None and window_end is not None and known_at<window_end:
                for claim in future_knowledge.finditer(clause):
                    before=re.split(r'[，,]|→|->|但是|但|因此',clause[:claim.start()])[-1]
                    if (_invalidation_event_condition(path,clause,claim) is True
                            or re.search(r'若|如果|假设|假如|一旦|预计|预期|可能|是否|不能(?:说|认定|断言|声称)|不应(?:说|认定|断言|声称)|无法(?:得知|确认|判断)|不能证明|不代表|不等于',before)):
                        continue
                    raise ValueError(f'{path}:目标结算窗口结束{window_end.isoformat()}仍晚于as_of{known_at.isoformat()}，不能把截至未来窗末的确认/核实状态写成已知事实；只能陈述截至分析时点尚未确认，或明确若截至收盘仍未确认的条件，不改变预测方向')
            for claim in capacity_change.finditer(clause):
                prefix=clause[:claim.start()]
                windows=list(current_window.finditer(prefix))
                if not windows or _invalidation_event_condition(path,clause,claim) is True:
                    continue
                local=re.split(r'[，,]|→|->|但是|但|然而',prefix)[-1]
                if re.search(r'若|如果|假设|假如|一旦|是否|无法确认|不能确认|不能(?:说|认定|断言)|不代表|不等于',re.split(r'但是|但|然而',prefix)[-1]):
                    continue
                window_scope=prefix[windows[-1].end():]
                # The window can date publication of a future plan rather
                # than the capacity becoming unavailable in that window.
                if (re.search(r'届时',local) and re.search(r'预计|预测|公布|发布|披露|收到|更新|修订',window_scope)):
                    continue  # The window dates forecasting/publication; 届时 dates the capacity.
                if (re.search(r'(?:公布|发布|披露|收到|更新|修订)[^。；;]*(?:未来|将于|计划于|预定)',window_scope)
                        and not re.search(r'但是|但|然而|目前|当前|现已|已经|实际',window_scope)):
                    continue
                sources=sources_before(clause,claim)
                if not re.search(r'(?<![A-Za-z0-9_])N\d+(?![A-Za-z0-9_])',prefix) and re.search(r'及其他|与其他|两项|两条|两处|分别|均|都',prefix):
                    cited=[(alias,cards[alias]) for alias in sorted(evidence_aliases)
                           if alias in cards and _notice_item(cards[alias])]
                    if cited:sources=cited
                for alias,source in sources:
                    if not _known_source(source,known_at):continue
                    timing_source=source
                    timing=notice_settlement_timing(source,session,now)
                    if not timing:
                        # Exact original source ID/version lookup precedes
                        # comparing the uncompressed notice, never its digest.
                        identity=complete_notice_identity(original_source(alias))
                        matches=[]
                        if identity:
                            for other_alias in cards:
                                original=original_source(other_alias)
                                if (complete_notice_identity(original)==identity
                                        and notice_settlement_timing(original,session,now)):
                                    matches.append(original)
                        intervals={(_moment(row.get('event_start') or (row.get('operational') or {}).get('event_start_utc')),
                                    _moment(row.get('event_end') or (row.get('operational') or {}).get('event_end_utc'))) for row in matches}
                        if matches and len(intervals)==1 and all(start and end and end>start for start,end in intervals):
                            timing_source=matches[0]
                            timing=notice_settlement_timing(timing_source,session,now)
                    if timing.get('as_of_relation')!='not_started':continue
                    prospective=bool(re.search(r'将|预计|预期|可能|届时',re.split(r'但是|但|然而',prefix)[-1])
                        and not re.search(r'已经|现已|已',claim.group()))
                    if timing.get('start_vs_target_window')=='starts_inside_target_window' and prospective:
                        continue
                    op=timing_source.get('operational') or {}
                    stamp=timing_source.get('event_start') or op.get('event_start_utc')
                    raise ValueError(f'{path}:{alias}公告开始{stamp}，{timing["description"]}；不能把该未来公告的容量下降写成本窗口内已发生的物理容量变化。窗口内公布计划不等于计划已生效；可分别陈述该N起止与容量安排，并讨论未来价格预期，不证明实际流量损失或指定净方向')
            for kind,pattern in patterns:
                for claim in pattern.finditer(clause):
                    if _invalidation_event_condition(path,clause,claim) is True:
                        continue  # A stated failure criterion is not today's timing.
                    before=re.split(r'[，,]|→|->|但是|但|因此',clause[:claim.start()])[-1]
                    after=clause[claim.end():claim.end()+10]
                    if kind in {'after','inside'} and (re.match(r'(?:才|将)?(?:结束|终止|届满|完成)',after)
                            or re.search(r'(?:结束|终止|截止|届满)(?:时间|时刻)?(?:在|于)?$',before)):
                        continue  # A start inside and an end after cutoff can both be true.
                    if re.search(r'若|如果|假设|假如|一旦|是否|不能确认|无法确认|不代表|不等于|不能证明|不能(?:说|认定|断言|声称)|并非|不是',before):continue
                    for alias,source in sources_before(clause,claim):
                        if not _known_source(source,_moment(now)):continue
                        timing=notice_settlement_timing(source,session,now)
                        relation=timing.get('start_vs_target_window')
                        wrong=(kind=='after' and relation in {'starts_inside_target_window','starts_before_target_window'}
                               or kind=='inside' and relation in {'at_or_after_target_window_end','starts_before_target_window'}
                               or kind=='started' and timing.get('as_of_relation')=='not_started')
                        if wrong:
                            op=source.get('operational') or {}
                            stamp=source.get('event_start') or op.get('event_start_utc')
                            raise ValueError(f'{path}:{alias}公告开始{stamp}，{timing["description"]}；未来于as_of不等于目标窗口之后，须按该N起止和目标窗口分别判断，不改变价格方向')


def _check_elapsed_notice_current_mechanism(result: dict, context: dict, prepared: dict) -> None:
    """An expired capacity interval needs a residual channel, not a label.

    Immutable notice prefixes describe their historical scope. They must not
    turn a bare current-risk mechanism into a qualified residual-flow claim.
    Only explicitly cited notice IDs are checked, never a same-name sibling.
    """
    cards={row.get('id'):row for row in prepared.get('window_news',[]) if isinstance(row,dict) and row.get('id')}
    cards['N0']=prepared.get('current_news') or context.get('current_news') or {}
    for alias,row in list(cards.items()):
        if row.get('same_as_current_news_id') in cards:
            cards[alias]=cards[row['same_as_current_news_id']]
    now=_moment(context.get('as_of') or prepared.get('as_of'))
    available=_current_source_rows(context,prepared)
    risk=re.compile(r'(?:供应|供给|供气)(?:约束|受限|减少|中断|短缺|缺口)|容量(?:约束|受限)|(?:上行|看涨|上涨)(?:压力|风险)?|推高|潜在供应|supply\s+(?:constraint|risk|disruption)|bullish|upward',re.I)
    reference=re.compile(r'(?<!\w)N\d+(?!\w)')
    connective=re.compile(r'(?:[\s\[\]、，,与和及&/]|and|or)*',re.I)
    for path,statement in _strings(result):
        if not (path.startswith('news_impact.bullish_evidence[') or path.startswith('analysis.evidence[')):
            continue
        matches=list(reference.finditer(statement));groups=[];previous=None
        for match in matches:
            if previous and connective.fullmatch(statement[previous.end():match.start()]):
                groups[-1][1].add(match.group())
            else:
                groups.append((match.start(),{match.group()}))
            previous=match
        for index,(start,aliases) in enumerate(groups):
            segment=statement[start:groups[index+1][0] if index+1<len(groups) else len(statement)]
            if 'TTF机制：' in segment:
                mechanism=segment.rsplit('TTF机制：',1)[-1]
            elif re.search(r'→|->',segment):
                mechanism=re.split(r'→|->',segment)[-1]
            else:
                mechanism=segment
            if not risk.search(mechanism):
                continue
            # A mechanism about the past or explicitly contingent on current
            # residual flows is not an assertion that the old capacity lasts.
            historical=bool(re.search(r'历史背景|历史比较|此前|当时|历史时段|past\s+(?:background|interval)|historical',mechanism,re.I)
                            and not re.search(r'当前|目前|今天|今日|仍然|继续推高|current|still',mechanism,re.I))
            residual_condition=bool(re.search(r'(?:若|如果|假设|一旦)[^。；;]{0,25}(?:独立|实际|计量)[^。；;]{0,12}(?:流量|供气)[^。；;]{0,18}(?:仍|继续|持续|受限|减少|未恢复|低于)',mechanism)
                                    or re.search(r'(?:残余|后续)[^。；;]{0,12}(?:风险|压力)[^。；;]{0,12}(?:取决于|有赖于|需核实)[^。；;]{0,18}(?:实际|计量|独立)[^。；;]{0,10}(?:流量|供气)',mechanism)
                                    or re.search(r'(?:当前|实际)(?:供应|供气|流量)?(?:约束|受限)(?:是否|能否)(?:仍|继续|持续)[^。；;]{0,12}(?:待核实|未知)',mechanism))
            if historical or residual_condition:
                continue
            for alias in sorted(aliases):
                notice=cards.get(alias)
                if not notice or not _notice_item(notice) or not _known_source(notice,now):
                    continue
                op=notice.get('operational') or {}
                end=_moment(notice.get('event_end') or op.get('event_end_utc') or op.get('event_end'))
                ended=bool(op.get('announced_end_passed') or notice.get('temporal_state')=='announced_end_elapsed_unverified_flow' or end and now and end<=now)
                if not ended:
                    continue
                entity=re.search(r'\bGassco\s+([^:：]{1,45})[:：]',str(notice.get('title','')),re.I)
                supported=False
                for source in available:
                    text=_source_text(source)
                    observed=_moment(source.get('observed_at') or source.get('measured_at') or source.get('measurement_at') or source.get('published_at') or source.get('time'))
                    if (not entity or entity[1].strip().casefold() not in text.casefold()
                            or end is None or observed is None or observed<=end or now and observed>now):
                        continue
                    for clause in _clauses(text):
                        if (_affirmed_actual_flow(clause) and not _CONDITIONAL.search(clause)
                                and any(signal['pressure']=='up' for signal in _matching_mechanisms(clause))):
                            supported=True
                if not supported:
                    raise ValueError(f'{path}:{alias}公告时段已过，前缀当前未知不证明残余约束；机制不能裸写供应约束作为当前看涨依据。须明确独立实流仍受限的条件/结束后已知残余流量证据，或仅列历史背景；不推断恢复、不改净方向')


def _check_source_fact_projection(result: dict, prepared: dict) -> None:
    if result.get('source_fact_checks') is not None:
        expected = source_fact_projection(prepared)
        canonical = lambda value:json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))
        observed=result['source_fact_checks']
        if expected is not None and isinstance(observed,dict) and 'fact_contract_version' not in observed:
            # Immutable <=2.1 stored tables remain readable under their exact
            # old factual projection. Fresh2.2 decoding requires the new const.
            legacy=copy.deepcopy(expected)
            for key in ('fact_contract_version','non_overlapping_notice_ids'):
                legacy.pop(key,None)
            for row in legacy.get('operator_notices',[]):
                row.pop('period_utc',None);row.pop('facility_type',None)
            legacy.get('pricing_observation',{}).pop('pricing_status',None)
            expected=legacy
        if expected is None or canonical(observed)!=canonical(expected):
            raise ValueError('source_fact_checks与本次已知输入的公告/行情事实表不一致；须逐项复制输入原值，不能修改类型、时态或把行情变化当公告已计价')


def _check_observed_absence_of_price_reaction(result: dict, context: dict, prepared: dict) -> None:
    """Observed no reaction differs from a weak-pressure model judgement."""
    reference=_known_price_reference(context,prepared)
    if reference.get('announcement_price_reaction') or reference.get('priced_in_evidence'):
        return
    modifier=r'(?:(?:任何|实际|可观测|可观察|可观|可测|可量化|明显|显著|即时|即刻|当前|当日|的)\s*){0,5}'
    patterns=[re.compile(r'(?:未|没有|并未|并没有)(?:能)?(?:产生|出现|引发|造成|带来|导致)'+modifier+r'(?:TTF价格|价格|气价|市场)(?:反应|变动|变化|波动|冲击)',re.I),
              re.compile(r'(?:TTF价格|价格|气价)(?:未|没有|并未)(?:产生|出现|发生)'+modifier+r'(?:反应|变动|变化|波动)',re.I),
              re.compile(r'(?:未|没有|并未)对(?:当前|当日)?(?:欧洲(?:天然气)?市场|TTF市场|TTF价格|价格|气价)(?:产生|造成|带来)(?:任何|实际|的)?(?:可观测|可观察|可观|可测|可量化)(?:的)?影响',re.I),
              re.compile(r'(?:未|没有|并未)(?:能)?(?:导致|造成|引发|产生|带来)(?:(?:当前|实际|物理|即时|当日)\s*){0,4}(?:供气|供应|供给|流量)(?:损失|减少|中断|冲击)(?:或|及|和|与|以及)'+modifier+r'(?:TTF价格|价格|气价|市场)(?:反应|变动|变化|波动)',re.I),
              re.compile(r'(?:无|没有)'+modifier+r'(?:TTF价格|价格|气价)(?:反应|变动|变化|波动)',re.I),
              re.compile(r'(?:TTF价格|价格|气价)(?:无|没有)'+modifier+r'(?:反应|变动|变化|波动)',re.I)]
    # "No immediate effect on TTF price yet" is still a definite zero
    # effect, not a lack of observations. Qualifying future judgements and
    # evidence/data objects retain the existing local scope checks below.
    temporary_price_absence=re.compile(r'对(?:当前|当日|现有)?(?:TTF价格|价格|气价)(?:目前|当前|暂时|暂|尚)?(?:无|没有)(?:即时|即刻|当前|当日|实际)?影响',re.I)
    no_effect_on_price=re.compile(r'(?:当前|目前|当日|现阶段)?不影响(?:当前|当日|现有)?(?:TTF价格|价格|气价)',re.I)
    unclear_market_reaction=re.compile(r'(?:市场|TTF价格|价格|气价)(?:对(?:该|此|本)?(?:公告|消息|新闻))?(?:的)?(?:价格)?反应(?:目前|当前|暂时|暂|尚|仍)?(?:不明显|不显著)',re.I)
    patterns.extend((temporary_price_absence,no_effect_on_price,unclear_market_reaction))
    # A price-forecast field can omit the repeated price noun. Only explicit
    # observable-zero predicates acquire that implied object: weak/limited
    # transmission judgements and explicit physical objects do not match.
    implicit_price_observation=re.compile(
        r'(?:(?:未|没有|并未|并没有)(?:能)?(?:产生|出现|引发|造成|带来|导致)|无|没有)'
        r'(?:(?:任何|实际|即时|当前|当日|的)\s*){0,3}'
        r'(?:可观测|可观察|可测|可量化)(?:的|\s){0,2}(?:冲击|影响)')
    physical_object=r'(?:(?:实际|计量|实测|物理|实物)?(?:天然气)?(?:流量|供气|供给|供应|产量|输气量|出口流量|交付量)|实流)'
    cards={row.get('id'):row for row in prepared.get('window_news',[]) if isinstance(row,dict) and row.get('id')}
    cards['N0']=context.get('current_news') or prepared.get('current_news') or {}
    for alias,row in list(cards.items()):
        if row.get('same_as_current_news_id') in cards:cards[alias]=cards[row['same_as_current_news_id']]
    now=_moment(context.get('as_of') or prepared.get('as_of'));invalid=[]
    for path,text in _strings(result):
        if '.scenarios.' in path or 'historical_comparisons' in path:continue
        for clause in _clauses(text):
            claim_patterns=patterns+([implicit_price_observation]
                if path in {'close_forecast.basis','close_forecast.summary'} else [])
            for pattern in claim_patterns:
                for match in pattern.finditer(clause):
                    before=re.split(r'[，,]|→|->|但是|但|因此',clause[:match.start()])[-1]
                    after=clause[match.end():match.end()+20]
                    if pattern in (temporary_price_absence,no_effect_on_price,unclear_market_reaction):
                        if pattern in (no_effect_on_price,unclear_market_reaction) and re.search(r'(?:没有|无)(?:任何)?证据(?:表明|证明|说明)\s*$',before):continue
                        if pattern is unclear_market_reaction and re.search(r'未见|尚未观察到|尚未看到',before):continue
                        full_before=clause[:match.start()]
                        if (re.match(r'^\s*(?:\[[NE]\d+\]\s*)?(?:若|如果|假设|假如|一旦|if\b)',clause,re.I)
                                and not re.search(r'但是|但|然而|不过|\bbut\b|\bhowever\b',full_before,re.I)):
                            continue  # Hypothetical consequent, not an observed price state.
                        if re.search(r'(?:并非|不是)\s*$',before):continue
                    if pattern is implicit_price_observation:
                        scoped_object=re.search(r'对(?P<object>[^。；;，,]{1,35})$',before)
                        if scoped_object and re.fullmatch(physical_object+r'(?:(?:及|与|和|、|以及)'+physical_object+r'){0,3}',scoped_object['object']):
                            continue  # The explicit object is physical, not an implicit price.
                    if re.search(r'若|如果|假设|假如|一旦|预计|预期|可能|无法确认|未确认|不能确认|是否|不能证明|不代表|不等于|不能(?:认定|宣称|说)',before):continue
                    if re.match(r'(?:的)?(?:证据|数据|观测|资料|记录)',after):continue
                    aliases=re.findall(r'(?<![A-Za-z0-9_])N\d+(?![A-Za-z0-9_])',clause)
                    sources=[cards[alias] for alias in aliases if alias in cards] if aliases else [cards['N0']]
                    supported=False
                    for source in sources:
                        if not _known_source(source,now):continue
                        for source_clause in _clauses(_source_text(source)):
                            # "无价格反应" and "价格无反应" are equivalent
                            # observed predicates; a source need not copy the
                            # generated word order. Evidence-absence is not a
                            # zero-reaction observation in either order.
                            for source_pattern in patterns:
                                for observed in source_pattern.finditer(source_clause):
                                    if (not re.search(r'未提供|缺少|缺乏|是否|若|如果|预计|预期|可能|无法确认|未确认|不能证明|不代表|不等于',source_clause[:observed.start()])
                                            and not (source_pattern is unclear_market_reaction and re.search(r'未见|尚未观察到|尚未看到|(?:没有|无)(?:任何)?证据(?:表明|证明|说明)\s*$',source_clause[:observed.start()]))
                                            and not re.match(r'(?:的)?(?:证据|数据|观测|资料|记录)',source_clause[observed.end():])):
                                        supported=True
                    if not supported and path not in invalid:invalid.append(path)
    if invalid:
        raise ValueError(f'{"、".join(invalid)}:输入未提供该消息的独立价格反应观测，不能断言未产生可观测价格反应/即时价格变动或可测市场影响；未见资料不等于观测为零。可保留弱传导/影响有限判断并明确反应未知，单个报价或消息前报价不是公告反应')


def _check_contract_party_roles(result: dict, context: dict, prepared: dict) -> None:
    from .source_facts import award_roles,normalized_party
    cards={row.get('id'):row for row in prepared.get('window_news',[]) if isinstance(row,dict) and row.get('id')}
    cards['N0']=context.get('current_news') or prepared.get('current_news') or {}
    for alias,row in list(cards.items()):
        if row.get('same_as_current_news_id') in cards:cards[alias]=cards[row['same_as_current_news_id']]
    now=_moment(context.get('as_of') or prepared.get('as_of'))
    def party_pattern(name):
        names=[normalized_party(name)]
        if names[0]=='tecnicas reunidas':names.append('reunidas')
        return r'(?<![a-z0-9])(?:'+'|'.join(r'\s*'.join(map(re.escape,value.split())) for value in names)+')'
    for path,text in _strings(result):
        if '.scenarios.' in path or 'historical_comparisons' in path:continue
        for clause in _clauses(text):
            aliases=re.findall(r'(?<!\w)N\d+(?!\w)',clause)
            selected=[(alias,cards[alias]) for alias in aliases if alias in cards] if aliases else [('N0',cards['N0'])]
            normalized=normalized_party(clause)
            for alias,source in selected:
                if not _known_source(source,now):continue
                roles=(source.get('source_reported_facts') or {}).get('contract_party_roles') or award_roles(source)
                recipients={normalized_party(role['recipient']) for role in roles}
                grantors={normalized_party(role['grantor']) for role in roles}
                for role in roles:
                    give,recv=role['grantor'],role['recipient']
                    # The same source may describe distinct contracts where a
                    # company has both roles; do not join those relationships.
                    if normalized_party(give) in recipients or normalized_party(recv) in grantors:continue
                    wrong_receive=re.search(party_pattern(give)+r'\s*(?:获授|获颁|获得|承接|拿下|赢得|received|(?:has|have)\s+(?:secured|won)|(?:was|is|has been)\s+awarded)[^。；;，,]{0,65}(?:合同|EPCM|委托|contract|assignment|deal)',normalized,re.I)
                    wrong_give=re.search(party_pattern(recv)+r'\s*(?:向)?\s*(?:授予|委托|awarded|entrusts|entrusted)[^。；;，,]{0,30}'+party_pattern(give),normalized,re.I)
                    match=wrong_receive or wrong_give
                    if match and not re.search(r'若|如果|假设|假如|一旦|不能|不得|并非|不代表|不等于',normalized[:match.start()]):
                        raise ValueError(f'{path}:{alias}原来源合同授予/委托方为{give}、获授/承接方为{recv}，不能把授予方写成获授方或反转双方角色；请保留该N具体交易，不套用其他N的合同，不要求复述角色或改变价格方向')



def _price_unit_identity(unit: str) -> tuple[str,str] | None:
    """Normalize spelling only; this is not a volume/energy conversion."""
    value=re.sub(r"\s+","",str(unit)).casefold().replace("per","/")
    value=value.replace("€","eur").replace("欧元","eur").replace("美元","usd").replace("英镑","gbp").replace("£","gbp").replace("$","usd")
    value=value.replace("兆瓦时","mwh").replace("千瓦时","kwh").replace("百万立方米","mcm").replace("立方米","m3").replace("m³","m3")
    match=re.fullmatch(r"(eur|usd|gbp|gbpence)/(mwh|kwh|mmbtu|therm|mcm|m3|mw)",value)
    return (match[1],match[2]) if match else None


def _check_ttf_price_unit_identity(result: dict, context: dict, prepared: dict) -> None:
    """An observed TTF price/delta keeps its supplied monetary-energy unit."""
    reference=_known_price_reference(context,prepared)
    quote_rows=[reference,reference.get('intraday') or {},reference.get('market_baseline') or {}]
    units={identity for row in quote_rows if isinstance(row,dict) and (identity:=_price_unit_identity(row.get('unit','')))}
    # Compression may omit unit from a retained observation. Its immutable
    # source unit is usable only when the exact same known quote survived,
    # never by recovering a filtered/future quote from the original snapshot.
    original=(context.get('price_reference') or {}).get('intraday') or {}
    retained=reference.get('intraday') or {}
    now=_moment(context.get('as_of') or prepared.get('as_of'))
    same_quote=(isinstance(original,dict) and isinstance(retained,dict)
        and original.get('contract') and all(original.get(key)==retained.get(key)
            for key in ('contract','value','observed_at','available_at'))
        and retained.get('value') is not None and retained.get('observed_at')
        and _known_source(original,now)
        and not any((stamp:=_moment(original.get(key))) is not None and now is not None and stamp>now
                    for key in ('fetched_at','known_at','price_known_at')))
    if same_quote and (identity:=_price_unit_identity(original.get('unit',''))):units.add(identity)
    # Only apply an identity contract when the supplied quote has an
    # unambiguous unit. Missing/filtered quotes do not invent a default unit.
    if len(units)!=1:return
    expected=next(iter(units))
    amount_unit=re.compile(r"(?P<amount>[+-]?\d+(?:\.\d+)?)\s*(?P<unit>(?:EUR|USD|GBP|€|£|\$|欧元|美元|英镑)\s*(?:/|per)\s*(?:MWh|kWh|MMBtu|therm|mcm|m3|m³|MW|百万立方米|立方米|兆瓦时|千瓦时))(?![A-Za-z0-9])",re.I)
    instrument=re.compile(r'(?P<ttf>TTF|荷兰天然气)|(?P<foreign>JKM|NBP|Henry\s+Hub|亨利港|英国天然气)',re.I)
    cards={row.get('id'):row for row in prepared.get('window_news',[]) if isinstance(row,dict) and row.get('id')}
    cards['N0']=context.get('current_news') or prepared.get('current_news') or {}
    for alias,row in list(cards.items()):
        if row.get('same_as_current_news_id') in cards:cards[alias]=cards[row['same_as_current_news_id']]
    known=_current_source_rows(context,prepared)
    quote_values=[reference.get('previous_price'),(reference.get('intraday') or {}).get('value')]
    quote_values.extend((reference.get('market_baseline') or {}).get(k) for k in ('previous_price','latest_price','observed_delta'))
    def source_rate_supported(source_topic,amount,identity,clause):
        aliases=re.findall(r'(?<![A-Za-z0-9_])N\d+(?![A-Za-z0-9_])',clause)
        sources=[cards[a] for a in aliases if a in cards] if aliases else known
        for source in sources:
            if not _known_source(source,now):continue
            for source_clause in _clauses(_source_text(source)):
                if not re.search(source_topic,source_clause,re.I):continue
                for supplied in amount_unit.finditer(source_clause):
                    if (_price_unit_identity(supplied['unit'])==identity and abs(float(supplied['amount'])-amount)<1e-9
                            and not re.search(r'若|如果|假设|预计|预期|可能|未提供|缺少|无法确认|whether|\bif\b|expected|forecast',source_clause[:supplied.start()],re.I)):
                        return True
        return False
    for path,text in _strings(result):
        for clause in _clauses(text):
            for match in amount_unit.finditer(clause):
                local=re.split(r'[，,；;]|→|->|但是|但',clause[:match.start()])[-1]
                instruments=list(instrument.finditer(local))
                scope=instruments[-1] if instruments else None
                observed=_price_unit_identity(match['unit'])
                if observed is None or observed==expected:continue
                cost_topic=r'transport(?:ation)?\s+(?:tariff|cost)|pipeline\s+tariff|tariff|输气(?:成本|费用|费率|资费)|运输(?:成本|费用|费率)|管道(?:成本|费用|费率|资费)'
                costs=list(re.finditer(cost_topic,local,re.I))
                price_nouns=list(re.finditer(r'气价|价格|报价|结算价|收盘价|price|quote',local,re.I))
                cost_object=bool(costs and (not price_nouns or costs[-1].start()>price_nouns[-1].start()))
                if cost_object and source_rate_supported(cost_topic,float(match['amount']),observed,clause):
                    continue  # A sourced transport cost is not a TTF quote.
                # A distinct quoted market retains its own unit, backed by
                # that cited/known instrument's observation, not the TTF unit.
                if scope is not None and scope['foreign']:
                    if (source_rate_supported(re.escape(scope['foreign']),float(match['amount']),observed,clause)
                            or re.search(r'若|如果|假设|一旦|\bif\b',local,re.I)):
                        continue
                    raise ValueError(f'{path}:不同市场{scope["foreign"]}报价{match.group()}须有该市场截至as_of已知的同单位来源，不能借TTF报价补其价格或单位；同句后续TTF价格仍须保留输入能源单位')
                exact_quote_amount=any(isinstance(value,(int,float)) and not isinstance(value,bool)
                    and abs(float(match['amount'])-value)<1e-9 for value in quote_values)
                price_scope=(bool(scope is not None and scope['ttf']) or exact_quote_amount
                    or bool(re.search(r'(?:本|该|此)合约|参考价|市场基准|收盘(?:价)?|结算(?:价)?|价格(?:上升|上涨|下降|下跌)|TTF|气价',local,re.I)))
                if not price_scope:continue
                # A warning not to substitute units is not a price assertion.
                if re.search(r'(?:单位|计量口径)(?:并非|不是|不应为|不能为)\s*$|(?:不能|不得)(?:写成|当作|改成|换成)\s*$',local):continue
                raise ValueError(f'{path}:TTF报价/参考变动单位必须保留输入{expected[0].upper()}/{expected[1]}，不能写成{match["unit"]}；能源报价不是体积容量，未经已给换算依据不得替换货币或分母。不改变预测方向')


def _analysis_semantic_checks(result: dict, context: dict, prepared: dict, *, source_index: list[dict] | None = None) -> Iterable[tuple[Any, tuple]]:
    """One ordered gate inventory for rejection and complete diagnostics."""
    yield _check_source_fact_projection,(result,prepared)
    yield _check_ttf_price_unit_identity,(result,context,prepared)
    yield _check_priced_in,(result,context,prepared)
    yield _check_pricing_unknown_not_directional_evidence,(result,)
    yield _check_unobserved_announcement_reaction,(result,context,prepared)
    yield _check_observed_absence_of_price_reaction,(result,context,prepared)
    yield _check_contract_party_roles,(result,context,prepared)
    yield _check_conditional_pricing_consequence,(result,context,prepared)
    yield _check_known_flow_quantity_not_claimed_missing,(result,context,prepared)
    yield _check_known_price_not_claimed_missing,(result,context,prepared)
    yield _check_target_previous_settlement_anchor,(result,context,prepared)
    yield _check_gassco_physical_state,(result,context,prepared)
    yield _check_notice_entity_production_stop,(result,context,prepared)
    yield _check_notice_settlement_timing,(result,context,prepared,source_index)
    yield _check_elapsed_notice_current_mechanism,(result,context,prepared)
    yield _check_missing_is_not_zero_impact,(result,)
    yield _check_unknown_as_fact,(result,context,prepared)
    yield _check_supply_demand_proportions,(result,context,prepared)
    yield _check_prospective_supply_scope,(result,context,prepared)
    yield _check_current_project_startup_scope,(result,context,prepared)
    yield _check_future_well_action_not_completed,(result,context,prepared)
    yield _check_notice_facility_pipeline_type,(result,context,prepared)
    yield _check_weather_demand,(result,context,prepared)
    yield _check_directional_conditions,(result,)
    yield _check_supply_risk_buckets,(result,context,prepared)
    yield _check_evidence_buckets,(result,)
    yield _check_source_bound_evidence_slot_prices,(result,context,prepared)
    yield _check_quantity_arrow_scope,(result,context,prepared)
    yield _check_neutral_capacity_risk_bucket,(result,context,prepared)
    yield _check_unverified_capacity_risk_exclusion,(result,context,prepared)
    yield _check_mixed_notice_type_aggregation,(result,context,prepared)
    yield _check_cited_mechanism,(result,context,prepared)
    for path, statement in _strings(result):
        yield _check_causal_statement,(path,statement)
    yield _check_summary_direction,(result,)
    yield _check_one_sided_actual_flow,(result,context,prepared)
    yield _check_observed_baseline,(result,context,prepared)


def collect_analysis_semantic_errors(result: dict, context: dict, prepared: dict, *, source_index: list[dict] | None = None) -> list[str]:
    """Collect independent existing gate failures without changing any input.

    A first error must not hide a separate supply-direction contradiction from
    the bounded repair request. This has exactly the validator's acceptance
    set; it neither adds a rule nor repairs any failed model evidence.
    """
    errors=[]
    for check,args in _analysis_semantic_checks(result,context,prepared,source_index=source_index):
        try:
            check(*args)
        except ValueError as exc:
            message=str(exc)
            if message not in errors:
                errors.append(message)
    return errors


def validate_analysis_semantics(result: dict, context: dict, prepared: dict, *, source_index: list[dict] | None = None) -> None:
    """Reject known semantic contradictions, leaving all outputs untouched.

    Call on the raw parsed model object *before* deterministic evidence
    materialization and again after factual/reference normalization. ValueError
    is suitable for the caller's bounded retry/failure flow. These deterministic
    checks are regression invariants, not a substitute for shadow evaluation.
    """
    for check,args in _analysis_semantic_checks(result,context,prepared,source_index=source_index):
        check(*args)
