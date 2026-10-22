#!/usr/bin/env python3
"""Read-only preservation and offline logic verifier for the TTF repair.

No model/network calls and no database writes. Model records, failed pilots,
and subsequent overrides remain separate evidence. Run with the project's
Python environment; --self-test uses only a temporary synthetic database.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import math
import re
import sqlite3
import statistics
import sys
import unittest
import xml.etree.ElementTree as ET
from collections import Counter
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PROTECTED_FIELDS = ("input_snapshot", "result", "detail_result")
STAGES = ("candidate-pilot", "candidate-required-status", "candidate-mixed-pilot", "candidate-comparison",
          "original-comparison", "candidate-finalvalidation", "candidate-final-validation",
          "candidate-release-validation", "candidate-production-validation", "candidate-verified-validation",
          "candidate-grounded-validation", "candidate-decoder-validation", "candidate-factchecks-validation",
          "candidate-integrity-validation", "candidate-parser-validation", "candidate-adaptive-validation",
          "candidate-budget-validation", "candidate-scoped-validation", "candidate-symmetric-validation",
          "candidate-scopeguard-validation", "candidate-feedback-validation", "candidate-contract-validation",
          "candidate-binding-validation", "candidate-source-validation", "candidate-provenance-validation",
          "candidate-revision-validation", "candidate-fieldfeedback-validation", "candidate-reaction-validation", "candidate-anchor-validation", "candidate-reviewwording-validation", "candidate-semantic-regeneration-validation", "candidate-price-observation-validation", "candidate-final-tense-validation", "candidate-bounded-output-validation", "candidate-final-output-scope-validation", "candidate-source-feedback-validation", "candidate-risk-polarity-validation", "candidate-physical-unit-validation", "candidate-evidence-slot-validation", "candidate-price-zero-consistency-validation", "candidate-pressure-predicate-validation")
DETAIL_STAGES = ("candidate-final-validation-detail", "candidate-release-validation-detail",
                 "candidate-production-validation-detail", "candidate-verified-validation-detail",
                 "candidate-grounded-validation-detail", "candidate-decoder-validation-detail",
                 "candidate-factchecks-validation-detail", "candidate-integrity-validation-detail",
                 "candidate-parser-validation-detail", "candidate-adaptive-validation-detail",
                 "candidate-budget-validation-detail", "candidate-scoped-validation-detail",
                 "candidate-symmetric-validation-detail", "candidate-scopeguard-validation-detail",
                 "candidate-feedback-validation-detail", "candidate-contract-validation-detail",
                 "candidate-binding-validation-detail", "candidate-source-validation-detail", "candidate-provenance-validation-detail",
                 "candidate-revision-validation-detail", "candidate-fieldfeedback-validation-detail", "candidate-reaction-validation-detail", "candidate-anchor-validation-detail", "candidate-reviewwording-validation-detail", "candidate-semantic-regeneration-validation-detail", "candidate-price-observation-validation-detail", "candidate-final-tense-validation-detail", "candidate-detail-grounding-validation-detail", "candidate-bounded-output-validation-detail", "candidate-final-output-scope-validation-detail", "candidate-source-feedback-validation-detail", "candidate-risk-polarity-validation-detail", "candidate-physical-unit-validation-detail", "candidate-evidence-slot-validation-detail", "candidate-price-zero-consistency-validation-detail", "candidate-pressure-predicate-validation-detail")
CASE_NAMES = {
    "supply_loss_40": "欧洲实际供气减少40",
    "supply_restore_40": "欧洲实际供气恢复增加40",
    "weak_exploration_no_quote": "弱勘探新闻／无报价",
    "warm_heating_demand": "温和天气降低采暖需求",
    "cold_heating_demand": "寒冷天气增加采暖需求",
    "negated_outage": "否定停产消息",
    "cumulative_shock_then_weak": "累计冲击后新增弱新闻",
    "weak_news_observed_market_up": "新闻弱／已观察行情上涨",
    "supplied_conflicting_mechanisms": "输入明确供需冲突",
    "future_planned_notice": "未来计划维护",
    "dismissed_notice_not_verified_recovery": "撤销公告／流量恢复未核实",
    "real_nyhamna": "真实Nyhamna公告",
    "real_eni": "真实Eni新闻",
    "supply_loss_40_with_flat_baseline": "实际供气减少40／参考基准持平",
    "supply_restore_40_with_flat_baseline": "实际供气恢复40／参考基准持平",
}
ANCHOR_CASE_NAMES = {
    'anchor_real_manual_missing_prevday': '真实手动新闻／缺上一交易日价格锚点',
    'anchor_gap_supply_loss_40': '缺价格锚点／当期实际供气减少40',
    'anchor_gap_supply_restore_40': '缺价格锚点／当期实际供气恢复40',
    'anchor_legal_prevday_weak_market_up': '合法前交易日锚点／弱新闻与上涨基准',
}
SEMANTIC_REGENERATION_STAGE = 'candidate-semantic-regeneration-validation'
SEMANTIC_REGENERATION_FIXTURE = 'semantic_regeneration_cases_v2_2.json'
SEMANTIC_REGENERATION_WELL_CASE = 'real_future_well_b1'
PRICE_OBSERVATION_STAGE = 'candidate-price-observation-validation'
FINAL_TENSE_STAGE='candidate-final-tense-validation'
DETAIL_GROUNDING_STAGE='candidate-detail-grounding-validation'
BOUNDED_OUTPUT_STAGE='candidate-bounded-output-validation'
FINAL_OUTPUT_SCOPE_STAGE='candidate-final-output-scope-validation'
SOURCE_FEEDBACK_STAGE='candidate-source-feedback-validation'
RISK_POLARITY_STAGE='candidate-risk-polarity-validation'
PHYSICAL_UNIT_STAGE='candidate-physical-unit-validation'
EVIDENCE_SLOT_STAGE='candidate-evidence-slot-validation'
PRICE_ZERO_CONSISTENCY_STAGE='candidate-price-zero-consistency-validation'
PRESSURE_PREDICATE_STAGE='candidate-pressure-predicate-validation'
BOUNDED_COHORT_STAGES=(BOUNDED_OUTPUT_STAGE,FINAL_OUTPUT_SCOPE_STAGE,SOURCE_FEEDBACK_STAGE,RISK_POLARITY_STAGE,PHYSICAL_UNIT_STAGE,EVIDENCE_SLOT_STAGE,PRICE_ZERO_CONSISTENCY_STAGE,PRESSURE_PREDICATE_STAGE)
CURRENT_COHORT_STAGES = (SEMANTIC_REGENERATION_STAGE,PRICE_OBSERVATION_STAGE,FINAL_TENSE_STAGE,DETAIL_GROUNDING_STAGE,*BOUNDED_COHORT_STAGES)
PATH_EQUIVALENCE_FILE = 'full_path_equivalence_v2_3.json'
PATH_EQUIVALENCE_SCHEMA = 'ttf-complete-path-equivalence-v1'
CASE_NAMES[SEMANTIC_REGENERATION_WELL_CASE] = '真实井源／将封堵弃置的事实时态'
INFERENCE_SOURCE_FILES = ('ttf_news/prompts.py', 'ttf_news/schemas.py', 'ttf_news/analysis.py',
                          'ttf_news/semantics.py', 'ttf_news/evidence.py', 'ttf_news/retrieval.py',
                          'ttf_news/context.py')
# Analysis contains both request construction and post-generation validators.
# Its exact real request data/system/schema comparison below is the input
# boundary; whole-file validator changes remain disclosed independently.
MODEL_INPUT_SOURCE_FILES = tuple(name for name in INFERENCE_SOURCE_FILES if name not in ('ttf_news/analysis.py','ttf_news/semantics.py'))


def sha(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


_PUBLIC_COPY_MAP=ContextVar('ttf_public_copy_map',default=None)


class PublicationManifest:
    """An explicit derived-copy byte map, never an implicit private fallback."""
    def __init__(self,path: Path):
        self.path=path.resolve()
        if not self.path.is_relative_to(ROOT.resolve()) or not self.path.is_file():
            raise ValueError('Publication manifest missing or outside project')
        def unique_object(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise ValueError('Duplicate publication manifest key/path')
                result[key]=value
            return result
        raw=self.path.read_bytes();data=json.loads(raw,object_pairs_hook=unique_object)
        if not isinstance(data,dict) or data.get('schema_version')!='ttf-publication-manifest-v1' or data.get('originals_modified') is not False or data.get('manifest_self_excluded') is not True:
            raise ValueError('Publication manifest schema or original-byte boundary is invalid')
        self.artifacts=data.get('artifacts');self.sha256=sha(raw);self.verified_paths=set()
        if not isinstance(self.artifacts,dict) or not self.artifacts:
            raise ValueError('Publication artifact map is missing')
        for label,row in self.artifacts.items():
            if not isinstance(label,str) or not isinstance(row,dict):raise ValueError('Publication entry malformed')
            self._path(label)
            if not all(re.fullmatch(r'[0-9a-f]{64}',str(row.get(key,''))) for key in ('source_bytes_sha256','public_bytes_sha256')):
                raise ValueError('Publication byte SHA is invalid')
            if (ROOT/label).resolve()==self.path:raise ValueError('Publication manifest cannot map itself')
        # Validate the complete declared map so an unrelated stale/tampered
        # published asset cannot be hidden by only checking one passing proof.
        for label in self.artifacts:self.identity(ROOT/label)

    def _path(self,label: str) -> Path:
        path=Path(label)
        if not label or '\\' in label or path.is_absolute() or str(path)!=label or any(part in ('..','.runtime','.git','original_source','backups') for part in path.parts):
            raise ValueError('Publication path must be exact project-relative identity')
        resolved=(ROOT/path).resolve()
        if not resolved.is_relative_to(ROOT.resolve()):raise ValueError('Publication path escapes project')
        cursor=ROOT/path
        while cursor!=ROOT:
            if cursor.is_symlink():raise ValueError('Publication artifact symlinks are not exact path identities')
            cursor=cursor.parent
        return resolved

    def identity(self,path: Path) -> str:
        resolved=path.resolve()
        if not resolved.is_relative_to(ROOT.resolve()):raise ValueError('Referenced public artifact escapes project')
        label=str(path.absolute().relative_to(ROOT.absolute()))
        if self._path(label)!=resolved:raise ValueError('Referenced public artifact path mismatch')
        row=self.artifacts.get(label)
        if not row or not resolved.is_file():raise ValueError('Referenced artifact is missing from publication map')
        if sha(resolved.read_bytes())!=row['public_bytes_sha256']:raise ValueError('Published artifact bytes differ from publication map')
        self.verified_paths.add(label)
        return row['source_bytes_sha256']


def artifact_source_sha(path: Path) -> str:
    manifest=_PUBLIC_COPY_MAP.get()
    return manifest.identity(path) if manifest is not None else sha(path.read_bytes())


def artifact_sha_matches(path: Path,expected: str | None) -> bool:
    if not isinstance(expected,str) or not re.fullmatch(r'[0-9a-f]{64}',expected):return False
    try:return artifact_source_sha(path)==expected
    except (OSError,ValueError):return False


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return path.name


def report_link(path: str) -> str:
    """A target relative to the generated reports/ Markdown file."""
    value = Path(path)
    try:
        return str(value.relative_to("reports"))
    except ValueError:
        return value.name


def safe(value: Any) -> Any:
    """Do not put local user paths, credentials, or thinking text in reports."""
    if isinstance(value, dict):
        return {key: safe(row) for key, row in value.items()
                if not re.search(r"password|secret|authorization|cookie|api.key|(?:^|_)token$|^thinking$", str(key), re.I)}
    if isinstance(value, list):
        return [safe(row) for row in value]
    if isinstance(value, str):
        value = value.replace(str(ROOT) + "/", "")
        return re.sub(r"(?:/Users/|/home/|/private/)[^\s\"'<>]+", "[local path]", value)
    return value


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def number(value: Any) -> float | None:
    return float(value) if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) else None


def verify_preservation(db: sqlite3.Connection, manifest: dict) -> dict:
    """Compare SQLite TEXT bytes, not JSON-normalized approximations."""
    failures = []
    old = manifest.get("analyses", {})
    rows = {row["id"]: dict(row) for row in db.execute("SELECT id,input_snapshot,result,detail_result FROM analyses")}
    checked = 0
    for identifier, protected in old.items():
        if identifier not in rows:
            failures.append({"kind": "missing_analysis", "id": identifier})
            continue
        for field, expected in protected.items():
            if field not in PROTECTED_FIELDS or expected is None:
                continue
            checked += 1
            current = rows[identifier].get(field)
            actual = sha(current) if current is not None else None
            if actual != expected:
                failures.append({"kind": "protected_field_changed", "id": identifier, "field": field,
                                 "expected_sha256": expected, "actual_sha256": actual})
    snapshots = {row["trade_date"]: dict(row) for row in db.execute("SELECT * FROM daily_snapshots")}
    for day, expected in manifest.get("snapshots", {}).items():
        if snapshots.get(day) != expected:
            failures.append({"kind": "frozen_snapshot_changed", "trade_date": day,
                             "expected": expected, "actual": snapshots.get(day)})
    validation = [dict(row) for row in db.execute("SELECT * FROM metrics WHERE type='validation_started' ORDER BY id")]
    indexed = {row["id"]: row for row in validation}
    for expected in manifest.get("validation_records", []):
        if indexed.get(expected["id"]) != expected:
            failures.append({"kind": "validation_record_changed", "id": expected["id"]})
    original_latest = max(manifest.get("validation_records", []), key=lambda row: row["id"], default=None)
    latest = validation[-1] if validation else None
    baseline_same = latest == original_latest
    if not baseline_same:
        failures.append({"kind": "latest_validation_baseline_changed",
                         "expected_id": (original_latest or {}).get("id"), "actual_id": (latest or {}).get("id")})
    return {"passed": not failures, "protected_analysis_count": len(old), "protected_nonnull_field_count": checked,
            "current_analysis_count": len(rows), "new_analysis_count": len(set(rows) - set(old)),
            "protected_snapshot_count": len(manifest.get("snapshots", {})),
            "new_snapshot_count": len(set(snapshots) - set(manifest.get("snapshots", {}))),
            "original_validation_record_count": len(manifest.get("validation_records", [])),
            "latest_baseline_unchanged": baseline_same,
            "original_baseline": {key: (original_latest or {}).get(key) for key in ("id", "created_at", "type")},
            "failures": failures}


BASELINE_CONTROL_SIGNS = {"supply_loss_40_with_flat_baseline": "up",
                          "supply_restore_40_with_flat_baseline": "down"}


def load_evaluation_contract_revision(evidence_dir: Path, fixture_path: Path, cases: list[dict]) -> dict | None:
    """Apply the explicit symmetric assessment amendment only to its frozen inputs."""
    path = evidence_dir/"evaluation_contract_revision_v1.json"
    if not path.exists():
        return None
    revision = read_json(path)
    if revision.get("revision_id") != "evaluation_contract_revision_v1":
        raise ValueError("Unknown evaluation contract revision")
    if revision.get("comparison_cases_sha256") != sha(fixture_path.read_bytes()):
        raise ValueError("Evaluation revision fixture SHA mismatch; source cases must not be rewritten")
    by_id = {row["id"]: row for row in cases}
    patches = revision.get("cases") or {}
    if set(patches) != set(BASELINE_CONTROL_SIGNS):
        raise ValueError("Evaluation revision may affect only the two additional baseline controls")
    for identifier, sign in BASELINE_CONTROL_SIGNS.items():
        patch, case = patches[identifier], by_id.get(identifier, {})
        original = {"expected_news": sign, "expected_close_sign": sign}
        allowed = {sign, "extreme_"+sign, "flat"}
        if patch.get("original_expectations") != original or any(case.get(key) != value for key, value in original.items()):
            raise ValueError("Evaluation revision must retain the original strict news and recorded close expectations")
        if set(patch.get("allowed_close_directions", [])) != allowed or patch.get("required_close_evidence_status") != "forecast":
            raise ValueError("Evaluation revision must require a forecast in the same family or flat, symmetrically")
    return {**revision, "file": relative(path), "sha256": sha(path.read_bytes())}


def explicit_future_well_action_check(result: dict, case: dict) -> dict:
    """A repair-specific real-source control, with no direction requirement."""
    required=case.get('required_explicit_future_well_action') is True
    if not required:return {'required':False,'passed':True,'evidence_paths':[]}
    paths=[]
    candidates=[]
    for section in ('news_impact','analysis'):
        value=result.get(section) or {}
        if isinstance(value.get('summary'),str):candidates.append((section+'.summary',value['summary']))
        if section=='analysis':
            for index,text in enumerate(value.get('evidence') or []):
                if isinstance(text,str):candidates.append((f'analysis.evidence[{index}]',text))
    future=r'(?:将(?:会|要)?|即将|计划|拟|预计|下一步|will(?:\s+now)?|is\s+(?:planned|scheduled|set)\s+to)'
    action=r'(?:封堵|封井|弃置|弃井|plug(?:ged|ging)?|abandon(?:ed|ment)?)'
    for path,text in candidates:
        aliases=re.findall(r'\[((?:N|E|P)\d+)\]',text)
        if aliases and 'N0' not in aliases:continue
        for clause in re.split(r'[。.!?！？;；\n]',text):
            for match in re.finditer(future+r'.{0,45}?'+action,clause,re.I):
                before=clause[max(0,match.start()-12):match.start()]
                if re.search(r'若|如果|假如|假设|倘若|\bif\b|(?:不会|不再|并非|否认|无需|没有|未曾|不|未|not)\s*$',before,re.I):continue
                if re.search(r'不|未|否认|无需|\bnot\b',match.group(),re.I):continue
                paths.append(path);break
            if path in paths:break
    return {'required':True,'passed':bool(paths),'evidence_paths':sorted(set(paths)),
            'scope':'Current N0 factual summaries/evidence must explicitly retain the source future plugging/abandonment action; bare action nouns or hypothetical scenarios do not satisfy this control.'}


def contract_checks(result: dict | None, case: dict, *, close_allowed: list[str] | None = None) -> dict:
    if not isinstance(result, dict):
        return {"passed": False, "reason": "no_final_result"}
    news = (result.get("news_impact") or {}).get("direction")
    close = result.get("close_forecast") or {}
    expected_news = case.get("expected_news")
    status = close.get("evidence_status", "forecast")
    accepted = {"up": {"up", "extreme_up"}, "down": {"down", "extreme_down"}}.get(expected_news, {expected_news})
    news_ok = expected_news is None or news in accepted
    close_ok = True
    close_checked = "expected_close" in case
    if close_checked:
        close_ok = close.get("direction") == case["expected_close"]
        close_ok = close_ok and status == ("insufficient_evidence" if case["expected_close"] is None else "forecast")
    expected_status = case.get("expected_close_status", case.get("expected_evidence_status",case.get('expected_status')))
    if expected_status is not None:
        close_ok = close_ok and status == expected_status
        close_checked = True
    expected_sign = case.get("expected_close_sign") if close_allowed is None else None
    sign_ok = True
    if expected_sign is not None:
        family = {"up": {"up", "extreme_up"}, "down": {"down", "extreme_down"}, "flat": {"flat"}}.get(expected_sign, set())
        sign_ok = close.get("direction") in family and status == "forecast"
        close_ok = close_ok and sign_ok
        close_checked = True
    allowed_ok = True
    if close_allowed is not None:
        # An explicit forecast is required; the legacy default status is not
        # evidence that a missing/null output provides a conditional outlook.
        allowed_ok = close.get("direction") in close_allowed and close.get("evidence_status") == "forecast"
        close_ok = close_ok and allowed_ok
        close_checked = True
    future_action=explicit_future_well_action_check(result,case)
    return {"passed": news_ok and close_ok and future_action['passed'], "expected_news": expected_news,
            "news_direction": news, "news_direction_checked": expected_news is not None,
            "news_direction_passed": news_ok, "close_contract_checked": close_checked,
            "expected_close": case.get("expected_close"), "expected_status": expected_status,
            "expected_close_sign": expected_sign, "close_sign_checked": expected_sign is not None,
            "close_sign_passed": sign_ok,
            "allowed_close_directions": close_allowed, "allowed_close_passed": allowed_ok,
            "close_direction": close.get("direction"), "close_evidence_status": status,
            "close_contract_passed": close_ok,'explicit_future_well_action':future_action}


def decoded_result(result: Any) -> dict | None:
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        value = result.strip()
        if value.startswith("```json") and value.endswith("```"):
            value = value[7:-3].strip()
        elif value.startswith("```") and value.endswith("```"):
            value = value[3:-3].strip()
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else None
        except ValueError:
            return None
    return None


def last_raw_response(record: dict) -> str | None:
    attempts = record.get("attempts") or []
    for attempt in reversed(attempts):
        content = ((attempt.get("response") or {}).get("message") or {}).get("content")
        if isinstance(content, str):
            return content
    calls = ((record.get("result") or {}).get("_meta") or record.get("audit") or {}).get("model_calls") or []
    for call in reversed(calls):
        if isinstance(call.get("raw_content"), str):
            return call["raw_content"]
    return None


def revalidate_result(result: dict | str, context: dict, detail: bool = False) -> dict:
    """Run the current analyst's raw checks, without requesting or rewriting output."""
    from ttf_news import analysis as a
    from ttf_news.semantics import validate_analysis_semantics
    try:
        prepared, meta = a.prepare_context(copy.deepcopy(context), detail, reserve_tokens=32)
        original = copy.deepcopy(decoded_result(result))
        canonical=isinstance(result,dict) and '_meta' in result
        recorded_meta=original.pop('_meta',None) if isinstance(original,dict) else None
        raw_source='explicit_raw_json' if isinstance(result,str) else 'provided_json_object_without_generation_provenance'
        if canonical:
            calls=(recorded_meta or {}).get('model_calls') or []
            last=calls[-1] if calls and isinstance(calls[-1],dict) else {}
            content=last.get('raw_content')
            if not isinstance(content,str) or not content.strip():
                raise ValueError('canonical结果缺少最后实际模型raw；不能证实模型原始数组限制或把后台追加风险当模型输出')
            if last.get('validation_error') or last.get('request_error'):
                raise ValueError('canonical结果的最后原始调用未通过；不得将失败raw追认为已接受输出')
            raw_source='saved_final_model_call_raw_content'
        else:
            content = result if isinstance(result, str) else a._json(original)
        parsed = a._parse_result(content, detail, meta["source_index"], context=context)
        a._validate_notice_source_facts(parsed, context, prepared)
        if a._unsupported_weather_lng_causality(parsed, context):
            raise ValueError("输入未支持天气导致LNG到港增加的因果断言")
        a._validate_prediction_semantics(parsed, context, detail)
        a._validate_historical_reactions(parsed, prepared)
        a._validate_priced_in(parsed, prepared)
        validate_analysis_semantics(parsed, context, prepared, source_index=meta["source_index"])
        if canonical:
            # The native bounds apply to the model raw, while the API also
            # carries deterministic source/data limitations. Rebuild that
            # exact backend materialization; never trim either stored array.
            reconstructed_meta={}
            a._add_known_limitations(parsed,context,reconstructed_meta)
            for key in ('deterministic_risk_flags','deterministic_missing_fields'):
                if not isinstance((recorded_meta or {}).get(key),list) or recorded_meta[key]!=reconstructed_meta[key]:
                    raise ValueError('canonical确定性追加来源与当前输入重建不一致：'+key)
            normalization=reconstructed_meta.get('model_risk_flags_normalization')
            recorded_normalization=(recorded_meta or {}).get('model_risk_flags_normalization')
            if normalization is not None and (recorded_normalization is not None or normalization.get('duplicates_removed',0)>0):
                if recorded_normalization!=normalization:
                    raise ValueError('canonical风险备注精确去重计数与原raw重建不一致')
            if parsed!=original:
                raise ValueError('canonical正文/数值/方向/引用或风险不等于最后严格raw及已核实后台追加；不得静默替换模型结果')
        # Reference normalization is allowed; forecasts/scalars never change.
        for section, fields in (("news_impact", ("direction", "contextual_news_score", "confidence", "signal_strength")),
                                ("close_forecast", ("direction", "confidence"))):
            for field in fields:
                if (original.get(section) or {}).get(field) != (parsed.get(section) or {}).get(field):
                    raise ValueError("离线校验意外改变了模型结论或数值")
        return {"passed": True, "prepared_context_sha256": meta["context_sha256"],
                "source_reference_count": len(meta["source_index"]), "context_window": meta["context_window"],
                "raw_validation_source":raw_source,'canonical_backend_materialization_verified':canonical,
                'recorded_model_risk_flags_normalization':(recorded_meta or {}).get('model_risk_flags_normalization') if canonical else None,
                'saved_model_raw_sha256':sha(content) if canonical else None,
                'stored_canonical_arrays_modified':False}
    except Exception as exc:
        return {"passed": False, "error_type": type(exc).__name__, "reason": safe(str(exc)[:1000])}


def duration(record: dict, attempts: list[dict]) -> dict:
    meta = ((record.get("result") or {}).get("_meta") or {})
    elapsed = number(meta.get("elapsed_seconds"))
    wall = None
    try:
        started = datetime.fromisoformat(record["started_at"].replace("Z", "+00:00"))
        finished = datetime.fromisoformat(record["finished_at"].replace("Z", "+00:00"))
        wall = max(0., (finished-started).total_seconds())
    except (KeyError, TypeError, ValueError):
        pass
    ns = [number((call.get("response") or {}).get("total_duration")) for call in attempts]
    ns = [value for value in ns if value is not None]
    calls = (meta or (record.get("audit") or {})).get("model_calls", [])
    if not ns:
        ns = [value for call in calls if (value := number(call.get("total_duration_ns"))) is not None]
    count = len(attempts) or len(calls)
    return {"attempts": count, "retries": max(0, count-1),
            "analyst_elapsed_seconds": elapsed, "wall_seconds": round(wall, 3) if wall is not None else None,
            "model_total_seconds": round(sum(ns)/1e9, 3) if ns else None,
            "truncated_attempts": sum((call.get("response") or {}).get("done_reason") == "length" for call in attempts),
            "recorded_validation_failures": sum(bool(call.get("validation_error")) for call in calls)}


def call_requested_reasoning(call: dict) -> str | None:
    """Read request configuration, including its public non-prose alias."""
    value=call.get('think') or call.get('request_reasoning')
    return value if value in ('low','medium','high') else None


def canonical_json(value: Any) -> str:
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False,default=str)


def ttf_source_hashes(hashes: dict | None = None) -> dict:
    return {key:value for key,value in (hashes if hashes is not None else code_hashes()).items()
            if key.startswith('ttf_news/') and key.endswith('.py')}


def record_execution_path(record: dict) -> dict:
    """Reconstruct the captured actual path, including backend result fields."""
    attempts=record.get('attempts') or []
    meta=(record.get('result') or {}).get('_meta') or record.get('audit') or {}
    calls=meta.get('model_calls') or []
    if not attempts or len(attempts)!=len(calls):raise ValueError('Complete request/response/call audit missing')
    signatures=[]
    for index,(attempt,call) in enumerate(zip(attempts,calls),1):
        if not isinstance(attempt.get('system'),str) or not isinstance(attempt.get('input'),dict) or not isinstance(attempt.get('response'),dict):raise ValueError('Complete recorded response missing')
        system_sha=sha(attempt['system'])
        # Explicit public-copy mode has already verified both whole-file byte
        # identities; JSON key ordering in the derived copy is not the original
        # request serialization. Preserve its captured request SHA in that mode.
        context_sha=call.get('context_sha256') if _PUBLIC_COPY_MAP.get() is not None else sha(json.dumps(attempt['input'],ensure_ascii=False,separators=(',',':'),allow_nan=False,default=str))
        content=((attempt['response'].get('message') or {}).get('content'))
        if not isinstance(content,str) or call.get('raw_content')!=content:raise ValueError('Raw response differs from call audit')
        if call.get('system_prompt_sha256')!=system_sha or call.get('context_sha256')!=context_sha:raise ValueError('Saved actual request SHA mismatch')
        if not re.fullmatch(r'[0-9a-f]{64}',str(call.get('response_schema_sha256',''))):raise ValueError('Actual response schema SHA unavailable')
        if call_requested_reasoning(call) is None:raise ValueError('Actual reasoning configuration unavailable')
        for key in ('num_ctx','num_predict','input_token_ceiling','input_token_estimate'):
            if not isinstance(call.get(key),int) or isinstance(call[key],bool) or call[key]<0:raise ValueError('Actual budget configuration unavailable')
        draft=call.get('repair_draft');request_draft=attempt['input'].get('UNTRUSTED_DRAFT')
        signatures.append({'attempt':index,'system_sha256':system_sha,'context_sha256':context_sha,
           'schema_sha256':call['response_schema_sha256'],'raw_response_sha256':sha(canonical_json(attempt['response'])),
           'raw_content_sha256':sha(content),'request_reasoning':call_requested_reasoning(call),
           **{key:call.get(key) for key in ('num_ctx','num_predict','input_token_ceiling','input_token_estimate','retry_policy',
                      'semantic_independent_regeneration','semantic_high_scope','semantic_high_scope_eligible','semantic_high_eligible')},
           'validation_stage':call.get('validation_stage'),'validation_errors':copy.deepcopy(call.get('validation_errors') or []),
           'validation_error':call.get('validation_error'),
           'full_validation_error':call.get('full_validation_error'),
           'semantic_source_only_feedback':call.get('semantic_source_only_feedback'),
           'repair_feedback_sha256':sha(canonical_json(call['repair_feedback'])) if call.get('repair_feedback') is not None else None,
           'request_feedback_sha256':sha(canonical_json(attempt['input']['REPAIR_FEEDBACK'])) if attempt['input'].get('REPAIR_FEEDBACK') is not None else None,
           'repair_draft_sha256':sha(canonical_json(draft)) if draft is not None else None,
           'request_draft_sha256':sha(canonical_json(request_draft)) if request_draft is not None else None})
    result=copy.deepcopy(record.get('result'));error=record.get('error')
    if result and not error:
        status='completed';result.pop('_meta',None);result_sha=sha(canonical_json(result))
    elif not result and isinstance(error,str) and error:
        status='failed';result_sha=None
    else:raise ValueError('Original terminal state is ambiguous')
    return {'attempt_count':len(attempts),'retry_count':len(attempts)-1,
            'model_call_configuration_sha256':sha(canonical_json(record.get('model_call_configuration') or {})),
            'attempts':signatures,'terminal_status':status,'terminal_error':error,
            'terminal_repair_draft':copy.deepcopy(meta.get('repair_draft')),
            'terminal_repair_feedback_budget':copy.deepcopy(meta.get('repair_feedback_budget')),
            'result_without_meta_sha256':result_sha}


def verify_path_equivalence(path: Path, record: dict, hashes: dict, stage: str,
                            current_prompt_version: str, proof: dict | None = None,
                            *, force_replay: bool = False) -> dict:
    """Only explicit complete replay evidence may cover a whole-source mismatch."""
    base={'compatible':False,'mode':'unverified','no_new_model_call':True}
    try:
        if not path.resolve().is_relative_to(ROOT.resolve()):raise ValueError('Record path is outside the project')
        if current_prompt_version!='ttf-news-standalone-2.3' or record.get('prompt_version')!=current_prompt_version:
            raise ValueError('Path equivalence is restricted to this actual 2.3 cohort')
        allowed={name+suffix for name in CURRENT_COHORT_STAGES for suffix in ('','-detail')}
        if stage not in allowed or path.parent.name!=stage:raise ValueError('Record is outside the explicitly allowed actual phases')
        if record.get('case') not in set(CASE_NAMES)|set(ANCHOR_CASE_NAMES):raise ValueError('Record is outside the twenty registered cases')
        if record.get('validation_only'):raise ValueError('Offline-only records cannot become actual execution')
        current=ttf_source_hashes(hashes);original=record.get('source_hashes') or {}
        if not current or not original:raise ValueError('Complete source identity unavailable')
        if original==current and not force_replay:return {**base,'compatible':True,'mode':'direct_final_source_execution'}
        proof_path=path.parent.parent/PATH_EQUIVALENCE_FILE
        if not proof_path.is_file():raise ValueError('Explicit complete-path proof missing')
        artifact_source_sha(proof_path);stored_proof=read_json(proof_path)
        if proof is not None and proof!=stored_proof:raise ValueError('In-memory proof differs from its immutable file')
        proof=stored_proof
        if proof.get('schema_version')!=PATH_EQUIVALENCE_SCHEMA or proof.get('prompt_version')!=current_prompt_version or proof.get('new_model_calls')!=0:
            raise ValueError('Complete-path proof schema/version/call boundary invalid')
        if proof.get('current_source_sha256')!=current:raise ValueError('Complete-path proof final source is stale')
        rows=proof.get('records')
        if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):raise ValueError('Complete-path proof records malformed')
        names=[row.get('source_record_file') for row in rows]
        if len(set(names))!=len(names):raise ValueError('Duplicate complete-path proof record')
        name=relative(path);matched=[row for row in rows if row.get('source_record_file')==name]
        if len(matched)!=1:raise ValueError('Record path is not explicitly proved')
        row=matched[0]
        if row.get('passed') is not True or row.get('new_model_calls')!=0:raise ValueError('Complete replay did not establish equivalence')
        if not artifact_sha_matches(path,row.get('source_record_sha256')):raise ValueError('Complete-path proof original record SHA mismatch')
        input_sha=sha(json.dumps(record.get('input_snapshot'),ensure_ascii=False,sort_keys=True))
        if record.get('input_sha256')!=input_sha or row.get('input_sha256')!=input_sha:raise ValueError('Complete-path proof input SHA mismatch')
        if row.get('original_source_sha256')!=original:raise ValueError('Complete-path proof original sources mismatch')
        actual=record_execution_path(record)
        if row.get('original_path')!=actual or row.get('replayed_path')!=actual:raise ValueError('Full request/validation/retry/result path differs')
        return {**base,'compatible':True,'mode':'same_version_actual_execution_with_verified_final_path_equivalence',
                'proof_file':relative(proof_path),'proof_sha256':artifact_source_sha(proof_path) if proof_path.is_file() else None,
                'source_record_sha256':row['source_record_sha256'],'original_generation_unchanged':True,
                'is_new_final_source_model_generation':False,'attempt_count':actual['attempt_count']}
    except (OSError,ValueError,TypeError,KeyError) as exc:
        return {**base,'reason':safe(str(exc))}


def verify_budget_terminal(path: Path, record: dict, hashes: dict, stage: str,
                           current_prompt_version: str, logic: dict,
                           proof: dict | None = None) -> dict:
    """A reproduced full-facts repair budget stop is an error, never a signal."""
    base={'verified':False,'counts_as_valid_output':False}
    meta=(record.get('result') or {}).get('_meta') or record.get('audit') or {}
    calls=meta.get('model_calls') or [];draft=meta.get('repair_draft') or {}
    raw=last_raw_response(record)
    from ttf_news.analysis import REPAIR_DRAFT_VERSION
    if (record.get('result') or not record.get('error') or len(calls)!=1 or len(record.get('attempts') or [])!=1
        or logic.get('passed') is not False or not calls[0].get('validation_error')
        or draft.get('version')!=REPAIR_DRAFT_VERSION or draft.get('reason')!='complete_sources_and_draft_exceed_budget'
        or not isinstance(raw,str) or draft.get('previous_raw_sha256')!=sha(raw)
        or '完整来源事实与有界修复草稿无法共同装入输入预算' not in record['error']):
        return {**base,'reason':'Not the explicitly audited one-call full-facts repair-budget boundary'}
    replay=verify_path_equivalence(path,record,hashes,stage,current_prompt_version,proof,force_replay=True)
    if not replay['compatible']:return {**base,'reason':replay.get('reason'),'complete_path_proof':replay}
    return {**base,'verified':True,'terminal_kind':'repair_draft_input_budget_exhausted',
            'actual_model_call_count':1,'retry_request_not_sent':True,'final_raw_independently_rejected':True,
            'complete_path_proof':replay,'notice':'One real rejected model response, then a reproduced full-facts plus bounded-draft input budget stop; not two model calls and not a correct prediction.'}


REAL_ANCHOR_VARIANCE_CASE='anchor_real_manual_missing_prevday'
REAL_ANCHOR_VARIANCE_RECORD_SHA='0f5f5fef742e674a754a6b924b4749c749ea843d1922cb9321625cb606f1329c'
REAL_ANCHOR_VARIANCE_FILE='real_anchor_unvalidated_label_variance_v2_3.json'


def verify_real_anchor_label_variance(path: Path, record: dict, case: dict, hashes: dict,
                                      logic: dict, policy: dict, path_proof: dict) -> dict:
    """One fixed real-source label disagreement, never a strict pass or score."""
    base={'verified':False,'strict_contract_passed':False,'counts_as_correct_prediction':False}
    try:
        expected_file=f'reports/repair_2026-10-20/{PRICE_OBSERVATION_STAGE}/{REAL_ANCHOR_VARIANCE_CASE}.json'
        if (case.get('id')!=REAL_ANCHOR_VARIANCE_CASE or record.get('case')!=REAL_ANCHOR_VARIANCE_CASE
            or relative(path)!=expected_file or not artifact_sha_matches(path,REAL_ANCHOR_VARIANCE_RECORD_SHA)
            or case.get('synthetic') is not False or record.get('synthetic') is not False
            or record.get('prompt_version')!='ttf-news-standalone-2.3' or record.get('validation_only')
            or not record.get('result') or record.get('error') or record.get('contract_passed') is not False):
            raise ValueError('Not the single preserved actual real-anchor label disagreement')
        checks=contract_checks(record['result'],case)
        if (case.get('expected_news')!='flat' or 'expected_close' not in case or case['expected_close'] is not None
            or checks.get('passed') is not False or checks.get('news_direction')!='down'
            or checks.get('close_contract_passed') is not True or checks.get('close_direction') is not None
            or checks.get('close_evidence_status')!='insufficient_evidence'
            or logic.get('passed') is not True or policy.get('compatible') is not True or path_proof.get('compatible') is not True):
            raise ValueError('Facts, price anchor, close contract, final semantics or actual path not valid')
        proof_path=path.parent.parent/REAL_ANCHOR_VARIANCE_FILE
        artifact_source_sha(proof_path);proof=read_json(proof_path)
        input_sha=sha(json.dumps(record['input_snapshot'],ensure_ascii=False,sort_keys=True))
        body=copy.deepcopy(record['result']);body.pop('_meta',None);body_sha=sha(canonical_json(body))
        if (proof.get('schema_version')!='ttf-real-anchor-label-variance-v1'
            or proof.get('case')!=REAL_ANCHOR_VARIANCE_CASE or proof.get('source_record_file')!=expected_file
            or proof.get('source_record_sha256')!=REAL_ANCHOR_VARIANCE_RECORD_SHA
            or proof.get('prompt_version')!=record['prompt_version'] or proof.get('input_sha256')!=input_sha
            or record.get('input_sha256')!=input_sha or proof.get('result_without_meta_sha256')!=body_sha
            or proof.get('source_sha256')!=ttf_source_hashes(hashes)
            or proof.get('original_expected_news')!='flat' or proof.get('actual_news_direction')!='down'
            or proof.get('strict_contract_passed') is not False or proof.get('counts_as_correct_prediction') is not False):
            raise ValueError('Fixed label-variance evidence is missing, stale or inconsistent')
        if _PUBLIC_COPY_MAP.get() is None and proof.get('source_result_canonical_sha256')!=sha(canonical_json(record['result'])):
            raise ValueError('Original complete result identity differs')
        fixture_path,_=verified_project_reference(proof.get('fixture') or {})
        frozen=[item for item in read_json(fixture_path) if item.get('id')==REAL_ANCHOR_VARIANCE_CASE]
        if len(frozen)!=1 or frozen[0]!=case:raise ValueError('Original frozen real case changed')
        refs=proof.get('independent_reviews') or [];reviewers=[]
        if len(refs)!=2:raise ValueError('Two independent full-prose Codex subagent reviews are required')
        for reference in refs:
            _,raw=verified_project_reference(reference);review=json.loads(raw);row=review.get('review') or {}
            if (review.get('reviewer_type')!='codex_subagent' or review.get('strict_contract_passed') is not False
                or review.get('material_known_logic_errors')!=[] or row.get('known_logic_errors')!=[]
                or row.get('verdict')!='qualified_direction_contract_mismatch'
                or row.get('case')!=REAL_ANCHOR_VARIANCE_CASE or row.get('actual_record_path')!=expected_file
                or row.get('actual_record_sha256')!=REAL_ANCHOR_VARIANCE_RECORD_SHA
                or row.get('actual_input_sha256')!=input_sha or row.get('synthetic') is not False
                or row.get('prompt_version')!=record['prompt_version'] or row.get('source_hashes')!=record.get('source_hashes')
                or row.get('result_canonical_sha256')!=proof.get('source_result_canonical_sha256')
                or not all((row.get('checks') or {}).get(key) for key in ('contract_parties','price_anchor','news_mechanism'))):
                raise ValueError('Independent source, anchor or prose review disagrees with the fixed record')
            reviewers.append(review.get('reviewer'))
        if set(reviewers)!={'/root/semantic_checks','/root/final_semantic_audit'}:
            raise ValueError('Independent reviewers are not the two recorded Codex subagents')
        return {**base,'verified':True,'category':'deliverable_real_news_unvalidated_label_disagreement',
                'case':REAL_ANCHOR_VARIANCE_CASE,'source_record_sha256':REAL_ANCHOR_VARIANCE_RECORD_SHA,
                'proof_file':relative(proof_path),'proof_sha256':artifact_source_sha(proof_path),
                'original_expected_news':'flat','actual_news_direction':'down','reviewer_type':'codex_subagent',
                'notice':'可交付分析，但真实新闻标签未经验证且与原flat期望不一致；严格契约仍失败，不计正确预测或市场准确率。两份独立全文复核由AI agent执行，保留措辞限定。'}
    except (OSError,ValueError,TypeError,KeyError) as exc:return {**base,'reason':safe(str(exc))}


def operational_label_delivery(row: dict, audit: dict | None = None) -> bool:
    evidence=(audit if audit is not None else row).get('operational_label_variance') or {}
    return (row.get('case')==REAL_ANCHOR_VARIANCE_CASE and not row.get('passed')
            and evidence.get('verified') is True and evidence.get('category')=='deliverable_real_news_unvalidated_label_disagreement'
            and evidence.get('source_record_sha256')==REAL_ANCHOR_VARIANCE_RECORD_SHA
            and evidence.get('strict_contract_passed') is False and evidence.get('counts_as_correct_prediction') is False)


async def replay_record_path(path: Path, hashes: dict, current_prompt_version: str) -> dict:
    """Run current Python logic using saved responses only; never open HTTP/DB."""
    from ttf_news import analysis as a
    raw=path.read_bytes();record=json.loads(raw);trace=[];mismatches=[];request_signatures=[]
    row={'source_record_file':relative(path),'source_record_sha256':sha(raw),
         'input_sha256':record.get('input_sha256'),'original_source_sha256':record.get('source_hashes'),
         'new_model_calls':0,'passed':False}
    try:
        stage=path.parent.name
        if stage not in {name+suffix for name in CURRENT_COHORT_STAGES for suffix in ('','-detail')} or record.get('prompt_version')!='ttf-news-standalone-2.3' or current_prompt_version!=record['prompt_version']:
            raise ValueError('Replay is restricted to the actual 2.3 twenty-case phases')
        row['original_path']=record_execution_path(record)
        if record.get('input_sha256')!=sha(json.dumps(record.get('input_snapshot'),ensure_ascii=False,sort_keys=True)):raise ValueError('Original input SHA mismatch')
        attempts=record['attempts'];calls=((record.get('result') or {}).get('_meta') or record.get('audit') or {})['model_calls']
        class RecordedResponsesAnalyst(a.NewsAnalyst):
            async def _request(self,system,data,detail):
                index=len(trace)
                if index>=len(attempts):
                    mismatches.append('Unexpected extra request');raise ValueError('Recorded responses exhausted; no model request sent')
                original=attempts[index]
                entry={'system':system,'input':copy.deepcopy(data)};trace.append(entry)
                signature={'attempt':index+1,'system_sha256':sha(system),'context_sha256':sha(a._json(data)),
                           'schema_sha256':sha(a._json(a.response_schema(detail,data)))}
                signature['differs_from_recorded']=[key for key,old in (
                    ('system_sha256',calls[index]['system_prompt_sha256']),
                    ('context_sha256',calls[index]['context_sha256']),
                    ('schema_sha256',calls[index]['response_schema_sha256'])) if signature[key]!=old]
                request_signatures.append(signature)
                same=(system==original['system'] and data==original['input'] and not signature['differs_from_recorded'])
                if not same:
                    mismatches.append(f"Attempt {index+1} differs: {', '.join(signature['differs_from_recorded'])}");raise ValueError(mismatches[-1])
                entry['response']=copy.deepcopy(original['response'])
                return copy.deepcopy(original['response'])
        analyst=RecordedResponsesAnalyst();configuration=record.get('model_call_configuration') or {}
        if any(configuration.get(key)!=getattr(analyst,key) for key in ('model','reasoning','semantic_high_retry','semantic_high_scope')):
            raise ValueError('Actual configuration does not match current production defaults')
        replayed={'model_call_configuration':configuration,'attempts':trace}
        meta=(record.get('result') or {}).get('_meta') or record.get('audit') or {}
        detail=bool(stage.endswith('-detail') or record.get('detail') or meta.get('detail') or (record.get('result') or {}).get('detail'))
        from unittest.mock import patch
        try:
            with patch('socket.socket.connect',side_effect=RuntimeError('Network is disabled during recorded-response replay')),patch('httpx.AsyncClient',side_effect=RuntimeError('HTTP is disabled during recorded-response replay')):
                replayed['result']=await analyst.analyze(copy.deepcopy(record['input_snapshot']),detail)
        except Exception as exc:
            replayed.update(error=str(exc),audit=getattr(exc,'audit',{}))
        row['request_mismatches']=mismatches
        row['replayed_request_signatures']=request_signatures
        replay_meta=(replayed.get('result') or {}).get('_meta') or replayed.get('audit') or {}
        row['replayed_validation_audit']=[{key:copy.deepcopy(call.get(key)) for key in
             ('validation_stage','validation_errors','validation_error','full_validation_error','context_sha256','system_prompt_sha256','response_schema_sha256','repair_draft','repair_feedback','semantic_source_only_feedback')}
             for call in replay_meta.get('model_calls') or []]
        row['replayed_terminal_error']=replayed.get('error')
        try:row['replayed_path']=record_execution_path(replayed)
        except ValueError as exc:
            row['replayed_path']=None;row['replayed_path_error']=str(exc)
        if row['replayed_path'] is not None:
            old=row['original_path'];new=row['replayed_path'];differences=[]
            for key in ('attempt_count','retry_count','terminal_status','terminal_error','terminal_repair_draft','terminal_repair_feedback_budget','result_without_meta_sha256'):
                if old.get(key)!=new.get(key):differences.append(key)
            for index,(before,after) in enumerate(zip(old['attempts'],new['attempts']),1):
                differences.extend(f'attempts[{index}].{key}' for key in set(before)|set(after) if before.get(key)!=after.get(key))
            row['path_differences']=sorted(differences)
        row['passed']=not mismatches and row['original_path']==row['replayed_path'] and len(trace)==len(attempts)
        if not row['passed']:row['reason']='Actual request/validation/repair/result path changed; a new actual terminal run is required'
    except (ValueError,TypeError,KeyError) as exc:row['reason']=safe(str(exc))
    if path.read_bytes()!=raw or ttf_source_hashes()!=ttf_source_hashes(hashes):
        row.update(passed=False,reason='Source or original record changed during replay')
    return row


async def build_path_equivalence(evidence_dir: Path) -> dict:
    from ttf_news.prompts import PROMPT_VERSION
    hashes=code_hashes();records=[]
    for stage in CURRENT_COHORT_STAGES:
        for suffix in ('','-detail'):
            for path in sorted((evidence_dir/(stage+suffix)).glob('*.json')):
                records.append(await replay_record_path(path,hashes,PROMPT_VERSION))
    return {'schema_version':PATH_EQUIVALENCE_SCHEMA,'generated_at':datetime.now(timezone.utc).isoformat(),
            'prompt_version':PROMPT_VERSION,'new_model_calls':0,'no_database_access':True,'no_network_calls':True,
            'current_source_sha256':ttf_source_hashes(hashes),'records':records,
            'equivalent_count':sum(row['passed'] for row in records),
            'boundary':'Saved real responses are replayed through final Python logic. This proves captured execution-path equality, not another model generation or deterministic reproduction of stochastic output.'}


def source_feedback_policy_compatibility(record: dict,meta: dict,calls: list,context: dict | None,detail: bool) -> dict:
    """Rebuild the actual source-only retry, not merely its audit flag."""
    from ttf_news import analysis as a
    base={'version':a.SOURCE_FEEDBACK_VERSION,'required':True,'actual_call_count':len(calls),'compatible':False}
    try:
        if len(calls)!=2 or not isinstance(context,dict):raise ValueError('Source-only retry needs two actual calls and complete input')
        previous,last=calls;raw=previous.get('raw_content')
        if (previous.get('validation_stage')!='fact_or_mechanism' or not isinstance(raw,str)
            or call_requested_reasoning(last)!='medium' or last.get('semantic_independent_regeneration')
            or last.get('semantic_source_only_feedback') is not True or meta.get('semantic_source_only_feedback') is not True):
            raise ValueError('Actual source-only semantic retry markers or reasoning do not match')
        system=a.system_prompt(detail)+a.SOURCE_FEEDBACK_INSTRUCTION
        request,reconstructed,draft=a._prepare_medium_repair(context,detail,system,previous.get('validation_error',''),
                                                          previous,'medium',include_draft=False)
        expected_feedback=a.source_feedback_payload(previous,previous.get('validation_error',''))
        if not expected_feedback['errors']:raise ValueError('Complete local diagnostics are missing')
        expected_audit=copy.deepcopy(reconstructed.get('repair_feedback'))
        if _PUBLIC_COPY_MAP.get() is not None:
            original_facts_sha=(last.get('repair_feedback') or {}).get('source_facts_sha256')
            if not re.fullmatch(r'[0-9a-f]{64}',str(original_facts_sha or '')):raise ValueError('Captured original source-fact SHA missing')
            expected_audit['source_facts_sha256']=original_facts_sha
        if (last.get('repair_feedback')!=expected_audit or last.get('repair_draft')!=draft
            or 'UNTRUSTED_DRAFT' in request or request.get('REPAIR_FEEDBACK')!=expected_feedback):
            raise ValueError('Complete feedback, previous raw, source facts or no-draft audit differs')
        if last.get('system_prompt_sha256')!=sha(system):raise ValueError('Actual retry system is not the source-only instruction')
        attempts=record.get('attempts') or []
        if attempts:
            if (len(attempts)!=2 or attempts[0].get('response',{}).get('message',{}).get('content')!=raw
                or attempts[1].get('system')!=system or attempts[1].get('input')!=request):
                raise ValueError('Saved actual retry is missing, contains a draft or differs from complete reconstruction')
        # The publication map verifies original and derived whole-file bytes;
        # original request serialization can differ only in dictionary order.
        if _PUBLIC_COPY_MAP.get() is None or not attempts:
            if last.get('context_sha256')!=sha(a._json(request)):raise ValueError('Actual complete retry context SHA differs')
        elif not re.fullmatch(r'[0-9a-f]{64}',str(last.get('context_sha256',''))):
            raise ValueError('Captured original context SHA is missing from the verified public copy')
        for key,expected in (('num_ctx',reconstructed['context_window']),('num_predict',reconstructed['output_token_limit']),
                             ('input_token_ceiling',reconstructed['input_token_ceiling']),('input_token_estimate',reconstructed['input_token_estimate'])):
            if last.get(key)!=expected:raise ValueError('Actual source-only retry budget differs: '+key)
        if last.get('response_schema_sha256')!=sha(a._json(a.response_schema(detail,request))):
            raise ValueError('Actual source-only retry schema differs')
        return {**base,'compatible':True,'reason':'current_complete_source_validator_feedback_executed',
                'previous_raw_sha256':sha(raw),'payload_sha256':last['repair_feedback']['payload_sha256'],
                'full_diagnostics':True,'candidate_draft_in_request':False,'complete_request_rebuilt':True}
    except (ValueError,TypeError,KeyError) as exc:return {**base,'reason':'source_only_feedback_not_verified','detail':safe(str(exc))}


def repair_draft_policy_compatibility(record: dict, meta: dict, calls: list, version: str,
                                      context: dict | None=None,detail: bool=False) -> dict:
    """Verify an executed medium retry used the bounded untrusted draft.

    First-pass outputs need no new inference. A readable failed draft followed
    by the old whole-JSON regeneration is not a test of the current repair path.
    """
    attempts=record.get('attempts') or []
    count=max(len(attempts),len(calls))
    base={'version':version,'actual_call_count':count,'required':False}
    if count<=1:
        return {**base,'compatible':True,'reason':'first_pass_no_repair_draft_needed'}
    if len(calls)>1 and call_requested_reasoning(calls[-1]) not in (None,'medium'):
        return {**base,'compatible':True,'reason':'not_default_medium_retry'}
    if calls and calls[0].get('validation_stage')=='fact_or_mechanism':
        return source_feedback_policy_compatibility(record,meta,calls,context,detail)
    if (meta.get('semantic_source_only_feedback') or any(call.get('semantic_source_only_feedback') or call.get('repair_feedback') for call in calls)):
        return {**base,'compatible':False,'reason':'source_only_feedback_used_outside_fact_or_mechanism_retry'}
    raw=(((attempts[0].get('response') or {}).get('message') or {}).get('content')) if attempts else None
    if not isinstance(raw,str) and calls:
        raw=calls[0].get('raw_content')
    if not isinstance(raw,str):
        return {**base,'compatible':False,'reason':'retry_previous_raw_unavailable'}
    text=raw.strip()
    if text.startswith('```json') and text.endswith('```'):text=text[7:-3].strip()
    elif text.startswith('```') and text.endswith('```'):text=text[3:-3].strip()
    try:
        candidate=json.loads(text,parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError,TypeError):candidate=None
    prose=False
    if isinstance(candidate,dict):
        prose=any(isinstance(candidate.get(section),dict) and
                  isinstance(candidate[section].get(field),str) and candidate[section][field].strip()
                  for section,field in (('news_impact','summary'),('analysis','summary'),('close_forecast','basis')))
        prose=prose or any(isinstance(candidate.get(section),dict) and
                          isinstance(candidate[section].get(field),list) and
                          any(isinstance(value,str) for value in candidate[section][field])
                          for section,fields in (('news_impact',('bullish_evidence','bearish_evidence','neutralizing_evidence')),('analysis',('evidence',)))
                          for field in fields)
    if not prose:
        return {**base,'compatible':True,'reason':'previous_candidate_has_no_readable_repair_draft'}
    base['required']=True
    audit=(calls[-1].get('repair_draft') if calls else None) or meta.get('repair_draft') or {}
    if audit.get('version')!=version or audit.get('included') is not True or audit.get('is_evidence') is not False:
        return {**base,'compatible':False,'reason':'readable_candidate_retry_without_current_draft_policy'}
    if audit.get('previous_raw_sha256')!=sha(raw):
        return {**base,'compatible':False,'reason':'repair_draft_previous_raw_hash_mismatch'}
    if not re.fullmatch(r'[0-9a-f]{64}',str(audit.get('payload_sha256',''))):
        return {**base,'compatible':False,'reason':'repair_draft_payload_hash_missing'}
    # Isolated model runs retain their request. Production model-call audit
    # carries the same payload/source hashes without copying the draft again.
    if len(attempts)>1 and isinstance(attempts[-1].get('input'),dict):
        request=attempts[-1]['input'];draft=request.get('UNTRUSTED_DRAFT')
        if not isinstance(draft,dict) or draft.get('is_evidence') is not False or draft.get('origin')!='previous_model_candidate':
            return {**base,'compatible':False,'reason':'recorded_retry_request_has_no_untrusted_draft'}
        canonical=lambda value:json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False,default=str)
        if sha(canonical(draft))!=audit['payload_sha256']:
            return {**base,'compatible':False,'reason':'repair_draft_payload_hash_mismatch'}
        facts={key:value for key,value in request.items() if key not in ('compression','UNTRUSTED_DRAFT')}
        if sha(canonical(facts))!=audit.get('source_facts_sha256'):
            return {**base,'compatible':False,'reason':'repair_draft_source_facts_hash_mismatch'}
    return {**base,'compatible':True,'reason':'current_bounded_untrusted_draft_executed',
            'previous_raw_sha256':audit['previous_raw_sha256'],'payload_sha256':audit['payload_sha256']}


def generation_policy_compatibility(record: dict, context: dict | None, detail: bool = False,
                                    prepared: dict | None = None) -> dict:
    """Check the executed path against the current default retry policy.

    A different source hash alone does not make an executed medium path
    incompatible. Conversely, later offline semantic validation cannot turn a
    explicit independent high experiment into the default production path.
    """
    meta=((record.get("result") or {}).get("_meta") or record.get("audit") or {})
    calls=meta.get("model_calls") or []
    # Independent regeneration is the recorded high experiment path. A public
    # copy may rename its request setting, and a missing setting cannot turn
    # that recorded path into the default medium repair.
    used=any(call.get("semantic_independent_regeneration") and
             (call_requested_reasoning(call)=='high' or call_requested_reasoning(call) is None)
             for call in calls if isinstance(call,dict))
    if not calls:
        used=bool(meta.get("semantic_independent_regeneration") and meta.get("reasoning")=="high")
    from ttf_news.analysis import NewsAnalyst, REPAIR_DRAFT_VERSION
    high_enabled=bool(NewsAnalyst().semantic_high_retry)
    base={"current_semantic_high_retry_enabled":high_enabled,
          "current_semantic_high_scope":"operator_notice",
          "recorded_semantic_high_scope":meta.get("semantic_high_scope") or (record.get("model_call_configuration") or {}).get("semantic_high_scope"),
          "semantic_high_independent_regeneration_used":used}
    if not used:
        draft=repair_draft_policy_compatibility(record,meta,calls,REPAIR_DRAFT_VERSION,context,detail)
        return {**base,"compatible":draft['compatible'],"current_news_is_operator_notice":None,
                "reason":"no_independent_high_path_used" if draft['compatible'] else draft['reason'],
                'repair_draft_policy':draft}
    if not isinstance(context,dict):
        return {**base,"compatible":False,"current_news_is_operator_notice":None,
                "reason":"independent_high_path_without_checkable_input"}
    try:
        from ttf_news import analysis as a
        if prepared is None:
            prepared,_=a.prepare_context(copy.deepcopy(context),detail,reserve_tokens=32)
        operator=a.semantic_high_scope_eligible(prepared,context,scope="operator_notice")
        compatible=high_enabled and bool(operator)
        return {**base,"compatible":compatible,"current_news_is_operator_notice":bool(operator),
                "reason":"independent_high_disabled_by_current_default" if not high_enabled else
                         "current_operator_notice_high_path_allowed" if operator else "non_operator_independent_high_outside_current_default"}
    except Exception as exc:
        return {**base,"compatible":False,"current_news_is_operator_notice":None,
                "reason":"operator_scope_input_check_failed","error":safe(str(exc)[:500])}


def inspect_record(path: Path, case: dict, stage: str, current_hashes: dict, context_fixture: str | None = None,
                   evaluation_revision: dict | None = None) -> dict:
    try:
        record = read_json(path)
        if not isinstance(record, dict):
            raise ValueError("record must be a JSON object")
    except (OSError, ValueError) as exc:
        return {"case": case["id"], "file": relative(path), "stage": stage, "passed": False,
                "record_error": safe(str(exc)), "status": "unreadable_or_in_progress"}
    result = record.get("result")
    raw_result = None
    attempts = record.get("attempts") if isinstance(record.get("attempts"), list) else []
    if not result and record.get("validation_only"):
        source = record.get("source_record")
        if source:
            source_path = ROOT/source
            try:
                source_record = read_json(source_path)
                result = source_record.get("result")
                raw_result = last_raw_response(source_record)
            except (OSError, ValueError):
                result = None
    engine = record.get("engine") or ("original" if stage.startswith("original") else "candidate")
    if record.get("context") is not None:
        context, context_source = record["context"], "record.context"
    elif record.get("input_snapshot") is not None:
        context, context_source = record["input_snapshot"], "record.input_snapshot"
    else:
        context = case.get("original_context" if engine == "original" else "candidate_context")
        context_source = context_fixture or "comparison_case_fixture"
    if isinstance(context, str):
        context = decoded_result(context)
    raw_result = raw_result or last_raw_response(record)
    candidate = result or raw_result
    parsed_candidate = decoded_result(candidate)
    original_checks = contract_checks(parsed_candidate, case)
    amendment = ((evaluation_revision or {}).get("cases") or {}).get(case["id"]) if engine != "original" else None
    checks = contract_checks(parsed_candidate, case, close_allowed=amendment["allowed_close_directions"]) if amendment else original_checks
    detail = bool(stage.endswith('-detail') or record.get("detail") or isinstance((parsed_candidate or {}).get("detail"), dict))
    logic = revalidate_result(candidate, context, detail) if candidate is not None and context else {"passed": False, "reason": "missing_result_or_context"}
    source_hashes = record.get("source_hashes") or {}
    relevant = INFERENCE_SOURCE_FILES
    changed = [name for name in relevant if source_hashes.get(name) and source_hashes[name] != current_hashes.get(name)]
    error = record.get("error")
    policy=generation_policy_compatibility(record,context,detail)
    from ttf_news.prompts import PROMPT_VERSION
    path_equivalence=verify_path_equivalence(path,record,current_hashes,stage,PROMPT_VERSION)
    budget_terminal=verify_budget_terminal(path,record,current_hashes,stage,PROMPT_VERSION,logic)
    label_variance=verify_real_anchor_label_variance(path,record,case,current_hashes,logic,policy,path_equivalence) if case.get('id')==REAL_ANCHOR_VARIANCE_CASE else {'verified':False}
    passed = bool(result) and not error and checks["passed"] and logic["passed"] and (policy["compatible"] or engine=="original")
    final = {"case": case["id"], "file": relative(path), "stage": stage, "engine": engine,
             "synthetic": record.get("synthetic", case.get("synthetic")), "passed": passed,
             "valid_output": passed,
             "output_role": "current_pipeline_valid_output" if passed else "diagnostic_preview_only",
             "raw_direction_preview": None if passed else {"news_direction":checks.get('news_direction'),
                 "close_direction":checks.get('close_direction'),'close_evidence_status':checks.get('close_evidence_status'),
                 'product_prediction':False,'counts_as_effective_output':False},
             "analysis_kind": "detail" if detail else "brief",
             "has_detail_result": isinstance((result or {}).get("detail"), dict),
             "status": "passed" if passed else "needs_attention", "recorded_error": safe(error),
             "recorded_contract_passed": record.get("contract_passed"),
             "recorded_run_succeeded": bool(result) and not error,
             "revalidation_input": "final_result" if result else "last_raw_response" if raw_result is not None else "missing",
             "revalidation_context_source": context_source,
             "prompt_version": record.get("prompt_version"), "model": safe(record.get("model")),
             "generation_prompt_version": record.get("prompt_version"),
             "generation_source_sha256": {name: source_hashes.get(name) for name in relevant},
             "generation_all_ttf_source_sha256":copy.deepcopy(source_hashes),
             "offline_revalidation_source_sha256": {name: current_hashes.get(name) for name in relevant},
             "offline_revalidation_is_new_model_call": False,
             "first_actual_request_matches_current_input": first_request_input_compatibility(record,context,detail),
             "reasoning": record.get("reasoning") or ((record.get("result") or {}).get("_meta") or {}).get("reasoning") or (record.get("audit") or {}).get("reasoning"),
             "expectation_checks": checks, "original_expectation_checks": original_checks,
             "evaluation_contract_revision": {"applied": bool(amendment),
                 "revision_id": (evaluation_revision or {}).get("revision_id") if amendment else None,
                 "file": (evaluation_revision or {}).get("file") if amendment else None,
                 "sha256": (evaluation_revision or {}).get("sha256") if amendment else None},
             "current_logic_revalidation": logic,
             "recorded_model_risk_flags_normalization":safe(((result or {}).get('_meta') or {}).get('model_risk_flags_normalization')),
             "current_generation_policy_compatibility":policy,
             "current_complete_path_equivalence":path_equivalence,
             "verified_budget_terminal":budget_terminal,
             "operational_label_variance":label_variance,
             "generation_source_files_changed": changed,
             "generated_with_current_prompt": bool(source_hashes.get("ttf_news/prompts.py")) and source_hashes.get("ttf_news/prompts.py") == current_hashes.get("ttf_news/prompts.py"),
             "input_sha256": record.get("input_sha256"), **duration(record, attempts)}
    if record.get("validation_only"):
        final["validation_only"] = True
        final["source_record"] = record.get("source_record")
    if operational_label_delivery(final):
        final['output_role']='deliverable_real_news_unvalidated_label_disagreement'
        final['operationally_deliverable']=True
    return final


def first_request_input_compatibility(record: dict, context: dict, detail: bool) -> dict:
    """Compare saved real request data/system/schema, without new inference."""
    from ttf_news import analysis as a
    from ttf_news.prompts import system_prompt
    attempts=record.get('attempts') or []
    meta=(record.get('result') or {}).get('_meta') or record.get('audit') or {}
    calls=meta.get('model_calls') or []
    if not attempts or not calls or not isinstance(context,dict):
        return {'passed':False,'reason':'saved_actual_request_or_metadata_missing'}
    try:
        data,_=a.prepare_context(copy.deepcopy(context),detail,reserve_tokens=32)
        data['compression'].update(validate_raw_evidence=True,request_reasoning=call_requested_reasoning(calls[0]) or (record.get('model_call_configuration') or {}).get('reasoning') or 'medium')
        current_schema_sha=sha(a._json(a.response_schema(detail,data)))
        same_data=attempts[0].get('input')==data
        same_system=attempts[0].get('system')==system_prompt(detail)
        same_schema=calls[0].get('response_schema_sha256')==current_schema_sha
        return {'passed':same_data and same_system and same_schema,'same_prepared_data':same_data,
                'same_system':same_system,'same_response_schema':same_schema,
                'current_response_schema_sha256':current_schema_sha,'no_model_call':True,
                'scope':'Actual first request input equivalence only; final gates and actual retry paths remain separately recorded.'}
    except Exception as exc:
        return {'passed':False,'reason':safe(str(exc)[:500])}


def summarize_anchor_validation(cases: list[dict], selected: dict, hashes: dict, prompt_version: str,
                                fixture: dict | None = None) -> dict:
    """Keep real v2.1 anchor runs separate from the frozen v2.0 fifteen."""
    identifiers = [case['id'] for case in cases]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError('Duplicate case in the separate anchor fixture')
    records = [selected[key] for key in identifiers if key in selected]
    missing = sorted(set(identifiers)-set(selected))
    current_runs = []
    for row in records:
        generated = row.get('generation_source_sha256') or {}
        # Offline validation cannot establish that the new model input was
        # actually exercised.  Exact source and actual request evidence must.
        if row.get('generation_prompt_version') == prompt_version and row.get('attempts', 0) > 0 and not row.get('validation_only') and (row.get('first_actual_request_matches_current_input') or {}).get('passed') is True and all(
                bool(hashes.get(name)) and generated.get(name) == hashes.get(name) for name in MODEL_INPUT_SOURCE_FILES):
            current_runs.append(row['case'])
    failures = [row['case'] for row in records if not row.get('passed')]
    return {'cohort': 'separate_anchor_v2_1_real_model_validation' if prompt_version=='ttf-news-standalone-2.1' else 'separate_anchor_current_version_real_model_validation',
            'scope': '独立四项价格锚点对照；与十五项机制对照分别列示。生成版本、实际请求与当前源码逐条核对；旧回答的离线复检不算当前版本新推理。',
            'current_prompt_version':prompt_version,
            'generation_prompt_version_counts':dict(Counter(row.get('generation_prompt_version') for row in records)),
            'fixture': fixture, 'required_case_count': len(cases), 'selected_case_count': len(records),
            'passed_case_count': sum(bool(row.get('passed')) for row in records),
            'missing_cases': missing, 'failing_cases': failures,
            'current_model_input_real_model_cases': sorted(current_runs),
            'current_model_input_real_model_case_count': len(current_runs),
            'generation_matches_all_final_sources_cases': sorted(row['case'] for row in records
                if all((row.get('generation_source_sha256') or {}).get(name)==hashes.get(name) for name in INFERENCE_SOURCE_FILES)),
            'gate_revalidated_after_source_change_cases': sorted(row['case'] for row in records
                if row.get('passed') and any((row.get('generation_source_sha256') or {}).get(name)!=hashes.get(name) for name in ('ttf_news/analysis.py','ttf_news/semantics.py'))),
            'version_boundary': '当前版本实际模型输入相关源码一致，最终语义门另行离线复检；生成时与最终语义源码SHA不同会逐条保留，不冒充最终源码同次冻结生成。',
            'passed': bool(cases) and not missing and not failures and len(current_runs) == len(cases),
            'records': records}


def semantic_regeneration_required(prompt_version: str) -> bool:
    match=re.fullmatch(r'ttf-news-standalone-(\d+)\.(\d+)',str(prompt_version))
    return bool(match and tuple(map(int,match.groups()))>=(2,2))


def release_selected_rows(report: dict) -> list[dict]:
    """A full fresh cohort never borrows a missing case from older phases."""
    fresh=report.get('semantic_regeneration_validation') or {}
    if semantic_regeneration_required(report.get('current_prompt_version','ttf-news-standalone-2.1')) or fresh.get('required'):
        return fresh.get('records') or []
    return ((report.get('comparison') or {}).get('selected_records') or [])+((report.get('anchor_validation') or {}).get('records') or [])


def release_detail_rows(report: dict) -> list[dict]:
    """Current actual details cannot borrow from, or regenerate, old cohorts."""
    if semantic_regeneration_required(report.get('current_prompt_version','')):
        return (report.get('semantic_regeneration_validation') or {}).get('detail_records') or []
    return (report.get('detail_validation') or {}).get('records') or []


def summarize_semantic_regeneration(cases: list[dict], groups: dict, hashes: dict,
                                    prompt_version: str, fixture: dict | None = None) -> dict:
    """Only new actual files in the explicit fresh phase can satisfy this cohort."""
    required=semantic_regeneration_required(prompt_version)
    expected=set(CASE_NAMES)-{SEMANTIC_REGENERATION_WELL_CASE}
    expected|=set(ANCHOR_CASE_NAMES)|{SEMANTIC_REGENERATION_WELL_CASE}
    identifiers=[case['id'] for case in cases]
    if cases and (len(identifiers)!=20 or len(set(identifiers))!=20 or set(identifiers)!=expected):
        raise ValueError('Fresh semantic-regeneration fixture must contain the original fifteen, four anchors and real_future_well_b1 exactly once')
    # This is a complete new execution cohort after brief request/schema
    # changes. Missing new files remain missing, even if old cases passed.
    bounded_cohort=any(stage in groups for stage in BOUNDED_COHORT_STAGES)
    cohort_stages=BOUNDED_COHORT_STAGES if bounded_cohort else CURRENT_COHORT_STAGES
    selected={}
    for stage in cohort_stages:
        for row in (groups.get(stage) or {}).get('records') or []:selected[row['case']]=row
    identifiers=identifiers or sorted(expected)
    records=[selected[key] for key in identifiers if key in selected]
    missing=sorted(set(identifiers)-set(selected));unexpected=sorted(set(selected)-set(identifiers))
    actual=[];exact=[];equivalent=[]
    for row in records:
        generated=row.get('generation_source_sha256') or {}
        real=(row.get('stage') in CURRENT_COHORT_STAGES and row.get('generation_prompt_version')==prompt_version
              and row.get('attempts',0)>0 and not row.get('validation_only')
              and (row.get('first_actual_request_matches_current_input') or {}).get('passed') is True)
        proved=(prompt_version=='ttf-news-standalone-2.3' and (row.get('current_complete_path_equivalence') or {}).get('compatible') is True
                and (row.get('current_complete_path_equivalence') or {}).get('mode')=='same_version_actual_execution_with_verified_final_path_equivalence')
        if real and (all(bool(hashes.get(name)) and generated.get(name)==hashes.get(name) for name in MODEL_INPUT_SOURCE_FILES) or proved):actual.append(row['case'])
        all_generated=row.get('generation_all_ttf_source_sha256') or generated
        if real and all_generated==ttf_source_hashes(hashes):exact.append(row['case'])
        elif real and proved:equivalent.append(row['case'])
    selected_details={}
    for stage in cohort_stages:
        for row in (groups.get(stage+'-detail') or {}).get('records') or []:selected_details[row['case']]=row
    details=list(selected_details.values())
    current_details=[row for row in details if row.get('passed') and row.get('has_detail_result')
                     and row.get('generation_prompt_version')==prompt_version and row.get('attempts',0)>0
                     and not row.get('validation_only') and (row.get('first_actual_request_matches_current_input') or {}).get('passed') is True
                     and ((row.get('generation_all_ttf_source_sha256') or row.get('generation_source_sha256'))==ttf_source_hashes(hashes)
                          or ((row.get('current_complete_path_equivalence') or {}).get('compatible') is True
                              and (row.get('current_complete_path_equivalence') or {}).get('mode')=='same_version_actual_execution_with_verified_final_path_equivalence'))]
    actual_details=[row for row in details if row.get('analysis_kind')=='detail' and row.get('generation_prompt_version')==prompt_version
        and row.get('attempts',0)>0 and not row.get('validation_only') and (row.get('recorded_run_succeeded') or row.get('recorded_error'))
        and (row.get('first_actual_request_matches_current_input') or {}).get('passed') is True
        and ((row.get('generation_all_ttf_source_sha256') or row.get('generation_source_sha256'))==ttf_source_hashes(hashes)
             or (row.get('current_complete_path_equivalence') or {}).get('compatible') is True)]
    failures=[row['case'] for row in records if not row.get('passed')]
    execution_complete=bool(cases) and not missing and not unexpected and len(exact)+len(equivalent)==20 and bool(actual_details)
    return {'required':required,'cohort':'semantic_regeneration_current_version_twenty_real_model_validation',
            'current_prompt_version':prompt_version,'fixture':fixture,'stage':SEMANTIC_REGENERATION_STAGE,
            'required_generation_stage':BOUNDED_OUTPUT_STAGE if bounded_cohort else None,
            'case_only_followup_stage':PRESSURE_PREDICATE_STAGE if bounded_cohort else None,
            'case_only_followup_stages':list(BOUNDED_COHORT_STAGES[1:]) if bounded_cohort else [],
            'earlier_same_version_phases_used_to_fill_missing_current_cases':False,
            'required_case_count':20,'selected_case_count':len(records),'passed_case_count':sum(bool(row.get('passed')) for row in records),
            'deliverable_unvalidated_label_disagreement_count':sum(operational_label_delivery(row) for row in records),
            'deliverable_unvalidated_label_disagreement_cases':[row['case'] for row in records if operational_label_delivery(row)],
            'unavailable_output_count':sum(not row.get('passed') and not operational_label_delivery(row) for row in records),
            'missing_cases':missing,'unexpected_cases':unexpected,'failing_cases':failures,
            'current_model_input_real_model_cases':sorted(actual),'current_model_input_real_model_case_count':len(actual),
            'generation_matches_all_final_sources_cases':sorted(exact),'generation_matches_all_final_sources_count':len(exact),
            'verified_same_version_path_equivalence_cases':sorted(equivalent),'verified_same_version_path_equivalence_count':len(equivalent),
            'final_source_bound_execution_count':len(exact)+len(equivalent),
            'generation_prompt_version_counts':dict(Counter(row.get('generation_prompt_version') for row in records)),
            'current_version_detail_count':len(current_details),'current_version_actual_detail_count':len(actual_details),'detail_records':details,
            'execution_complete':execution_complete,
            'passed':execution_complete and not failures and bool(current_details) and all(row.get('passed') for row in details),
            'scope':'本次同版本二十项实际样本：原十五项机制、四项价格锚点与一项真实井源。最后源码直接执行与原实际执行后完整路径等价分别列数；后者不是最后源码新模型调用。受影响案例须后续已注册阶段的新实际终态，最新失败不回退；全部初次产物另行保留。不是行情准确率。',
            'records':records}


def distribution(values: list[float]) -> dict:
    values = sorted(values)
    if not values:
        return {"count": 0, "median": None, "p95": None, "max": None}
    return {"count": len(values), "median": round(statistics.median(values), 3),
            "p95": round(values[max(0, math.ceil(.95*len(values))-1)], 3), "max": round(values[-1], 3)}


def summarize_stage(records: list[dict]) -> dict:
    measured = [r["analyst_elapsed_seconds"] if r.get("analyst_elapsed_seconds") is not None else r.get("wall_seconds") for r in records]
    by_kind = {kind: distribution([r["analyst_elapsed_seconds"] if r.get("analyst_elapsed_seconds") is not None else r["wall_seconds"]
                                  for r in records if r.get("analysis_kind") == kind and
                                  (r.get("analyst_elapsed_seconds") is not None or r.get("wall_seconds") is not None)])
               for kind in ("brief", "detail")}
    return {"record_count": len(records), "passed_current_contract_and_logic": sum(r.get("passed", False) for r in records),
            "recorded_run_failures": sum(not r.get("recorded_run_succeeded", False) for r in records),
            "current_logic_failures": sum(not (r.get("current_logic_revalidation") or {}).get("passed", False) for r in records),
            "current_generation_policy_incompatible": sum(not (r.get("current_generation_policy_compatibility") or {"compatible":True}).get("compatible") for r in records),
            "retries": sum(r.get("retries", 0) for r in records), "model_attempts": sum(r.get("attempts", 0) for r in records),
            "truncated_attempts": sum(r.get("truncated_attempts", 0) for r in records),
            "latency_seconds": distribution([v for v in measured if v is not None]),
            "latency_by_analysis_kind": by_kind,
            "news_direction_counts": dict(Counter((r.get("expectation_checks") or {}).get("news_direction") for r in records if r.get('passed'))),
            "news_direction_distribution_basis": "Strict-contract passed outputs only; failed/rejected raw previews and unvalidated real-label disagreements are excluded. Independent full-prose review is performed by Codex subagents.",
            "diagnostic_raw_news_direction_counts": dict(Counter((r.get("expectation_checks") or {}).get("news_direction") for r in records if not r.get('passed'))),
            "records": records}


def candidate_direction_display(row: dict | None, closing: bool = False) -> str:
    if row is None:return '待运行'
    checks=row.get('expectation_checks') or {}
    direction=checks.get('close_direction' if closing else 'news_direction')
    value=f"{direction} / {checks.get('close_evidence_status')}" if closing else str(direction)
    if operational_label_delivery(row):return f'可交付／标签未验证（{value}）'
    if not row.get('passed'):
        return f'无有效输出（raw: {value}）' if direction is not None else '无有效输出'
    return value


def candidate_check_display(row: dict | None) -> str:
    if row is None:return '待运行'
    if row.get('passed'):return '通过'
    return '严格未通过／标签分歧可交付' if operational_label_delivery(row) else '需处理'


RELEASE_POLICY_ID='ttf-explicit-error-release-v1'
RELEASE_PROOFS=('independent_review','live_smoke','error_ui','public_scan','frontend_build','backend_deployment')
ERROR_UI_COMPOSITE_KIND='existing_real_failed_record_ui_plus_current_gate_revalidation_and_state_binding'


def known_accepted_error_state(path: Path, prompt_version: str) -> list[dict]:
    """A claimed resolution needs a current-version immutable proof anchor."""
    try:
        if _PUBLIC_COPY_MAP.get() is not None:artifact_source_sha(path)
        rows=copy.deepcopy(read_json(path)['records'])
        if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):
            raise ValueError('Accepted error state is malformed')
        for error in rows:
            error['resolution_verified']=False
            if error.get('status')!='resolved_with_real_validation':continue
            reference=error.get('resolution_evidence') or {};proof=ROOT/str(reference.get('file') or '')
            if proof.resolve().is_relative_to(ROOT.resolve()) and proof.is_file() and artifact_sha_matches(proof,reference.get('sha256')):
                resolution=read_json(proof)
                if not isinstance(resolution,dict):continue
                error['resolution_verified']=resolution.get('known_error_id')==error.get('id') and resolution.get('passed') is True and resolution.get('real_model_validation') is True and resolution.get('prompt_version')==prompt_version
        return rows
    except (OSError,ValueError,TypeError,KeyError):
        return [{'id':'accepted_error_state_unavailable','status':'unverified','resolution_verified':False}]


def evaluate_release_decision(report: dict, proofs: dict, selected_audits: dict, known_errors: list[dict] | None = None) -> dict:
    """Separate operational release evidence from unchanged all-output strict.

    The original product contract allows an explicit error after two failures.
    There is no benchmark availability quota and no rejected-as-correct score.
    """
    comparison=report.get('comparison') or {};original_rows=comparison.get('selected_records') or []
    anchor=report.get('anchor_validation') or {}
    anchor_required=report.get('current_prompt_version')=='ttf-news-standalone-2.1' or bool(anchor)
    anchor_rows=anchor.get('records') or []
    fresh=report.get('semantic_regeneration_validation') or {}
    fresh_required=semantic_regeneration_required(report.get('current_prompt_version')) or fresh.get('required') is True
    rows=release_selected_rows(report)
    detail_rows=release_detail_rows(report)
    label_disagreements=[row for row in rows if operational_label_delivery(row,selected_audits.get(row['case']) or {})]
    label_ids={row['case'] for row in label_disagreements}
    accepted=[row for row in rows if row.get('passed') or row['case'] in label_ids]
    rejected=[row for row in rows if not row.get('passed') and row['case'] not in label_ids]
    accepted_ids={row['case'] for row in accepted};rejected_ids={row['case'] for row in rejected}
    terminal=True
    for row in rows:
        audit=selected_audits.get(row['case']) or {}
        if row.get('passed') or row['case'] in label_ids:
            terminal=terminal and bool(audit.get('has_result')) and not audit.get('error')
        else:
            budget=audit.get('verified_budget_terminal') or {}
            budget_stop=(report.get('current_prompt_version')=='ttf-news-standalone-2.3' and budget.get('verified') is True
                         and budget.get('terminal_kind')=='repair_draft_input_budget_exhausted'
                         and budget.get('final_raw_independently_rejected') is True
                         and (budget.get('complete_path_proof') or {}).get('compatible') is True
                         and (budget.get('complete_path_proof') or {}).get('mode')=='same_version_actual_execution_with_verified_final_path_equivalence'
                         and audit.get('model_call_count')==1)
            terminal=terminal and not audit.get('has_result') and bool(audit.get('error')) and (audit.get('model_call_count')==2 or budget_stop)
    required_count=fresh.get('required_case_count',20) if fresh_required else comparison.get('required_case_count',0)+(anchor.get('required_case_count',0) if anchor_required else 0)
    checks={'all_scenarios_terminal':bool(rows) and len(rows)==required_count and len({row['case'] for row in rows})==len(rows) and not comparison.get('missing_cases') and not anchor.get('missing_cases') and terminal,
            'preservation':(report.get('preservation') or {}).get('passed') is True,
            'unit_regressions':(report.get('unit_tests') or {}).get('passed') is True,
            'frontend_tests':(report.get('frontend') or {}).get('test_pass_count',0)>0 and (report.get('frontend') or {}).get('test_fail_count')==0,
            'detail_valid_output':(bool(detail_rows) and all(row.get('passed') and row.get('has_detail_result') and row.get('analysis_kind')=='detail' for row in detail_rows)) if fresh_required else (report.get('detail_validation') or {}).get('passed') is True}
    if report.get('current_prompt_version')=='ttf-news-standalone-2.3':
        def verified_detail_terminal(row):
            audit=selected_audits.get('detail:'+row['case']) or {}
            if row.get('passed'):
                return audit.get('has_result') is True and audit.get('has_detail_result') is True and not audit.get('error')
            return (audit.get('has_result') is False and audit.get('has_detail_result') is False and bool(audit.get('error'))
                and audit.get('model_call_count')==2 and audit.get('complete_actual_attempt_audit') is True
                and audit.get('result_is_null') is True and audit.get('detail_is_null') is True
                and audit.get('production_two_failure_terminal') is True
                and audit.get('final_raw_independently_rejected') is True
                and (audit.get('current_complete_path_equivalence') or {}).get('compatible') is True)
        checks['operational_detail_terminal']=bool(detail_rows) and all(verified_detail_terminal(row) for row in detail_rows)
    if anchor_required:
        anchor_deliverable=anchor.get('passed') is True or (fresh_required and report.get('current_prompt_version')=='ttf-news-standalone-2.3'
            and bool(anchor_rows) and all(row.get('passed') or row['case'] in label_ids for row in anchor_rows))
        checks['anchor_current_version_real_model_validation' if fresh_required else 'anchor_v2_1_real_model_validation']=anchor.get('required_case_count')==4 and anchor_deliverable and anchor.get('current_model_input_real_model_case_count')==4
        if fresh_required and report.get('current_prompt_version')=='ttf-news-standalone-2.3':
            def verified_anchor_terminal(row):
                audit=selected_audits.get(row['case']) or {}
                if (audit.get('current_complete_path_equivalence') or {}).get('compatible') is not True:return False
                if row.get('passed') or row['case'] in label_ids:
                    return audit.get('has_result') is True and not audit.get('error')
                budget=audit.get('verified_budget_terminal') or {}
                verified_budget=(audit.get('model_call_count')==1 and budget.get('verified') is True
                    and budget.get('terminal_kind')=='repair_draft_input_budget_exhausted'
                    and budget.get('final_raw_independently_rejected') is True
                    and (budget.get('complete_path_proof') or {}).get('compatible') is True
                    and (budget.get('complete_path_proof') or {}).get('mode')=='same_version_actual_execution_with_verified_final_path_equivalence')
                two_failures=(audit.get('model_call_count')==2 and audit.get('complete_actual_attempt_audit') is True
                    and audit.get('production_two_failure_terminal') is True)
                return (audit.get('has_result') is False and audit.get('result_is_null') is True and bool(audit.get('error'))
                    and audit.get('final_raw_independently_rejected') is True and (two_failures or verified_budget))
            checks['operational_anchor_terminal']=(anchor.get('required_case_count')==4
                and len(anchor_rows)==4 and len({row['case'] for row in anchor_rows})==4
                and anchor.get('current_model_input_real_model_case_count')==4
                and all(verified_anchor_terminal(row) for row in anchor_rows))
    if fresh_required:
        detail_actual=fresh.get('current_version_actual_detail_count',fresh.get('current_version_detail_count',0)) if report.get('current_prompt_version')=='ttf-news-standalone-2.3' else fresh.get('current_version_detail_count',0)
        checks['fresh_twenty_current_version_real_model_validation']=fresh.get('required_case_count')==20 and fresh.get('execution_complete') is True and fresh.get('current_model_input_real_model_case_count')==20 and fresh.get('final_source_bound_execution_count',fresh.get('generation_matches_all_final_sources_count'))==20 and detail_actual>0
    if report.get('current_prompt_version')=='ttf-news-standalone-2.3':
        checks['fresh_record_paths_still_bound_to_final_source']=bool(rows) and all((selected_audits.get(row['case'],{}).get('current_complete_path_equivalence') or {}).get('compatible') is True for row in rows)
        checks['fresh_detail_paths_still_bound_to_final_source']=bool(detail_rows) and all((selected_audits.get('detail:'+row['case'],{}).get('current_complete_path_equivalence') or {}).get('compatible') is True for row in detail_rows)
    unresolved=[row for row in (known_errors or []) if row.get('status')!='resolved_with_real_validation' or row.get('resolution_verified') is not True]
    checks['no_known_accepted_errors']=not unresolved
    data={name:(proofs.get(name) or {}).get('data') or {} for name in RELEASE_PROOFS}
    verified={name:(proofs.get(name) or {}).get('verified') is True for name in RELEASE_PROOFS}
    review=data['independent_review'];review_hashes=review.get('selected_record_sha256') or {}
    checks['accepted_outputs_independently_reviewed']=verified['independent_review'] and review.get('all_valid_outputs_reviewed') is True and review.get('known_accepted_logic_errors')==[] and set(review.get('reviewed_accepted_cases') or [])==accepted_ids and set(review.get('rejected_cases') or [])==rejected_ids and all(review_hashes.get(identifier)==(selected_audits.get(identifier) or {}).get('file_sha256') for identifier in accepted_ids)
    checks['accepted_detail_independently_reviewed']=verified['independent_review'] and review.get('accepted_detail_reviewed') is True and all((review.get('accepted_detail_record_sha256') or {}).get(row['case'])==(selected_audits.get('detail:'+row['case']) or {}).get('file_sha256') for row in detail_rows if row.get('passed'))
    rejected_detail_rows=[row for row in detail_rows if not row.get('passed')]
    if report.get('current_prompt_version')=='ttf-news-standalone-2.3' and rejected_detail_rows:
        checks['rejected_details_independently_reviewed']=verified['independent_review'] and review.get('rejected_details_independently_checked') is True and set(review.get('rejected_detail_cases') or [])=={row['case'] for row in rejected_detail_rows} and all((review.get('rejected_detail_record_sha256') or {}).get(row['case'])==(selected_audits.get('detail:'+row['case']) or {}).get('file_sha256') for row in rejected_detail_rows)
    rejected_anchor_rows=[row for row in anchor_rows if not row.get('passed') and row['case'] not in label_ids]
    if report.get('current_prompt_version')=='ttf-news-standalone-2.3' and rejected_anchor_rows:
        rejected_anchor_ids={row['case'] for row in rejected_anchor_rows}
        anchor_review_hashes=review.get('rejected_anchor_record_sha256') or {}
        checks['rejected_anchors_independently_reviewed']=(verified['independent_review']
            and review.get('rejected_anchors_independently_checked') is True
            and set(review.get('rejected_anchor_cases') or [])==rejected_anchor_ids
            and set(anchor_review_hashes)==rejected_anchor_ids
            and all(anchor_review_hashes.get(row['case'])==(selected_audits.get(row['case']) or {}).get('file_sha256') for row in rejected_anchor_rows))
    core=('ttf_news/analysis.py','ttf_news/semantics.py','ttf_news/prompts.py')+(('ttf_news/context.py',) if anchor_required else ())
    checks['reviewed_current_source']=verified['independent_review'] and all(re.fullmatch(r'[0-9a-f]{64}',str((report.get('code_sha256') or {}).get(name,''))) and (review.get('source_sha256') or {}).get(name)==(report.get('code_sha256') or {}).get(name) for name in core)
    smoke=data['live_smoke'];checks['real_v2_success_end_to_end']=verified['live_smoke'] and smoke.get('status')=='passed' and smoke.get('failed_stages')==[] and smoke.get('missing_required_stages')==[]
    if anchor_required:
        checks['real_v2_success_end_to_end']=checks['real_v2_success_end_to_end'] and smoke.get('expected_prompt_version',smoke.get('prompt_version'))==report.get('current_prompt_version')
        stages={row.get('name'):row.get('checks') or {} for row in smoke.get('stages') or [] if row.get('status')=='passed'}
        live_review_rows=review.get('live_current_version_outputs')
        if live_review_rows is None and report.get('current_prompt_version')=='ttf-news-standalone-2.1':live_review_rows=review.get('live_v2_1_outputs')
        reviewed_live={row.get('role'):row for row in live_review_rows or []}
        required_live={'manual_brief':'manual_real_brief','manual_detail':'manual_real_detail','current_live':'current_live_forecast_v2'}
        checks['real_current_version_outputs_independently_reviewed' if fresh_required else 'real_v2_1_outputs_independently_reviewed']=verified['independent_review'] and set(reviewed_live)==set(required_live) and all(
            bool(stages.get(stage,{}).get('analysis_id')) and bool(re.fullmatch(r'[0-9a-f]{64}',str(stages[stage].get('summary_sha256',''))))
            and reviewed_live[role].get('analysis_id')==stages[stage].get('analysis_id')
            and reviewed_live[role].get('summary_sha256')==stages[stage].get('summary_sha256')
            and reviewed_live[role].get('prompt_version')==report.get('current_prompt_version')
            and reviewed_live[role].get('verdict')=='pass' and reviewed_live[role].get('known_logic_errors')==[]
            for role,stage in required_live.items())
    ui=data['error_ui']
    combined=ui.get('evidence_kind')=='existing_real_failed_analysis_api_ui_plus_v2_isolated_rejection' and ui.get('same_record_v2_end_to_end') is False and ui.get('v2_isolated_failure_cases')==sorted(rejected_ids)
    current_combined=ui.get('evidence_kind')==ERROR_UI_COMPOSITE_KIND and ui.get('same_record_v2_end_to_end') is False and ui.get('backend_prompt_version')==report.get('current_prompt_version') and ui.get('record_origin_prompt_version')!=report.get('current_prompt_version') and (proofs.get('error_ui') or {}).get('linked_current_rejections_verified') is True and (proofs.get('error_ui') or {}).get('linked_state_binding_verified') is True and (proofs.get('error_ui') or {}).get('linked_ui_observation_verified') is True
    same_v2=ui.get('evidence_kind')=='real_v2_failed_analysis_api_ui' and ui.get('same_record_v2_end_to_end') is True and ui.get('prompt_version')==report.get('current_prompt_version')
    api_null=ui.get('api_result_is_null',ui.get('api_result_null'))
    ui_visible=ui.get('ui_error_visible',ui.get('error_visible'))
    direction_count=ui.get('ui_direction_element_count',ui.get('direction_elements'))
    no_flat=ui.get('default_flat_used') is False if 'default_flat_used' in ui else ui.get('no_flat_fallback') is True
    checks['explicit_error_ui_without_direction']=verified['error_ui'] and ui.get('status')=='passed' and (combined or same_v2 or current_combined) and api_null is True and ui_visible is True and direction_count==0 and no_flat
    scan=data['public_scan'];checks['public_assets_scanned']=verified['public_scan'] and scan.get('status')=='passed' and scan.get('finding_count')==0 and (scan.get('database') or {}).get('unchanged') is True
    checks['frontend_build']=verified['frontend_build'] and data['frontend_build'].get('passed') is True
    deployment=data['backend_deployment'];checks['deployed_current_backend']=verified['backend_deployment'] and deployment.get('new_prompt_version',deployment.get('prompt_version'))==report.get('current_prompt_version') and all((deployment.get('source_hashes') or {}).get(name)==(report.get('code_sha256') or {}).get(name) for name in core)
    strict_only_checks={'detail_valid_output':checks['detail_valid_output']} if report.get('current_prompt_version')=='ttf-news-standalone-2.3' else {}
    if report.get('current_prompt_version')=='ttf-news-standalone-2.3' and anchor_required:
        strict_only_checks['anchor_current_version_real_model_validation']=checks['anchor_current_version_real_model_validation']
    operational_checks={name:value for name,value in checks.items() if name not in strict_only_checks}
    blockers=[name for name,value in operational_checks.items() if not value]
    availability_rows=rows if fresh_required else original_rows
    availability_required=required_count if fresh_required else comparison.get('required_case_count')
    return {'generated_at':datetime.now(timezone.utc).isoformat(),'policy_id':RELEASE_POLICY_ID,
            'status':'ready_with_known_model_availability_limits' if not blockers else 'not_ready',
            'release_ready':not blockers,'strict_report_status':report.get('status'),
            'strict_all_outputs_success':all(row.get('passed') for row in rows) and len(rows)==required_count and checks['detail_valid_output'],
            'strict_standard_unchanged':True,
            'availability':{'cohort':fresh.get('cohort') if fresh_required else 'original_fifteen_generated_v2_0_offline_revalidated','effective_outputs':sum(bool(row.get('passed')) for row in availability_rows),
                'strict_contract_passed_outputs':sum(bool(row.get('passed')) for row in availability_rows),
                'deliverable_unvalidated_real_label_disagreements':len(label_disagreements),
                'deliverable_unvalidated_real_label_disagreement_cases':sorted(label_ids),
                'total_deliverable_analyses':sum(bool(row.get('passed')) for row in availability_rows)+len(label_disagreements),
                'completed_scenarios':len(availability_rows),'required_scenarios':availability_required,'rejected_outputs':sum(not row.get('passed') and row['case'] not in label_ids for row in availability_rows),'rejected_cases':sorted(row['case'] for row in availability_rows if not row.get('passed') and row['case'] not in label_ids),'rejected_counted_as_correct_predictions':False,
                'label_disagreement_counted_as_correct_prediction':False,'market_accuracy':None,'is_v2_1_new_model_performance':False,'is_current_version_new_model_performance':bool(fresh_required and checks.get('fresh_twenty_current_version_real_model_validation')),'generation_prompt_version_counts':dict(Counter(row.get('generation_prompt_version') for row in availability_rows))},
            'real_news_label_disagreements':[{'case':row['case'],**selected_audits[row['case']]['operational_label_variance']} for row in label_disagreements],
            'execution_source_binding':{'direct_final_source_case_count':fresh.get('generation_matches_all_final_sources_count',0),
                'verified_same_version_complete_path_case_count':fresh.get('verified_same_version_path_equivalence_count',0),
                'same_version_path_equivalence_is_new_final_source_model_call':False},
            'current_release_detail_cases':[row['case'] for row in detail_rows],
            'diagnostic_detail_availability':{'valid_outputs':sum(bool(row.get('passed')) for row in detail_rows),'unavailable_outputs':len(rejected_detail_rows),
                'rejected_cases':[row['case'] for row in rejected_detail_rows],'rejected_counted_as_correct_predictions':False,
                'production_success_requirement_unchanged':True},
            'historical_details_not_used_as_current_execution':[row.get('file') or row['case'] for row in (report.get('detail_validation') or {}).get('records') or [] if row not in detail_rows],
            'verified_one_call_budget_failures':[{'case':row['case'],**selected_audits[row['case']]['verified_budget_terminal']} for row in rejected if (selected_audits.get(row['case'],{}).get('verified_budget_terminal') or {}).get('verified') is True],
            'anchor_availability':{'cohort':anchor.get('cohort','separate_anchor_v2_1_real_model_validation'),'effective_outputs':sum(bool(row.get('passed')) for row in anchor_rows),
                'deliverable_unvalidated_real_label_disagreements':sum(row['case'] in label_ids for row in anchor_rows),
                'completed_scenarios':len(anchor_rows),'required_scenarios':anchor.get('required_case_count',0),'rejected_outputs':sum(not row.get('passed') and row['case'] not in label_ids for row in anchor_rows),'rejected_cases':sorted(row['case'] for row in anchor_rows if not row.get('passed') and row['case'] not in label_ids),'current_model_input_real_model_case_count':anchor.get('current_model_input_real_model_case_count',0),
                'all_anchor_outputs_strict_success':len(anchor_rows)==4 and all(row.get('passed') for row in anchor_rows),
                'operational_anchor_terminal':checks.get('operational_anchor_terminal'),
                'rejected_counted_as_correct_predictions':False,'market_accuracy':None},
            'historical_cohorts':report.get('historical_cohorts') or {},
            'prominent_notice':('未全通过；带已知模型可用性限制发布。拒绝输出不算正确预测。' if rejected else '输出与实际市场准确率分别评估。')+(' 另有真实新闻标签分歧的可交付分析，严格契约仍未通过，不算正确预测。' if label_disagreements else ''),
            'checks':checks,'strict_only_checks':strict_only_checks,'operational_checks':operational_checks,'blockers':blockers,
            'known_accepted_errors':known_errors or [],'unresolved_accepted_error_count':len(unresolved),
            'evidence':{name:{key:value for key,value in (proofs.get(name) or {}).items() if key!='data'} for name in RELEASE_PROOFS},
            'error_ui_evidence_boundary':'Real v2 failed analysis shown through the actual API/UI for the same record.' if same_v2 else 'Existing earlier-version real failed record displayed in the current API/UI, plus final current-gate rejection of retained model raw and current runtime state-binding tests; not a new current-version model failure.' if current_combined else 'Existing real failed-record API/UI display plus current v2 isolated rejection and state-binding regressions; not the same-record v2 end-to-end failure.',
            'public_pages_followup_pending':ui.get('public_pages_followup_pending') is True,
            'no_model_calls':True,'no_database_access':True,'no_network_calls':True,
            'policy_basis':'Original product requirement: after two validation failures show explicit error; never invent up/flat. All accepted outputs need independent causal review and real successful v2 manual/detail/English/current-live end-to-end evidence. No minimum diagnostic availability percentage is imposed.',
            'anchor_policy_correction':'The former operational all-four-anchor-success requirement introduced a diagnostic-subset availability quota absent from the original policy. The existing all-success check stays factual/strict-only. Operational acceptance requires all4 current-input actual paths bound to final source, accepted-output review or exact independently reviewed genuine rejection; rejected anchors stay unavailable and are not correct forecasts. Actual production manual/detail/English/current-live success remains mandatory.',
            'detail_policy_correction':'The former operational all-diagnostic-details-success check duplicated the strict requirement. detail_valid_output remains factual and may be false; a fully audited two-response rejection is terminal and unavailable. Actual production manual/detail/English/current-live success and independent review remain mandatory.'}


def verified_project_reference(reference: dict) -> tuple[Path, bytes]:
    if not isinstance(reference,dict):raise ValueError('Linked evidence reference is malformed')
    path=ROOT/str(reference.get('file') or '')
    if not path.resolve().is_relative_to(ROOT.resolve()) or not path.is_file():
        raise ValueError('Linked evidence missing or outside project')
    raw=path.read_bytes()
    if not artifact_sha_matches(path,reference.get('sha256')):raise ValueError('Linked evidence SHA mismatch')
    return path,raw


def verify_composite_error_ui(data: dict, report: dict) -> dict:
    """Bind an old real error UI to current gates and runtime failure tests."""
    _,raw=verified_project_reference(data.get('ui_observation') or {})
    observation=json.loads(raw)
    fields=('status','analysis_id','record_origin_prompt_version','backend_prompt_version',
            'api_result_is_null','ui_error_visible','ui_direction_element_count','default_flat_used',
            'screenshot_file','screenshot_sha256','api_response_sha256')
    if not isinstance(observation,dict) or any(observation.get(key)!=data.get(key) for key in fields):
        raise ValueError('Composite UI differs from the anchored actual observation')
    _,raw=verified_project_reference(data.get('current_gate_revalidation') or {})
    regression=json.loads(raw)
    if not isinstance(regression,dict):raise ValueError('Current-gate regression is malformed')
    selected=release_selected_rows(report)
    retained={row.get('case'):row for row in regression.get('records') or []}
    if regression.get('passed') is not True or regression.get('current_prompt_version')!=report.get('current_prompt_version') or set(retained)!={row['case'] for row in selected}:
        raise ValueError('Current-gate regression does not cover the selected cohorts')
    core=('ttf_news/analysis.py','ttf_news/semantics.py','ttf_news/prompts.py','ttf_news/context.py')
    if any((regression.get('source_sha256') or {}).get(key)!=(report.get('code_sha256') or {}).get(key) for key in core):
        raise ValueError('Retained rejection proof was not checked with final current source')
    for row in selected:
        current=retained[row['case']]
        path=ROOT/row['file']
        if not path.resolve().is_relative_to(ROOT.resolve()) or not path.is_file() or not artifact_sha_matches(path,current.get('file_sha256')):
            raise ValueError('Retained model record SHA mismatch')
        if row.get('passed') or operational_label_delivery(row):
            if current.get('final_output_regression_passed') is not True:raise ValueError('Accepted retained output failed current gates')
        elif current.get('current_logic_revalidation',{}).get('passed') is not False or current.get('recorded_run_succeeded') is not False:
            raise ValueError('Rejected raw not independently rejected by final current gates')
    path,raw=verified_project_reference(data.get('state_binding_junit') or {})
    if not artifact_sha_matches(path,(report.get('unit_tests') or {}).get('sha256')) or not junit(path).get('passed'):
        raise ValueError('State-binding test proof does not match the passed current test suite')
    cases=[node for node in ET.fromstring(raw).iter('testcase') if node.get('name','').startswith('test_real_generation_or_precommit_failure_remains_failed_and_retryable')]
    for kind in ('brief','detail','translation'):
        for defect in ('generation','precommit'):
            kinds={kind,'short'} if kind=='brief' else {kind}
            defects={defect,'commit'} if defect=='precommit' else {defect}
            matching=[]
            for node in cases:
                name=node.get('name','')
                parameters=set(name.split('[',1)[1].rstrip(']').split('-')) if '[' in name else set()
                if parameters & kinds and parameters & defects:matching.append(node)
            if not matching or any(node.find(tag) is not None for node in matching for tag in ('failure','error','skipped')):
                raise ValueError('Current runtime failure-to-null state-binding regression missing or failed')
    return {'linked_current_rejections_verified':True,'linked_state_binding_verified':True,'linked_ui_observation_verified':True}


def _build_release_decision(report_path: Path, checklist_path: Path) -> dict:
    report=read_json(report_path);checklist=read_json(checklist_path);proofs={}
    anchored=checklist.get('policy_id')==RELEASE_POLICY_ID and artifact_sha_matches(report_path,checklist.get('main_report_sha256'))
    for name in RELEASE_PROOFS:
        reference=(checklist.get('evidence') or {}).get(name) or {};entry={'verified':False,'file':reference.get('file'),'sha256':reference.get('sha256')}
        try:
            path=ROOT/str(reference.get('file') or '')
            if not path.resolve().is_relative_to(ROOT.resolve()) or not path.is_file():raise ValueError('Evidence file missing or outside project')
            raw=path.read_bytes()
            if not anchored or not artifact_sha_matches(path,reference.get('sha256')):raise ValueError('Unanchored or stale evidence hash')
            data={'passed':bool(re.search(r'(?:✓|✔)?\s*built in\s+[\d.]+',raw.decode('utf-8')))} if name=='frontend_build' else json.loads(raw)
            if not isinstance(data,dict):raise ValueError('Evidence must be a JSON object')
            if name=='error_ui':
                screenshot=ROOT/str(data.get('screenshot_file') or data.get('screenshot') or '')
                if not screenshot.resolve().is_relative_to(ROOT.resolve()) or not screenshot.is_file() or not artifact_sha_matches(screenshot,data.get('screenshot_sha256')):
                    raise ValueError('Error UI screenshot missing or SHA mismatch')
                entry['screenshot_verified']=True
                if data.get('evidence_kind')==ERROR_UI_COMPOSITE_KIND:
                    entry.update(verify_composite_error_ui(data,report))
                if data.get('evidence_kind')=='real_v2_failed_analysis_api_ui':
                    api_path=ROOT/str(data.get('api_evidence_file') or data.get('api_record') or '')
                    if not api_path.resolve().is_relative_to(ROOT.resolve()) or not api_path.is_file():raise ValueError('Linked v2 API evidence missing')
                    api_bytes=api_path.read_bytes();api=json.loads(api_bytes)
                    if not (api.get('analysis_id')==data.get('analysis_id') and api.get('prompt_version')==data.get('prompt_version') and api.get('http_status')==200 and api.get('status')=='failed' and api.get('result_is_null') is True and api.get('no_generated_direction') is True):raise ValueError('Linked same-record v2 API error evidence disagrees')
                    entry['api_evidence_sha256']=sha(api_bytes)
            entry.update(verified=True,data=data)
            if _PUBLIC_COPY_MAP.get() is not None:entry['public_artifact_sha256']=sha(raw)
        except (OSError,ValueError,TypeError) as exc:entry['error']=safe(str(exc))
        proofs[name]=entry
    audits={};current_hashes=code_hashes()
    selected_rows=release_selected_rows(report)
    for row in selected_rows:
        try:
            path=ROOT/row['file'];raw=path.read_bytes();record=json.loads(raw);meta=(record.get('result') or {}).get('_meta') or record.get('audit') or {}
            audits[row['case']]={'file_sha256':artifact_source_sha(path),'has_result':bool(record.get('result')),'error':record.get('error'),'model_call_count':max(len(record.get('attempts') or []),len(meta.get('model_calls') or []))}
            if report.get('current_prompt_version')=='ttf-news-standalone-2.3':audits[row['case']]['current_complete_path_equivalence']=verify_path_equivalence(path,record,current_hashes,row.get('stage',path.parent.name),report['current_prompt_version'])
            if report.get('current_prompt_version')=='ttf-news-standalone-2.3' and row['case'] in ANCHOR_CASE_NAMES:
                audit=audits[row['case']]
                audit['result_is_null']=record.get('result') is None
                try:audit['complete_actual_attempt_audit']=record_execution_path(record)['attempt_count']==2
                except (ValueError,TypeError,KeyError):audit['complete_actual_attempt_audit']=False
                audit['production_two_failure_terminal']=(str(record.get('error') or '').startswith('模型JSON两次校验失败，未生成方向：')
                    and len(meta.get('model_calls') or [])==2 and all(call.get('validation_error') for call in meta['model_calls']))
                audit['final_raw_independently_rejected']=(revalidate_result(last_raw_response(record),record.get('input_snapshot') or {},False).get('passed') is False
                    if not record.get('result') else False)
            if row['case']==REAL_ANCHOR_VARIANCE_CASE:
                fixture=ROOT/'reports/repair_2026-10-20/semantic_regeneration_cases_v2_2.json'
                if _PUBLIC_COPY_MAP.get() is not None:artifact_source_sha(fixture)
                case=next((item for item in read_json(fixture) if item.get('id')==REAL_ANCHOR_VARIANCE_CASE),{})
                context=record.get('input_snapshot') or {}
                audits[row['case']]['operational_label_variance']=verify_real_anchor_label_variance(path,record,case,current_hashes,
                    revalidate_result(record.get('result'),context,False),generation_policy_compatibility(record,context,False),
                    audits[row['case']].get('current_complete_path_equivalence') or {})
            if (meta.get('repair_draft') or {}).get('reason')=='complete_sources_and_draft_exceed_budget':
                context=record.get('input_snapshot') or {}
                detail=bool(record.get('detail') or meta.get('detail'))
                logic=revalidate_result(last_raw_response(record),context,detail)
                audits[row['case']]['verified_budget_terminal']=verify_budget_terminal(path,record,current_hashes,row.get('stage',path.parent.name),report.get('current_prompt_version'),logic)
            if _PUBLIC_COPY_MAP.get() is not None:audits[row['case']]['public_artifact_sha256']=sha(raw)
        except (OSError,ValueError,TypeError,KeyError):audits[row['case']]={}
    for row in release_detail_rows(report):
        try:
            path=ROOT/row['file'];record=read_json(path);meta=(record.get('result') or {}).get('_meta') or record.get('audit') or {}
            audit={'file_sha256':artifact_source_sha(path),'has_result':bool(record.get('result')),
                'has_detail_result':bool(record.get('detail_result') or (record.get('result') or {}).get('detail')),
                'result_is_null':record.get('result') is None,'detail_is_null':record.get('detail_result') is None and (record.get('result') or {}).get('detail') is None,
                'error':record.get('error'),'model_call_count':max(len(record.get('attempts') or []),len(meta.get('model_calls') or []))}
            audits['detail:'+row['case']]=audit
            if report.get('current_prompt_version')=='ttf-news-standalone-2.3':
                audit['current_complete_path_equivalence']=verify_path_equivalence(path,record,current_hashes,row.get('stage',path.parent.name),report['current_prompt_version'])
                try:audit['complete_actual_attempt_audit']=record_execution_path(record)['attempt_count']==2
                except (ValueError,TypeError,KeyError):audit['complete_actual_attempt_audit']=False
                audit['production_two_failure_terminal']=str(record.get('error') or '').startswith('模型JSON两次校验失败，未生成方向：') and len(meta.get('model_calls') or [])==2 and all(call.get('validation_error') for call in meta['model_calls'])
                audit['final_raw_independently_rejected']=revalidate_result(last_raw_response(record),record.get('input_snapshot') or {},True).get('passed') is False if not record.get('result') else False
        except (OSError,ValueError,TypeError,KeyError):audits['detail:'+row['case']]={}
    error_path=ROOT/'reports/repair_2026-10-20/release_known_accepted_errors.json'
    known_errors=known_accepted_error_state(error_path,report.get('current_prompt_version'))
    output=evaluate_release_decision(report,proofs,audits,known_errors);output.update(main_report={'file':relative(report_path),'sha256':artifact_source_sha(report_path)},checklist={'file':relative(checklist_path),'sha256':artifact_source_sha(checklist_path),'anchored':anchored})
    return safe(output)


def build_release_decision(report_path: Path, checklist_path: Path, *, public_copy: bool=False,
                           publication_manifest: Path | None=None) -> dict:
    if publication_manifest is not None and not public_copy:
        raise ValueError('Publication manifest is only accepted with explicit --public-copy')
    manifest=PublicationManifest(publication_manifest or ROOT/'reports/publication_manifest.json') if public_copy else None
    token=_PUBLIC_COPY_MAP.set(manifest)
    try:
        output=_build_release_decision(report_path,checklist_path)
        output['artifact_verification_mode']='explicit_public_derived_copy' if public_copy else 'private_exact_source_bytes'
        if manifest is not None:
            output['publication_manifest']={'file':relative(manifest.path),'sha256':manifest.sha256,
                'verified_artifact_count':len(manifest.verified_paths),'source_and_public_bytes_identical':False}
            output['public_copy_boundary']='Source SHA identities are retained. Public bytes are explicitly derived artifacts verified against the publication map; this is not original-byte preservation or a new model run.'
            output['main_report']['public_artifact_sha256']=sha(report_path.read_bytes())
            output['checklist']['public_artifact_sha256']=sha(checklist_path.read_bytes())
        return output
    finally:_PUBLIC_COPY_MAP.reset(token)


def summarize_details(groups: dict) -> dict:
    """Select final detail checks independently from all brief scenarios."""
    selected = {}
    for stage in DETAIL_STAGES:
        for record in groups.get(stage, {}).get("records", []):
            selected[record["case"]] = record
    records = [selected[key] for key in sorted(selected)]
    passed = [row for row in records if row.get("passed") and row.get("analysis_kind") == "detail" and row.get("has_detail_result")]
    failed = [row["case"] for row in records if row not in passed]
    summary = summarize_stage(records)
    return {**summary, "passed": bool(passed) and not failed, "minimum_passed_required": 1,
            "selected_case_count": len(records), "passed_detail_count": len(passed),
            "failing_cases": failed, "pending": not bool(records),
            "override_cases": [row["case"] for row in records if row["stage"] in DETAIL_STAGES[1:]],
            "selection_rule": "pressure-predicate detail supersedes price-zero-consistency/evidence-slot/physical-unit/risk-polarity/source-feedback/final-output-scope/bounded-output/detail-grounding/final-tense/price-observation/semantic-regeneration/reviewwording/anchor/reaction/fieldfeedback/revision/provenance/source/binding/contract/feedback/scopeguard/symmetric/scoped/budget/adaptive/parser/integrity/factchecks/decoder/grounded/verified/production/release/final detail including failures; never replaces a brief scenario"}


def select_briefs(groups: dict) -> dict:
    if any(stage in groups for stage in BOUNDED_COHORT_STAGES):
        final={}
        for stage in BOUNDED_COHORT_STAGES:
            for record in (groups.get(stage) or {}).get('records',[]):final[record['case']]=record
        return final
    final = {}
    for stage in ("candidate-comparison", "candidate-finalvalidation", "candidate-final-validation",
                  "candidate-release-validation", "candidate-production-validation", "candidate-verified-validation",
                  "candidate-grounded-validation", "candidate-decoder-validation", "candidate-factchecks-validation",
                  "candidate-integrity-validation", "candidate-parser-validation", "candidate-adaptive-validation",
                  "candidate-budget-validation", "candidate-scoped-validation", "candidate-symmetric-validation",
                  "candidate-scopeguard-validation", "candidate-feedback-validation", "candidate-contract-validation",
                  "candidate-binding-validation", "candidate-source-validation", "candidate-provenance-validation",
                  "candidate-revision-validation", "candidate-fieldfeedback-validation", "candidate-reaction-validation", "candidate-anchor-validation", "candidate-reviewwording-validation", "candidate-semantic-regeneration-validation", "candidate-price-observation-validation", "candidate-final-tense-validation"):
        for record in groups.get(stage, {}).get("records", []):
            final[record["case"]] = record
    return final


def use_frozen_case_fixture(stage: str, frozen_exists: bool) -> bool:
    """Later source/contract repairs use current inputs, retaining v1 originals."""
    current_stages=STAGES[STAGES.index('candidate-release-validation'):]+DETAIL_STAGES[DETAIL_STAGES.index('candidate-release-validation-detail'):]
    return bool(frozen_exists and stage not in current_stages)


def original_required_case_ids(cases: list[dict], frozen_cases: list[dict] | None = None) -> set[str]:
    """Additional candidate-only controls do not expand the frozen old model run."""
    return {case["id"] for case in (frozen_cases if frozen_cases is not None else cases)
            if case.get("requires_original_comparison", True)}


def unfinished_original_cases(originals: dict) -> list[str]:
    """An old terminal failure is evidence; a partial/unreadable file is not."""
    return sorted(identifier for identifier, row in originals.items()
                  if row.get("status") == "unreadable_or_in_progress"
                  or not row.get("recorded_run_succeeded") and not row.get("recorded_error"))


def junit(path: Path) -> dict:
    if not path.exists():
        return {"status": "pending", "passed": False, "file": relative(path)}
    try:
        root = ET.parse(path).getroot()
        suites = [node for node in root.iter("testsuite") if not list(node.iterfind("testsuite"))]
        totals = {key: sum(int(float(node.get(key, "0"))) for node in suites) for key in ("tests", "failures", "errors", "skipped")}
        failed = [{"name": node.get("name"), "class": node.get("classname"),
                   "reason": safe((failure.get("message") or failure.text or "")[:500])}
                  for node in root.iter("testcase") for failure in [*node.findall("failure"), *node.findall("error")]]
        passed = totals["tests"] > 0 and not totals["failures"] and not totals["errors"]
        return {"status": "passed" if passed else "failed", "passed": passed, "file": relative(path),
                **totals, "failed_cases": failed, "sha256": sha(path.read_bytes())}
    except (ET.ParseError, OSError, ValueError) as exc:
        return {"status": "unreadable_or_in_progress", "passed": False, "file": relative(path), "reason": safe(str(exc))}


def select_unit_test_evidence(evidence_dir: Path, explicit: Path | None = None) -> Path:
    if explicit is not None:return explicit
    # The later final suite supersedes its predecessor even while incomplete;
    # never silently fall back to an older successful test file.
    for name in ('final-release-unit-tests.xml','final-unit-tests.xml'):
        final=evidence_dir/name
        if final.exists():return final
    return evidence_dir/'unit-tests.xml'


def frontend_log(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {"status": "not_provided", "required_by_this_report": False}
    text = path.read_text(errors="replace")
    # Log capture alone is not proof of the command's exit status. Include
    # explicit TAP counts/build marker when available and retain the log link.
    passes = re.findall(r"(?:#\s*)?pass\s+(\d+)", text, re.I)
    fails = re.findall(r"(?:#\s*)?fail\s+(\d+)", text, re.I)
    return {"status": "provided", "file": relative(path), "sha256": sha(path.read_bytes()),
            "test_pass_count": int(passes[-1]) if passes else None,
            "test_fail_count": int(fails[-1]) if fails else None,
            "build_success_marker": bool(re.search(r"built in|build completed", text, re.I)),
            "required_by_this_report": False}


def code_hashes() -> dict:
    files = list((ROOT/"ttf_news").glob("*.py")) + list((ROOT/"scripts").glob("*.py"))
    files += [path for path in (ROOT/"frontend/src").rglob("*") if path.is_file() and path.suffix in {".tsx", ".ts", ".mjs", ".css"}]
    files += [ROOT/"frontend/package.json", ROOT/"frontend/package-lock.json"]
    return {relative(path): sha(path.read_bytes()) for path in sorted(set(files)) if path.exists()}


def asset_checks(manifest: dict, evidence_dir: Path) -> dict:
    checked = {}
    for name, path, expected in (("pre_repair_backup", Path(manifest.get("backup", "")), manifest.get("backup_sha256")),
                                 ("original_source_archive", evidence_dir/"original_source.tar", manifest.get("source_archive_sha256"))):
        exists = path.is_file()
        actual = sha(path.read_bytes()) if exists else None
        checked[name] = {"file": relative(path), "exists": exists, "sha256": actual, "hash_matches": bool(expected and actual == expected)}
    return checked


def build_report(db_path: Path, evidence_dir: Path, frontend: Path | None = None, unit_tests_path: Path | None = None) -> dict:
    from ttf_news.prompts import PROMPT_VERSION
    manifest = read_json(evidence_dir/"preservation_manifest.json")
    cases = read_json(evidence_dir/"comparison_cases.json")
    if isinstance(cases, dict):
        cases = cases.get("cases", [])
    cases_by_id = {case["id"]: case for case in cases}
    anchor_fixture_path = evidence_dir/'anchor_cases_v2_1.json'
    anchor_cases = read_json(anchor_fixture_path) if anchor_fixture_path.exists() else []
    if not isinstance(anchor_cases, list) or any(not isinstance(row, dict) or 'id' not in row for row in anchor_cases):
        raise ValueError('Separate anchor fixture must be a JSON array of case objects')
    anchor_cases_by_id = {case['id']: case for case in anchor_cases}
    if set(cases_by_id) & set(anchor_cases_by_id):
        raise ValueError('Anchor cases must remain separate from the original fifteen')
    regeneration_fixture_path=evidence_dir/SEMANTIC_REGENERATION_FIXTURE
    regeneration_cases=read_json(regeneration_fixture_path) if regeneration_fixture_path.exists() else []
    if not isinstance(regeneration_cases,list) or any(not isinstance(row,dict) or 'id' not in row for row in regeneration_cases):
        raise ValueError('Fresh semantic-regeneration fixture must be a JSON array of case objects')
    regeneration_by_id={case['id']:case for case in regeneration_cases}
    for identifier,original in {**cases_by_id,**anchor_cases_by_id}.items():
        if identifier not in regeneration_by_id:continue
        fresh=regeneration_by_id[identifier]
        if fresh.get('candidate_context')!=original.get('candidate_context') or any(fresh.get(key)!=original.get(key) for key in ('expected_news','expected_close','expected_close_sign','expected_close_evidence_status')):
            raise ValueError('Fresh cohort changed a frozen comparison input or expectation: '+identifier)
    evaluation_revision = load_evaluation_contract_revision(evidence_dir, evidence_dir/"comparison_cases.json", cases)
    frozen_path = evidence_dir/"comparison_cases_v1.json"
    frozen_cases = read_json(frozen_path) if frozen_path.exists() else cases
    if isinstance(frozen_cases, dict):
        frozen_cases = frozen_cases.get("cases", [])
    frozen_cases_by_id = {case["id"]: case for case in frozen_cases}
    with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        preservation = verify_preservation(db, manifest)
    assets = asset_checks(manifest, evidence_dir)
    preservation["backup_assets"] = assets
    preservation["passed"] = preservation["passed"] and all(row["hash_matches"] for row in assets.values())
    hashes = code_hashes()
    groups = {}
    for stage in STAGES + DETAIL_STAGES:
        records = []
        for path in sorted((evidence_dir/stage).glob("*.json")):
            try:
                raw = read_json(path)
                identifier = raw.get("case", path.stem) if isinstance(raw, dict) else path.stem
            except (OSError, ValueError):
                identifier = path.stem
            use_frozen = use_frozen_case_fixture(stage,frozen_path.exists())
            is_regeneration=stage in {name+suffix for name in CURRENT_COHORT_STAGES for suffix in ('','-detail')}
            lookup = frozen_cases_by_id if use_frozen else regeneration_by_id if is_regeneration else {**cases_by_id, **anchor_cases_by_id}
            case = lookup.get(identifier, {"id": identifier})
            fixture = frozen_path if use_frozen else regeneration_fixture_path if is_regeneration else anchor_fixture_path if identifier in anchor_cases_by_id else evidence_dir/"comparison_cases.json"
            records.append(inspect_record(path, case, stage, hashes, relative(fixture), evaluation_revision))
        groups[stage] = summarize_stage(records)
    details = summarize_details(groups)
    final = select_briefs(groups)
    anchor_validation = summarize_anchor_validation(anchor_cases, final, hashes, PROMPT_VERSION,
                        {'file':relative(anchor_fixture_path),'sha256':sha(anchor_fixture_path.read_bytes())} if anchor_fixture_path.exists() else None)
    regeneration_validation=summarize_semantic_regeneration(regeneration_cases,groups,hashes,PROMPT_VERSION,
                            {'file':relative(regeneration_fixture_path),'sha256':sha(regeneration_fixture_path.read_bytes())} if regeneration_fixture_path.exists() else None)
    historical_groups={stage:value for stage,value in groups.items() if stage not in {name+suffix for name in CURRENT_COHORT_STAGES for suffix in ('','-detail')}}
    historical_selected=select_briefs(historical_groups)
    historical_cohorts={}
    for cohort,identifiers in (('previous_fifteen',set(cases_by_id)),('previous_four_anchors',set(anchor_cases_by_id))):
        rows=[historical_selected[key] for key in sorted(identifiers) if key in historical_selected]
        historical_cohorts[cohort]={'scope':'Earlier actual generation phases retained, with original terminal status and current offline checks separately visible; not new current-version inference.',
             'required_case_count':len(identifiers),'recorded_effective_output_count':sum(bool(row.get('recorded_run_succeeded')) for row in rows),
             'current_revalidation_effective_output_count':sum(bool(row.get('passed')) for row in rows),
             'generation_prompt_version_counts':dict(Counter(row.get('generation_prompt_version') for row in rows)),
             'is_current_version_new_model_performance':False,'records':rows}
    historical_cohorts['previous_detail_validation']=summarize_details(historical_groups)
    known_errors=known_accepted_error_state(evidence_dir/'release_known_accepted_errors.json',PROMPT_VERSION)
    unresolved_errors=[row for row in known_errors if row.get('resolution_verified') is not True]
    missing = sorted(set(cases_by_id) - set(final))
    failed = sorted(identifier for identifier, record in final.items() if identifier in cases_by_id and not record["passed"])
    originals = {record["case"]: record for record in groups["original-comparison"]["records"]}
    original_required = original_required_case_ids(cases, frozen_cases if frozen_path.exists() else None)
    original_missing = sorted(original_required-set(originals))
    original_unfinished = unfinished_original_cases({identifier: row for identifier, row in originals.items() if identifier in original_required})
    unit = junit(select_unit_test_evidence(evidence_dir,unit_tests_path))
    front = frontend_log(frontend)
    comparison = {"required_case_count": len(cases), "selected_case_count": sum(identifier in cases_by_id for identifier in final),
                  "passed_case_count": sum(record["passed"] for identifier, record in final.items() if identifier in cases_by_id),
                  "missing_cases": missing, "failing_cases": failed,
                  "original_comparison_count": len(originals), "original_comparison_pending": bool(original_missing or original_unfinished),
                  "original_required_case_count": len(original_required),
                  "original_comparison_missing_cases": original_missing,
                  "original_comparison_unfinished_cases": original_unfinished,
                  "selected_records": [final[case["id"]] for case in cases if case["id"] in final],
                  "override_cases": sorted(identifier for identifier, record in final.items() if identifier in cases_by_id and (record["stage"].startswith("candidate-final") or record["stage"] in ("candidate-release-validation", "candidate-production-validation", "candidate-verified-validation", "candidate-grounded-validation", "candidate-decoder-validation", "candidate-factchecks-validation", "candidate-integrity-validation", "candidate-parser-validation", "candidate-adaptive-validation", "candidate-budget-validation", "candidate-scoped-validation", "candidate-symmetric-validation", "candidate-scopeguard-validation", "candidate-feedback-validation", "candidate-contract-validation", "candidate-binding-validation", "candidate-source-validation", "candidate-provenance-validation", "candidate-revision-validation", "candidate-fieldfeedback-validation", "candidate-reaction-validation", "candidate-anchor-validation", "candidate-reviewwording-validation", "candidate-semantic-regeneration-validation", "candidate-price-observation-validation"))),
                  "release_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-release-validation"),
                  "production_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-production-validation"),
                  "verified_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-verified-validation"),
                  "grounded_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-grounded-validation"),
                  "decoder_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-decoder-validation"),
                  "factchecks_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-factchecks-validation"),
                  "integrity_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-integrity-validation"),
                  "parser_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-parser-validation"),
                  "adaptive_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-adaptive-validation"),
                  "budget_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-budget-validation"),
                  "scoped_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-scoped-validation"),
                  "symmetric_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-symmetric-validation"),
                  "scopeguard_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-scopeguard-validation"),
                  "feedback_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-feedback-validation"),
                  "contract_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-contract-validation"),
                  "binding_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-binding-validation"),
                  "source_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-source-validation"),
                  "provenance_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-provenance-validation"),
                  "revision_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-revision-validation"),
                  "fieldfeedback_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-fieldfeedback-validation"),
                  "reaction_validation_cases": sorted(identifier for identifier, record in final.items() if record["stage"] == "candidate-reaction-validation"),
                  "anchor_validation_cases": [],
                  "cohort": "fifteen_mechanism_cases_latest_current_version_selection" if regeneration_validation['required'] else "original_fifteen_generated_v2_0_with_current_offline_revalidation",
                  "generation_prompt_version_counts": dict(Counter(final[case['id']].get('generation_prompt_version') for case in cases if case['id'] in final)),
                  "current_offline_revalidation_is_new_model_generation": False,
                  "evaluation_revision_applied_cases": sorted(identifier for identifier, record in final.items()
                                                             if record.get("evaluation_contract_revision", {}).get("applied")),
                  "generation_policy_incompatible_cases": sorted(identifier for identifier, record in final.items()
                                                                  if identifier in cases_by_id and not record["current_generation_policy_compatibility"]["compatible"]),
                  "cases_generated_with_current_prompt": sum(record.get("generated_with_current_prompt", False) for identifier, record in final.items() if identifier in cases_by_id)}
    outstanding = []
    if not preservation["passed"]: outstanding.append("原记录／备份保全检查未通过")
    if not unit["passed"]: outstanding.append("单元测试缺失或仍有失败")
    if missing: outstanding.append("候选对照尚未齐备：" + ", ".join(missing))
    if failed: outstanding.append("当前逻辑／方向契约／生产策略兼容性未通过：" + ", ".join(failed))
    if comparison["generation_policy_incompatible_cases"]:
        outstanding.append("所选案例执行了当前默认禁用的独立high路径，须按生产默认重新实测：" + ", ".join(comparison["generation_policy_incompatible_cases"]))
    if original_missing: outstanding.append("原版对照尚未齐备：" + ", ".join(original_missing))
    if original_unfinished: outstanding.append("原版对照存在未完成或不可读产物：" + ", ".join(original_unfinished))
    if comparison["cases_generated_with_current_prompt"] < len(cases):
        outstanding.append("尚有案例未由当前提示词生成；离线复检不能替代最终版本实测")
    if details["passed_detail_count"] < details["minimum_passed_required"]:
        outstanding.append("尚无最终详析通过；至少需要一条完整详析实测通过")
    if details["failing_cases"]:
        outstanding.append("所选最终详析仍有失败：" + ", ".join(details["failing_cases"]))
    if front.get("test_fail_count", 0): outstanding.append("前端日志仍有失败测试")
    if unresolved_errors:
        outstanding.append('真实运行中已接受错误尚未闭合：'+', '.join(str(row.get('id')) for row in unresolved_errors))
    if not anchor_validation['passed']:
        outstanding.append('独立价格锚点四项当前版本真实模型验证尚未全部通过；不由旧15项离线复检替代')
    if regeneration_validation['required'] and not regeneration_validation['passed']:
        outstanding.append('当前版本二十项新实际生成及详析尚未全部通过；不回退旧阶段或把旧版本离线复验当新执行')
    deployment_path = evidence_dir/"deployment.json"
    deployment = {"status": "not_recorded"}
    if deployment_path.exists():
        try: deployment = safe(read_json(deployment_path))
        except (OSError, ValueError) as exc: deployment = {"status": "unreadable", "reason": safe(str(exc))}
    warning = "现存推理结果已按当前代码重新校验；生成时源码SHA差异逐项保留，不能视为所有案例均由同一次冻结版本生成。生产默认禁用独立high重生，采用两次medium及明确诊断反馈。未实际走独立high路径的旧结果可兼容，即使旧配置曾开启high；显式开启的独立high实验保留，但即使语义通过也须按生产默认重新实测，不计生产通过。"
    return safe({"generated_at": datetime.now(timezone.utc).isoformat(), "status": "needs_attention" if outstanding else "passed",
                 "report_kind": "isolated_logic_and_preservation_validation", "current_prompt_version": PROMPT_VERSION,
                 "evaluation_contract_revision": evaluation_revision,
                 "no_model_calls": True, "database_access": "read_only", "database_modified": False,
                 "scope": "隔离供需机制、收盘方向/信息不足契约与原记录保全；不是交易日预测准确率。行情仍为reference，不报告官方结算成绩。",
                 "price_time_provenance_note": "旧迁移日价的 fetched_at 历史以既有时钟为准；新抓取以 HTTP 返回时刻记录；无法重建旧数据更精确的实际到达时间。",
                 "preservation_manifest": relative(evidence_dir/"preservation_manifest.json"),
                 "preservation_manifest_sha256": sha((evidence_dir/"preservation_manifest.json").read_bytes()),
                 "comparison_case_fixtures": [{"file": relative(path), "sha256": sha(path.read_bytes())}
                                               for path in (evidence_dir/"comparison_cases.json", frozen_path, evidence_dir/"comparison_cases_v2.json") if path.exists()],
                 "original_public_commit": manifest.get("original_public_commit"),
                 "preservation": preservation, "unit_tests": unit, "frontend": front,
                 "comparison": comparison, "anchor_validation": anchor_validation,
                 "semantic_regeneration_validation":regeneration_validation,
                 "historical_cohorts":historical_cohorts,
                 "known_accepted_errors": known_errors, "unresolved_accepted_error_count": len(unresolved_errors),
                 "detail_validation": details, "all_stages": groups, "outstanding": outstanding,
                 "version_note": warning, "code_sha256": hashes, "deployment": deployment})


def markdown(report: dict) -> str:
    p, c, unit = report["preservation"], report["comparison"], report["unit_tests"]
    good = report["status"] == "passed"
    snapshots_unchanged = not any(row.get("kind") == "frozen_snapshot_changed" for row in p["failures"])
    lines = ["# TTF 新闻分析逻辑修复验证", "", f"生成时间：{report['generated_at']}。当前版本：`{report['current_prompt_version']}`。",
             "", "**结论：" + ("当前已提供证据的逻辑与记录保全检查通过。" if good else "尚需处理／等待剩余证据，不能据此宣布全部修复验收通过。") + "**", "",
             "本报告离线复检已有产物，没有调用模型或写数据库。只检查场景机制、方向契约、重试与记录保全；行情仍为 reference，不统计官方结算准确率。", "",
             report["price_time_provenance_note"], "",
             "## 原记录保全", "",
             f"- 保全检查：{'通过' if p['passed'] else '未通过'}；原有 {p['protected_analysis_count']} 条分析、{p['protected_nonnull_field_count']} 个非空输入/简报/详析字段逐字节 SHA-256 比对。",
             f"- 原有 {p['protected_snapshot_count']} 个冻结结算快照：{'逐项不变' if snapshots_unchanged else '出现缺失或变化'}；允许新增记录，目前新增分析 {p['new_analysis_count']} 条、冻结快照 {p['new_snapshot_count']} 个。",
             f"- 原验收基线：metric {p['original_baseline']['id']}，{p['original_baseline']['created_at']}；{'保持不变' if p['latest_baseline_unchanged'] else '发生变化，需处理'}。",
             f"- 原数据库备份与原源码归档 SHA-256：{'全部一致' if all(v['hash_matches'] for v in p['backup_assets'].values()) else '存在缺失或变化'}。", "",
             "## 测试与隔离对照", "",
             f"- 单元测试：{unit.get('tests', 0)} 项，失败 {unit.get('failures', 0)}，错误 {unit.get('errors', 0)}，跳过 {unit.get('skipped', 0)}；状态 `{unit['status']}`。",
             f"- 必需候选场景 {c['required_case_count']} 项，已有 {c['selected_case_count']} 项，当前方向/状态契约、逻辑及生产策略兼容性通过 {c['passed_case_count']} 项。",
             f"- 原版对照已有 {c['original_comparison_count']}／{c['original_required_case_count']} 项；新增候选专用控制不扩增原版必需场景。后续最终验证覆盖 {len(c['override_cases'])} 项；早期失败试跑仍保留在各阶段统计。", "",
             f"这15项所选回答的实际生成版本分布为 `{json.dumps(c.get('generation_prompt_version_counts',{}), ensure_ascii=False)}`。本报告按 `{report['current_prompt_version']}` 源码离线复检，并保留原生成版本、实际请求和SHA。仅当新阶段的实际请求与源码全部匹配，才计入下方当前版本二十项新实测；旧阶段另列历史。", "",
             "运行失败或当前语义／契约未通过时，原始 raw 方向只用于调试，不是产品发布预测，不计入有效方向分布；表格明确标注无有效输出。独立全文复核由AI agent执行，失败不算正确预测。", "",
             "| 场景 | 原版新闻方向 | 候选新闻方向 | 候选收盘方向／状态 | 当前逻辑、契约与策略 | 调用／重试 | 耗时秒 |", "|---|---|---|---|---|---|---|"]
    if report.get('known_accepted_errors'):
        notice=['', '## 真实运行已接受错误与闭合状态', '',
                f"**尚未闭合 {report.get('unresolved_accepted_error_count',0)} 项。隔离场景或结构流程通过不能替代对应真实错误的修复与端到端证明。**", '',
                '| 错误 | 原分析 | 闭合状态 | 独立证明 |', '|---|---|---|---|']
        for row in report['known_accepted_errors']:
            proof=row.get('resolution_evidence') or row.get('review_evidence') or {}
            link=f"[证据]({report_link(proof['file'])})" if proof.get('file') else '待补充'
            origin=row.get('analysis_id') or ', '.join(row.get('affected_cases') or []) or '—'
            notice.append(f"| {row.get('id')} | {origin} | {'已验证闭合' if row.get('resolution_verified') else '未闭合'} | {link} |")
        # Keep the original comparison table contiguous after this notice.
        at=lines.index('## 原记录保全')
        lines[at:at]=notice+['']
    selected = {r["case"]: r for r in c["selected_records"]}
    old = {r["case"]: r for r in report["all_stages"]["original-comparison"]["records"]}
    case_order = list(CASE_NAMES)
    case_order += sorted((set(selected) | set(old)) - set(case_order))
    for identifier in case_order:
        current = selected.get(identifier)
        prior = old.get(identifier)
        if not current and not prior and identifier not in c["missing_cases"]:continue
        before = (prior.get("expectation_checks") or {}).get("news_direction") if prior else "待运行"
        now = candidate_direction_display(current)
        closing = candidate_direction_display(current,True)
        result = candidate_check_display(current)
        timing = (current or {}).get("analyst_elapsed_seconds", (current or {}).get("wall_seconds"))
        link = f"[{CASE_NAMES.get(identifier, identifier)}]({report_link((current or prior)['file'])})" if current or prior else CASE_NAMES.get(identifier, identifier)
        lines.append(f"| {link} | {before} | {now} | {closing} | {result} | {(current or {}).get('attempts', '—')} / {(current or {}).get('retries', '—')} | {timing if timing is not None else '—'} |")
    anchor = report.get('anchor_validation') or {}
    if anchor:
        lines += ['', '## 独立价格锚点真实模型对照', '',
                  f"必需 {anchor.get('required_case_count',0)} 项，已有 {anchor.get('selected_case_count',0)} 项，当前逻辑与契约通过 {anchor.get('passed_case_count',0)} 项；确认由当前模型输入相关源码实际调用模型 {anchor.get('current_model_input_real_model_case_count',0)} 项。该四项与上表十五项机制分别统计；新阶段若运行，则一并归入独立二十项 cohort。", '',
                  '缺上一交易日锚点时保留绝对报价和新闻供需方向；不拿旧日期价差当昨收，不用flat填未知。有源方向新闻允许有依据的条件预测或明确缺锚点，无需强行复制新闻方向。', '',
                  '| 场景 | 实际生成版本 | 新闻方向 | 收盘方向／状态 | 当前检查 | 调用／重试 |',
                  '|---|---|---|---|---|---|']
        by_id={row['case']:row for row in anchor.get('records',[])}
        for identifier in ANCHOR_CASE_NAMES:
            row=by_id.get(identifier)
            label=ANCHOR_CASE_NAMES[identifier]
            if row:
                label=f"[{label}]({report_link(row['file'])})"
            lines.append(f"| {label} | {(row or {}).get('generation_prompt_version','待运行')} | {candidate_direction_display(row)} | {candidate_direction_display(row,True)} | {candidate_check_display(row)} | {(row or {}).get('attempts','—')} / {(row or {}).get('retries','—')} |")
    fresh=report.get('semantic_regeneration_validation') or {}
    if fresh.get('required') or fresh.get('selected_case_count'):
        lines += ['', '## 当前版本二十项全新实际回归', '',
                  f"必需 {fresh.get('required_case_count',20)} 项，新阶段实际文件 {fresh.get('selected_case_count',0)} 项，严格契约通过 {fresh.get('passed_case_count',0)} 项，可交付但真实标签未验证的分歧 {fresh.get('deliverable_unvalidated_label_disagreement_count',0)} 项，无可交付输出 {fresh.get('unavailable_output_count',0)} 项；当前输入与真实请求匹配 {fresh.get('current_model_input_real_model_case_count',0)} 项，最后源码直接执行 {fresh.get('generation_matches_all_final_sources_count',0)} 项，同版本实际样本经最终完整路径等价验证 {fresh.get('verified_same_version_path_equivalence_count',0)} 项，完整详析（直接执行或完整等价）通过 {fresh.get('current_version_detail_count',0)} 条。",
                  '', fresh.get('scope',''), '',
                  '最终当前样本仅由完整 bounded-output 二十项、final-output-scope、source-feedback、risk-polarity、physical-unit、evidence-slot、price-zero-consistency 与最后 pressure-predicate 的受影响案例新实际终态依次合并；后续失败或未完不回退旧成功。缺项保持 missing，semantic-regeneration、price-observation、final-tense 与 detail-grounding 不补位，全部旧记录仍保留在各阶段中。阶段注册不代表已执行。本轮 pressure-predicate 仅 real_eni 因第二轮最终诊断与终态错误文案变化需新实际终态；其余20条（19简报与1详析）完整路径等价，保留原生成来源，不称新生成。校验源或纠错请求变化后的未受影响样本必须有逐轮完整请求／校验／草稿／终稿显式等价，复验不算新模型调用。原真实EPCM窄范围标签分歧仅适用已固定旧record的SHA与两份AI agent独立全文复核，不豁免任何无源价格结果句；新结果不自动豁免原flat契约。失败 raw 不是有效预测，行情准确率仍未计算。', '',
                  '| 场景 | 实际生成版本 | 新闻方向 | 收盘方向／状态 | 当前检查 | 调用／重试 |',
                  '|---|---|---|---|---|---|']
        current={row['case']:row for row in fresh.get('records',[])}
        for identifier in list(CASE_NAMES)+list(ANCHOR_CASE_NAMES):
            row=current.get(identifier);label=CASE_NAMES.get(identifier,ANCHOR_CASE_NAMES.get(identifier,identifier))
            if row:label=f"[{label}]({report_link(row['file'])})"
            lines.append(f"| {label} | {(row or {}).get('generation_prompt_version','待运行')} | {candidate_direction_display(row)} | {candidate_direction_display(row,True)} | {candidate_check_display(row)} | {(row or {}).get('attempts','—')} / {(row or {}).get('retries','—')} |")
    history=report.get('historical_cohorts') or {}
    if history:
        lines += ['', '## 保留的旧版本实际记录', '',
                  '以下为新 semantic-regeneration 阶段之前的选择。原始运行终态与当前离线检查分别记录；旧2.0／2.1结果不是新版本性能。完整记录文件与各阶段失败保留。', '',
                  '| 历史 cohort | 原实际有效输出／必需 | 实际生成版本 |', '|---|---|---|']
        for key in ('previous_fifteen','previous_four_anchors'):
            item=history.get(key) or {}
            lines.append(f"| {key} | {item.get('recorded_effective_output_count',0)} / {item.get('required_case_count',0)} | `{json.dumps(item.get('generation_prompt_version_counts',{}),ensure_ascii=False)}` |")
    revision = report.get("evaluation_contract_revision")
    if revision:
        lines += ["", "### 显式评估合同修订", "",
                  f"[{revision['revision_id']}]({report_link(revision['file'])}) 仅对两项新增持平基准控制对称修订：新闻供需方向仍严格；收盘须为有依据的条件 forecast，可同向或处于 ±0.30 欧元/MWh 的 flat 带。40 mcm/d 没有给出价格弹性，不能据此要求必然跨过该价格阈值。原 fixture SHA、原始回答及 recorded_contract_passed 全部保留；这不是实际准确率或幅度验证。", "",
                  "| 控制场景 | 原记录契约 | 原 fixture 复检 | 修订后契约 | 当前语义复检 |", "|---|---|---|---|---|"]
        for identifier in BASELINE_CONTROL_SIGNS:
            row = selected.get(identifier)
            if row:
                show = lambda value: "通过" if value else "未通过"
                lines.append(f"| {CASE_NAMES[identifier]} | {show(row.get('recorded_contract_passed'))} | {show(row.get('original_expectation_checks', {}).get('passed'))} | {show(row.get('expectation_checks', {}).get('passed'))} | {show(row.get('current_logic_revalidation', {}).get('passed'))} |")
    details = report["detail_validation"]
    latency = details["latency_seconds"]
    lines += ["", "其中弱相关新闻仍可判 flat；供气恢复应呈下行机制；缺少收盘证据可明确标记 insufficient_evidence，而非填 flat。冲突、未来维护和真实新闻案例不预先强塞方向，只检查实际证据和逻辑。", "",
              "## 详细分析验证", "",
              f"- 最终选择 {details['selected_case_count']} 条详析，通过 {details['passed_detail_count']} 条，失败 {len(details['failing_cases'])} 条；至少一条完整详析通过且所选详析均无失败，才满足此项验收。",
              f"- 所选详析调用 {details['model_attempts']} 次、重试 {details['retries']} 次；耗时中位数 {latency['median']} 秒、P95 {latency['p95']} 秒。",
              f"- 同场景按已注册阶段优先级选择实际存在的最新详析（当前最高为 `{DETAIL_STAGES[-1]}`）；失败不回退，尚无记录的阶段不计为完成。简报的 {c['required_case_count']} 个场景独立统计。旧详析失败仍保留在下表。", "",
              "| 详析场景 | 选择阶段 | 当前契约与逻辑 | 调用／重试 | 耗时秒 |", "|---|---|---|---|---|"]
    for row in details["records"]:
        passed = row["passed"] and row.get("analysis_kind") == "detail" and row.get("has_detail_result")
        seconds = row.get("analyst_elapsed_seconds") if row.get("analyst_elapsed_seconds") is not None else row.get("wall_seconds")
        lines.append(f"| [{CASE_NAMES.get(row['case'], row['case'])}]({report_link(row['file'])}) | {row['stage']} | {'通过' if passed else '需处理'} | {row.get('attempts', 0)} / {row.get('retries', 0)} | {seconds if seconds is not None else '—'} |")
    lines += ["", "## 各阶段完整记录", "", "| 阶段 | 记录数 | 当前契约＋逻辑＋策略通过 | 原始运行失败 | 当前逻辑拒绝 | 策略不兼容 | 调用／重试 | 中位／P95秒 |", "|---|---|---|---|---|---|---|---|"]
    for name, group in report["all_stages"].items():
        latency=group["latency_seconds"]
        lines.append(f"| {name} | {group['record_count']} | {group['passed_current_contract_and_logic']} | {group['recorded_run_failures']} | {group['current_logic_failures']} | {group['current_generation_policy_incompatible']} | {group['model_attempts']} / {group['retries']} | {latency['median']} / {latency['p95']} |")
    lines += ["", report["version_note"], "", "## 剩余事项", ""]
    lines += ["- " + item for item in report["outstanding"]] or ["- 当前所需方向、逻辑、测试及记录保全检查均通过。"]
    deployment=report.get("deployment") or {}
    lines += ["", "## 部署与可复查材料", "", f"部署记录状态：`{deployment.get('status', 'provided')}`。没有部署记录时，本报告不会宣称网站已更新。", "",
              "- [完整机器可读报告](logic_repair_validation_2026-10-20.json)",
              f"- [原记录保全清单]({report_link(report['preservation_manifest'])})",
              f"- [单元测试记录]({report_link(unit['file'])})", "",
              "源码 SHA-256、每个场景的当前校验失败原因、输出状态、重试与时延均在 JSON 中；原始试跑不覆盖、不删改。"]
    return "\n".join(lines) + "\n"


class VerifierSelfTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("CREATE TABLE analyses(id TEXT PRIMARY KEY,input_snapshot TEXT,result TEXT,detail_result TEXT);CREATE TABLE daily_snapshots(trade_date TEXT PRIMARY KEY,cutoff TEXT,analysis_id TEXT,frozen_at TEXT);CREATE TABLE metrics(id INTEGER PRIMARY KEY,created_at TEXT,type TEXT,data TEXT);")
        self.db.execute("INSERT INTO analyses VALUES ('old','{\"a\":1}','{\"direction\":\"flat\"}',NULL)")
        self.db.execute("INSERT INTO daily_snapshots VALUES ('2026-10-19','cutoff','old','frozen')")
        self.db.execute("INSERT INTO metrics VALUES (210,'start','validation_started','original')")
        self.manifest = {"analyses": {"old": {"input_snapshot": sha('{"a":1}'), "result": sha('{"direction":"flat"}')}},
                         "snapshots": {"2026-10-19": dict(self.db.execute("SELECT * FROM daily_snapshots").fetchone())},
                         "validation_records": [dict(self.db.execute("SELECT * FROM metrics").fetchone())]}

    def tearDown(self): self.db.close()

    def test_revalidate_result_passes_explicit_original_source_index(self):
        from unittest.mock import patch
        from ttf_news import analysis as a
        from ttf_news.semantics import validate_analysis_semantics
        context, result = self.canonical_risk_fixture()
        prepared, meta = a.prepare_context(copy.deepcopy(context), False, reserve_tokens=32)
        source_index = meta["source_index"]
        self.assertTrue(source_index)
        with patch.object(a, "prepare_context", return_value=(prepared, meta)), patch(
                "ttf_news.semantics.validate_analysis_semantics", wraps=validate_analysis_semantics) as semantic_check:
            check = revalidate_result(result, context)
        self.assertTrue(check["passed"], check)
        semantic_check.assert_called_once()
        self.assertIs(semantic_check.call_args.kwargs["source_index"], source_index)
        self.assertIs(semantic_check.call_args.args[1], context)
        self.assertIs(semantic_check.call_args.args[2], prepared)

    def test_final_unit_evidence_never_falls_back_to_older_passed_xml(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);old=base/'unit-tests.xml';old.write_text('<testsuite tests="1" failures="0" errors="0"><testcase/></testsuite>')
            self.assertEqual(select_unit_test_evidence(base),old)
            final=base/'final-unit-tests.xml';final.write_text('<testsuite')
            self.assertEqual(select_unit_test_evidence(base),final);self.assertFalse(junit(select_unit_test_evidence(base))['passed'])
            release=base/'final-release-unit-tests.xml';release.write_text('<testsuite')
            self.assertEqual(select_unit_test_evidence(base),release);self.assertFalse(junit(select_unit_test_evidence(base))['passed'])
            self.assertEqual(select_unit_test_evidence(base,old),old)
    def publication_fixture(self,base):
        path=base/'reports/artifact.json';path.parent.mkdir(parents=True);path.write_text('{"answer":1}\n')
        source=sha(path.read_bytes());path.write_text('{\n  "answer": 1\n}\n')
        manifest=base/'reports/publication_manifest.json'
        data={'schema_version':'ttf-publication-manifest-v1','originals_modified':False,'manifest_self_excluded':True,
              'artifacts':{'reports/artifact.json':{'source_bytes_sha256':source,'public_bytes_sha256':sha(path.read_bytes()),'transformation':'JSON serialization','is_derived':True}}}
        manifest.write_text(json.dumps(data));return path,source,manifest,data
    def test_public_copy_is_explicit_and_preserves_source_vs_public_sha_identities(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);path,source,manifest,_=self.publication_fixture(base)
            with patch.object(sys.modules[__name__],'ROOT',base):
                self.assertFalse(artifact_sha_matches(path,source))
                mapped=PublicationManifest(manifest);token=_PUBLIC_COPY_MAP.set(mapped)
                try:
                    self.assertTrue(artifact_sha_matches(path,source));self.assertEqual(artifact_source_sha(path),source)
                    self.assertNotEqual(source,sha(path.read_bytes()))
                finally:_PUBLIC_COPY_MAP.reset(token)
                self.assertFalse(artifact_sha_matches(path,source))
    def test_public_copy_rejects_missing_stale_tampered_or_wrong_path_map(self):
        import tempfile
        from unittest.mock import patch
        for change in ('missing_entry','stale_public','tampered_file','traversal','absolute','duplicate'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                base=Path(directory);path,_,manifest,data=self.publication_fixture(base)
                if change=='missing_entry':data['artifacts']={}
                elif change=='stale_public':data['artifacts']['reports/artifact.json']['public_bytes_sha256']='f'*64
                elif change=='tampered_file':path.write_text('{"answer":2}')
                elif change in ('traversal','absolute'):
                    data['artifacts']['../artifact.json' if change=='traversal' else str(path)]=data['artifacts'].pop('reports/artifact.json')
                if change=='duplicate':manifest.write_text('{"schema_version":"a","schema_version":"b"}')
                else:manifest.write_text(json.dumps(data))
                with patch.object(sys.modules[__name__],'ROOT',base),self.assertRaises(ValueError):PublicationManifest(manifest)
    def test_public_copy_cannot_accept_wrong_source_reference_or_unmapped_identical_file(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);path,_,manifest,_=self.publication_fixture(base);other=base/'unmapped.json';other.write_text('{}')
            with patch.object(sys.modules[__name__],'ROOT',base):
                token=_PUBLIC_COPY_MAP.set(PublicationManifest(manifest))
                try:
                    self.assertFalse(artifact_sha_matches(path,'e'*64));self.assertFalse(artifact_sha_matches(other,sha(other.read_bytes())))
                finally:_PUBLIC_COPY_MAP.reset(token)
    def test_public_manifest_cannot_be_supplied_as_an_implicit_private_fallback(self):
        with self.assertRaises(ValueError):build_release_decision(Path('unused'),Path('unused'),publication_manifest=Path('unused'))
    def test_public_release_loader_verifies_derived_bytes_without_overwriting_source_decision(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);report,proofs,_=self.release_fixture();review=proofs['independent_review']['data'];source_decision=base/'release_decision.json';source_decision.write_text('{"source_decision":"stays"}')
            for row in report['comparison']['selected_records']:
                path=base/(row['case']+'.json');row['file']=path.name
                model={'case':row['case'],'result':{'_meta':{'model_calls':[{}]}} if row['passed'] else None,'error':None if row['passed'] else 'two rejected outputs','audit':{'model_calls':[{},{}]}}
                path.write_text(json.dumps(model))
                if row['passed']:review['selected_record_sha256'][row['case']]=sha(path.read_bytes())
            screenshot=base/'observed.png';screenshot.write_bytes(b'fixed-unit-test-image')
            proofs['error_ui']['data'].update(screenshot_file=screenshot.name,screenshot_sha256=sha(screenshot.read_bytes()))
            rp=base/'report.json';rp.write_text(json.dumps(report));references={}
            for name,proof in proofs.items():
                path=base/(name+('.log' if name=='frontend_build' else '.json'))
                path.write_text('built in 1.20s' if name=='frontend_build' else json.dumps(proof['data']))
                references[name]={'file':path.name,'sha256':sha(path.read_bytes())}
            errors=base/'reports/repair_2026-10-20/release_known_accepted_errors.json';errors.parent.mkdir(parents=True);errors.write_text('{"records":[]}')
            cp=base/'checks.json';cp.write_text(json.dumps({'policy_id':RELEASE_POLICY_ID,'main_report_sha256':sha(rp.read_bytes()),'evidence':references}))
            sources={str(path.relative_to(base)):sha(path.read_bytes()) for path in base.rglob('*') if path.is_file()}
            for path in base.rglob('*.json'):path.write_text(json.dumps(read_json(path),separators=(',',':'))+'\n')
            source_decision_before=source_decision.read_bytes()
            manifest=base/'reports/publication_manifest.json';manifest.write_text(json.dumps({'schema_version':'ttf-publication-manifest-v1','originals_modified':False,'manifest_self_excluded':True,'artifacts':{label:{'source_bytes_sha256':source,'public_bytes_sha256':sha((base/label).read_bytes()),'transformation':'unit test serialization','is_derived':source!=sha((base/label).read_bytes())} for label,source in sources.items()}}))
            with patch.object(sys.modules[__name__],'ROOT',base):
                private=build_release_decision(rp,cp)
                self.assertFalse(private['release_ready'])
                public=build_release_decision(rp,cp,public_copy=True,publication_manifest=manifest)
                self.assertTrue(public['release_ready']);self.assertEqual(public['artifact_verification_mode'],'explicit_public_derived_copy')
                self.assertEqual(public['main_report']['sha256'],sources['report.json']);self.assertEqual(public['main_report']['public_artifact_sha256'],sha(rp.read_bytes()))
                self.assertEqual(source_decision.read_bytes(),source_decision_before)
                self.assertIsNone(_PUBLIC_COPY_MAP.get())

    def test_exact_preservation(self): self.assertTrue(verify_preservation(self.db, self.manifest)["passed"])
    def test_new_rows_and_formerly_null_detail_allowed(self):
        self.db.execute("INSERT INTO analyses VALUES ('new','new','new',NULL)")
        self.db.execute("UPDATE analyses SET detail_result='new detail' WHERE id='old'")
        self.assertTrue(verify_preservation(self.db, self.manifest)["passed"])
    def test_json_whitespace_change_detected(self):
        self.db.execute("UPDATE analyses SET input_snapshot='{\"a\": 1}' WHERE id='old'")
        self.assertFalse(verify_preservation(self.db, self.manifest)["passed"])
    def test_old_record_deletion_detected(self):
        self.db.execute("DELETE FROM analyses")
        self.assertFalse(verify_preservation(self.db, self.manifest)["passed"])
    def test_snapshot_change_detected(self):
        self.db.execute("UPDATE daily_snapshots SET analysis_id='new'")
        self.assertFalse(verify_preservation(self.db, self.manifest)["passed"])
    def test_baseline_reset_detected(self):
        self.db.execute("INSERT INTO metrics VALUES(211,'later','validation_started','new')")
        self.assertFalse(verify_preservation(self.db, self.manifest)["passed"])
    def test_baseline_edit_detected(self):
        self.db.execute("UPDATE metrics SET data='changed'")
        self.assertFalse(verify_preservation(self.db, self.manifest)["passed"])
    def test_explicit_null_requires_insufficient_status(self):
        r={"news_impact":{"direction":"flat"},"close_forecast":{"direction":None,"evidence_status":"insufficient_evidence"}}
        self.assertTrue(contract_checks(r,{"expected_news":"flat","expected_close":None})["passed"])
        r["close_forecast"]["evidence_status"]="forecast"
        self.assertFalse(contract_checks(r,{"expected_close":None})["passed"])
    def test_absent_close_target_not_null_target(self):
        r={"news_impact":{"direction":"up"},"close_forecast":{"direction":"up","evidence_status":"forecast"}}
        self.assertTrue(contract_checks(r,{"expected_news":"up"})["passed"])
        self.assertFalse(contract_checks(r,{"expected_close":None})["passed"])
    def test_safe_report_hides_thinking_and_local_user_path(self):
        self.assertEqual(safe({"thinking":"hidden","file":"/Users/name/private/db.sqlite","thinking_char_count":5}), {"file":"[local path]","thinking_char_count":5})
    def test_last_raw_response_preserves_terminal_failure(self):
        self.assertEqual(last_raw_response({"attempts": [{"response":{"message":{"content":"first"}}},
                                                        {"response":{"message":{"content":"truncated"}}}]}), "truncated")
    def test_fenced_raw_can_be_inspected_without_rewriting(self):
        self.assertEqual(decoded_result('```json\n{"news_impact":{"direction":"down"}}\n```'),
                         {"news_impact":{"direction":"down"}})
        self.assertIsNone(decoded_result('{"news_impact":'))
    def canonical_risk_fixture(self):
        from ttf_news import analysis as a
        from ttf_news.prompts import SHORT_SHAPE
        context={'as_of':'2026-10-14T03:00:00Z','analysis_kind':'manual',
                 'session':{'session_date':'2026-10-14','close_at':'2026-10-14T15:15:00Z'},
                 'current_news':{'id':'manual-risk-fixture','source':'synthetic_control',
                     'title':'Exploration permit extended','body':'The permit extension allows further exploration and appraisal planning.',
                     'url':'https://example.test/permit','published_at':'2026-10-14T02:00:00Z','timestamp_precision':'exact'},
                 'window_news':[],'historical_events':[],'previous_sessions':[],'price_reference':{}}
        value=copy.deepcopy(SHORT_SHAPE)
        value['news_impact'].update(direction='flat',contextual_news_score=0.,summary='勘探许可延长，未提供产量变化，欧洲供给传导较弱',
            bullish_evidence=[],bearish_evidence=[],neutralizing_evidence=[])
        value['close_forecast'].update(direction=None,evidence_status='insufficient_evidence',
            summary='缺少同合约上一交易日价格，暂不形成收盘预判',basis='新闻传导较弱，且缺少同合约上一交易日价格锚点',extreme_move_risk='价格基准不足')
        value['analysis'].update(summary='新增事实仅为勘探许可延长',evidence=['[N0]许可延长用于勘探评估 → TTF供应方向尚不明确'],
            priced_in_comment='未提供独立价格反应，无法确认是否计价')
        raw=canonical_json(value);_,prepared_meta=a.prepare_context(copy.deepcopy(context),False,reserve_tokens=32)
        canonical=a._parse_result(raw,False,prepared_meta['source_index'],context=context)
        meta={'model_calls':[{'raw_content':raw}]}
        a._add_known_limitations(canonical,context,meta);canonical['_meta']=meta
        return context,canonical
    def test_canonical_backend_risks_are_rebuilt_without_weakening_raw_limit(self):
        context,result=self.canonical_risk_fixture();before=copy.deepcopy(result)
        self.assertGreater(len(result['risk_flags']),2)
        self.assertEqual(json.loads(result['_meta']['model_calls'][-1]['raw_content'])['risk_flags'],[])
        check=revalidate_result(result,context)
        self.assertTrue(check['passed']);self.assertTrue(check['canonical_backend_materialization_verified'])
        self.assertEqual(check['raw_validation_source'],'saved_final_model_call_raw_content')
        self.assertFalse(check['stored_canonical_arrays_modified']);self.assertEqual(result,before)
    def test_canonical_metadata_cannot_hide_overlimit_or_nonexact_duplicate_model_risks(self):
        for risks in (['甲','乙','丙'],['甲','甲 ']):
            with self.subTest(risks=risks):
                context,result=self.canonical_risk_fixture();before=copy.deepcopy(result)
                raw=json.loads(result['_meta']['model_calls'][-1]['raw_content']);raw['risk_flags']=risks
                result['_meta']['model_calls'][-1]['raw_content']=canonical_json(raw)
                check=revalidate_result(result,context)
                self.assertFalse(check['passed']);self.assertIn('risk_flags',check['reason'])
                self.assertEqual(result['risk_flags'],before['risk_flags'])
    def test_exact_duplicate_risk_remarks_require_transparent_exact_set_materialization(self):
        from ttf_news import analysis as a
        context,result=self.canonical_risk_fixture()
        value=json.loads(result['_meta']['model_calls'][-1]['raw_content'])
        note='price_reference:输入未提供可用的前一同合约价格锚点'
        value['risk_flags']=[note,note];raw=canonical_json(value)
        _,prepared_meta=a.prepare_context(copy.deepcopy(context),False,reserve_tokens=32)
        canonical=a._parse_result(raw,False,prepared_meta['source_index'],context=context)
        metadata={'model_calls':[{'raw_content':raw}]};a._add_known_limitations(canonical,context,metadata);canonical['_meta']=metadata
        before=copy.deepcopy(canonical);check=revalidate_result(canonical,context)
        self.assertTrue(check['passed']);self.assertEqual(canonical,before)
        audit=check['recorded_model_risk_flags_normalization']
        self.assertEqual((audit['raw_count'],audit['exact_unique_count'],audit['duplicates_removed']),(2,1,1))
        self.assertEqual(json.loads(canonical['_meta']['model_calls'][-1]['raw_content'])['risk_flags'],[note,note])
        for change in ('missing','wrong_count'):
            with self.subTest(change=change):
                bad=copy.deepcopy(canonical)
                if change=='missing':bad['_meta'].pop('model_risk_flags_normalization')
                else:bad['_meta']['model_risk_flags_normalization']['duplicates_removed']=0
                self.assertFalse(revalidate_result(bad,context)['passed'])
    def test_canonical_missing_or_failed_final_raw_is_not_verified_generation(self):
        for change in ('missing_raw','empty_calls','failed_call'):
            with self.subTest(change=change):
                context,result=self.canonical_risk_fixture()
                if change=='missing_raw':result['_meta']['model_calls'][-1].pop('raw_content')
                elif change=='empty_calls':result['_meta']['model_calls']=[]
                else:result['_meta']['model_calls'][-1]['validation_error']='preserved actual failure'
                self.assertFalse(revalidate_result(result,context)['passed'])
    def test_canonical_prose_scalars_references_and_extra_risks_must_match_saved_raw(self):
        for change in ('prose','direction','score','reference','extra_risk','missing_provenance','forged_provenance'):
            with self.subTest(change=change):
                context,result=self.canonical_risk_fixture()
                if change=='prose':result['analysis']['summary']='伪造的新正文'
                elif change=='direction':result['news_impact']['direction']='up'
                elif change=='score':result['news_impact']['contextual_news_score']=2.
                elif change=='reference':result['references'][0]['url']='https://example.test/forged'
                elif change=='extra_risk':result['risk_flags'].append('伪造风险')
                elif change=='missing_provenance':result['_meta'].pop('deterministic_risk_flags')
                else:result['_meta']['deterministic_risk_flags'].append('伪造风险')
                self.assertFalse(revalidate_result(result,context)['passed'])
    def test_report_link_tolerates_external_evidence_directory(self):
        self.assertEqual(report_link("reports/repair_2026-10-20/unit-tests.xml"), "repair_2026-10-20/unit-tests.xml")
        self.assertEqual(report_link("unit-tests.xml"), "unit-tests.xml")
    def detail_row(self, stage, *, passed=True, has_detail=True):
        return {"case":"supply_restore_40", "stage":stage, "passed":passed,
                "analysis_kind":"detail" if has_detail else "brief", "has_detail_result":has_detail,
                "analyst_elapsed_seconds":20, "wall_seconds":21, "attempts":2, "retries":1,
                "recorded_run_succeeded":passed, "current_logic_revalidation":{"passed":passed}}
    def test_detail_release_overrides_old_failure_without_changing_brief(self):
        old=self.detail_row(DETAIL_STAGES[0],passed=False)
        latest=self.detail_row(DETAIL_STAGES[1])
        groups={DETAIL_STAGES[0]:{"records":[old]},DETAIL_STAGES[1]:{"records":[latest]},
                "candidate-release-validation":{"records":[{"case":"supply_restore_40","passed":False}]}}
        detail=summarize_details(groups)
        self.assertTrue(detail["passed"])
        self.assertEqual(detail["passed_detail_count"],1)
        self.assertEqual(detail["model_attempts"],2)
        self.assertEqual(detail["retries"],1)
        self.assertFalse(groups[DETAIL_STAGES[0]]["records"][0]["passed"])
        self.assertFalse(groups["candidate-release-validation"]["records"][0]["passed"])
    def test_detail_latest_failure_cannot_fall_back_to_earlier_success(self):
        groups={DETAIL_STAGES[0]:{"records":[self.detail_row(DETAIL_STAGES[0])]},
                DETAIL_STAGES[1]:{"records":[self.detail_row(DETAIL_STAGES[1],passed=False)]}}
        detail=summarize_details(groups)
        self.assertFalse(detail["passed"])
        self.assertEqual(detail["failing_cases"],["supply_restore_40"])
    def test_no_detail_is_pending_and_does_not_pass(self):
        detail=summarize_details({})
        self.assertTrue(detail["pending"])
        self.assertFalse(detail["passed"])
        self.assertEqual(detail["passed_detail_count"],0)
    def test_brief_result_in_detail_directory_does_not_satisfy_detail_requirement(self):
        detail=summarize_details({DETAIL_STAGES[1]:{"records":[self.detail_row(DETAIL_STAGES[1],has_detail=False)]}})
        self.assertFalse(detail["passed"])
        self.assertEqual(detail["passed_detail_count"],0)
    def test_production_detail_supersedes_release_and_keeps_prior_failures(self):
        prior=self.detail_row("candidate-release-validation-detail",passed=False)
        production=self.detail_row("candidate-production-validation-detail")
        groups={"candidate-release-validation-detail":{"records":[prior]},
                "candidate-production-validation-detail":{"records":[production]}}
        detail=summarize_details(groups)
        self.assertTrue(detail["passed"])
        self.assertEqual(detail["records"][0]["stage"],"candidate-production-validation-detail")
        self.assertEqual(detail["override_cases"],["supply_restore_40"])
        self.assertFalse(groups["candidate-release-validation-detail"]["records"][0]["passed"])
    def test_production_detail_failure_does_not_select_successful_release(self):
        groups={"candidate-release-validation-detail":{"records":[self.detail_row("candidate-release-validation-detail")]},
                "candidate-production-validation-detail":{"records":[self.detail_row("candidate-production-validation-detail",passed=False)]}}
        detail=summarize_details(groups)
        self.assertFalse(detail["passed"])
        self.assertEqual(detail["records"][0]["stage"],"candidate-production-validation-detail")
    def test_production_brief_overrides_release_but_never_selects_detail(self):
        prior={"case":"supply_restore_40","stage":"candidate-release-validation","passed":False}
        production={"case":"supply_restore_40","stage":"candidate-production-validation","passed":True}
        groups={"candidate-release-validation":{"records":[prior]},
                "candidate-production-validation":{"records":[production]},
                "candidate-production-validation-detail":{"records":[self.detail_row("candidate-production-validation-detail",passed=False)]}}
        selected=select_briefs(groups)
        self.assertIs(selected["supply_restore_40"],production)
        self.assertFalse(groups["candidate-release-validation"]["records"][0]["passed"])
    def test_production_brief_failure_never_falls_back_to_prior_success(self):
        prior={"case":"supply_restore_40","stage":"candidate-release-validation","passed":True}
        production={"case":"supply_restore_40","stage":"candidate-production-validation","passed":False}
        selected=select_briefs({"candidate-release-validation":{"records":[prior]},
                                "candidate-production-validation":{"records":[production]}})
        self.assertFalse(selected["supply_restore_40"]["passed"])
        self.assertIs(selected["supply_restore_40"],production)
    def test_verified_brief_supersedes_production_without_changing_old_record(self):
        old={"case":"supply_restore_40","stage":"candidate-production-validation","passed":False}
        latest={"case":"supply_restore_40","stage":"candidate-verified-validation","passed":True}
        groups={old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}}
        self.assertIs(select_briefs(groups)["supply_restore_40"],latest)
        self.assertFalse(old["passed"])
    def test_verified_brief_failure_does_not_select_successful_production(self):
        old={"case":"supply_restore_40","stage":"candidate-production-validation","passed":True}
        latest={"case":"supply_restore_40","stage":"candidate-verified-validation","passed":False}
        self.assertIs(select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})["supply_restore_40"],latest)
        self.assertFalse(latest["passed"])
    def test_verified_detail_supersedes_production_and_preserves_old_failure(self):
        old=self.detail_row("candidate-production-validation-detail",passed=False)
        latest=self.detail_row("candidate-verified-validation-detail")
        groups={old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}}
        detail=summarize_details(groups)
        self.assertTrue(detail["passed"])
        self.assertIs(detail["records"][0],latest)
        self.assertFalse(old["passed"])
    def test_verified_detail_failure_does_not_select_successful_production(self):
        old=self.detail_row("candidate-production-validation-detail")
        latest=self.detail_row("candidate-verified-validation-detail",passed=False)
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertFalse(detail["passed"])
        self.assertIs(detail["records"][0],latest)
    def test_grounded_brief_supersedes_verified_and_preserves_old_failure(self):
        old={"case":"real_nyhamna","stage":"candidate-verified-validation","passed":False}
        latest={"case":"real_nyhamna","stage":"candidate-grounded-validation","passed":True}
        groups={old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}}
        self.assertIs(select_briefs(groups)["real_nyhamna"],latest)
        self.assertFalse(old["passed"])
    def test_grounded_brief_failure_does_not_select_successful_verified(self):
        old={"case":"real_nyhamna","stage":"candidate-verified-validation","passed":True}
        latest={"case":"real_nyhamna","stage":"candidate-grounded-validation","passed":False}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["real_nyhamna"],latest)
        self.assertFalse(selected["real_nyhamna"]["passed"])
    def test_grounded_detail_supersedes_verified_and_preserves_old_failure(self):
        old=self.detail_row("candidate-verified-validation-detail",passed=False)
        latest=self.detail_row("candidate-grounded-validation-detail")
        groups={old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}}
        detail=summarize_details(groups)
        self.assertTrue(detail["passed"])
        self.assertIs(detail["records"][0],latest)
        self.assertFalse(old["passed"])
    def test_grounded_detail_failure_does_not_select_successful_verified(self):
        old=self.detail_row("candidate-verified-validation-detail")
        latest=self.detail_row("candidate-grounded-validation-detail",passed=False)
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertFalse(detail["passed"])
        self.assertIs(detail["records"][0],latest)
    def test_decoder_brief_supersedes_grounded_and_preserves_old_failure(self):
        old={"case":"real_nyhamna","stage":"candidate-grounded-validation","passed":False}
        latest={"case":"real_nyhamna","stage":"candidate-decoder-validation","passed":True}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["real_nyhamna"],latest)
        self.assertFalse(old["passed"])
    def test_decoder_brief_failure_does_not_select_successful_grounded(self):
        old={"case":"real_nyhamna","stage":"candidate-grounded-validation","passed":True}
        latest={"case":"real_nyhamna","stage":"candidate-decoder-validation","passed":False}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["real_nyhamna"],latest)
        self.assertFalse(selected["real_nyhamna"]["passed"])
    def test_decoder_detail_supersedes_grounded_and_preserves_old_failure(self):
        old=self.detail_row("candidate-grounded-validation-detail",passed=False)
        latest=self.detail_row("candidate-decoder-validation-detail")
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertTrue(detail["passed"])
        self.assertIs(detail["records"][0],latest)
        self.assertFalse(old["passed"])
    def test_decoder_detail_failure_does_not_select_successful_grounded(self):
        old=self.detail_row("candidate-grounded-validation-detail")
        latest=self.detail_row("candidate-decoder-validation-detail",passed=False)
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertFalse(detail["passed"])
        self.assertIs(detail["records"][0],latest)
    def test_expected_close_sign_accepts_regular_and_extreme_family(self):
        for sign in ("up","down"):
            for direction in (sign,"extreme_"+sign):
                result={"news_impact":{"direction":sign},"close_forecast":{"direction":direction,"evidence_status":"forecast"}}
                self.assertTrue(contract_checks(result,{"expected_close_sign":sign})["passed"])
    def test_expected_close_sign_rejects_flat_opposite_or_insufficient(self):
        for direction,status in (("flat","forecast"),("down","forecast"),(None,"insufficient_evidence"),("up","insufficient_evidence")):
            result={"close_forecast":{"direction":direction,"evidence_status":status}}
            self.assertFalse(contract_checks(result,{"expected_close_sign":"up"})["passed"])
    def test_close_sign_does_not_override_exact_or_null_close_contract(self):
        result={"close_forecast":{"direction":"extreme_up","evidence_status":"forecast"}}
        self.assertFalse(contract_checks(result,{"expected_close":"up","expected_close_sign":"up"})["passed"])
        result["close_forecast"]["direction"]="up"
        self.assertFalse(contract_checks(result,{"expected_close":None,"expected_close_sign":"up"})["passed"])
    def test_invalid_close_sign_does_not_silently_pass(self):
        self.assertFalse(contract_checks({"close_forecast":{"direction":"up"}},{"expected_close_sign":"unknown"})["passed"])
    def test_frozen_original_scope_excludes_new_candidate_controls(self):
        frozen=[{"id":"old"}]
        current=[*frozen,{"id":"new_control","requires_original_comparison":False}]
        self.assertEqual(original_required_case_ids(current,frozen),{"old"})
    def test_explicit_candidate_only_scope_also_works_without_frozen_fixture(self):
        self.assertEqual(original_required_case_ids([{"id":"old"},{"id":"new_control","requires_original_comparison":False}]),{"old"})
    def test_factchecks_brief_supersedes_decoder_and_keeps_decoder_failure(self):
        old={"case":"real_nyhamna","stage":"candidate-decoder-validation","passed":False}
        latest={"case":"real_nyhamna","stage":"candidate-factchecks-validation","passed":True}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["real_nyhamna"],latest)
        self.assertFalse(old["passed"])
    def test_factchecks_brief_failure_does_not_select_successful_decoder(self):
        old={"case":"real_nyhamna","stage":"candidate-decoder-validation","passed":True}
        latest={"case":"real_nyhamna","stage":"candidate-factchecks-validation","passed":False}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["real_nyhamna"],latest)
        self.assertFalse(selected["real_nyhamna"]["passed"])
    def test_factchecks_detail_supersedes_decoder_and_keeps_decoder_failure(self):
        old=self.detail_row("candidate-decoder-validation-detail",passed=False)
        latest=self.detail_row("candidate-factchecks-validation-detail")
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertTrue(detail["passed"])
        self.assertIs(detail["records"][0],latest)
        self.assertFalse(old["passed"])
    def test_factchecks_detail_failure_does_not_select_successful_decoder(self):
        old=self.detail_row("candidate-decoder-validation-detail")
        latest=self.detail_row("candidate-factchecks-validation-detail",passed=False)
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertFalse(detail["passed"])
        self.assertIs(detail["records"][0],latest)
    def test_integrity_brief_supersedes_factchecks_and_keeps_factchecks_failure(self):
        old={"case":"real_nyhamna","stage":"candidate-factchecks-validation","passed":False}
        latest={"case":"real_nyhamna","stage":"candidate-integrity-validation","passed":True}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["real_nyhamna"],latest)
        self.assertFalse(old["passed"])
    def test_integrity_brief_failure_does_not_select_successful_factchecks(self):
        old={"case":"real_nyhamna","stage":"candidate-factchecks-validation","passed":True}
        latest={"case":"real_nyhamna","stage":"candidate-integrity-validation","passed":False}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["real_nyhamna"],latest)
        self.assertFalse(selected["real_nyhamna"]["passed"])
    def test_integrity_detail_supersedes_factchecks_and_keeps_factchecks_failure(self):
        old=self.detail_row("candidate-factchecks-validation-detail",passed=False)
        latest=self.detail_row("candidate-integrity-validation-detail")
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertTrue(detail["passed"])
        self.assertIs(detail["records"][0],latest)
        self.assertFalse(old["passed"])
    def test_integrity_detail_failure_does_not_select_successful_factchecks(self):
        old=self.detail_row("candidate-factchecks-validation-detail")
        latest=self.detail_row("candidate-integrity-validation-detail",passed=False)
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertFalse(detail["passed"])
        self.assertIs(detail["records"][0],latest)
    def test_parser_brief_supersedes_integrity_and_keeps_integrity_failure(self):
        old={"case":"future_planned_notice","stage":"candidate-integrity-validation","passed":False}
        latest={"case":"future_planned_notice","stage":"candidate-parser-validation","passed":True}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["future_planned_notice"],latest)
        self.assertFalse(old["passed"])
    def test_parser_brief_failure_does_not_select_successful_integrity(self):
        old={"case":"future_planned_notice","stage":"candidate-integrity-validation","passed":True}
        latest={"case":"future_planned_notice","stage":"candidate-parser-validation","passed":False}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["future_planned_notice"],latest)
        self.assertFalse(selected["future_planned_notice"]["passed"])
    def test_parser_detail_supersedes_integrity_and_keeps_integrity_failure(self):
        old=self.detail_row("candidate-integrity-validation-detail",passed=False)
        latest=self.detail_row("candidate-parser-validation-detail")
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertTrue(detail["passed"])
        self.assertIs(detail["records"][0],latest)
        self.assertFalse(old["passed"])
    def test_parser_detail_failure_does_not_select_successful_integrity(self):
        old=self.detail_row("candidate-integrity-validation-detail")
        latest=self.detail_row("candidate-parser-validation-detail",passed=False)
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertFalse(detail["passed"])
        self.assertIs(detail["records"][0],latest)
    def test_adaptive_brief_supersedes_parser_and_keeps_parser_failure(self):
        old={"case":"future_planned_notice","stage":"candidate-parser-validation","passed":False}
        latest={"case":"future_planned_notice","stage":"candidate-adaptive-validation","passed":True}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["future_planned_notice"],latest)
        self.assertFalse(old["passed"])
    def test_adaptive_brief_failure_does_not_select_successful_parser(self):
        old={"case":"future_planned_notice","stage":"candidate-parser-validation","passed":True}
        latest={"case":"future_planned_notice","stage":"candidate-adaptive-validation","passed":False}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["future_planned_notice"],latest)
        self.assertFalse(selected["future_planned_notice"]["passed"])
    def test_adaptive_detail_supersedes_parser_and_keeps_parser_failure(self):
        old=self.detail_row("candidate-parser-validation-detail",passed=False)
        latest=self.detail_row("candidate-adaptive-validation-detail")
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertTrue(detail["passed"])
        self.assertIs(detail["records"][0],latest)
        self.assertFalse(old["passed"])
    def test_adaptive_detail_failure_does_not_select_successful_parser(self):
        old=self.detail_row("candidate-parser-validation-detail")
        latest=self.detail_row("candidate-adaptive-validation-detail",passed=False)
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertFalse(detail["passed"])
        self.assertIs(detail["records"][0],latest)
    def test_unreadable_original_artifact_is_not_complete_evidence(self):
        self.assertEqual(unfinished_original_cases({"old":{"status":"unreadable_or_in_progress"}}),["old"])
    def test_old_terminal_failure_is_complete_evidence_but_not_success(self):
        row={"status":"needs_attention","recorded_run_succeeded":False,"recorded_error":"model output validation failed"}
        self.assertEqual(unfinished_original_cases({"old":row}),[])
        self.assertFalse(row["recorded_run_succeeded"])
    def test_original_without_result_or_terminal_error_remains_pending(self):
        self.assertEqual(unfinished_original_cases({"old":{"status":"needs_attention","recorded_run_succeeded":False,"recorded_error":None}}),["old"])
    def test_budget_brief_has_highest_priority_and_keeps_adaptive_failure(self):
        old={"case":"dismissed_notice_not_verified_recovery","stage":"candidate-adaptive-validation","passed":False}
        latest={"case":"dismissed_notice_not_verified_recovery","stage":"candidate-budget-validation","passed":True}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["dismissed_notice_not_verified_recovery"],latest)
        self.assertFalse(old["passed"])
    def test_budget_brief_failure_does_not_select_successful_adaptive(self):
        old={"case":"dismissed_notice_not_verified_recovery","stage":"candidate-adaptive-validation","passed":True}
        latest={"case":"dismissed_notice_not_verified_recovery","stage":"candidate-budget-validation","passed":False}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["dismissed_notice_not_verified_recovery"],latest)
        self.assertFalse(selected["dismissed_notice_not_verified_recovery"]["passed"])
    def test_budget_detail_has_highest_priority_and_keeps_adaptive_failure(self):
        old=self.detail_row("candidate-adaptive-validation-detail",passed=False)
        latest=self.detail_row("candidate-budget-validation-detail")
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertTrue(detail["passed"])
        self.assertIs(detail["records"][0],latest)
        self.assertFalse(old["passed"])
    def test_budget_detail_failure_does_not_select_successful_adaptive(self):
        old=self.detail_row("candidate-adaptive-validation-detail")
        latest=self.detail_row("candidate-budget-validation-detail",passed=False)
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertFalse(detail["passed"])
        self.assertIs(detail["records"][0],latest)
    def test_scoped_brief_supersedes_budget_and_keeps_budget_failure(self):
        old={"case":"negated_outage","stage":"candidate-budget-validation","passed":False}
        latest={"case":"negated_outage","stage":"candidate-scoped-validation","passed":True}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["negated_outage"],latest)
        self.assertFalse(old["passed"])
    def test_scoped_brief_failure_does_not_select_successful_budget(self):
        old={"case":"negated_outage","stage":"candidate-budget-validation","passed":True}
        latest={"case":"negated_outage","stage":"candidate-scoped-validation","passed":False}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["negated_outage"],latest)
        self.assertFalse(selected["negated_outage"]["passed"])
    def test_scoped_detail_supersedes_budget_and_keeps_budget_failure(self):
        old=self.detail_row("candidate-budget-validation-detail",passed=False)
        latest=self.detail_row("candidate-scoped-validation-detail")
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertTrue(detail["passed"])
        self.assertIs(detail["records"][0],latest)
        self.assertFalse(old["passed"])
    def test_scoped_detail_failure_does_not_select_successful_budget(self):
        old=self.detail_row("candidate-budget-validation-detail")
        latest=self.detail_row("candidate-scoped-validation-detail",passed=False)
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertFalse(detail["passed"])
        self.assertIs(detail["records"][0],latest)
    def test_symmetric_stages_registered_as_last_priority(self):
        self.assertLess(STAGES.index("candidate-scoped-validation"),STAGES.index("candidate-symmetric-validation"))
        self.assertLess(DETAIL_STAGES.index("candidate-scoped-validation-detail"),DETAIL_STAGES.index("candidate-symmetric-validation-detail"))
    def test_symmetric_brief_supersedes_scoped_and_keeps_scoped_failure(self):
        old={"case":"weak_exploration_no_quote","stage":"candidate-scoped-validation","passed":False}
        latest={"case":"weak_exploration_no_quote","stage":"candidate-symmetric-validation","passed":True}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["weak_exploration_no_quote"],latest)
        self.assertFalse(old["passed"])
    def test_symmetric_brief_failure_cannot_fall_back_to_scoped_success(self):
        old={"case":"weak_exploration_no_quote","stage":"candidate-scoped-validation","passed":True}
        latest={"case":"weak_exploration_no_quote","stage":"candidate-symmetric-validation","passed":False}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["weak_exploration_no_quote"],latest)
        self.assertFalse(selected["weak_exploration_no_quote"]["passed"])
    def test_symmetric_detail_supersedes_scoped_and_keeps_scoped_failure(self):
        old=self.detail_row("candidate-scoped-validation-detail",passed=False)
        latest=self.detail_row("candidate-symmetric-validation-detail")
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertTrue(detail["passed"])
        self.assertIs(detail["records"][0],latest)
        self.assertFalse(old["passed"])
    def test_symmetric_detail_failure_cannot_fall_back_to_scoped_success(self):
        old=self.detail_row("candidate-scoped-validation-detail")
        latest=self.detail_row("candidate-symmetric-validation-detail",passed=False)
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertFalse(detail["passed"])
        self.assertIs(detail["records"][0],latest)
    def test_scopeguard_stages_registered_as_last_priority(self):
        self.assertLess(STAGES.index("candidate-symmetric-validation"),STAGES.index("candidate-scopeguard-validation"))
        self.assertLess(DETAIL_STAGES.index("candidate-symmetric-validation-detail"),DETAIL_STAGES.index("candidate-scopeguard-validation-detail"))
    def test_scopeguard_brief_supersedes_symmetric_without_hiding_wrong_raw(self):
        old={"case":"dismissed_notice_not_verified_recovery","stage":"candidate-symmetric-validation","passed":False,"recorded_contract_passed":True}
        latest={"case":"dismissed_notice_not_verified_recovery","stage":"candidate-scopeguard-validation","passed":True}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["dismissed_notice_not_verified_recovery"],latest)
        self.assertFalse(old["passed"])
        self.assertTrue(old["recorded_contract_passed"])
    def test_scopeguard_brief_failure_cannot_fall_back_to_symmetric_success(self):
        old={"case":"dismissed_notice_not_verified_recovery","stage":"candidate-symmetric-validation","passed":True}
        latest={"case":"dismissed_notice_not_verified_recovery","stage":"candidate-scopeguard-validation","passed":False}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["dismissed_notice_not_verified_recovery"],latest)
        self.assertFalse(selected["dismissed_notice_not_verified_recovery"]["passed"])
    def test_scopeguard_detail_supersedes_symmetric_without_hiding_failure(self):
        old=self.detail_row("candidate-symmetric-validation-detail",passed=False)
        latest=self.detail_row("candidate-scopeguard-validation-detail")
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertTrue(detail["passed"])
        self.assertIs(detail["records"][0],latest)
        self.assertFalse(old["passed"])
    def test_scopeguard_detail_failure_cannot_fall_back_to_symmetric_success(self):
        old=self.detail_row("candidate-symmetric-validation-detail")
        latest=self.detail_row("candidate-scopeguard-validation-detail",passed=False)
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertFalse(detail["passed"])
        self.assertIs(detail["records"][0],latest)
    def test_feedback_stages_registered_as_last_priority(self):
        self.assertLess(STAGES.index("candidate-scopeguard-validation"),STAGES.index("candidate-feedback-validation"))
        self.assertLess(DETAIL_STAGES.index("candidate-scopeguard-validation-detail"),DETAIL_STAGES.index("candidate-feedback-validation-detail"))
    def test_feedback_brief_supersedes_scopeguard_without_hiding_failure(self):
        old={"case":"future_planned_notice","stage":"candidate-scopeguard-validation","passed":False}
        latest={"case":"future_planned_notice","stage":"candidate-feedback-validation","passed":True}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["future_planned_notice"],latest)
        self.assertFalse(old["passed"])
    def test_feedback_brief_failure_cannot_fall_back_to_scopeguard_success(self):
        old={"case":"future_planned_notice","stage":"candidate-scopeguard-validation","passed":True}
        latest={"case":"future_planned_notice","stage":"candidate-feedback-validation","passed":False}
        selected=select_briefs({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertIs(selected["future_planned_notice"],latest)
        self.assertFalse(selected["future_planned_notice"]["passed"])
    def test_feedback_detail_supersedes_scopeguard_without_hiding_failure(self):
        old=self.detail_row("candidate-scopeguard-validation-detail",passed=False)
        latest=self.detail_row("candidate-feedback-validation-detail")
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertTrue(detail["passed"])
        self.assertIs(detail["records"][0],latest)
        self.assertFalse(old["passed"])
    def test_feedback_detail_failure_cannot_fall_back_to_scopeguard_success(self):
        old=self.detail_row("candidate-scopeguard-validation-detail")
        latest=self.detail_row("candidate-feedback-validation-detail",passed=False)
        detail=summarize_details({old["stage"]:{"records":[old]},latest["stage"]:{"records":[latest]}})
        self.assertFalse(detail["passed"])
        self.assertIs(detail["records"][0],latest)
    def test_contract_stages_have_last_priority(self):
        self.assertLess(STAGES.index('candidate-feedback-validation'),STAGES.index('candidate-contract-validation'))
        self.assertLess(DETAIL_STAGES.index('candidate-feedback-validation-detail'),DETAIL_STAGES.index('candidate-contract-validation-detail'))
    def test_contract_brief_supersedes_feedback_preserving_original_failure(self):
        old={'case':'future_planned_notice','stage':'candidate-feedback-validation','passed':False,'recorded_error':'old contract marker failure'}
        latest={'case':'future_planned_notice','stage':'candidate-contract-validation','passed':True}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['future_planned_notice'],latest)
        self.assertFalse(old['passed'])
        self.assertEqual(old['recorded_error'],'old contract marker failure')
    def test_contract_brief_failure_cannot_fall_back_to_feedback_success(self):
        old={'case':'future_planned_notice','stage':'candidate-feedback-validation','passed':True}
        latest={'case':'future_planned_notice','stage':'candidate-contract-validation','passed':False}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['future_planned_notice'],latest)
        self.assertFalse(selected['future_planned_notice']['passed'])
    def test_unfinished_contract_brief_cannot_fall_back_to_feedback_success(self):
        old={'case':'future_planned_notice','stage':'candidate-feedback-validation','passed':True}
        latest={'case':'future_planned_notice','stage':'candidate-contract-validation','passed':False,'status':'unreadable_or_in_progress'}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['future_planned_notice'],latest)
        self.assertFalse(selected['future_planned_notice']['passed'])
    def test_contract_detail_supersedes_feedback_without_replacing_brief(self):
        old=self.detail_row('candidate-feedback-validation-detail',passed=False)
        latest=self.detail_row('candidate-contract-validation-detail')
        groups={old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}}
        detail=summarize_details(groups)
        self.assertTrue(detail['passed'])
        self.assertIs(detail['records'][0],latest)
        self.assertEqual(select_briefs(groups),{})
        self.assertFalse(old['passed'])
    def test_contract_detail_failure_cannot_fall_back_to_feedback_success(self):
        old=self.detail_row('candidate-feedback-validation-detail')
        latest=self.detail_row('candidate-contract-validation-detail',passed=False)
        detail=summarize_details({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertFalse(detail['passed'])
        self.assertIs(detail['records'][0],latest)
    def test_contract_stage_binds_current_case_fixture_and_original_stays_frozen(self):
        for stage in ('candidate-contract-validation','candidate-contract-validation-detail'):
            self.assertFalse(use_frozen_case_fixture(stage,True))
        self.assertTrue(use_frozen_case_fixture('original-comparison',True))
        self.assertTrue(use_frozen_case_fixture('candidate-comparison',True))
        self.assertFalse(use_frozen_case_fixture('original-comparison',False))
    def test_contract_stage_keeps_source_differences_and_current_generation_policy(self):
        import tempfile
        from unittest.mock import patch
        row=self.policy_record(False,'medium')
        row.update(case='weak',input_snapshot={'current_news':{'source':'offshore_energy'}},source_hashes={'ttf_news/analysis.py':'older-source'})
        row['result'].update(news_impact={'direction':'flat'},close_forecast={'direction':None,'evidence_status':'insufficient_evidence'})
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'weak.json';path.write_text(json.dumps(row));before=path.read_bytes()
            with patch.object(sys.modules[__name__],'revalidate_result',return_value={'passed':True}):
                result=inspect_record(path,{'id':'weak','expected_news':'flat'},'candidate-contract-validation',{'ttf_news/analysis.py':'current-source'})
            self.assertTrue(result['passed'])
            self.assertEqual(result['generation_source_files_changed'],['ttf_news/analysis.py'])
            self.assertTrue(result['current_generation_policy_compatibility']['compatible'])
            self.assertEqual(path.read_bytes(),before)
    def test_binding_stages_have_last_priority(self):
        self.assertLess(STAGES.index('candidate-contract-validation'),STAGES.index('candidate-binding-validation'))
        self.assertLess(DETAIL_STAGES.index('candidate-contract-validation-detail'),DETAIL_STAGES.index('candidate-binding-validation-detail'))
    def test_binding_brief_supersedes_contract_without_reclassifying_old_failure(self):
        old={'case':'real_nyhamna','stage':'candidate-contract-validation','passed':False,'recorded_error':'alias binding false positive plus genuine prose defects'}
        latest={'case':'real_nyhamna','stage':'candidate-binding-validation','passed':True}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['real_nyhamna'],latest)
        self.assertFalse(old['passed'])
        self.assertIn('genuine prose defects',old['recorded_error'])
    def test_binding_brief_failure_cannot_fall_back_to_contract_success(self):
        old={'case':'real_nyhamna','stage':'candidate-contract-validation','passed':True}
        latest={'case':'real_nyhamna','stage':'candidate-binding-validation','passed':False}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['real_nyhamna'],latest)
        self.assertFalse(selected['real_nyhamna']['passed'])
    def test_unfinished_binding_brief_cannot_fall_back_to_contract_success(self):
        old={'case':'real_nyhamna','stage':'candidate-contract-validation','passed':True}
        latest={'case':'real_nyhamna','stage':'candidate-binding-validation','passed':False,'status':'unreadable_or_in_progress'}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['real_nyhamna'],latest)
        self.assertFalse(selected['real_nyhamna']['passed'])
    def test_binding_detail_supersedes_contract_without_replacing_brief(self):
        old=self.detail_row('candidate-contract-validation-detail',passed=False)
        latest=self.detail_row('candidate-binding-validation-detail')
        groups={old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}}
        selected=summarize_details(groups)
        self.assertTrue(selected['passed'])
        self.assertIs(selected['records'][0],latest)
        self.assertEqual(select_briefs(groups),{})
        self.assertFalse(old['passed'])
    def test_binding_detail_failure_cannot_fall_back_to_contract_success(self):
        old=self.detail_row('candidate-contract-validation-detail')
        latest=self.detail_row('candidate-binding-validation-detail',passed=False)
        selected=summarize_details({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertFalse(selected['passed'])
        self.assertIs(selected['records'][0],latest)
    def test_binding_stage_uses_current_fixtures_and_original_stays_frozen(self):
        self.assertFalse(use_frozen_case_fixture('candidate-binding-validation',True))
        self.assertFalse(use_frozen_case_fixture('candidate-binding-validation-detail',True))
        self.assertTrue(use_frozen_case_fixture('original-comparison',True))
        self.assertTrue(use_frozen_case_fixture('candidate-comparison',True))
    def test_source_stages_have_last_priority(self):
        self.assertLess(STAGES.index('candidate-binding-validation'),STAGES.index('candidate-source-validation'))
        self.assertLess(DETAIL_STAGES.index('candidate-binding-validation-detail'),DETAIL_STAGES.index('candidate-source-validation-detail'))
    def test_source_brief_supersedes_binding_preserving_old_failure(self):
        old={'case':'real_eni','stage':'candidate-binding-validation','passed':False,'recorded_error':'announcement false positive and actual commissioning/unit defects'}
        latest={'case':'real_eni','stage':'candidate-source-validation','passed':True}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['real_eni'],latest)
        self.assertFalse(old['passed'])
        self.assertIn('actual commissioning/unit defects',old['recorded_error'])
    def test_source_brief_failure_cannot_fall_back_to_binding_success(self):
        old={'case':'real_eni','stage':'candidate-binding-validation','passed':True}
        latest={'case':'real_eni','stage':'candidate-source-validation','passed':False}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['real_eni'],latest)
        self.assertFalse(selected['real_eni']['passed'])
    def test_unfinished_source_brief_cannot_fall_back_to_binding_success(self):
        old={'case':'real_eni','stage':'candidate-binding-validation','passed':True}
        latest={'case':'real_eni','stage':'candidate-source-validation','passed':False,'status':'unreadable_or_in_progress'}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['real_eni'],latest)
        self.assertFalse(selected['real_eni']['passed'])
    def test_source_detail_supersedes_binding_without_replacing_brief(self):
        old=self.detail_row('candidate-binding-validation-detail',passed=False)
        latest=self.detail_row('candidate-source-validation-detail')
        groups={old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}}
        selected=summarize_details(groups)
        self.assertTrue(selected['passed'])
        self.assertIs(selected['records'][0],latest)
        self.assertEqual(select_briefs(groups),{})
        self.assertFalse(old['passed'])
    def test_source_detail_failure_cannot_fall_back_to_binding_success(self):
        old=self.detail_row('candidate-binding-validation-detail')
        latest=self.detail_row('candidate-source-validation-detail',passed=False)
        selected=summarize_details({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertFalse(selected['passed'])
        self.assertIs(selected['records'][0],latest)
    def test_source_stage_uses_current_fixtures_and_original_stays_frozen(self):
        self.assertFalse(use_frozen_case_fixture('candidate-source-validation',True))
        self.assertFalse(use_frozen_case_fixture('candidate-source-validation-detail',True))
        self.assertTrue(use_frozen_case_fixture('original-comparison',True))
        self.assertTrue(use_frozen_case_fixture('candidate-comparison',True))
    def test_provenance_stages_have_last_priority(self):
        self.assertLess(STAGES.index('candidate-source-validation'),STAGES.index('candidate-provenance-validation'))
        self.assertLess(DETAIL_STAGES.index('candidate-source-validation-detail'),DETAIL_STAGES.index('candidate-provenance-validation-detail'))
    def test_provenance_brief_supersedes_source_preserving_old_failure(self):
        old={'case':'real_eni','stage':'candidate-source-validation','passed':False,'recorded_error':'current N0 reference missing; original run remains failed'}
        latest={'case':'real_eni','stage':'candidate-provenance-validation','passed':True}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['real_eni'],latest)
        self.assertFalse(old['passed'])
        self.assertIn('original run remains failed',old['recorded_error'])
    def test_provenance_brief_failure_cannot_fall_back_to_source_success(self):
        old={'case':'real_eni','stage':'candidate-source-validation','passed':True}
        latest={'case':'real_eni','stage':'candidate-provenance-validation','passed':False}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['real_eni'],latest)
        self.assertFalse(selected['real_eni']['passed'])
    def test_unfinished_provenance_brief_cannot_fall_back_to_source_success(self):
        old={'case':'real_eni','stage':'candidate-source-validation','passed':True}
        latest={'case':'real_eni','stage':'candidate-provenance-validation','passed':False,'status':'unreadable_or_in_progress'}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['real_eni'],latest)
        self.assertFalse(selected['real_eni']['passed'])
    def test_provenance_detail_supersedes_source_without_replacing_brief(self):
        old=self.detail_row('candidate-source-validation-detail',passed=False)
        latest=self.detail_row('candidate-provenance-validation-detail')
        groups={old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}}
        selected=summarize_details(groups)
        self.assertTrue(selected['passed'])
        self.assertIs(selected['records'][0],latest)
        self.assertEqual(select_briefs(groups),{})
        self.assertFalse(old['passed'])
    def test_provenance_detail_failure_cannot_fall_back_to_source_success(self):
        old=self.detail_row('candidate-source-validation-detail')
        latest=self.detail_row('candidate-provenance-validation-detail',passed=False)
        selected=summarize_details({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertFalse(selected['passed'])
        self.assertIs(selected['records'][0],latest)
    def test_provenance_stage_uses_current_fixtures_and_original_stays_frozen(self):
        self.assertFalse(use_frozen_case_fixture('candidate-provenance-validation',True))
        self.assertFalse(use_frozen_case_fixture('candidate-provenance-validation-detail',True))
        self.assertTrue(use_frozen_case_fixture('original-comparison',True))
        self.assertTrue(use_frozen_case_fixture('candidate-comparison',True))
    def test_revision_stages_have_last_priority(self):
        self.assertLess(STAGES.index('candidate-provenance-validation'),STAGES.index('candidate-revision-validation'))
        self.assertLess(DETAIL_STAGES.index('candidate-provenance-validation-detail'),DETAIL_STAGES.index('candidate-revision-validation-detail'))
    def test_revision_brief_supersedes_provenance_preserving_old_failure(self):
        old={'case':'real_eni','stage':'candidate-provenance-validation','passed':False,'recorded_error':'real unknown-flow zero-impact prose remains failed'}
        latest={'case':'real_eni','stage':'candidate-revision-validation','passed':True}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['real_eni'],latest)
        self.assertFalse(old['passed'])
        self.assertIn('real unknown-flow zero-impact',old['recorded_error'])
    def test_revision_brief_failure_cannot_fall_back_to_provenance_success(self):
        old={'case':'real_eni','stage':'candidate-provenance-validation','passed':True}
        latest={'case':'real_eni','stage':'candidate-revision-validation','passed':False}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['real_eni'],latest)
        self.assertFalse(selected['real_eni']['passed'])
    def test_unfinished_revision_brief_cannot_fall_back_to_provenance_success(self):
        old={'case':'real_eni','stage':'candidate-provenance-validation','passed':True}
        latest={'case':'real_eni','stage':'candidate-revision-validation','passed':False,'status':'unreadable_or_in_progress'}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['real_eni'],latest)
        self.assertFalse(selected['real_eni']['passed'])
    def test_revision_detail_supersedes_provenance_without_replacing_brief(self):
        old=self.detail_row('candidate-provenance-validation-detail',passed=False)
        latest=self.detail_row('candidate-revision-validation-detail')
        groups={old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}}
        selected=summarize_details(groups)
        self.assertTrue(selected['passed'])
        self.assertIs(selected['records'][0],latest)
        self.assertEqual(select_briefs(groups),{})
        self.assertFalse(old['passed'])
    def test_revision_detail_failure_cannot_fall_back_to_provenance_success(self):
        old=self.detail_row('candidate-provenance-validation-detail')
        latest=self.detail_row('candidate-revision-validation-detail',passed=False)
        selected=summarize_details({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertFalse(selected['passed'])
        self.assertIs(selected['records'][0],latest)
    def test_revision_stage_uses_current_fixtures_and_original_stays_frozen(self):
        self.assertFalse(use_frozen_case_fixture('candidate-revision-validation',True))
        self.assertFalse(use_frozen_case_fixture('candidate-revision-validation-detail',True))
        self.assertTrue(use_frozen_case_fixture('original-comparison',True))
        self.assertTrue(use_frozen_case_fixture('candidate-comparison',True))
    def test_fieldfeedback_stages_have_last_priority_without_reusing_early_final(self):
        self.assertLess(STAGES.index('candidate-revision-validation'),STAGES.index('candidate-fieldfeedback-validation'))
        self.assertLess(DETAIL_STAGES.index('candidate-revision-validation-detail'),DETAIL_STAGES.index('candidate-fieldfeedback-validation-detail'))
        self.assertEqual(STAGES.count('candidate-final-validation'),1)
        self.assertEqual(DETAIL_STAGES.count('candidate-final-validation-detail'),1)
    def test_fieldfeedback_brief_supersedes_revision_preserving_old_failure(self):
        old={'case':'future_planned_notice','stage':'candidate-revision-validation','passed':False,'recorded_error':'unpriced assertion remains invalid'}
        latest={'case':'future_planned_notice','stage':'candidate-fieldfeedback-validation','passed':True}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['future_planned_notice'],latest)
        self.assertFalse(old['passed'])
        self.assertEqual(old['recorded_error'],'unpriced assertion remains invalid')
    def test_fieldfeedback_brief_failure_cannot_fall_back_to_revision_success(self):
        old={'case':'future_planned_notice','stage':'candidate-revision-validation','passed':True}
        latest={'case':'future_planned_notice','stage':'candidate-fieldfeedback-validation','passed':False}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['future_planned_notice'],latest)
        self.assertFalse(selected['future_planned_notice']['passed'])
    def test_unfinished_fieldfeedback_brief_cannot_fall_back_to_revision_success(self):
        old={'case':'future_planned_notice','stage':'candidate-revision-validation','passed':True}
        latest={'case':'future_planned_notice','stage':'candidate-fieldfeedback-validation','passed':False,'status':'unreadable_or_in_progress'}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['future_planned_notice'],latest)
        self.assertFalse(selected['future_planned_notice']['passed'])
    def test_fieldfeedback_detail_supersedes_revision_without_replacing_brief(self):
        old=self.detail_row('candidate-revision-validation-detail',passed=False)
        latest=self.detail_row('candidate-fieldfeedback-validation-detail')
        groups={old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}}
        selected=summarize_details(groups)
        self.assertTrue(selected['passed'])
        self.assertIs(selected['records'][0],latest)
        self.assertEqual(select_briefs(groups),{})
        self.assertFalse(old['passed'])
    def test_fieldfeedback_detail_failure_cannot_fall_back_to_revision_success(self):
        old=self.detail_row('candidate-revision-validation-detail')
        latest=self.detail_row('candidate-fieldfeedback-validation-detail',passed=False)
        selected=summarize_details({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertFalse(selected['passed'])
        self.assertIs(selected['records'][0],latest)
    def test_fieldfeedback_stage_uses_current_fixtures_and_original_stays_frozen(self):
        self.assertFalse(use_frozen_case_fixture('candidate-fieldfeedback-validation',True))
        self.assertFalse(use_frozen_case_fixture('candidate-fieldfeedback-validation-detail',True))
        self.assertTrue(use_frozen_case_fixture('original-comparison',True))
        self.assertTrue(use_frozen_case_fixture('candidate-comparison',True))
    def test_reaction_stages_have_last_priority(self):
        self.assertLess(STAGES.index('candidate-fieldfeedback-validation'),STAGES.index('candidate-reaction-validation'))
        self.assertLess(DETAIL_STAGES.index('candidate-fieldfeedback-validation-detail'),DETAIL_STAGES.index('candidate-reaction-validation-detail'))
    def test_reaction_brief_supersedes_fieldfeedback_preserving_recorded_machine_pass(self):
        old={'case':'future_planned_notice','stage':'candidate-fieldfeedback-validation','passed':False,'recorded_contract_passed':True}
        latest={'case':'future_planned_notice','stage':'candidate-reaction-validation','passed':True}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['future_planned_notice'],latest)
        self.assertFalse(old['passed']);self.assertTrue(old['recorded_contract_passed'])
    def test_reaction_brief_failure_cannot_fall_back_to_fieldfeedback_success(self):
        old={'case':'future_planned_notice','stage':'candidate-fieldfeedback-validation','passed':True}
        latest={'case':'future_planned_notice','stage':'candidate-reaction-validation','passed':False}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['future_planned_notice'],latest)
        self.assertFalse(selected['future_planned_notice']['passed'])
    def test_unfinished_reaction_brief_cannot_fall_back_to_fieldfeedback_success(self):
        old={'case':'future_planned_notice','stage':'candidate-fieldfeedback-validation','passed':True}
        latest={'case':'future_planned_notice','stage':'candidate-reaction-validation','passed':False,'status':'unreadable_or_in_progress'}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['future_planned_notice'],latest)
        self.assertFalse(selected['future_planned_notice']['passed'])
    def test_reaction_detail_supersedes_fieldfeedback_without_replacing_brief(self):
        old=self.detail_row('candidate-fieldfeedback-validation-detail',passed=False)
        latest=self.detail_row('candidate-reaction-validation-detail')
        groups={old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}}
        selected=summarize_details(groups)
        self.assertTrue(selected['passed']);self.assertIs(selected['records'][0],latest)
        self.assertEqual(select_briefs(groups),{});self.assertFalse(old['passed'])
    def test_reaction_detail_failure_cannot_fall_back_to_fieldfeedback_success(self):
        old=self.detail_row('candidate-fieldfeedback-validation-detail')
        latest=self.detail_row('candidate-reaction-validation-detail',passed=False)
        selected=summarize_details({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertFalse(selected['passed']);self.assertIs(selected['records'][0],latest)
    def test_reaction_stage_uses_current_fixtures_and_original_stays_frozen(self):
        self.assertFalse(use_frozen_case_fixture('candidate-reaction-validation',True))
        self.assertFalse(use_frozen_case_fixture('candidate-reaction-validation-detail',True))
        self.assertTrue(use_frozen_case_fixture('original-comparison',True))
        self.assertTrue(use_frozen_case_fixture('candidate-comparison',True))
    def test_anchor_stages_have_last_priority(self):
        self.assertLess(STAGES.index('candidate-anchor-validation'),STAGES.index('candidate-reviewwording-validation'))
        self.assertLess(DETAIL_STAGES.index('candidate-anchor-validation-detail'),DETAIL_STAGES.index('candidate-reviewwording-validation-detail'))
    def test_anchor_brief_supersedes_reaction_preserving_original_record(self):
        old={'case':'gap_control','stage':'candidate-reaction-validation','passed':False,'recorded_contract_passed':True}
        latest={'case':'gap_control','stage':'candidate-anchor-validation','passed':True}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['gap_control'],latest)
        self.assertFalse(old['passed']);self.assertTrue(old['recorded_contract_passed'])
    def test_anchor_brief_failure_cannot_fall_back_to_reaction_success(self):
        old={'case':'gap_control','stage':'candidate-reaction-validation','passed':True}
        latest={'case':'gap_control','stage':'candidate-anchor-validation','passed':False}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['gap_control'],latest);self.assertFalse(selected['gap_control']['passed'])
    def test_unfinished_anchor_brief_cannot_fall_back_to_reaction_success(self):
        old={'case':'gap_control','stage':'candidate-reaction-validation','passed':True}
        latest={'case':'gap_control','stage':'candidate-anchor-validation','passed':False,'status':'unreadable_or_in_progress'}
        selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertIs(selected['gap_control'],latest);self.assertFalse(selected['gap_control']['passed'])
    def test_anchor_detail_supersedes_reaction_without_replacing_brief(self):
        old=self.detail_row('candidate-reaction-validation-detail',passed=False);latest=self.detail_row('candidate-anchor-validation-detail')
        groups={old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}}
        selected=summarize_details(groups);self.assertTrue(selected['passed']);self.assertIs(selected['records'][0],latest)
        self.assertEqual(select_briefs(groups),{});self.assertFalse(old['passed'])
    def test_anchor_detail_failure_cannot_fall_back_to_reaction_success(self):
        old=self.detail_row('candidate-reaction-validation-detail');latest=self.detail_row('candidate-anchor-validation-detail',passed=False)
        selected=summarize_details({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
        self.assertFalse(selected['passed']);self.assertIs(selected['records'][0],latest)
    def test_anchor_stage_uses_current_fixtures_and_original_stays_frozen(self):
        self.assertFalse(use_frozen_case_fixture('candidate-anchor-validation',True));self.assertFalse(use_frozen_case_fixture('candidate-anchor-validation-detail',True))
        self.assertTrue(use_frozen_case_fixture('original-comparison',True));self.assertTrue(use_frozen_case_fixture('candidate-comparison',True))
    def test_reviewwording_priority_remains_after_anchor(self):
        self.assertLess(STAGES.index('candidate-anchor-validation'),STAGES.index('candidate-reviewwording-validation'))
        self.assertLess(DETAIL_STAGES.index('candidate-anchor-validation-detail'),DETAIL_STAGES.index('candidate-reviewwording-validation-detail'))
    def test_reviewwording_actual_run_supersedes_anchor_false_positive_without_rewriting_it(self):
        old={'case':'anchor_real_manual_missing_prevday','stage':'candidate-anchor-validation','passed':False,'recorded_run_succeeded':False}
        new={**old,'stage':'candidate-reviewwording-validation','passed':True,'recorded_run_succeeded':True}
        rows=select_briefs({old['stage']:{'records':[old]},new['stage']:{'records':[new]}})
        self.assertIs(rows[old['case']],new);self.assertFalse(old['passed'])
    def test_reviewwording_latest_failure_cannot_fall_back_to_anchor_success(self):
        old={'case':'anchor_real_manual_missing_prevday','stage':'candidate-anchor-validation','passed':True}
        new={**old,'stage':'candidate-reviewwording-validation','passed':False}
        rows=select_briefs({old['stage']:{'records':[old]},new['stage']:{'records':[new]}})
        self.assertIs(rows[old['case']],new);self.assertFalse(rows[old['case']]['passed'])
    def test_reviewwording_unfinished_file_cannot_fall_back_to_anchor_success(self):
        old={'case':'anchor_real_manual_missing_prevday','stage':'candidate-anchor-validation','passed':True}
        new={**old,'stage':'candidate-reviewwording-validation','passed':False,'status':'unreadable_or_in_progress'}
        self.assertIs(select_briefs({old['stage']:{'records':[old]},new['stage']:{'records':[new]}})[old['case']],new)
    def test_reviewwording_detail_remains_separate_from_brief(self):
        old=self.detail_row('candidate-anchor-validation-detail',passed=False);new=self.detail_row('candidate-reviewwording-validation-detail')
        groups={old['stage']:{'records':[old]},new['stage']:{'records':[new]}}
        self.assertIs(summarize_details(groups)['records'][0],new);self.assertEqual(select_briefs(groups),{})
    def test_reviewwording_detail_latest_failure_cannot_fall_back(self):
        old=self.detail_row('candidate-anchor-validation-detail');new=self.detail_row('candidate-reviewwording-validation-detail',passed=False)
        self.assertFalse(summarize_details({old['stage']:{'records':[old]},new['stage']:{'records':[new]}})['passed'])
    def test_reviewwording_uses_current_explicit_fixture_not_old_frozen_inputs(self):
        self.assertFalse(use_frozen_case_fixture('candidate-reviewwording-validation',True))
        self.assertFalse(use_frozen_case_fixture('candidate-reviewwording-validation-detail',True))
        self.assertTrue(use_frozen_case_fixture('original-comparison',True))
    def test_semantic_regeneration_precedes_explicit_price_observation_repairs(self):
        self.assertLess(STAGES.index(SEMANTIC_REGENERATION_STAGE),STAGES.index(PRICE_OBSERVATION_STAGE))
        self.assertLess(DETAIL_STAGES.index(SEMANTIC_REGENERATION_STAGE+'-detail'),DETAIL_STAGES.index(PRICE_OBSERVATION_STAGE+'-detail'))
    def test_semantic_regeneration_does_not_rewrite_earlier_terminal_attempt(self):
        old={'case':'real_eni','stage':'candidate-fieldfeedback-validation','passed':False,'error':'two rejected responses'}
        new={**old,'stage':'candidate-semantic-regeneration-validation','passed':True,'error':None}
        self.assertIs(select_briefs({old['stage']:{'records':[old]},new['stage']:{'records':[new]}})['real_eni'],new)
        self.assertFalse(old['passed']);self.assertEqual(old['error'],'two rejected responses')
    def test_semantic_regeneration_failure_cannot_fall_back_to_earlier_success(self):
        old={'case':'control','stage':'candidate-reviewwording-validation','passed':True}
        new={**old,'stage':'candidate-semantic-regeneration-validation','passed':False}
        for status in ('failed','unreadable_or_in_progress'):
            with self.subTest(status=status):
                new['status']=status
                selected=select_briefs({old['stage']:{'records':[old]},new['stage']:{'records':[new]}})
                self.assertIs(selected['control'],new);self.assertFalse(selected['control']['passed'])
    def test_semantic_regeneration_detail_is_independent_of_brief(self):
        old=self.detail_row('candidate-reviewwording-validation-detail',passed=False)
        new=self.detail_row('candidate-semantic-regeneration-validation-detail')
        groups={old['stage']:{'records':[old]},new['stage']:{'records':[new]}}
        self.assertIs(summarize_details(groups)['records'][0],new);self.assertEqual(select_briefs(groups),{})
    def test_semantic_regeneration_detail_failure_never_falls_back(self):
        old=self.detail_row('candidate-reviewwording-validation-detail')
        new=self.detail_row('candidate-semantic-regeneration-validation-detail',passed=False)
        summary=summarize_details({old['stage']:{'records':[old]},new['stage']:{'records':[new]}})
        self.assertFalse(summary['passed']);self.assertIs(summary['records'][0],new)
    def test_semantic_regeneration_uses_explicit_current_inputs(self):
        self.assertFalse(use_frozen_case_fixture('candidate-semantic-regeneration-validation',True))
        self.assertFalse(use_frozen_case_fixture('candidate-semantic-regeneration-validation-detail',True))
        self.assertTrue(use_frozen_case_fixture('original-comparison',True))
    def test_price_observation_is_latest_and_preserves_all_original_attempts(self):
        self.assertLess(STAGES.index(SEMANTIC_REGENERATION_STAGE),STAGES.index(PRICE_OBSERVATION_STAGE))
        old={'case':'weak_exploration_no_quote','stage':SEMANTIC_REGENERATION_STAGE,'passed':True}
        for status in ('failed','unreadable_or_in_progress'):
            latest={**old,'stage':PRICE_OBSERVATION_STAGE,'passed':False,'status':status}
            selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
            self.assertIs(selected[old['case']],latest);self.assertTrue(old['passed'])
        self.assertFalse(use_frozen_case_fixture(PRICE_OBSERVATION_STAGE,True))
    def test_final_tense_actual_terminal_has_highest_priority_without_fallback(self):
        self.assertLess(STAGES.index(PRICE_OBSERVATION_STAGE),STAGES.index(FINAL_TENSE_STAGE))
        self.assertLess(DETAIL_STAGES.index(PRICE_OBSERVATION_STAGE+'-detail'),DETAIL_STAGES.index(FINAL_TENSE_STAGE+'-detail'))
        old={'case':SEMANTIC_REGENERATION_WELL_CASE,'stage':PRICE_OBSERVATION_STAGE,'passed':True}
        for status in ('failed','unreadable_or_in_progress'):
            latest={**old,'stage':FINAL_TENSE_STAGE,'passed':False,'status':status}
            selected=select_briefs({old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}})
            self.assertIs(selected[old['case']],latest);self.assertTrue(old['passed'])
        before=self.detail_row(PRICE_OBSERVATION_STAGE+'-detail');after=self.detail_row(FINAL_TENSE_STAGE+'-detail',passed=False)
        self.assertFalse(summarize_details({before['stage']:{'records':[before]},after['stage']:{'records':[after]}})['passed'])
        self.assertFalse(use_frozen_case_fixture(FINAL_TENSE_STAGE,True))
    def test_detail_grounding_is_latest_detail_only_and_registered_for_complete_path_proof(self):
        self.assertLess(DETAIL_STAGES.index(FINAL_TENSE_STAGE+'-detail'),DETAIL_STAGES.index(DETAIL_GROUNDING_STAGE+'-detail'))
        self.assertIn(DETAIL_GROUNDING_STAGE,CURRENT_COHORT_STAGES)
        self.assertNotIn(DETAIL_GROUNDING_STAGE,STAGES)
        self.assertFalse(use_frozen_case_fixture(DETAIL_GROUNDING_STAGE+'-detail',True))
    def test_detail_grounding_latest_failure_or_partial_never_falls_back(self):
        old=self.detail_row(PRICE_OBSERVATION_STAGE+'-detail')
        for status in ('failed','unreadable_or_in_progress'):
            with self.subTest(status=status):
                latest=self.detail_row(DETAIL_GROUNDING_STAGE+'-detail',passed=False)
                latest['status']=status
                groups={old['stage']:{'records':[old]},latest['stage']:{'records':[latest]}}
                chosen=summarize_details(groups)
                self.assertIs(chosen['records'][0],latest)
                self.assertFalse(chosen['passed']);self.assertTrue(old['passed'])
                self.assertEqual(select_briefs(groups),{})
    def test_detail_grounding_missing_record_does_not_invent_current_completion(self):
        old=self.detail_row(PRICE_OBSERVATION_STAGE+'-detail',passed=False)
        summary=summarize_details({old['stage']:{'records':[old]}})
        self.assertIs(summary['records'][0],old)
        self.assertFalse(summary['passed'])
        self.assertNotEqual(summary['records'][0]['stage'],DETAIL_GROUNDING_STAGE+'-detail')
    def test_bounded_output_complete_cohort_is_highest_and_uses_explicit_current_fixture(self):
        self.assertLess(STAGES.index(FINAL_TENSE_STAGE),STAGES.index(BOUNDED_OUTPUT_STAGE))
        self.assertLess(DETAIL_STAGES.index(DETAIL_GROUNDING_STAGE+'-detail'),DETAIL_STAGES.index(BOUNDED_OUTPUT_STAGE+'-detail'))
        self.assertIn(BOUNDED_OUTPUT_STAGE,CURRENT_COHORT_STAGES)
        self.assertFalse(use_frozen_case_fixture(BOUNDED_OUTPUT_STAGE,True))
        self.assertFalse(use_frozen_case_fixture(BOUNDED_OUTPUT_STAGE+'-detail',True))
    def test_bounded_output_failure_or_partial_does_not_select_earlier_success(self):
        old={'case':'supply_loss_40','stage':FINAL_TENSE_STAGE,'passed':True}
        for status in ('failed','unreadable_or_in_progress'):
            with self.subTest(status=status):
                latest={**old,'stage':BOUNDED_OUTPUT_STAGE,'passed':False,'status':status}
                groups={old['stage']:{'records':[old]},BOUNDED_OUTPUT_STAGE:{'records':[latest]}}
                self.assertIs(select_briefs(groups)['supply_loss_40'],latest)
                self.assertFalse(latest['passed']);self.assertTrue(old['passed'])
                before=self.detail_row(DETAIL_GROUNDING_STAGE+'-detail')
                after=self.detail_row(BOUNDED_OUTPUT_STAGE+'-detail',passed=False)
                summary=summarize_details({before['stage']:{'records':[before]},after['stage']:{'records':[after]}})
                self.assertIs(summary['records'][0],after);self.assertFalse(summary['passed'])
    def test_bounded_output_missing_cases_and_detail_never_borrow_previous_current_cohort(self):
        cases,groups,hashes,version=self.fresh_cohort_fixture()
        groups[BOUNDED_OUTPUT_STAGE]={'records':[]}
        groups[BOUNDED_OUTPUT_STAGE+'-detail']={'records':[]}
        self.assertEqual(select_briefs(groups),{})
        summary=summarize_semantic_regeneration(cases,groups,hashes,version)
        self.assertEqual(summary['selected_case_count'],0)
        self.assertEqual(set(summary['missing_cases']),{case['id'] for case in cases})
        self.assertEqual(summary['detail_records'],[])
        self.assertFalse(summary['execution_complete'])
        self.assertFalse(summary['passed'])
    def test_bounded_output_requires_all_twenty_new_actuals_and_new_detail(self):
        cases,groups,hashes,version=self.fresh_cohort_fixture()
        old_rows=groups[SEMANTIC_REGENERATION_STAGE]['records']
        latest=[{**row,'stage':BOUNDED_OUTPUT_STAGE} for row in old_rows]
        groups[BOUNDED_OUTPUT_STAGE]={'records':latest[:-1]}
        groups[BOUNDED_OUTPUT_STAGE+'-detail']={'records':[]}
        summary=summarize_semantic_regeneration(cases,groups,hashes,version)
        self.assertEqual(summary['missing_cases'],[latest[-1]['case']])
        self.assertEqual(summary['current_version_actual_detail_count'],0)
        self.assertFalse(summary['execution_complete'])
        groups[BOUNDED_OUTPUT_STAGE]['records'].append(latest[-1])
        previous=groups[SEMANTIC_REGENERATION_STAGE+'-detail']['records'][0]
        detail={**previous,'stage':BOUNDED_OUTPUT_STAGE+'-detail'}
        groups[BOUNDED_OUTPUT_STAGE+'-detail']['records'].append(detail)
        summary=summarize_semantic_regeneration(cases,groups,hashes,version)
        self.assertTrue(summary['execution_complete']);self.assertTrue(summary['passed'])
        self.assertTrue(all(row['stage']==BOUNDED_OUTPUT_STAGE for row in summary['records']))
        self.assertEqual(summary['detail_records'],[detail])
    def test_final_output_scope_is_latest_case_only_stage_and_keeps_frozen_inputs(self):
        self.assertLess(STAGES.index(BOUNDED_OUTPUT_STAGE),STAGES.index(FINAL_OUTPUT_SCOPE_STAGE))
        self.assertLess(DETAIL_STAGES.index(BOUNDED_OUTPUT_STAGE+'-detail'),DETAIL_STAGES.index(FINAL_OUTPUT_SCOPE_STAGE+'-detail'))
        self.assertIn(FINAL_OUTPUT_SCOPE_STAGE,CURRENT_COHORT_STAGES)
        self.assertFalse(use_frozen_case_fixture(FINAL_OUTPUT_SCOPE_STAGE,True))
        self.assertFalse(use_frozen_case_fixture(FINAL_OUTPUT_SCOPE_STAGE+'-detail',True))
    def test_final_output_scope_merges_bounded_only_and_latest_failure_never_falls_back(self):
        old={'case':'old_only','stage':PRICE_OBSERVATION_STAGE,'passed':True}
        base={'case':'cold_heating_demand','stage':BOUNDED_OUTPUT_STAGE,'passed':True}
        retained={'case':'supply_loss_40','stage':BOUNDED_OUTPUT_STAGE,'passed':True}
        for status in ('failed','unreadable_or_in_progress'):
            with self.subTest(status=status):
                new={**base,'stage':FINAL_OUTPUT_SCOPE_STAGE,'passed':False,'status':status}
                groups={PRICE_OBSERVATION_STAGE:{'records':[old]},BOUNDED_OUTPUT_STAGE:{'records':[base,retained]},
                        FINAL_OUTPUT_SCOPE_STAGE:{'records':[new]}}
                selected=select_briefs(groups)
                self.assertEqual(set(selected),{base['case'],retained['case']})
                self.assertIs(selected[base['case']],new);self.assertIs(selected[retained['case']],retained)
                self.assertFalse(new['passed']);self.assertTrue(base['passed'])
    def test_final_output_scope_missing_base_cannot_borrow_older_success_or_detail(self):
        cases,groups,hashes,version=self.fresh_cohort_fixture()
        retained=groups[SEMANTIC_REGENERATION_STAGE]['records'][0]
        follow={**retained,'stage':FINAL_OUTPUT_SCOPE_STAGE}
        groups[FINAL_OUTPUT_SCOPE_STAGE]={'records':[follow]}
        groups[FINAL_OUTPUT_SCOPE_STAGE+'-detail']={'records':[]}
        self.assertEqual(select_briefs(groups),{follow['case']:follow})
        summary=summarize_semantic_regeneration(cases,groups,hashes,version)
        self.assertEqual(summary['selected_case_count'],1);self.assertEqual(len(summary['missing_cases']),19)
        self.assertEqual(summary['detail_records'],[]);self.assertFalse(summary['execution_complete'])
    def test_final_output_scope_detail_is_case_only_latest_and_never_changes_briefs(self):
        cases,groups,hashes,version=self.fresh_cohort_fixture()
        rows=[{**row,'stage':BOUNDED_OUTPUT_STAGE} for row in groups[SEMANTIC_REGENERATION_STAGE]['records']]
        base={**groups[SEMANTIC_REGENERATION_STAGE+'-detail']['records'][0],'stage':BOUNDED_OUTPUT_STAGE+'-detail'}
        latest={**base,'stage':FINAL_OUTPUT_SCOPE_STAGE+'-detail','passed':False,'has_detail_result':False,
                'recorded_run_succeeded':False,'recorded_error':'two actual rejected responses','attempts':2}
        groups.update({BOUNDED_OUTPUT_STAGE:{'records':rows},BOUNDED_OUTPUT_STAGE+'-detail':{'records':[base]},
                       FINAL_OUTPUT_SCOPE_STAGE:{'records':[]},FINAL_OUTPUT_SCOPE_STAGE+'-detail':{'records':[latest]}})
        summary=summarize_semantic_regeneration(cases,groups,hashes,version)
        self.assertEqual(summary['detail_records'],[latest]);self.assertEqual(summary['passed_case_count'],20)
        self.assertFalse(summary['passed']);self.assertTrue(base['passed'])
    def test_source_feedback_is_latest_registered_case_only_phase(self):
        self.assertLess(STAGES.index(FINAL_OUTPUT_SCOPE_STAGE),STAGES.index(SOURCE_FEEDBACK_STAGE))
        self.assertLess(DETAIL_STAGES.index(FINAL_OUTPUT_SCOPE_STAGE+'-detail'),DETAIL_STAGES.index(SOURCE_FEEDBACK_STAGE+'-detail'))
        self.assertIn(SOURCE_FEEDBACK_STAGE,CURRENT_COHORT_STAGES)
        self.assertFalse(use_frozen_case_fixture(SOURCE_FEEDBACK_STAGE,True))
        self.assertFalse(use_frozen_case_fixture(SOURCE_FEEDBACK_STAGE+'-detail',True))
    def test_source_feedback_merges_only_bounded_and_scope_current_cases(self):
        old={'case':'old_only','stage':PRICE_OBSERVATION_STAGE,'passed':True}
        retained={'case':'supply_loss_40','stage':BOUNDED_OUTPUT_STAGE,'passed':True}
        base={'case':'cold_heating_demand','stage':BOUNDED_OUTPUT_STAGE,'passed':True}
        scoped={'case':'future_planned_notice','stage':FINAL_OUTPUT_SCOPE_STAGE,'passed':True}
        new={**base,'stage':SOURCE_FEEDBACK_STAGE,'passed':False,'status':'failed'}
        groups={old['stage']:{'records':[old]},BOUNDED_OUTPUT_STAGE:{'records':[base,retained]},
                FINAL_OUTPUT_SCOPE_STAGE:{'records':[scoped]},SOURCE_FEEDBACK_STAGE:{'records':[new]}}
        selected=select_briefs(groups)
        self.assertEqual(set(selected),{base['case'],retained['case'],scoped['case']})
        self.assertIs(selected[base['case']],new)
        self.assertIs(selected[retained['case']],retained)
        self.assertIs(selected[scoped['case']],scoped)
    def test_source_feedback_latest_failed_or_partial_never_falls_back(self):
        for status in ('failed','unreadable_or_in_progress'):
            with self.subTest(status=status):
                base={'case':'real_eni','stage':BOUNDED_OUTPUT_STAGE,'passed':True}
                scoped={**base,'stage':FINAL_OUTPUT_SCOPE_STAGE}
                new={**base,'stage':SOURCE_FEEDBACK_STAGE,'passed':False,'status':status}
                groups={BOUNDED_OUTPUT_STAGE:{'records':[base]},FINAL_OUTPUT_SCOPE_STAGE:{'records':[scoped]},
                        SOURCE_FEEDBACK_STAGE:{'records':[new]}}
                self.assertIs(select_briefs(groups)[base['case']],new)
                self.assertFalse(new['passed']);self.assertTrue(base['passed']);self.assertTrue(scoped['passed'])
    def test_source_feedback_missing_base_does_not_borrow_old_cases_or_detail(self):
        cases,groups,hashes,version=self.fresh_cohort_fixture()
        one={**groups[SEMANTIC_REGENERATION_STAGE]['records'][0],'stage':SOURCE_FEEDBACK_STAGE}
        groups[SOURCE_FEEDBACK_STAGE]={'records':[one]}
        groups[SOURCE_FEEDBACK_STAGE+'-detail']={'records':[]}
        summary=summarize_semantic_regeneration(cases,groups,hashes,version)
        self.assertEqual(summary['records'],[one]);self.assertEqual(len(summary['missing_cases']),19)
        self.assertEqual(summary['detail_records'],[]);self.assertFalse(summary['execution_complete'])
        self.assertEqual(summary['case_only_followup_stage'],PRESSURE_PREDICATE_STAGE)
        self.assertEqual(summary['case_only_followup_stages'],[FINAL_OUTPUT_SCOPE_STAGE,SOURCE_FEEDBACK_STAGE,RISK_POLARITY_STAGE,PHYSICAL_UNIT_STAGE,EVIDENCE_SLOT_STAGE,PRICE_ZERO_CONSISTENCY_STAGE,PRESSURE_PREDICATE_STAGE])
    def test_source_feedback_detail_overrides_scope_failure_or_success_without_altering_briefs(self):
        cases,groups,hashes,version=self.fresh_cohort_fixture()
        rows=[{**row,'stage':BOUNDED_OUTPUT_STAGE} for row in groups[SEMANTIC_REGENERATION_STAGE]['records']]
        scoped={**groups[SEMANTIC_REGENERATION_STAGE+'-detail']['records'][0],'stage':FINAL_OUTPUT_SCOPE_STAGE+'-detail'}
        for status in ('failed','unreadable_or_in_progress'):
            with self.subTest(status=status):
                new={**scoped,'stage':SOURCE_FEEDBACK_STAGE+'-detail','passed':False,'status':status,
                     'has_detail_result':False,'recorded_run_succeeded':False,'recorded_error':'saved actual rejection'}
                groups.update({BOUNDED_OUTPUT_STAGE:{'records':rows},FINAL_OUTPUT_SCOPE_STAGE+'-detail':{'records':[scoped]},
                               SOURCE_FEEDBACK_STAGE:{'records':[]},SOURCE_FEEDBACK_STAGE+'-detail':{'records':[new]}})
                summary=summarize_semantic_regeneration(cases,groups,hashes,version)
                self.assertEqual(summary['passed_case_count'],20);self.assertEqual(summary['detail_records'],[new])
                self.assertIs(summarize_details(groups)['records'][0],new)
                self.assertFalse(summary['passed']);self.assertTrue(scoped['passed'])
    def test_risk_polarity_is_latest_case_only_stage_and_uses_unchanged_fixture(self):
        self.assertLess(STAGES.index(RISK_POLARITY_STAGE),STAGES.index(PHYSICAL_UNIT_STAGE))
        self.assertLess(DETAIL_STAGES.index(RISK_POLARITY_STAGE+'-detail'),DETAIL_STAGES.index(PHYSICAL_UNIT_STAGE+'-detail'))
        self.assertEqual(BOUNDED_COHORT_STAGES,(BOUNDED_OUTPUT_STAGE,FINAL_OUTPUT_SCOPE_STAGE,SOURCE_FEEDBACK_STAGE,RISK_POLARITY_STAGE,PHYSICAL_UNIT_STAGE,EVIDENCE_SLOT_STAGE,PRICE_ZERO_CONSISTENCY_STAGE,PRESSURE_PREDICATE_STAGE))
        self.assertIn(RISK_POLARITY_STAGE,CURRENT_COHORT_STAGES)
        self.assertFalse(use_frozen_case_fixture(RISK_POLARITY_STAGE,True))
        self.assertFalse(use_frozen_case_fixture(RISK_POLARITY_STAGE+'-detail',True))
    def test_risk_polarity_failure_or_partial_never_recovers_any_earlier_success(self):
        for status in ('failed','unreadable_or_in_progress'):
            with self.subTest(status=status):
                base={'case':'dismissed_notice_not_verified_recovery','stage':BOUNDED_OUTPUT_STAGE,'passed':True,'recorded_contract_passed':True}
                scoped={**base,'stage':FINAL_OUTPUT_SCOPE_STAGE}
                feedback={**base,'stage':SOURCE_FEEDBACK_STAGE}
                latest={**base,'stage':RISK_POLARITY_STAGE,'passed':False,'status':status}
                retained={'case':'supply_loss_40','stage':BOUNDED_OUTPUT_STAGE,'passed':True}
                groups={BOUNDED_OUTPUT_STAGE:{'records':[base,retained]},FINAL_OUTPUT_SCOPE_STAGE:{'records':[scoped]},
                        SOURCE_FEEDBACK_STAGE:{'records':[feedback]},RISK_POLARITY_STAGE:{'records':[latest]}}
                selected=select_briefs(groups)
                self.assertIs(selected[base['case']],latest);self.assertIs(selected[retained['case']],retained)
                self.assertFalse(latest['passed']);self.assertTrue(feedback['passed']);self.assertTrue(feedback['recorded_contract_passed'])
    def test_risk_polarity_stage_alone_keeps_missing_twenty_cases_and_detail_missing(self):
        cases,groups,hashes,version=self.fresh_cohort_fixture()
        row={**groups[SEMANTIC_REGENERATION_STAGE]['records'][0],'stage':RISK_POLARITY_STAGE}
        groups[RISK_POLARITY_STAGE]={'records':[row]};groups[RISK_POLARITY_STAGE+'-detail']={'records':[]}
        summary=summarize_semantic_regeneration(cases,groups,hashes,version)
        self.assertEqual(summary['records'],[row]);self.assertEqual(len(summary['missing_cases']),19)
        self.assertEqual(select_briefs(groups),{row['case']:row});self.assertEqual(summary['detail_records'],[])
        self.assertFalse(summary['execution_complete']);self.assertFalse(summary['passed'])
    def test_risk_polarity_detail_failure_preserves_strict_failure_and_does_not_change_brief_labels(self):
        cases,groups,hashes,version=self.fresh_cohort_fixture()
        rows=[{**row,'stage':BOUNDED_OUTPUT_STAGE} for row in groups[SEMANTIC_REGENERATION_STAGE]['records']]
        previous={**groups[SEMANTIC_REGENERATION_STAGE+'-detail']['records'][0],'stage':SOURCE_FEEDBACK_STAGE+'-detail'}
        latest={**previous,'stage':RISK_POLARITY_STAGE+'-detail','passed':False,'has_detail_result':False,
                'recorded_run_succeeded':False,'recorded_error':'actual two rejected model responses','attempts':2}
        groups.update({BOUNDED_OUTPUT_STAGE:{'records':rows},SOURCE_FEEDBACK_STAGE+'-detail':{'records':[previous]},
                       RISK_POLARITY_STAGE:{'records':[]},RISK_POLARITY_STAGE+'-detail':{'records':[latest]}})
        before=copy.deepcopy(rows);summary=summarize_semantic_regeneration(cases,groups,hashes,version)
        self.assertEqual(summary['records'],before);self.assertEqual(summary['passed_case_count'],20)
        self.assertEqual(summary['detail_records'],[latest]);self.assertFalse(summary['passed'])
        self.assertIs(summarize_details(groups)['records'][0],latest);self.assertTrue(previous['passed'])
    def test_physical_unit_is_latest_case_only_stage_and_uses_unchanged_fixture(self):
        self.assertLess(STAGES.index(PHYSICAL_UNIT_STAGE),STAGES.index(EVIDENCE_SLOT_STAGE))
        self.assertLess(DETAIL_STAGES.index(PHYSICAL_UNIT_STAGE+'-detail'),DETAIL_STAGES.index(EVIDENCE_SLOT_STAGE+'-detail'))
        self.assertEqual(BOUNDED_COHORT_STAGES[-5:],(RISK_POLARITY_STAGE,PHYSICAL_UNIT_STAGE,EVIDENCE_SLOT_STAGE,PRICE_ZERO_CONSISTENCY_STAGE,PRESSURE_PREDICATE_STAGE))
        self.assertIn(PHYSICAL_UNIT_STAGE,CURRENT_COHORT_STAGES)
        self.assertFalse(use_frozen_case_fixture(PHYSICAL_UNIT_STAGE,True))
        self.assertFalse(use_frozen_case_fixture(PHYSICAL_UNIT_STAGE+'-detail',True))
    def test_physical_unit_failure_or_partial_never_recovers_any_earlier_success(self):
        for status in ('failed','unreadable_or_in_progress'):
            with self.subTest(status=status):
                base={'case':'anchor_real_manual_missing_prevday','stage':BOUNDED_OUTPUT_STAGE,'passed':True,'recorded_contract_passed':True}
                groups={stage:{'records':[{**base,'stage':stage}]} for stage in BOUNDED_COHORT_STAGES[:BOUNDED_COHORT_STAGES.index(PHYSICAL_UNIT_STAGE)]}
                retained={'case':'supply_loss_40','stage':BOUNDED_OUTPUT_STAGE,'passed':True}
                groups[BOUNDED_OUTPUT_STAGE]['records'].append(retained)
                latest={**base,'stage':PHYSICAL_UNIT_STAGE,'passed':False,'status':status}
                groups[PHYSICAL_UNIT_STAGE]={'records':[latest]}
                selected=select_briefs(groups)
                self.assertIs(selected[base['case']],latest);self.assertIs(selected[retained['case']],retained)
                self.assertFalse(latest['passed'])
                self.assertTrue(groups[RISK_POLARITY_STAGE]['records'][0]['recorded_contract_passed'])
    def test_physical_unit_stage_alone_does_not_fill_from_prebounded_history(self):
        cases,groups,hashes,version=self.fresh_cohort_fixture()
        row={**groups[SEMANTIC_REGENERATION_STAGE]['records'][0],'stage':PHYSICAL_UNIT_STAGE}
        groups[PHYSICAL_UNIT_STAGE]={'records':[row]};groups[PHYSICAL_UNIT_STAGE+'-detail']={'records':[]}
        summary=summarize_semantic_regeneration(cases,groups,hashes,version)
        self.assertEqual(summary['records'],[row]);self.assertEqual(len(summary['missing_cases']),19)
        self.assertEqual(select_briefs(groups),{row['case']:row});self.assertEqual(summary['detail_records'],[])
        self.assertFalse(summary['execution_complete']);self.assertFalse(summary['passed'])
    def test_physical_unit_detail_failure_preserves_strict_failure_and_brief_labels(self):
        cases,groups,hashes,version=self.fresh_cohort_fixture()
        rows=[{**row,'stage':BOUNDED_OUTPUT_STAGE} for row in groups[SEMANTIC_REGENERATION_STAGE]['records']]
        previous={**groups[SEMANTIC_REGENERATION_STAGE+'-detail']['records'][0],'stage':RISK_POLARITY_STAGE+'-detail'}
        latest={**previous,'stage':PHYSICAL_UNIT_STAGE+'-detail','passed':False,'has_detail_result':False,
                'recorded_run_succeeded':False,'recorded_error':'actual two-response rejection','attempts':2}
        groups.update({BOUNDED_OUTPUT_STAGE:{'records':rows},RISK_POLARITY_STAGE+'-detail':{'records':[previous]},
                       PHYSICAL_UNIT_STAGE:{'records':[]},PHYSICAL_UNIT_STAGE+'-detail':{'records':[latest]}})
        before=copy.deepcopy(rows);summary=summarize_semantic_regeneration(cases,groups,hashes,version)
        self.assertEqual(summary['records'],before);self.assertEqual(summary['passed_case_count'],20)
        self.assertEqual(summary['detail_records'],[latest]);self.assertFalse(summary['passed'])
        self.assertIs(summarize_details(groups)['records'][0],latest);self.assertTrue(previous['passed'])
    def test_evidence_slot_is_latest_case_only_stage_and_uses_unchanged_fixture(self):
        self.assertLess(STAGES.index(EVIDENCE_SLOT_STAGE),STAGES.index(PRICE_ZERO_CONSISTENCY_STAGE))
        self.assertLess(DETAIL_STAGES.index(EVIDENCE_SLOT_STAGE+'-detail'),DETAIL_STAGES.index(PRICE_ZERO_CONSISTENCY_STAGE+'-detail'))
        self.assertEqual(BOUNDED_COHORT_STAGES[-4:],(PHYSICAL_UNIT_STAGE,EVIDENCE_SLOT_STAGE,PRICE_ZERO_CONSISTENCY_STAGE,PRESSURE_PREDICATE_STAGE))
        self.assertIn(EVIDENCE_SLOT_STAGE,CURRENT_COHORT_STAGES)
        self.assertFalse(use_frozen_case_fixture(EVIDENCE_SLOT_STAGE,True))
        self.assertFalse(use_frozen_case_fixture(EVIDENCE_SLOT_STAGE+'-detail',True))
    def test_evidence_slot_failed_or_partial_brief_and_detail_never_fall_back(self):
        for status in ('failed','unreadable_or_in_progress'):
            with self.subTest(status=status):
                cases,groups,hashes,version=self.fresh_cohort_fixture()
                briefs=[{**x,'stage':BOUNDED_OUTPUT_STAGE} for x in groups[SEMANTIC_REGENERATION_STAGE]['records']]
                previous={**briefs[0],'stage':PHYSICAL_UNIT_STAGE,'recorded_contract_passed':True}
                latest={**previous,'stage':EVIDENCE_SLOT_STAGE,'passed':False,'status':status}
                old_detail={**groups[SEMANTIC_REGENERATION_STAGE+'-detail']['records'][0],'stage':PHYSICAL_UNIT_STAGE+'-detail'}
                new_detail={**old_detail,'stage':EVIDENCE_SLOT_STAGE+'-detail','passed':False,'status':status,
                            'has_detail_result':False,'recorded_run_succeeded':False,'recorded_error':'saved actual failure'}
                groups.update({BOUNDED_OUTPUT_STAGE:{'records':briefs},PHYSICAL_UNIT_STAGE:{'records':[previous]},
                    PHYSICAL_UNIT_STAGE+'-detail':{'records':[old_detail]},EVIDENCE_SLOT_STAGE:{'records':[latest]},
                    EVIDENCE_SLOT_STAGE+'-detail':{'records':[new_detail]}})
                selected=select_briefs(groups);summary=summarize_semantic_regeneration(cases,groups,hashes,version)
                self.assertIs(selected[latest['case']],latest);self.assertFalse(selected[latest['case']]['passed'])
                self.assertTrue(previous['passed']);self.assertTrue(previous['recorded_contract_passed'])
                self.assertEqual(summary['detail_records'],[new_detail]);self.assertFalse(summary['passed'])
                self.assertIs(summarize_details(groups)['records'][0],new_detail)
    def test_price_zero_consistency_is_latest_case_only_stage_and_keeps_fixture(self):
        self.assertLess(STAGES.index(PRICE_ZERO_CONSISTENCY_STAGE),STAGES.index(PRESSURE_PREDICATE_STAGE))
        self.assertLess(DETAIL_STAGES.index(PRICE_ZERO_CONSISTENCY_STAGE+'-detail'),DETAIL_STAGES.index(PRESSURE_PREDICATE_STAGE+'-detail'))
        self.assertEqual(BOUNDED_COHORT_STAGES[-3:],(EVIDENCE_SLOT_STAGE,PRICE_ZERO_CONSISTENCY_STAGE,PRESSURE_PREDICATE_STAGE))
        self.assertIn(PRICE_ZERO_CONSISTENCY_STAGE,CURRENT_COHORT_STAGES)
        self.assertFalse(use_frozen_case_fixture(PRICE_ZERO_CONSISTENCY_STAGE,True))
        self.assertFalse(use_frozen_case_fixture(PRICE_ZERO_CONSISTENCY_STAGE+'-detail',True))
    def test_price_zero_consistency_failure_partial_and_missing_never_restore_prior_success(self):
        for status in ('failed','unreadable_or_in_progress'):
            with self.subTest(status=status):
                cases,groups,hashes,version=self.fresh_cohort_fixture()
                old={**groups[SEMANTIC_REGENERATION_STAGE]['records'][0],'case':'future_planned_notice','stage':EVIDENCE_SLOT_STAGE}
                latest={**old,'stage':PRICE_ZERO_CONSISTENCY_STAGE,'passed':False,'status':status,'recorded_contract_passed':False}
                prior_detail={**groups[SEMANTIC_REGENERATION_STAGE+'-detail']['records'][0],'stage':EVIDENCE_SLOT_STAGE+'-detail'}
                new_detail={**prior_detail,'stage':PRICE_ZERO_CONSISTENCY_STAGE+'-detail','passed':False,'status':status,
                    'has_detail_result':False,'recorded_run_succeeded':False,'recorded_error':'saved actual rejection'}
                groups.update({EVIDENCE_SLOT_STAGE:{'records':[old]},EVIDENCE_SLOT_STAGE+'-detail':{'records':[prior_detail]},
                    PRICE_ZERO_CONSISTENCY_STAGE:{'records':[latest]},PRICE_ZERO_CONSISTENCY_STAGE+'-detail':{'records':[new_detail]}})
                selected=select_briefs(groups);summary=summarize_semantic_regeneration(cases,groups,hashes,version)
                self.assertIs(selected['future_planned_notice'],latest);self.assertFalse(latest['passed']);self.assertTrue(old['passed'])
                self.assertEqual(len(summary['records']),1);self.assertEqual(len(summary['missing_cases']),19)
                self.assertEqual(summary['detail_records'],[new_detail]);self.assertFalse(summary['passed'])
                self.assertIs(summarize_details(groups)['records'][0],new_detail)
    def _pressure_markdown_selection_text(self):
        return next(value for value in markdown.__code__.co_consts
                    if isinstance(value, str) and value.startswith('最终当前样本仅由完整 bounded-output'))

    def test_pressure_predicate_is_final_case_only_stage_and_preserves_current_fixture(self):
        self.assertEqual(STAGES[-1], PRESSURE_PREDICATE_STAGE)
        self.assertEqual(DETAIL_STAGES[-1], PRESSURE_PREDICATE_STAGE + '-detail')
        self.assertEqual(BOUNDED_COHORT_STAGES[-2:], (PRICE_ZERO_CONSISTENCY_STAGE, PRESSURE_PREDICATE_STAGE))
        self.assertIn(PRESSURE_PREDICATE_STAGE, CURRENT_COHORT_STAGES)
        self.assertIn("pressure-predicate", self._pressure_markdown_selection_text())
        self.assertFalse(use_frozen_case_fixture(PRESSURE_PREDICATE_STAGE, True))
        self.assertFalse(use_frozen_case_fixture(PRESSURE_PREDICATE_STAGE + '-detail', True))

    def test_pressure_predicate_latest_failure_and_partial_keep_other_cases_without_old_fallback(self):
        base = {'case': 'real_eni', 'stage': BOUNDED_OUTPUT_STAGE, 'passed': True, 'recorded_contract_passed': True}
        retained = {'case': 'supply_loss_40', 'stage': BOUNDED_OUTPUT_STAGE, 'passed': True}
        old_only = {'case': 'old_only', 'stage': SEMANTIC_REGENERATION_STAGE, 'passed': True}
        for status in ('failed', 'unreadable_or_in_progress'):
            with self.subTest(status=status):
                previous = {**base, 'stage': PRICE_ZERO_CONSISTENCY_STAGE}
                latest = {**base, 'stage': PRESSURE_PREDICATE_STAGE, 'passed': False, 'status': status}
                groups = {BOUNDED_OUTPUT_STAGE: {'records': [base, retained]},
                          SEMANTIC_REGENERATION_STAGE: {'records': [old_only]},
                          PRICE_ZERO_CONSISTENCY_STAGE: {'records': [previous]},
                          PRESSURE_PREDICATE_STAGE: {'records': [latest]}}
                selected = select_briefs(groups)
                self.assertEqual(set(selected), {'real_eni', 'supply_loss_40'})
                self.assertIs(selected['real_eni'], latest)
                self.assertIs(selected['supply_loss_40'], retained)
                self.assertFalse(selected['real_eni']['passed'])
                self.assertTrue(previous['passed']); self.assertTrue(previous['recorded_contract_passed'])

    def test_pressure_predicate_stage_alone_does_not_complete_or_borrow_missing_cohort(self):
        cases, groups, hashes, version = self.fresh_cohort_fixture()
        row = {**groups[SEMANTIC_REGENERATION_STAGE]['records'][0], 'stage': PRESSURE_PREDICATE_STAGE}
        groups[PRESSURE_PREDICATE_STAGE] = {'records': [row]}
        groups[PRESSURE_PREDICATE_STAGE + '-detail'] = {'records': []}
        summary = summarize_semantic_regeneration(cases, groups, hashes, version)
        self.assertEqual(summary['records'], [row]); self.assertEqual(len(summary['missing_cases']), 19)
        self.assertEqual(summary['detail_records'], []); self.assertFalse(summary['execution_complete'])
        self.assertEqual(summary['case_only_followup_stage'], PRESSURE_PREDICATE_STAGE)
        self.assertFalse(summary['passed'])

    def test_pressure_predicate_detail_latest_failure_never_changes_briefs_or_uses_old_success(self):
        cases, groups, hashes, version = self.fresh_cohort_fixture()
        rows = [{**row, 'stage': BOUNDED_OUTPUT_STAGE} for row in groups[SEMANTIC_REGENERATION_STAGE]['records']]
        previous = {**groups[SEMANTIC_REGENERATION_STAGE + '-detail']['records'][0],
                    'stage': PRICE_ZERO_CONSISTENCY_STAGE + '-detail'}
        for status in ('failed', 'unreadable_or_in_progress'):
            with self.subTest(status=status):
                latest = {**previous, 'stage': PRESSURE_PREDICATE_STAGE + '-detail', 'passed': False,
                          'status': status, 'has_detail_result': False, 'recorded_run_succeeded': False,
                          'recorded_error': 'saved actual rejection'}
                groups.update({BOUNDED_OUTPUT_STAGE: {'records': rows},
                               PRICE_ZERO_CONSISTENCY_STAGE + '-detail': {'records': [previous]},
                               PRESSURE_PREDICATE_STAGE: {'records': []},
                               PRESSURE_PREDICATE_STAGE + '-detail': {'records': [latest]}})
                summary = summarize_semantic_regeneration(cases, groups, hashes, version)
                self.assertEqual(summary['records'], rows); self.assertEqual(summary['passed_case_count'], 20)
                self.assertEqual(summary['detail_records'], [latest]); self.assertFalse(summary['passed'])
                self.assertIs(summarize_details(groups)['records'][0], latest); self.assertTrue(previous['passed'])

    def complete_path_fixture(self,base,budget=False):
        from ttf_news.analysis import REPAIR_DRAFT_VERSION
        stage=SEMANTIC_REGENERATION_STAGE;directory=base/'reports/repair_2026-10-20'/stage;directory.mkdir(parents=True,exist_ok=True)
        data={'facts':['unchanged'],'compression':{'request_reasoning':'medium'}};system='captured synthetic system'
        content=canonical_json({'synthetic_model_output':True});response={'message':{'content':content},'created_at':'fixed','total_duration':123,'done_reason':'stop'}
        call={'think':'medium','num_ctx':8192,'num_predict':3072,'input_token_ceiling':4800,'input_token_estimate':40,
              'retry_policy':'disabled','raw_content':content,'system_prompt_sha256':sha(system),
              'response_schema_sha256':sha('{}'),'context_sha256':sha(json.dumps(data,ensure_ascii=False,separators=(',',':')))}
        original={'ttf_news/analysis.py':'a'*64,'ttf_news/semantics.py':'a'*64,'ttf_news/prompts.py':'a'*64}
        current={**original,'ttf_news/semantics.py':'b'*64}
        configuration={'model':'gpt-oss:20b','reasoning':'medium','semantic_high_retry':False,'semantic_high_scope':'operator_notice'}
        record={'case':'real_eni','prompt_version':'ttf-news-standalone-2.3','source_hashes':original,
                'model_call_configuration':configuration,'input_snapshot':{'current_news':{'title':'captured real-news placeholder'}},
                'attempts':[{'system':system,'input':data,'response':response}]}
        meta={'model_calls':[call]}
        if budget:
            call.update(validation_stage='fact_or_mechanism',validation_errors=['wrong actual fact'],validation_error='wrong actual fact')
            meta['repair_draft']={'version':REPAIR_DRAFT_VERSION,'reason':'complete_sources_and_draft_exceed_budget','previous_raw_sha256':sha(content)}
            record.update(error='完整来源事实与有界修复草稿无法共同装入输入预算；未删减来源或生成替代方向',audit=meta)
        else:record['result']={'news_impact':{'direction':'flat'},'risk_flags':[],'_meta':meta}
        record['input_sha256']=sha(json.dumps(record['input_snapshot'],ensure_ascii=False,sort_keys=True))
        path=directory/'real_eni.json';path.write_text(json.dumps(record,ensure_ascii=False))
        actual=record_execution_path(record)
        proof={'schema_version':PATH_EQUIVALENCE_SCHEMA,'prompt_version':record['prompt_version'],'new_model_calls':0,
               'current_source_sha256':copy.deepcopy(current),'records':[{'source_record_file':str(path.relative_to(base)),
               'source_record_sha256':sha(path.read_bytes()),'input_sha256':record['input_sha256'],
               'original_source_sha256':copy.deepcopy(original),'new_model_calls':0,'passed':True,
               'original_path':actual,'replayed_path':copy.deepcopy(actual)}]}
        (directory.parent/PATH_EQUIVALENCE_FILE).write_text(json.dumps(proof,ensure_ascii=False))
        return path,record,current,proof
    def test_whole_source_mismatch_needs_an_explicit_complete_current_path_proof(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);path,record,current,proof=self.complete_path_fixture(base)
            with patch.object(sys.modules[__name__],'ROOT',base):
                checked=verify_path_equivalence(path,record,current,path.parent.name,record['prompt_version'])
                self.assertTrue(checked['compatible']);self.assertFalse(checked['is_new_final_source_model_generation'])
                (path.parent.parent/PATH_EQUIVALENCE_FILE).unlink()
                self.assertFalse(verify_path_equivalence(path,record,current,path.parent.name,record['prompt_version'])['compatible'])
                self.assertTrue(verify_path_equivalence(path,record,record['source_hashes'],path.parent.name,record['prompt_version'])['compatible'])
    def test_complete_path_public_copy_requires_explicit_dual_byte_identity(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);path,record,current,proof=self.complete_path_fixture(base)
            old=sha(path.read_bytes());path.write_text(json.dumps(record,ensure_ascii=False,sort_keys=True,indent=2))
            derived=read_json(path);proof_path=path.parent.parent/PATH_EQUIVALENCE_FILE
            manifest_path=base/'reports/publication_manifest.json'
            artifacts={str(path.relative_to(base)):{'source_bytes_sha256':old,'public_bytes_sha256':sha(path.read_bytes()),'is_derived':True,'transformation':'JSON order'},
                       str(proof_path.relative_to(base)):{'source_bytes_sha256':sha(proof_path.read_bytes()),'public_bytes_sha256':sha(proof_path.read_bytes()),'is_derived':False,'transformation':'unchanged'}}
            manifest_path.write_text(json.dumps({'schema_version':'ttf-publication-manifest-v1','originals_modified':False,'manifest_self_excluded':True,'artifacts':artifacts}))
            with patch.object(sys.modules[__name__],'ROOT',base):
                self.assertFalse(verify_path_equivalence(path,derived,current,path.parent.name,record['prompt_version'])['compatible'])
                token=_PUBLIC_COPY_MAP.set(PublicationManifest(manifest_path))
                try:self.assertTrue(verify_path_equivalence(path,derived,current,path.parent.name,record['prompt_version'])['compatible'])
                finally:_PUBLIC_COPY_MAP.reset(token)
    def test_complete_path_proof_rejects_stale_source_record_or_any_changed_path(self):
        import tempfile
        from unittest.mock import patch
        for defect in ('current_source','record_sha','input_sha','original_source','system','schema','validation','draft','result','calls','new_model_call','old_version','old_stage','failed_replay'):
            with self.subTest(defect=defect),tempfile.TemporaryDirectory() as directory:
                base=Path(directory);path,record,current,proof=self.complete_path_fixture(base);row=proof['records'][0];version=record['prompt_version'];stage=path.parent.name
                if defect=='current_source':proof['current_source_sha256']['ttf_news/semantics.py']='c'*64
                elif defect=='record_sha':row['source_record_sha256']='c'*64
                elif defect=='input_sha':row['input_sha256']='c'*64
                elif defect=='original_source':row['original_source_sha256']={}
                elif defect in ('system','schema','validation','draft'):
                    key={'system':'system_sha256','schema':'schema_sha256','validation':'validation_error','draft':'request_draft_sha256'}[defect]
                    row['replayed_path']['attempts'][0][key]='changed'
                elif defect=='result':row['replayed_path']['result_without_meta_sha256']='c'*64
                elif defect=='calls':row['replayed_path']['attempt_count']=2
                elif defect=='new_model_call':row['new_model_calls']=1
                elif defect=='old_version':version='ttf-news-standalone-2.2'
                elif defect=='old_stage':stage='candidate-symmetric-validation'
                else:row['passed']=False
                (path.parent.parent/PATH_EQUIVALENCE_FILE).write_text(json.dumps(proof,ensure_ascii=False))
                with patch.object(sys.modules[__name__],'ROOT',base):self.assertFalse(verify_path_equivalence(path,record,current,stage,version,proof)['compatible'])
    def test_zero_call_replay_uses_saved_responses_and_detects_changed_generation_or_result(self):
        import tempfile
        from unittest.mock import patch
        from ttf_news import analysis as a
        for change in ('none','system','result','detail_stage'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                base=Path(directory);path,record,current,_=self.complete_path_fixture(base)
                if change=='detail_stage':
                    moved=path.parent.with_name(path.parent.name+'-detail');path.parent.rename(moved);path=moved/path.name
                requested_details=[]
                class FakeAnalyst:
                    def __init__(self):
                        for key,value in record['model_call_configuration'].items():setattr(self,key,value)
                    async def analyze(self,context,detail):
                        requested_details.append(detail)
                        attempt=record['attempts'][0]
                        await self._request(attempt['system']+(' changed' if change=='system' else ''),attempt['input'],detail)
                        result=copy.deepcopy(record['result'])
                        if change=='result':result['risk_flags']=['new materialized fact']
                        return result
                before=path.read_bytes()
                with patch.object(sys.modules[__name__],'ROOT',base),patch.object(sys.modules[__name__],'code_hashes',return_value=current),patch.object(a,'NewsAnalyst',FakeAnalyst),patch.object(a,'response_schema',return_value={}):
                    replay=asyncio.run(replay_record_path(path,current,record['prompt_version']))
                self.assertEqual(replay['passed'],change in ('none','detail_stage'));self.assertEqual(replay['new_model_calls'],0)
                self.assertEqual(requested_details,[change=='detail_stage'])
                if change=='system':
                    self.assertIn('system_sha256',replay['replayed_request_signatures'][0]['differs_from_recorded'])
                    self.assertTrue(replay['request_mismatches']);self.assertIn('replayed_terminal_error',replay)
                elif change=='result':self.assertIn('result_without_meta_sha256',replay['path_differences'])
                self.assertEqual(path.read_bytes(),before)
    def test_budget_terminal_requires_independent_rejection_and_complete_reproduced_audit(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);path,record,current,proof=self.complete_path_fixture(base,budget=True)
            with patch.object(sys.modules[__name__],'ROOT',base):
                checked=verify_budget_terminal(path,record,current,path.parent.name,record['prompt_version'],{'passed':False})
                self.assertTrue(checked['verified']);self.assertEqual(checked['actual_model_call_count'],1);self.assertFalse(checked['counts_as_valid_output'])
                self.assertFalse(verify_budget_terminal(path,record,current,path.parent.name,record['prompt_version'],{'passed':True})['verified'])
                (path.parent.parent/PATH_EQUIVALENCE_FILE).unlink()
                self.assertFalse(verify_budget_terminal(path,record,record['source_hashes'],path.parent.name,record['prompt_version'],{'passed':False})['verified'])
                normal=copy.deepcopy(record);normal['audit'].pop('repair_draft')
                self.assertFalse(verify_budget_terminal(path,normal,current,path.parent.name,record['prompt_version'],{'passed':False},proof)['verified'])
    def test_failed_detail_directory_is_revalidated_as_detail_without_result_flag(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);path,record,current,_=self.complete_path_fixture(base,budget=True)
            moved=path.parent.with_name(path.parent.name+'-detail');path.parent.rename(moved);path=moved/path.name
            with patch.object(sys.modules[__name__],'ROOT',base),patch.object(sys.modules[__name__],'revalidate_result',return_value={'passed':False}) as validate:
                checked=inspect_record(path,{'id':record['case'],'expected_news':'flat'},moved.name,current)
            self.assertEqual(checked['analysis_kind'],'detail');self.assertTrue(validate.call_args.args[2])
    def real_label_variance_fixture(self,base):
        identifier=REAL_ANCHOR_VARIANCE_CASE;directory=base/'reports/repair_2026-10-20'/PRICE_OBSERVATION_STAGE;directory.mkdir(parents=True)
        case={'id':identifier,'synthetic':False,'expected_news':'flat','expected_close':None,'candidate_context':{'current_news':{'title':'real-source contract'}}}
        record={'case':identifier,'synthetic':False,'prompt_version':'ttf-news-standalone-2.3','contract_passed':False,
                'input_snapshot':case['candidate_context'],'result':{'news_impact':{'direction':'down'},'close_forecast':{'direction':None,'evidence_status':'insufficient_evidence'}}}
        record['input_sha256']=sha(json.dumps(record['input_snapshot'],ensure_ascii=False,sort_keys=True))
        hashes={'ttf_news/analysis.py':'a'*64};record['source_hashes']=copy.deepcopy(hashes)
        path=directory/(identifier+'.json');path.write_text(json.dumps(record));record_sha=sha(path.read_bytes())
        def reference(p):return {'file':str(p.relative_to(base)),'sha256':sha(p.read_bytes())}
        fixture=directory.parent/'semantic_regeneration_cases_v2_2.json';fixture.write_text(json.dumps([case]))
        proof={'schema_version':'ttf-real-anchor-label-variance-v1','case':identifier,'prompt_version':record['prompt_version'],
               'source_record_file':str(path.relative_to(base)),'source_record_sha256':record_sha,'input_sha256':record['input_sha256'],
               'result_without_meta_sha256':sha(canonical_json(record['result'])),'source_result_canonical_sha256':sha(canonical_json(record['result'])),
               'source_sha256':copy.deepcopy(hashes),'original_expected_news':'flat','actual_news_direction':'down','strict_contract_passed':False,
               'counts_as_correct_prediction':False,'fixture':reference(fixture),'independent_reviews':[]}
        review_paths=[]
        for index,reviewer in enumerate(('/root/semantic_checks','/root/final_semantic_audit')):
            item={'reviewer_type':'codex_subagent','reviewer':reviewer,'strict_contract_passed':False,'material_known_logic_errors':[],
                  'review':{'case':identifier,'actual_record_path':str(path.relative_to(base)),'actual_record_sha256':record_sha,
                     'actual_input_sha256':record['input_sha256'],'synthetic':False,'prompt_version':record['prompt_version'],'source_hashes':hashes,
                     'result_canonical_sha256':sha(canonical_json(record['result'])),'known_logic_errors':[],
                     'verdict':'qualified_direction_contract_mismatch','checks':{key:'independently checked' for key in ('contract_parties','price_anchor','news_mechanism')}}}
            p=directory.parent/f'independent-{index}.json';p.write_text(json.dumps(item));review_paths.append(p);proof['independent_reviews'].append(reference(p))
        proof_path=directory.parent/REAL_ANCHOR_VARIANCE_FILE;proof_path.write_text(json.dumps(proof))
        return path,record,case,hashes,proof,proof_path,review_paths
    def test_real_label_disagreement_is_separate_delivery_never_strict_or_accuracy(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);path,record,case,hashes,_,_,_=self.real_label_variance_fixture(base)
            with patch.object(sys.modules[__name__],'ROOT',base),patch.object(sys.modules[__name__],'REAL_ANCHOR_VARIANCE_RECORD_SHA',sha(path.read_bytes())):
                checked=verify_real_anchor_label_variance(path,record,case,hashes,{'passed':True},{'compatible':True},{'compatible':True})
                row={'case':case['id'],'passed':False,'operational_label_variance':checked,'expectation_checks':contract_checks(record['result'],case)}
                self.assertTrue(checked['verified']);self.assertTrue(operational_label_delivery(row))
                self.assertFalse(contract_checks(record['result'],case)['passed']);self.assertFalse(record['contract_passed'])
                self.assertFalse(checked['counts_as_correct_prediction']);self.assertIn('标签未验证',candidate_direction_display(row))
                self.assertFalse(operational_label_delivery(row,{}))
    def test_real_label_disagreement_never_waives_unreviewed_synthetic_fact_anchor_or_other_case(self):
        import tempfile
        from unittest.mock import patch
        defects=('missing_review','changed_review','review_p2','review_wrong_source','same_reviewer','synthetic','other_case','wrong_record','wrong_anchor','wrong_role','p2_logic','wrong_close','wrong_source','wrong_path','wrong_policy','strict_relabelled')
        for defect in defects:
            with self.subTest(defect=defect),tempfile.TemporaryDirectory() as directory:
                base=Path(directory);path,record,case,hashes,proof,proof_path,review_paths=self.real_label_variance_fixture(base)
                logic={'passed':True};policy={'compatible':True};complete={'compatible':True};pinned=sha(path.read_bytes())
                if defect=='missing_review':proof['independent_reviews'].pop()
                elif defect=='changed_review':review_paths[0].write_text('{}')
                elif defect in ('review_p2','review_wrong_source','same_reviewer'):
                    review=read_json(review_paths[0])
                    if defect=='review_p2':review['material_known_logic_errors']=['known causal error']
                    elif defect=='review_wrong_source':review['review']['source_hashes']={}
                    else:review['reviewer']='/root/final_semantic_audit'
                    review_paths[0].write_text(json.dumps(review));proof['independent_reviews'][0]['sha256']=sha(review_paths[0].read_bytes())
                elif defect=='synthetic':case['synthetic']=True
                elif defect=='other_case':case['id']='supply_restore_40_with_flat_baseline'
                elif defect=='wrong_record':pinned='f'*64
                elif defect in ('wrong_anchor','wrong_role','p2_logic'):logic['passed']=False
                elif defect=='wrong_close':record['result']['close_forecast'].update(direction='down',evidence_status='forecast')
                elif defect=='wrong_source':hashes['ttf_news/analysis.py']='b'*64
                elif defect=='wrong_path':complete['compatible']=False
                elif defect=='wrong_policy':policy['compatible']=False
                else:proof['strict_contract_passed']=True
                proof_path.write_text(json.dumps(proof))
                with patch.object(sys.modules[__name__],'ROOT',base),patch.object(sys.modules[__name__],'REAL_ANCHOR_VARIANCE_RECORD_SHA',pinned):
                    self.assertFalse(verify_real_anchor_label_variance(path,record,case,hashes,logic,policy,complete)['verified'])
    def test_real_label_reviews_keep_generation_source_and_need_final_path_after_gate_change(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);path,record,case,hashes,proof,proof_path,_=self.real_label_variance_fixture(base)
            current={**hashes,'ttf_news/analysis.py':'b'*64};proof['source_sha256']=copy.deepcopy(current)
            proof_path.write_text(json.dumps(proof))
            with patch.object(sys.modules[__name__],'ROOT',base),patch.object(sys.modules[__name__],'REAL_ANCHOR_VARIANCE_RECORD_SHA',sha(path.read_bytes())):
                checked=verify_real_anchor_label_variance(path,record,case,current,{'passed':True},{'compatible':True},{'compatible':True})
                self.assertTrue(checked['verified']);self.assertNotEqual(record['source_hashes'],current)
                self.assertFalse(verify_real_anchor_label_variance(path,record,case,current,{'passed':True},{'compatible':True},{'compatible':False})['verified'])
    def fresh_cohort_fixture(self):
        version='ttf-news-standalone-2.2';hashes={name:'a'*64 for name in INFERENCE_SOURCE_FILES}
        cases=[{'id':identifier} for identifier in list(CASE_NAMES)+list(ANCHOR_CASE_NAMES)]
        rows=[{'case':case['id'],'stage':SEMANTIC_REGENERATION_STAGE,'passed':True,'attempts':1,
               'recorded_run_succeeded':True,'generation_prompt_version':version,
               'generation_source_sha256':dict(hashes),'first_actual_request_matches_current_input':{'passed':True}}
              for case in cases]
        detail={**rows[0],'stage':SEMANTIC_REGENERATION_STAGE+'-detail','analysis_kind':'detail','has_detail_result':True}
        groups={SEMANTIC_REGENERATION_STAGE:{'records':rows},SEMANTIC_REGENERATION_STAGE+'-detail':{'records':[detail]}}
        return cases,groups,hashes,version
    def test_fresh_cohort_counts_twenty_new_real_cases_and_current_detail(self):
        cases,groups,hashes,version=self.fresh_cohort_fixture()
        summary=summarize_semantic_regeneration(cases,groups,hashes,version)
        self.assertTrue(summary['passed']);self.assertTrue(summary['execution_complete'])
        self.assertEqual(summary['current_model_input_real_model_case_count'],20)
        self.assertEqual(summary['generation_matches_all_final_sources_count'],20)
        self.assertEqual(summary['current_version_detail_count'],1)
        self.assertIn(SEMANTIC_REGENERATION_WELL_CASE,{row['case'] for row in summary['records']})
    def test_fresh_cohort_failed_actual_detail_is_complete_but_never_strict_success(self):
        cases,groups,hashes,version=self.fresh_cohort_fixture()
        row=groups[SEMANTIC_REGENERATION_STAGE+'-detail']['records'][0]
        row.update(passed=False,has_detail_result=False,recorded_run_succeeded=False,recorded_error='two invalid actual responses')
        summary=summarize_semantic_regeneration(cases,groups,hashes,version)
        self.assertTrue(summary['execution_complete']);self.assertFalse(summary['passed'])
        self.assertEqual(summary['current_version_detail_count'],0);self.assertEqual(summary['current_version_actual_detail_count'],1)
    def test_current_cohort_counts_direct_generation_and_explicit_full_path_equivalence_separately(self):
        cases,groups,hashes,_=self.fresh_cohort_fixture();version='ttf-news-standalone-2.3'
        for group in groups.values():
            for row in group['records']:row['generation_prompt_version']=version
        row=groups[SEMANTIC_REGENERATION_STAGE]['records'][1]
        row['generation_source_sha256']['ttf_news/semantics.py']='b'*64
        row['current_complete_path_equivalence']={'compatible':True,'mode':'same_version_actual_execution_with_verified_final_path_equivalence'}
        summary=summarize_semantic_regeneration(cases,groups,hashes,version)
        self.assertTrue(summary['passed']);self.assertEqual(summary['generation_matches_all_final_sources_count'],19)
        self.assertEqual(summary['verified_same_version_path_equivalence_count'],1);self.assertEqual(summary['final_source_bound_execution_count'],20)
        row['current_complete_path_equivalence']['mode']='direct_final_source_execution'
        self.assertFalse(summarize_semantic_regeneration(cases,groups,hashes,version)['passed'])
    def test_fresh_cohort_never_borrows_success_from_earlier_phases(self):
        cases,groups,hashes,version=self.fresh_cohort_fixture()
        missing=groups[SEMANTIC_REGENERATION_STAGE]['records'].pop()
        groups['candidate-reviewwording-validation']={'records':[missing]}
        summary=summarize_semantic_regeneration(cases,groups,hashes,version)
        self.assertFalse(summary['passed']);self.assertEqual(summary['missing_cases'],[missing['case']])
        self.assertEqual(summary['selected_case_count'],19)
    def test_fresh_cohort_old_version_or_offline_output_is_not_new_execution(self):
        for change in ('old_version','offline_only','no_request','no_attempts','changed_source'):
            cases,groups,hashes,version=self.fresh_cohort_fixture();row=groups[SEMANTIC_REGENERATION_STAGE]['records'][0]
            if change=='old_version':row['generation_prompt_version']='ttf-news-standalone-2.1'
            elif change=='offline_only':row['validation_only']=True
            elif change=='no_request':row['first_actual_request_matches_current_input']['passed']=False
            elif change=='no_attempts':row['attempts']=0
            else:row['generation_source_sha256']['ttf_news/analysis.py']='b'*64
            with self.subTest(change=change):
                summary=summarize_semantic_regeneration(cases,groups,hashes,version)
                self.assertFalse(summary['passed']);self.assertFalse(summary['execution_complete'])
    def test_fresh_cohort_requires_new_current_source_detail(self):
        cases,groups,hashes,version=self.fresh_cohort_fixture();detail=groups.pop(SEMANTIC_REGENERATION_STAGE+'-detail')
        groups['candidate-binding-validation-detail']=detail
        self.assertFalse(summarize_semantic_regeneration(cases,groups,hashes,version)['passed'])
    def test_fresh_cohort_preserves_rejected_attempt_as_execution_not_prediction(self):
        cases,groups,hashes,version=self.fresh_cohort_fixture();row=groups[SEMANTIC_REGENERATION_STAGE]['records'][0]
        row.update(passed=False,recorded_run_succeeded=False,attempts=2,recorded_error='two invalid outputs')
        summary=summarize_semantic_regeneration(cases,groups,hashes,version)
        self.assertTrue(summary['execution_complete']);self.assertFalse(summary['passed'])
        self.assertEqual(summary['passed_case_count'],19);self.assertEqual(summary['failing_cases'],[row['case']])
    def test_fresh_fixture_cannot_drop_duplicate_or_replace_a_required_case(self):
        for change in ('drop','duplicate','replace'):
            cases,groups,hashes,version=self.fresh_cohort_fixture()
            if change=='drop':cases.pop()
            elif change=='duplicate':cases[-1]=cases[0]
            else:cases[-1]={'id':'unregistered_case'}
            with self.subTest(change=change),self.assertRaises(ValueError):summarize_semantic_regeneration(cases,groups,hashes,version)
    def test_real_well_control_needs_explicit_current_source_future_action(self):
        case={'required_explicit_future_well_action':True,'expected_news':None,'expected_close':None,'expected_status':'insufficient_evidence'}
        for text in ('[N0]该井将永久封堵并弃置。','[N0]The well will now be permanently plugged and abandoned.'):
            result={'news_impact':{'direction':'flat','summary':text},'close_forecast':{'direction':None,'evidence_status':'insufficient_evidence'}}
            with self.subTest(text=text):self.assertTrue(contract_checks(result,case)['passed'])
        for text in ('[N0]该井已永久封堵。','[N0]永久封井，未投入商业产能。','[N2]该井将永久封堵。','[N0]若该井将永久封堵，供应可能减少。','[N0]不会将该井永久封堵。'):
            result={'news_impact':{'direction':'flat','summary':text},'close_forecast':{'direction':None,'evidence_status':'insufficient_evidence'}}
            with self.subTest(text=text):self.assertFalse(contract_checks(result,case)['passed'])
    def test_well_source_fact_contract_does_not_impose_a_news_direction(self):
        case={'required_explicit_future_well_action':True,'expected_news':None,'expected_close':None}
        for direction in ('up','down','flat'):
            result={'news_impact':{'direction':direction,'summary':'[N0]该井下一步将封堵并弃置。'},'close_forecast':{'direction':None,'evidence_status':'insufficient_evidence'}}
            self.assertTrue(contract_checks(result,case)['passed'])
    def test_current_release_selection_includes_real_well_and_has_no_legacy_fallback(self):
        report={'current_prompt_version':'ttf-news-standalone-2.2','comparison':{'selected_records':[{'case':'old'}]}}
        self.assertEqual(release_selected_rows(report),[])
        report['semantic_regeneration_validation']={'records':[{'case':SEMANTIC_REGENERATION_WELL_CASE}]}
        self.assertEqual(release_selected_rows(report),[{'case':SEMANTIC_REGENERATION_WELL_CASE}])
    def revision_case(self,sign='down'):
        identifier='supply_restore_40_with_flat_baseline' if sign=='down' else 'supply_loss_40_with_flat_baseline'
        return {'id':identifier,'expected_news':sign,'expected_close_sign':sign}
    def revision_result(self,news='down',close='flat',status='forecast'):
        return {'news_impact':{'direction':news},'close_forecast':{'direction':close,'evidence_status':status}}
    def revision_document(self,fixture_bytes=b'[]'):
        return {'revision_id':'evaluation_contract_revision_v1','comparison_cases_sha256':sha(fixture_bytes),
                'cases':{identifier:{'original_expectations':{'expected_news':sign,'expected_close_sign':sign},
                                     'allowed_close_directions':[sign,'extreme_'+sign,'flat'],
                                     'required_close_evidence_status':'forecast'}
                         for identifier,sign in BASELINE_CONTROL_SIGNS.items()}}
    def test_revision_is_symmetric_without_rewriting_news_or_original_close_contract(self):
        for sign in ('up','down'):
            case=self.revision_case(sign);before=copy.deepcopy(case);result=self.revision_result(sign)
            self.assertFalse(contract_checks(result,case)['passed'])
            allowed=[sign,'extreme_'+sign,'flat']
            revised=contract_checks(result,case,close_allowed=allowed)
            self.assertTrue(revised['passed'])
            self.assertTrue(revised['news_direction_checked'])
            self.assertEqual(case,before)
            opposite='up' if sign=='down' else 'down'
            self.assertFalse(contract_checks(self.revision_result(opposite),case,close_allowed=allowed)['passed'])
            self.assertFalse(contract_checks(self.revision_result(sign,opposite),case,close_allowed=allowed)['passed'])
    def test_revision_requires_explicit_nonnull_conditional_forecast(self):
        case=self.revision_case();allowed=['down','extreme_down','flat']
        for close,status in [(None,'insufficient_evidence'),('flat','insufficient_evidence'),(None,'forecast')]:
            with self.subTest(close=close,status=status):
                self.assertFalse(contract_checks(self.revision_result(close=close,status=status),case,close_allowed=allowed)['passed'])
        result=self.revision_result();result['close_forecast'].pop('evidence_status')
        self.assertFalse(contract_checks(result,case,close_allowed=allowed)['passed'])
    def test_revision_does_not_override_an_exact_close_target(self):
        case={**self.revision_case(),'expected_close':'down'}
        self.assertFalse(contract_checks(self.revision_result(),case,close_allowed=['down','extreme_down','flat'])['passed'])
    def test_revision_loader_anchors_fixture_hash_and_retains_original_fields(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);cases=[self.revision_case('up'),self.revision_case('down')]
            fixture=base/'comparison_cases.json';fixture.write_text(json.dumps(cases))
            doc=self.revision_document(fixture.read_bytes());path=base/'evaluation_contract_revision_v1.json';path.write_text(json.dumps(doc))
            original=fixture.read_bytes();loaded=load_evaluation_contract_revision(base,fixture,cases)
            self.assertEqual(loaded['comparison_cases_sha256'],sha(original))
            self.assertEqual(fixture.read_bytes(),original)
            fixture.write_bytes(original+b' ')
            with self.assertRaisesRegex(ValueError,'SHA mismatch'):load_evaluation_contract_revision(base,fixture,cases)
    def test_revision_loader_rejects_asymmetric_or_expanded_scope(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);cases=[self.revision_case('up'),self.revision_case('down')]
            fixture=base/'comparison_cases.json';fixture.write_text(json.dumps(cases));path=base/'evaluation_contract_revision_v1.json'
            doc=self.revision_document(fixture.read_bytes());doc['cases']['other']={};path.write_text(json.dumps(doc))
            with self.assertRaisesRegex(ValueError,'only the two'):load_evaluation_contract_revision(base,fixture,cases)
            doc=self.revision_document(fixture.read_bytes());doc['cases'][cases[0]['id']]['allowed_close_directions'].append('down');path.write_text(json.dumps(doc))
            with self.assertRaisesRegex(ValueError,'symmetrically'):load_evaluation_contract_revision(base,fixture,cases)
    def test_revision_record_keeps_original_failure_and_cannot_waive_bad_semantics(self):
        import tempfile
        from unittest.mock import patch
        case=self.revision_case();revision=self.revision_document();row=self.policy_record(False,'medium')
        row.update(case=case['id'],input_snapshot={'current_news':{'source':'manual'}},contract_passed=False)
        row['result'].update(self.revision_result())
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'control.json';path.write_text(json.dumps(row));before=path.read_bytes()
            with patch.object(sys.modules[__name__],'revalidate_result',return_value={'passed':True}):
                good=inspect_record(path,case,'candidate-contract-validation',{},evaluation_revision=revision)
            self.assertTrue(good['passed'])
            self.assertFalse(good['recorded_contract_passed'])
            self.assertFalse(good['original_expectation_checks']['passed'])
            self.assertTrue(good['expectation_checks']['passed'])
            with patch.object(sys.modules[__name__],'revalidate_result',return_value={'passed':False,'reason':'fabricated post-announcement reaction'}):
                bad=inspect_record(path,case,'candidate-contract-validation',{},evaluation_revision=revision)
            self.assertTrue(bad['expectation_checks']['passed'])
            self.assertFalse(bad['passed'])
            self.assertEqual(path.read_bytes(),before)
    def anchor_summary_fixture(self):
        hashes={name:'a'*64 for name in INFERENCE_SOURCE_FILES}
        cases=[{'id':key} for key in ANCHOR_CASE_NAMES]
        selected={case['id']:{'case':case['id'],'passed':True,'attempts':1,
                  'generation_prompt_version':'ttf-news-standalone-2.1',
                  'first_actual_request_matches_current_input':{'passed':True},
                  'generation_source_sha256':copy.deepcopy(hashes)} for case in cases}
        return cases,selected,hashes
    def test_anchor_cohort_is_separate_from_original_fifteen(self):
        cases,selected,hashes=self.anchor_summary_fixture();selected['old_case']={'case':'old_case','passed':False}
        summary=summarize_anchor_validation(cases,selected,hashes,'ttf-news-standalone-2.1')
        self.assertTrue(summary['passed']);self.assertEqual(summary['required_case_count'],4)
        self.assertNotIn('old_case',{row['case'] for row in summary['records']})
    def test_anchor_offline_revalidation_cannot_replace_actual_current_model_run(self):
        for change in ('version','source','requests','validation_only','actual_input'):
            cases,selected,hashes=self.anchor_summary_fixture();row=next(iter(selected.values()))
            if change=='version':row['generation_prompt_version']='ttf-news-standalone-2.0'
            elif change=='source':row['generation_source_sha256']['ttf_news/context.py']='b'*64
            elif change=='requests':row['attempts']=0
            elif change=='validation_only':row['validation_only']=True
            else:row['first_actual_request_matches_current_input']['passed']=False
            with self.subTest(change=change):
                summary=summarize_anchor_validation(cases,selected,hashes,'ttf-news-standalone-2.1')
                self.assertFalse(summary['passed']);self.assertEqual(summary['current_model_input_real_model_case_count'],3)
    def test_anchor_missing_or_failed_case_never_substitutes_original_success(self):
        cases,selected,hashes=self.anchor_summary_fixture();identifier=cases[0]['id'];del selected[identifier]
        missing=summarize_anchor_validation(cases,selected,hashes,'ttf-news-standalone-2.1')
        self.assertEqual(missing['missing_cases'],[identifier]);self.assertFalse(missing['passed'])
        selected[identifier]={'case':identifier,'passed':False}
        failed=summarize_anchor_validation(cases,selected,hashes,'ttf-news-standalone-2.1')
        self.assertEqual(failed['failing_cases'],[identifier]);self.assertFalse(failed['passed'])
    def test_anchor_gate_only_change_is_disclosed_without_claiming_same_frozen_generation(self):
        cases,selected,hashes=self.anchor_summary_fixture();row=next(iter(selected.values()))
        row['generation_source_sha256']['ttf_news/semantics.py']='b'*64
        summary=summarize_anchor_validation(cases,selected,hashes,'ttf-news-standalone-2.1')
        self.assertTrue(summary['passed']);self.assertIn(row['case'],summary['gate_revalidated_after_source_change_cases'])
        self.assertNotIn(row['case'],summary['generation_matches_all_final_sources_cases'])
    def test_anchor_analysis_validator_change_requires_actual_input_equivalence(self):
        cases,selected,hashes=self.anchor_summary_fixture();row=next(iter(selected.values()))
        row['generation_source_sha256']['ttf_news/analysis.py']='b'*64
        summary=summarize_anchor_validation(cases,selected,hashes,'ttf-news-standalone-2.1')
        self.assertTrue(summary['passed']);self.assertIn(row['case'],summary['gate_revalidated_after_source_change_cases'])
        row['first_actual_request_matches_current_input']['passed']=False
        self.assertFalse(summarize_anchor_validation(cases,selected,hashes,'ttf-news-standalone-2.1')['passed'])
    def test_anchor_duplicate_ids_are_rejected(self):
        cases,selected,hashes=self.anchor_summary_fixture()
        with self.assertRaises(ValueError):summarize_anchor_validation(cases+[cases[0]],selected,hashes,'ttf-news-standalone-2.1')
    def policy_record(self,independent=True,think="high"):
        calls=[{"think":"medium","semantic_independent_regeneration":False}]
        if independent or think!='medium':calls.append({"think":think,"semantic_independent_regeneration":independent})
        return {"result":{"_meta":{"model_calls":calls}}}
    def test_failed_flat_preview_is_not_displayed_as_valid_prediction(self):
        row={'passed':False,'expectation_checks':{'news_direction':'flat','close_direction':'flat','close_evidence_status':'forecast'}}
        before=copy.deepcopy(row)
        self.assertEqual(candidate_direction_display(row),'无有效输出（raw: flat）')
        self.assertIn('无有效输出',candidate_direction_display(row,True))
        self.assertEqual(row,before)
    def test_direction_distribution_excludes_failed_and_rejected_raw(self):
        rows=[{'passed':True,'expectation_checks':{'news_direction':'up'}},
              {'passed':False,'recorded_run_succeeded':False,'expectation_checks':{'news_direction':'flat'}},
              {'passed':False,'recorded_run_succeeded':True,'expectation_checks':{'news_direction':'down'}}]
        summary=summarize_stage(rows)
        self.assertEqual(summary['news_direction_counts'],{'up':1})
        self.assertEqual(summary['diagnostic_raw_news_direction_counts'],{'flat':1,'down':1})
    def test_valid_flat_and_no_close_outlook_remain_distinct(self):
        row={'passed':True,'expectation_checks':{'news_direction':'flat','close_direction':None,'close_evidence_status':'insufficient_evidence'}}
        self.assertEqual(candidate_direction_display(row),'flat')
        self.assertEqual(candidate_direction_display(row,True),'None / insufficient_evidence')
    def release_fixture(self):
        accepted=[{'case':'case'+str(i),'passed':True} for i in range(13)]
        rejected=[{'case':identifier,'passed':False} for identifier in ('future_planned_notice','real_eni')]
        source={name:'a'*64 for name in ('ttf_news/analysis.py','ttf_news/semantics.py','ttf_news/prompts.py')}
        report={'status':'needs_attention','current_prompt_version':'ttf-news-standalone-2.0','code_sha256':source,
                'comparison':{'selected_records':accepted+rejected,'required_case_count':15,'missing_cases':[]},
                'preservation':{'passed':True},'unit_tests':{'passed':True},'frontend':{'test_pass_count':24,'test_fail_count':0},'detail_validation':{'passed':True}}
        audits={row['case']:{'has_result':row['passed'],'error':None if row['passed'] else 'two validation failures','model_call_count':1 if row['passed'] else 2,'file_sha256':'b'*64} for row in accepted+rejected}
        docs={'independent_review':{'all_valid_outputs_reviewed':True,'accepted_detail_reviewed':True,'known_accepted_logic_errors':[],'reviewed_accepted_cases':[row['case'] for row in accepted],'rejected_cases':[row['case'] for row in rejected],'selected_record_sha256':{row['case']:'b'*64 for row in accepted},'source_sha256':source},
              'live_smoke':{'status':'passed','failed_stages':[],'missing_required_stages':[]},
              'error_ui':{'status':'passed','evidence_kind':'existing_real_failed_analysis_api_ui_plus_v2_isolated_rejection','same_record_v2_end_to_end':False,'api_result_is_null':True,'ui_error_visible':True,'ui_direction_element_count':0,'default_flat_used':False,'v2_isolated_failure_cases':sorted(row['case'] for row in rejected)},
              'public_scan':{'status':'passed','finding_count':0,'database':{'unchanged':True}},'frontend_build':{'passed':True},
              'backend_deployment':{'new_prompt_version':'ttf-news-standalone-2.0','source_hashes':source}}
        return report,{name:{'verified':True,'file':name+'.json','sha256':'c'*64,'data':data} for name,data in docs.items()},audits
    def test_release_error_contract_keeps_strict_failure_and_rejections_out_of_accuracy(self):
        report,proofs,audits=self.release_fixture();before=copy.deepcopy(report)
        decision=evaluate_release_decision(report,proofs,audits)
        self.assertTrue(decision['release_ready']);self.assertFalse(decision['strict_all_outputs_success'])
        self.assertEqual(decision['strict_report_status'],'needs_attention');self.assertEqual(report,before)
        self.assertEqual(decision['availability']['effective_outputs'],13)
        self.assertEqual(decision['availability']['rejected_outputs'],2)
        self.assertFalse(decision['availability']['rejected_counted_as_correct_predictions'])
        self.assertIsNone(decision['availability']['market_accuracy'])
    def test_release_cannot_approve_unfinished_future_or_missing_evidence(self):
        report,proofs,audits=self.release_fixture();audits['future_planned_notice']['error']=None
        self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def release_anchor_fixture(self):
        report,proofs,audits=self.release_fixture();version='ttf-news-standalone-2.1'
        report['current_prompt_version']=version
        source=report['code_sha256'];source['ttf_news/context.py']='a'*64
        cases,selected,hashes=self.anchor_summary_fixture()
        report['anchor_validation']=summarize_anchor_validation(cases,selected,hashes,version)
        review=proofs['independent_review']['data']
        for identifier,row in selected.items():
            audits[identifier]={'has_result':True,'error':None,'model_call_count':1,'file_sha256':'b'*64}
            review['reviewed_accepted_cases'].append(identifier);review['selected_record_sha256'][identifier]='b'*64
        proofs['live_smoke']['data']['expected_prompt_version']=version
        live_stages={'manual_brief':'manual_real_brief','manual_detail':'manual_real_detail','current_live':'current_live_forecast_v2'}
        proofs['live_smoke']['data']['stages']=[{'name':stage,'status':'passed','checks':{'analysis_id':role,'summary_sha256':'d'*64}} for role,stage in live_stages.items()]
        review['live_v2_1_outputs']=[{'role':role,'analysis_id':role,'summary_sha256':'d'*64,'prompt_version':version,'verdict':'pass','known_logic_errors':[]} for role in live_stages]
        proofs['backend_deployment']['data']['new_prompt_version']=version
        return report,proofs,audits
    def release_fresh_fixture(self):
        report,proofs,_=self.release_anchor_fixture();cases,groups,hashes,version=self.fresh_cohort_fixture()
        fresh=summarize_semantic_regeneration(cases,groups,hashes,version)
        report['current_prompt_version']=version;report['semantic_regeneration_validation']=fresh
        report['comparison']={'selected_records':[row for row in fresh['records'] if row['case'] in CASE_NAMES and row['case']!=SEMANTIC_REGENERATION_WELL_CASE],
                              'required_case_count':15,'missing_cases':[]}
        report['anchor_validation']=summarize_anchor_validation([case for case in cases if case['id'] in ANCHOR_CASE_NAMES],
                                      {row['case']:row for row in fresh['records']},hashes,version)
        report['historical_cohorts']={'previous_fifteen':{'recorded_effective_output_count':13,'required_case_count':15,'is_current_version_new_model_performance':False}}
        report['detail_validation']={'passed':True,'records':fresh['detail_records']}
        audits={row['case']:{'has_result':True,'error':None,'model_call_count':1,'file_sha256':'b'*64} for row in fresh['records']}
        for row in fresh['detail_records']:audits['detail:'+row['case']]={'file_sha256':'b'*64,'has_result':True,'has_detail_result':True,'error':None,'model_call_count':1}
        review=proofs['independent_review']['data'];review.update(reviewed_accepted_cases=[row['case'] for row in fresh['records']],rejected_cases=[],selected_record_sha256={row['case']:'b'*64 for row in fresh['records']},accepted_detail_record_sha256={row['case']:'b'*64 for row in fresh['detail_records']})
        review['live_current_version_outputs']=review.pop('live_v2_1_outputs')
        for row in review['live_current_version_outputs']:row['prompt_version']=version
        proofs['live_smoke']['data']['expected_prompt_version']=version
        proofs['backend_deployment']['data']['new_prompt_version']=version
        proofs['error_ui']['data'].update(evidence_kind='real_v2_failed_analysis_api_ui',same_record_v2_end_to_end=True,prompt_version=version)
        return report,proofs,audits
    def test_release_v2_2_counts_only_twenty_new_cases_and_retains_older_cohorts(self):
        report,proofs,audits=self.release_fresh_fixture();decision=evaluate_release_decision(report,proofs,audits)
        self.assertTrue(decision['release_ready']);self.assertEqual(decision['availability']['required_scenarios'],20)
        self.assertEqual(decision['availability']['effective_outputs'],20)
        self.assertTrue(decision['availability']['is_current_version_new_model_performance'])
        self.assertEqual(decision['historical_cohorts']['previous_fifteen']['recorded_effective_output_count'],13)
    def test_release_v2_2_requires_actual_current_cohort_not_old_nineteen(self):
        for change in ('no_fresh','no_well','old_input','old_detail'):
            report,proofs,audits=self.release_fresh_fixture()
            fresh=report['semantic_regeneration_validation']
            if change=='no_fresh':report.pop('semantic_regeneration_validation')
            elif change=='no_well':fresh['records']=[row for row in fresh['records'] if row['case']!=SEMANTIC_REGENERATION_WELL_CASE]
            elif change=='old_input':fresh['current_model_input_real_model_case_count']=19
            else:fresh['current_version_detail_count']=0
            with self.subTest(change=change):self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def test_release_v2_2_cannot_use_v2_1_live_review_or_smoke(self):
        for change in ('old_review_key','old_review_version','old_smoke','old_ui'):
            report,proofs,audits=self.release_fresh_fixture();review=proofs['independent_review']['data']
            if change=='old_review_key':review['live_v2_1_outputs']=review.pop('live_current_version_outputs')
            elif change=='old_review_version':review['live_current_version_outputs'][0]['prompt_version']='ttf-news-standalone-2.1'
            elif change=='old_smoke':proofs['live_smoke']['data']['expected_prompt_version']='ttf-news-standalone-2.1'
            else:proofs['error_ui']['data']['prompt_version']='ttf-news-standalone-2.1'
            with self.subTest(change=change):self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def test_release_v2_2_explicit_terminal_model_error_remains_error_not_prediction(self):
        report,proofs,audits=self.release_fresh_fixture();fresh=report['semantic_regeneration_validation']
        identifier='real_eni';row=next(row for row in fresh['records'] if row['case']==identifier)
        row.update(passed=False,recorded_run_succeeded=False,attempts=2);fresh.update(passed=False,passed_case_count=19,failing_cases=[identifier])
        audits[identifier].update(has_result=False,error='two invalid outputs',model_call_count=2)
        review=proofs['independent_review']['data'];review['reviewed_accepted_cases'].remove(identifier);review['rejected_cases']=[identifier]
        decision=evaluate_release_decision(report,proofs,audits)
        self.assertTrue(decision['release_ready']);self.assertFalse(decision['strict_all_outputs_success'])
        self.assertEqual(decision['availability']['effective_outputs'],19)
        self.assertEqual(decision['availability']['rejected_cases'],[identifier]);self.assertFalse(decision['availability']['rejected_counted_as_correct_predictions'])
    def release_current_fixture(self):
        report,proofs,audits=self.release_fresh_fixture();version='ttf-news-standalone-2.3'
        report['current_prompt_version']=version
        fresh=report['semantic_regeneration_validation'];fresh['current_prompt_version']=version
        for row in fresh['records']+fresh['detail_records']:row['generation_prompt_version']=version
        for row in proofs['independent_review']['data']['live_current_version_outputs']:row['prompt_version']=version
        proofs['live_smoke']['data']['expected_prompt_version']=version
        proofs['backend_deployment']['data']['new_prompt_version']=version;proofs['error_ui']['data']['prompt_version']=version
        for audit in audits.values():audit['current_complete_path_equivalence']={'compatible':True,'mode':'direct_final_source_execution'}
        return report,proofs,audits
    def rejected_current_anchor_fixture(self):
        report,proofs,audits=self.release_current_fixture();identifier='anchor_real_manual_missing_prevday'
        fresh=report['semantic_regeneration_validation'];row=next(x for x in fresh['records'] if x['case']==identifier)
        row.update(passed=False,recorded_run_succeeded=False,attempts=2)
        for item in report['anchor_validation']['records']:
            if item['case']==identifier:item.update(passed=False,recorded_run_succeeded=False,attempts=2)
        report['anchor_validation'].update(passed=False,passed_case_count=3,failing_cases=[identifier])
        fresh.update(passed=False,passed_case_count=19,failing_cases=[identifier]);report['status']='needs_attention'
        audit=audits[identifier];audit.update(has_result=False,result_is_null=True,error='模型JSON两次校验失败，未生成方向：two saved invalid responses',
            model_call_count=2,complete_actual_attempt_audit=True,production_two_failure_terminal=True,final_raw_independently_rejected=True)
        review=proofs['independent_review']['data'];review['reviewed_accepted_cases'].remove(identifier);review['rejected_cases']=[identifier]
        review.update(rejected_anchors_independently_checked=True,rejected_anchor_cases=[identifier],rejected_anchor_record_sha256={identifier:'b'*64})
        return report,proofs,audits,row,audit
    def test_operational_anchor_rejection_keeps_original_strict_failure_and_unavailable_status(self):
        report,proofs,audits,row,_=self.rejected_current_anchor_fixture();before=copy.deepcopy(report)
        decision=evaluate_release_decision(report,proofs,audits)
        self.assertTrue(decision['release_ready']);self.assertFalse(decision['strict_all_outputs_success'])
        self.assertFalse(decision['checks']['anchor_current_version_real_model_validation'])
        self.assertFalse(decision['strict_only_checks']['anchor_current_version_real_model_validation'])
        self.assertTrue(decision['checks']['operational_anchor_terminal'])
        self.assertTrue(decision['checks']['rejected_anchors_independently_reviewed'])
        self.assertEqual(decision['anchor_availability']['effective_outputs'],3)
        self.assertEqual(decision['anchor_availability']['rejected_outputs'],1)
        self.assertFalse(decision['anchor_availability']['all_anchor_outputs_strict_success'])
        self.assertFalse(decision['anchor_availability']['rejected_counted_as_correct_predictions'])
        self.assertFalse(row['passed']);self.assertEqual(report,before)
    def test_operational_anchor_rejection_requires_actual_raw_audit_final_path_and_exact_independent_review(self):
        for defect in ('one_call','cancelled','incomplete','nonnull','has_result','raw_not_rejected','wrong_path','unreviewed','wrong_review_hash','wrong_review_cases','unverified_review','old_input','missing_anchor'):
            with self.subTest(defect=defect):
                report,proofs,audits,row,audit=self.rejected_current_anchor_fixture();review=proofs['independent_review']['data']
                if defect=='one_call':audit['model_call_count']=1
                elif defect=='cancelled':audit['production_two_failure_terminal']=False
                elif defect=='incomplete':audit['complete_actual_attempt_audit']=False
                elif defect=='nonnull':audit['result_is_null']=False
                elif defect=='has_result':audit['has_result']=True
                elif defect=='raw_not_rejected':audit['final_raw_independently_rejected']=False
                elif defect=='wrong_path':audit['current_complete_path_equivalence']['compatible']=False
                elif defect=='unreviewed':review['rejected_anchors_independently_checked']=False
                elif defect=='wrong_review_hash':review['rejected_anchor_record_sha256'][row['case']]='c'*64
                elif defect=='wrong_review_cases':review['rejected_anchor_cases']=[]
                elif defect=='unverified_review':proofs['independent_review']['verified']=False
                elif defect=='old_input':report['anchor_validation']['current_model_input_real_model_case_count']=3
                else:report['anchor_validation']['records']=[x for x in report['anchor_validation']['records'] if x['case']!=row['case']]
                self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def test_operational_anchor_terminal_has_no_subset_availability_quota(self):
        report,proofs,audits,row,_=self.rejected_current_anchor_fixture();review=proofs['independent_review']['data']
        for identifier in ANCHOR_CASE_NAMES:
            for entry in report['semantic_regeneration_validation']['records']+report['anchor_validation']['records']:
                if entry['case']==identifier:entry.update(passed=False,recorded_run_succeeded=False,attempts=2)
            audits[identifier].update(has_result=False,result_is_null=True,error='two actual invalid model outputs',model_call_count=2,
                complete_actual_attempt_audit=True,production_two_failure_terminal=True,final_raw_independently_rejected=True)
            if identifier in review['reviewed_accepted_cases']:review['reviewed_accepted_cases'].remove(identifier)
        review.update(rejected_cases=sorted(ANCHOR_CASE_NAMES),rejected_anchors_independently_checked=True,
            rejected_anchor_cases=sorted(ANCHOR_CASE_NAMES),rejected_anchor_record_sha256={key:'b'*64 for key in ANCHOR_CASE_NAMES})
        decision=evaluate_release_decision(report,proofs,audits)
        self.assertTrue(decision['release_ready']);self.assertTrue(decision['checks']['operational_anchor_terminal'])
        self.assertEqual(decision['anchor_availability']['effective_outputs'],0)
        self.assertEqual(decision['anchor_availability']['rejected_outputs'],4)
        self.assertFalse(decision['strict_all_outputs_success'])
    def test_operational_anchor_budget_terminal_needs_verified_actual_full_fact_budget_proof(self):
        report,proofs,audits,row,audit=self.rejected_current_anchor_fixture()
        audit.update(model_call_count=1,complete_actual_attempt_audit=False,production_two_failure_terminal=False,error='complete facts and repair budget exhausted')
        self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
        audit['verified_budget_terminal']={'verified':True}
        self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
        audit['verified_budget_terminal']={'verified':True,'terminal_kind':'repair_draft_input_budget_exhausted','actual_model_call_count':1,
            'final_raw_independently_rejected':True,'complete_path_proof':{'compatible':True,'mode':'same_version_actual_execution_with_verified_final_path_equivalence'}}
        self.assertTrue(evaluate_release_decision(report,proofs,audits)['release_ready'])
        audit['verified_budget_terminal']['final_raw_independently_rejected']=False
        self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def test_operational_anchor_error_does_not_waive_fresh_production_or_known_error_guard(self):
        for defect in ('production_detail','production_live','accepted_P2','known_error'):
            with self.subTest(defect=defect):
                report,proofs,audits,_,_=self.rejected_current_anchor_fixture()
                if defect=='production_detail':proofs['live_smoke']['data']['stages']=[x for x in proofs['live_smoke']['data']['stages'] if x['name']!='manual_real_detail']
                elif defect=='production_live':proofs['live_smoke']['data']['status']='failed'
                elif defect=='accepted_P2':proofs['independent_review']['data']['known_accepted_logic_errors']=['a material accepted error']
                errors=[{'id':'unclosed','status':'open','resolution_verified':False}] if defect=='known_error' else []
                self.assertFalse(evaluate_release_decision(report,proofs,audits,errors)['release_ready'])
    def test_current_release_one_call_requires_strictly_verified_full_fact_budget_terminal(self):
        report,proofs,audits=self.release_current_fixture();identifier='real_eni'
        fresh=report['semantic_regeneration_validation'];row=next(row for row in fresh['records'] if row['case']==identifier)
        row.update(passed=False,recorded_run_succeeded=False,attempts=1);fresh.update(passed=False,passed_case_count=19,failing_cases=[identifier])
        audit=audits[identifier];audit.update(has_result=False,error='input budget exhausted',model_call_count=1)
        review=proofs['independent_review']['data'];review['reviewed_accepted_cases'].remove(identifier);review['rejected_cases']=[identifier]
        self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
        audit['verified_budget_terminal']={'verified':True}
        self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
        audit['verified_budget_terminal']={'verified':True,'terminal_kind':'repair_draft_input_budget_exhausted','actual_model_call_count':1,
             'final_raw_independently_rejected':True,'counts_as_valid_output':False,
             'complete_path_proof':{'compatible':True,'mode':'same_version_actual_execution_with_verified_final_path_equivalence'}}
        decision=evaluate_release_decision(report,proofs,audits)
        self.assertTrue(decision['release_ready']);self.assertFalse(decision['strict_all_outputs_success'])
        self.assertEqual(decision['availability']['effective_outputs'],19);self.assertEqual(decision['verified_one_call_budget_failures'][0]['actual_model_call_count'],1)
        audit['verified_budget_terminal']['final_raw_independently_rejected']=False
        self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def test_current_release_rechecks_compatible_path_evidence_after_report_creation(self):
        report,proofs,audits=self.release_current_fixture()
        self.assertTrue(evaluate_release_decision(report,proofs,audits)['release_ready'])
        audits['real_eni']['current_complete_path_equivalence']['compatible']=False
        self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def test_current_release_real_label_disagreement_has_third_column_without_strict_pass(self):
        report,proofs,audits=self.release_current_fixture();identifier=REAL_ANCHOR_VARIANCE_CASE
        row=next(item for item in report['semantic_regeneration_validation']['records'] if item['case']==identifier)
        row['passed']=False;report['anchor_validation']['passed']=False;report['status']='needs_attention'
        evidence={'verified':True,'category':'deliverable_real_news_unvalidated_label_disagreement',
                  'source_record_sha256':REAL_ANCHOR_VARIANCE_RECORD_SHA,'strict_contract_passed':False,'counts_as_correct_prediction':False}
        row['operational_label_variance']=copy.deepcopy(evidence)
        self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
        audits[identifier]['operational_label_variance']=evidence
        decision=evaluate_release_decision(report,proofs,audits)
        self.assertTrue(decision['release_ready']);self.assertFalse(decision['strict_all_outputs_success']);self.assertFalse(row['passed'])
        availability=decision['availability']
        self.assertEqual(availability['strict_contract_passed_outputs'],19)
        self.assertEqual(availability['deliverable_unvalidated_real_label_disagreements'],1)
        self.assertEqual(availability['rejected_outputs'],0);self.assertEqual(availability['total_deliverable_analyses'],20)
        self.assertFalse(availability['label_disagreement_counted_as_correct_prediction'])
        self.assertEqual(decision['anchor_availability']['rejected_outputs'],0)
        evidence['counts_as_correct_prediction']=True
        self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def test_current_release_detail_uses_only_fresh_actual_without_historical_fallback(self):
        report,proofs,audits=self.release_current_fixture()
        details=report['semantic_regeneration_validation']['detail_records']
        historical={'case':'historical_other_case','file':'old-model-detail.json','stage':'candidate-binding-validation-detail','passed':False}
        report['detail_validation']={'passed':False,'records':[historical]+details}
        decision=evaluate_release_decision(report,proofs,audits)
        self.assertTrue(decision['release_ready'])
        self.assertEqual(decision['current_release_detail_cases'],[row['case'] for row in details])
        self.assertEqual(decision['historical_details_not_used_as_current_execution'],['old-model-detail.json'])
        details[0]['passed']=False
        self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
        details.clear();historical.update(passed=True,has_detail_result=True,analysis_kind='detail')
        self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def rejected_current_detail_fixture(self):
        report,proofs,audits=self.release_current_fixture();row=report['semantic_regeneration_validation']['detail_records'][0]
        row.update(passed=False,has_detail_result=False,recorded_run_succeeded=False,attempts=2)
        report['detail_validation']['passed']=False;report['status']='needs_attention'
        fresh=report['semantic_regeneration_validation'];fresh.update(passed=False,current_version_detail_count=0,current_version_actual_detail_count=1)
        audit=audits['detail:'+row['case']];audit.update(has_result=False,has_detail_result=False,error='two actual invalid responses',
            model_call_count=2,complete_actual_attempt_audit=True,result_is_null=True,detail_is_null=True,
            production_two_failure_terminal=True,final_raw_independently_rejected=True)
        review=proofs['independent_review']['data'];review.update(rejected_details_independently_checked=True,
            rejected_detail_cases=[row['case']],rejected_detail_record_sha256={row['case']:'b'*64})
        return report,proofs,audits,row,audit
    def test_operational_diagnostic_detail_rejection_never_becomes_valid_or_correct(self):
        report,proofs,audits,row,_=self.rejected_current_detail_fixture();decision=evaluate_release_decision(report,proofs,audits)
        self.assertTrue(decision['release_ready']);self.assertFalse(decision['checks']['detail_valid_output'])
        self.assertTrue(decision['checks']['operational_detail_terminal']);self.assertFalse(decision['strict_all_outputs_success'])
        self.assertEqual(decision['diagnostic_detail_availability']['valid_outputs'],0)
        self.assertEqual(decision['diagnostic_detail_availability']['unavailable_outputs'],1)
        self.assertFalse(decision['diagnostic_detail_availability']['rejected_counted_as_correct_predictions'])
        self.assertFalse(row['passed'])
        proofs['live_smoke']['data']['stages']=[entry for entry in proofs['live_smoke']['data']['stages'] if entry['name']!='manual_real_detail']
        self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def test_operational_detail_rejection_requires_real_two_failures_null_result_and_review(self):
        for defect in ('one_call','incomplete','non_null','unrejected','wrong_path','cancelled','unreviewed','changed_review'):
            with self.subTest(defect=defect):
                report,proofs,audits,row,audit=self.rejected_current_detail_fixture()
                if defect=='one_call':audit['model_call_count']=1
                elif defect=='incomplete':audit['complete_actual_attempt_audit']=False
                elif defect=='non_null':audit['result_is_null']=False
                elif defect=='unrejected':audit['final_raw_independently_rejected']=False
                elif defect=='wrong_path':audit['current_complete_path_equivalence']['compatible']=False
                elif defect=='cancelled':audit['production_two_failure_terminal']=False
                elif defect=='unreviewed':proofs['independent_review']['data']['rejected_details_independently_checked']=False
                else:proofs['independent_review']['data']['rejected_detail_record_sha256'][row['case']]='c'*64
                self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def test_release_v2_1_requires_separate_real_anchor_runs_and_reviews(self):
        report,proofs,audits=self.release_anchor_fixture()
        decision=evaluate_release_decision(report,proofs,audits)
        self.assertTrue(decision['release_ready'])
        self.assertEqual(decision['availability']['required_scenarios'],15)
        self.assertEqual(decision['availability']['effective_outputs'],13)
        self.assertEqual(decision['anchor_availability']['required_scenarios'],4)
        self.assertEqual(decision['anchor_availability']['effective_outputs'],4)
        del report['anchor_validation']
        self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def test_release_v2_1_rejects_old_smoke_or_missing_anchor_prose_review(self):
        for change in ('old_smoke','missing_review','unverified_source'):
            report,proofs,audits=self.release_anchor_fixture()
            if change=='old_smoke':proofs['live_smoke']['data']['expected_prompt_version']='ttf-news-standalone-2.0'
            elif change=='missing_review':proofs['independent_review']['data']['reviewed_accepted_cases'].pop()
            else:report['anchor_validation']['passed']=False
            with self.subTest(change=change):self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def test_release_reads_actual_final_deployment_prompt_version_field(self):
        report,proofs,audits=self.release_anchor_fixture();deployment=proofs['backend_deployment']['data']
        deployment['prompt_version']=deployment.pop('new_prompt_version')
        self.assertTrue(evaluate_release_decision(report,proofs,audits)['release_ready'])
        deployment['prompt_version']='earlier'
        self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def test_release_v2_1_requires_actual_live_prose_review_matching_smoke_ids_and_hashes(self):
        for change in ('missing','wrong_id','wrong_hash','actual_error','old_version','missing_stage'):
            report,proofs,audits=self.release_anchor_fixture();review=proofs['independent_review']['data']
            if change=='missing':review.pop('live_v2_1_outputs')
            elif change=='wrong_id':review['live_v2_1_outputs'][0]['analysis_id']='other'
            elif change=='wrong_hash':review['live_v2_1_outputs'][0]['summary_sha256']='e'*64
            elif change=='actual_error':review['live_v2_1_outputs'][0]['known_logic_errors']=['stale previous-day anchor accepted']
            elif change=='old_version':review['live_v2_1_outputs'][0]['prompt_version']='ttf-news-standalone-2.0'
            else:proofs['live_smoke']['data']['stages'].pop()
            with self.subTest(change=change):
                decision=evaluate_release_decision(report,proofs,audits)
                self.assertFalse(decision['release_ready']);self.assertIn('real_v2_1_outputs_independently_reviewed',decision['blockers'])
    def composite_error_ui_fixture(self,base,fresh=False):
        report,proofs,audits=self.release_fresh_fixture() if fresh else self.release_anchor_fixture()
        data={'evidence_kind':ERROR_UI_COMPOSITE_KIND,'same_record_v2_end_to_end':False,
              'status':'passed','analysis_id':'old-real-error','record_origin_prompt_version':'ttf-news-standalone-2.0',
              'backend_prompt_version':report['current_prompt_version'],'api_result_is_null':True,'ui_error_visible':True,
              'ui_direction_element_count':0,'default_flat_used':False,'screenshot_file':'real.png',
              'screenshot_sha256':'d'*64,'api_response_sha256':'e'*64}
        observation=base/'observed.json';observation.write_text(json.dumps(data))
        data['ui_observation']={'file':observation.name,'sha256':sha(observation.read_bytes())}
        rows=release_selected_rows(report);retained=[]
        for row in rows:
            file=base/(row['case']+'.json');file.write_text(json.dumps({'case':row['case'],'actual':True}))
            row['file']=file.name
            retained.append({'case':row['case'],'file_sha256':sha(file.read_bytes()),'final_output_regression_passed':True,
                             'recorded_run_succeeded':bool(row['passed']),'current_logic_revalidation':{'passed':bool(row['passed'])}})
        regression=base/'regression.json';regression.write_text(json.dumps({'passed':True,'current_prompt_version':report['current_prompt_version'],'source_sha256':report['code_sha256'],'records':retained}))
        data['current_gate_revalidation']={'file':regression.name,'sha256':sha(regression.read_bytes())}
        xml=ET.Element('testsuite',tests='6',failures='0',errors='0',skipped='0')
        for kind in ('short','detail','translation'):
            for defect in ('generation','commit'):ET.SubElement(xml,'testcase',name='test_real_generation_or_precommit_failure_remains_failed_and_retryable['+defect+'-'+kind+']')
        junit_file=base/'unit.xml';junit_file.write_bytes(ET.tostring(xml))
        report['unit_tests']['sha256']=sha(junit_file.read_bytes())
        data['state_binding_junit']={'file':junit_file.name,'sha256':sha(junit_file.read_bytes())}
        return report,proofs,audits,data
    def test_composite_error_ui_binds_old_record_to_current_rejections_and_failure_state_tests(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);report,proofs,audits,data=self.composite_error_ui_fixture(base)
            with patch.object(sys.modules[__name__],'ROOT',base):checked=verify_composite_error_ui(data,report)
            proofs['error_ui'].update(checked,data=data)
            decision=evaluate_release_decision(report,proofs,audits)
            self.assertTrue(decision['release_ready']);self.assertIn('not a new current-version model failure',decision['error_ui_evidence_boundary'])
    def test_current_composite_error_ui_must_bind_all_twenty_including_real_well(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);report,proofs,audits,data=self.composite_error_ui_fixture(base,fresh=True)
            with patch.object(sys.modules[__name__],'ROOT',base):checked=verify_composite_error_ui(data,report)
            self.assertTrue(checked['linked_current_rejections_verified'])
            path=base/'regression.json';regression=read_json(path)
            regression['records']=[row for row in regression['records'] if row['case']!=SEMANTIC_REGENERATION_WELL_CASE]
            path.write_text(json.dumps(regression));data['current_gate_revalidation']['sha256']=sha(path.read_bytes())
            with patch.object(sys.modules[__name__],'ROOT',base),self.assertRaises(ValueError):verify_composite_error_ui(data,report)
    def test_composite_error_ui_rejects_missing_or_changed_linked_proofs(self):
        import tempfile
        from unittest.mock import patch
        for change in ('raw_file','stale_source','accepted_rejected_raw','missing_failure_test','observation_mismatch'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                base=Path(directory);report,_,_,data=self.composite_error_ui_fixture(base)
                if change=='raw_file':(base/report['comparison']['selected_records'][0]['file']).write_text('{}')
                elif change in ('stale_source','accepted_rejected_raw'):
                    path=base/'regression.json';regression=read_json(path)
                    if change=='stale_source':regression['source_sha256']['ttf_news/context.py']='f'*64
                    else:next(row for row in regression['records'] if row['case']=='real_eni')['current_logic_revalidation']['passed']=True
                    path.write_text(json.dumps(regression));data['current_gate_revalidation']['sha256']=sha(path.read_bytes())
                elif change=='missing_failure_test':
                    path=base/'unit.xml';xml=ET.parse(path).getroot();xml.remove(list(xml)[0]);path.write_bytes(ET.tostring(xml))
                    data['state_binding_junit']['sha256']=sha(path.read_bytes());report['unit_tests']['sha256']=sha(path.read_bytes())
                else:data['analysis_id']='other-record'
                with patch.object(sys.modules[__name__],'ROOT',base),self.assertRaises(ValueError):verify_composite_error_ui(data,report)
        report,proofs,audits=self.release_fixture();proofs['live_smoke']['verified']=False
        self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def test_release_blocks_accepted_logic_errors_or_stale_manual_source_hashes(self):
        for change in ('logic','hash','source'):
            report,proofs,audits=self.release_fixture();review=proofs['independent_review']['data']
            if change=='logic':review['known_accepted_logic_errors']=['unsupported price claim']
            elif change=='hash':review['selected_record_sha256']['case0']='d'*64
            else:review['source_sha256']={}
            with self.subTest(change=change):self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def test_release_requires_explicit_no_direction_error_ui_and_evidence_boundary(self):
        for field,value in [('default_flat_used',True),('ui_direction_element_count',1),('same_record_v2_end_to_end',True)]:
            report,proofs,audits=self.release_fixture();proofs['error_ui']['data'][field]=value
            with self.subTest(field=field):self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def test_release_accepts_real_v2_same_record_api_ui_failure_evidence(self):
        report,proofs,audits=self.release_fixture();ui=proofs['error_ui']['data']
        ui.update(evidence_kind='real_v2_failed_analysis_api_ui',same_record_v2_end_to_end=True,prompt_version=report['current_prompt_version'])
        decision=evaluate_release_decision(report,proofs,audits)
        self.assertTrue(decision['release_ready']);self.assertIn('same record',decision['error_ui_evidence_boundary'])
        ui['prompt_version']='older-version';self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def test_release_reads_observed_error_ui_field_names_without_claiming_pages_deployed(self):
        report,proofs,audits=self.release_fixture();ui=proofs['error_ui']['data']
        for key in ('api_result_is_null','ui_error_visible','ui_direction_element_count','default_flat_used'):ui.pop(key)
        ui.update(evidence_kind='real_v2_failed_analysis_api_ui',same_record_v2_end_to_end=True,prompt_version=report['current_prompt_version'],api_result_null=True,error_visible=True,direction_elements=0,no_flat_fallback=True,public_pages_followup_pending=True)
        decision=evaluate_release_decision(report,proofs,audits)
        self.assertTrue(decision['release_ready']);self.assertTrue(decision['public_pages_followup_pending'])
    def test_release_does_not_hide_a_stored_invalid_result_as_rejected_error(self):
        report,proofs,audits=self.release_fixture();audits['real_eni']['has_result']=True
        self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def test_release_requires_independent_detail_review_too(self):
        report,proofs,audits=self.release_fixture();proofs['independent_review']['data']['accepted_detail_reviewed']=False
        self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def test_release_known_accepted_error_blocks_even_with_all_structural_proofs(self):
        report,proofs,audits=self.release_fixture()
        errors=[{'id':'stale_previous_day_anchor','status':'open'}]
        decision=evaluate_release_decision(report,proofs,audits,errors)
        self.assertFalse(decision['release_ready']);self.assertIn('no_known_accepted_errors',decision['blockers'])
        errors[0].update(status='resolved_with_real_validation',resolution_verified=False)
        self.assertFalse(evaluate_release_decision(report,proofs,audits,errors)['release_ready'])
        errors[0]['resolution_verified']=True
        self.assertTrue(evaluate_release_decision(report,proofs,audits,errors)['release_ready'])
    def test_known_accepted_error_closure_requires_immutable_current_real_validation(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);proof=base/'resolution.json';state=base/'errors.json'
            resolution={'known_error_id':'e1','passed':True,'real_model_validation':True,'prompt_version':'2.1'}
            proof.write_text(json.dumps(resolution));record={'id':'e1','status':'resolved_with_real_validation','resolution_evidence':{'file':'resolution.json','sha256':sha(proof.read_bytes())}}
            state.write_text(json.dumps({'records':[record]}));before=state.read_bytes()
            with patch.object(sys.modules[__name__],'ROOT',base):
                self.assertTrue(known_accepted_error_state(state,'2.1')[0]['resolution_verified'])
                self.assertFalse(known_accepted_error_state(state,'2.0')[0]['resolution_verified'])
                proof.write_text('{}');self.assertFalse(known_accepted_error_state(state,'2.1')[0]['resolution_verified'])
                self.assertEqual(state.read_bytes(),before)
    def test_release_public_findings_or_failed_real_smoke_block_publication(self):
        for name,field,value in [('public_scan','finding_count',1),('live_smoke','status','failed'),('frontend_build','passed',False)]:
            report,proofs,audits=self.release_fixture();proofs[name]['data'][field]=value
            with self.subTest(name=name):self.assertFalse(evaluate_release_decision(report,proofs,audits)['release_ready'])
    def test_release_evidence_loader_requires_exact_main_and_evidence_hashes(self):
        import tempfile
        from unittest.mock import patch
        report,proofs,_=self.release_fixture()
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);rp=base/'report.json';rp.write_text(json.dumps(report));references={}
            for name,proof in proofs.items():
                path=base/(name+('.log' if name=='frontend_build' else '.json'))
                path.write_text('✓ built in 1.20s' if name=='frontend_build' else json.dumps(proof['data']))
                references[name]={'file':path.name,'sha256':sha(path.read_bytes())}
            cp=base/'checks.json';checklist={'policy_id':RELEASE_POLICY_ID,'main_report_sha256':sha(rp.read_bytes()),'evidence':references};cp.write_text(json.dumps(checklist))
            with patch.object(sys.modules[__name__],'ROOT',base):
                before=rp.read_bytes();fresh=build_release_decision(rp,cp)
                self.assertTrue(fresh['evidence']['live_smoke']['verified']);self.assertEqual(rp.read_bytes(),before)
                (base/'live_smoke.json').write_text('{}')
                stale=build_release_decision(rp,cp);self.assertFalse(stale['evidence']['live_smoke']['verified'])
                checklist['main_report_sha256']='0'*64;cp.write_text(json.dumps(checklist))
                unanchored=build_release_decision(rp,cp)
                self.assertTrue(all(not row['verified'] for row in unanchored['evidence'].values()))
    def draft_retry_record(self,included=True,raw=None):
        from ttf_news.analysis import REPAIR_DRAFT_VERSION
        canonical=lambda value:json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False,default=str)
        raw=raw if raw is not None else canonical({'news_impact':{'summary':'当前容量风险，实际流量未知'}})
        facts={'current_news':{'id':'N0','facts':'原始供给事实'}}
        draft={'is_evidence':False,'origin':'previous_model_candidate','content':{'news_summary':'当前容量风险，实际流量未知'}}
        audit={'version':REPAIR_DRAFT_VERSION,'is_evidence':False,'included':True,'previous_raw_sha256':sha(raw),
               'payload_sha256':sha(canonical(draft)),'source_facts_sha256':sha(canonical(facts))}
        calls=[{'think':'medium','semantic_independent_regeneration':False,'raw_content':raw},
               {'think':'medium','semantic_independent_regeneration':False}]
        second={**copy.deepcopy(facts),'compression':{'input_budget':12200}}
        if included:calls[1]['repair_draft']=copy.deepcopy(audit);second['UNTRUSTED_DRAFT']=draft
        return {'result':{'_meta':{'model_calls':calls}},'attempts':[
            {'input':copy.deepcopy(facts),'response':{'message':{'content':raw}}},
            {'input':second,'response':{'message':{'content':'{}'}}}]}
    def test_medium_success_compatible_even_if_generation_source_changed(self):
        row=self.policy_record(False,"medium")
        row['source_hashes']={'ttf_news/analysis.py':'old-source'}
        result=generation_policy_compatibility(row,{'current_news':{'source':'offshore_energy'}})
        self.assertTrue(result['compatible'])
        self.assertFalse(result['semantic_high_independent_regeneration_used'])
    def test_non_operator_medium_repair_compatible(self):
        result=generation_policy_compatibility(self.draft_retry_record(),{'current_news':{'source':'manual'}})
        self.assertEqual(result['reason'],'no_independent_high_path_used')
        self.assertTrue(result['compatible'])
        self.assertEqual(result['repair_draft_policy']['reason'],'current_bounded_untrusted_draft_executed')
    def test_old_two_call_readable_candidate_is_not_current_repair_policy(self):
        row=self.draft_retry_record(False);before=copy.deepcopy(row)
        result=generation_policy_compatibility(row,{'current_news':{'source':'manual'}})
        self.assertFalse(result['compatible'])
        self.assertTrue(result['repair_draft_policy']['required'])
        self.assertEqual(result['reason'],'readable_candidate_retry_without_current_draft_policy')
        self.assertEqual(row,before)
    def test_old_malformed_candidate_has_no_usable_draft_path_to_verify(self):
        row=self.draft_retry_record(False,raw='{"news_impact":')
        result=generation_policy_compatibility(row,{'current_news':{'source':'manual'}})
        self.assertTrue(result['compatible'])
        self.assertFalse(result['repair_draft_policy']['required'])
    def test_old_two_call_without_previous_raw_cannot_claim_current_policy(self):
        row=self.draft_retry_record(False);row.pop('attempts');row['result']['_meta']['model_calls'][0].pop('raw_content')
        result=generation_policy_compatibility(row,{})
        self.assertFalse(result['compatible'])
        self.assertEqual(result['reason'],'retry_previous_raw_unavailable')
    def test_current_draft_policy_checks_version_and_previous_raw_identity(self):
        for key,value,reason in [('version','older-policy','readable_candidate_retry_without_current_draft_policy'),
                                 ('previous_raw_sha256','0'*64,'repair_draft_previous_raw_hash_mismatch')]:
            row=self.draft_retry_record();row['result']['_meta']['model_calls'][1]['repair_draft'][key]=value
            with self.subTest(key=key):
                result=generation_policy_compatibility(row,{})
                self.assertFalse(result['compatible']);self.assertEqual(result['reason'],reason)
    def test_current_draft_policy_checks_request_payload_and_source_facts(self):
        for kind in ('missing_payload','changed_payload','changed_facts'):
            row=self.draft_retry_record();request=row['attempts'][1]['input']
            if kind=='missing_payload':request.pop('UNTRUSTED_DRAFT')
            elif kind=='changed_payload':request['UNTRUSTED_DRAFT']['content']['news_summary']='不同草稿'
            else:request['current_news']['facts']='被修改事实'
            with self.subTest(kind=kind):self.assertFalse(generation_policy_compatibility(row,{})['compatible'])
    def test_production_call_audit_verifies_draft_without_duplicating_request(self):
        row=self.draft_retry_record();row.pop('attempts')
        self.assertTrue(generation_policy_compatibility(row,{})['compatible'])
    def test_top_level_draft_audit_is_supported_when_call_only_keeps_raw(self):
        row=self.draft_retry_record();meta=row['result']['_meta'];meta['repair_draft']=meta['model_calls'][1].pop('repair_draft')
        self.assertTrue(generation_policy_compatibility(row,{})['compatible'])
    def source_feedback_retry_record(self):
        from ttf_news import analysis as a
        context={'as_of':'2026-10-20T12:15:00+00:00','current_news':{'id':'synthetic-source-policy-control',
                 'title':'Confirmed European gas supply reduction','summary':'European gas supply fell by 40 mcm/d.',
                 'published_at':'2026-10-20T12:10:00+00:00','source':'synthetic'},
                 'window_news':[],'historical_events':[],'previous_sessions':[],'price_reference':{},'coverage':{}}
        raw=a._json({'news_impact':{'summary':'unverified zero price reaction'}})
        first={'think':'medium','semantic_independent_regeneration':False,'raw_content':raw,'validation_stage':'fact_or_mechanism',
               'validation_errors':['analysis.summary: '+('完整的局部来源错误诊断。'*30),'news_impact.bullish_evidence: supply sign wrong'],
               'full_validation_error':'full original semantic error','validation_error':'bounded display diagnostic','done_reason':'stop'}
        system=a.system_prompt(False)+a.SOURCE_FEEDBACK_INSTRUCTION
        request,reconstructed,draft=a._prepare_medium_repair(context,False,system,first['validation_error'],first,'medium',include_draft=False)
        second={'think':'medium','semantic_independent_regeneration':False,'semantic_source_only_feedback':True,
                'repair_feedback':copy.deepcopy(reconstructed['repair_feedback']),'repair_draft':draft,
                'system_prompt_sha256':sha(system),'context_sha256':sha(a._json(request)),
                'response_schema_sha256':sha(a._json(a.response_schema(False,request))),
                'num_ctx':reconstructed['context_window'],'num_predict':reconstructed['output_token_limit'],
                'input_token_ceiling':reconstructed['input_token_ceiling'],'input_token_estimate':reconstructed['input_token_estimate']}
        row={'result':{'_meta':{'model_calls':[first,second],'semantic_source_only_feedback':True}},
             'attempts':[{'response':{'message':{'content':raw}}},{'system':system,'input':request,'response':{'message':{'content':'{}'}}}]}
        return row,context
    def test_source_only_retry_rebuilds_complete_actual_feedback_without_candidate(self):
        row,context=self.source_feedback_retry_record();before=copy.deepcopy(row)
        result=generation_policy_compatibility(row,context)
        self.assertTrue(result['compatible']);self.assertTrue(result['repair_draft_policy']['complete_request_rebuilt'])
        self.assertFalse(result['repair_draft_policy']['candidate_draft_in_request'])
        feedback=row['attempts'][1]['input']['REPAIR_FEEDBACK']
        self.assertGreater(len(feedback['errors'][0]),320);self.assertEqual(len(feedback['errors']),2)
        self.assertEqual(row,before)
    def test_source_only_production_audit_requires_exact_reconstructable_hashes(self):
        row,context=self.source_feedback_retry_record();row.pop('attempts')
        self.assertTrue(generation_policy_compatibility(row,context)['compatible'])
        row['result']['_meta']['model_calls'][1]['context_sha256']='0'*64
        self.assertFalse(generation_policy_compatibility(row,context)['compatible'])
    def test_source_only_flag_cannot_replace_feedback_or_complete_input(self):
        for missing in ('repair_feedback','repair_draft','context','request'):
            row,context=self.source_feedback_retry_record()
            if missing in ('repair_feedback','repair_draft'):row['result']['_meta']['model_calls'][1].pop(missing)
            elif missing=='context':context=None
            else:row['attempts'][1].pop('input')
            with self.subTest(missing=missing):self.assertFalse(generation_policy_compatibility(row,context)['compatible'])
    def test_source_only_rejects_changed_or_truncated_diagnostics_previous_raw_and_facts(self):
        for change in ('feedback','previous_raw','source_facts','budget','system','schema','truncated_diagnostic'):
            row,context=self.source_feedback_retry_record();call=row['result']['_meta']['model_calls'][1]
            if change=='feedback':row['attempts'][1]['input']['REPAIR_FEEDBACK']['errors'].pop()
            elif change=='previous_raw':call['repair_feedback']['previous_raw_sha256']='0'*64
            elif change=='source_facts':context['current_news']['summary']='Different source fact'
            elif change=='budget':call['input_token_ceiling']-=1
            elif change=='system':row['attempts'][1]['system']+='edited'
            elif change=='schema':call['response_schema_sha256']='0'*64
            else:row['result']['_meta']['model_calls'][0]['validation_errors'][0]=row['result']['_meta']['model_calls'][0]['validation_errors'][0][:100]
            with self.subTest(change=change):self.assertFalse(generation_policy_compatibility(row,context)['compatible'])
    def test_source_only_rejects_candidate_draft_even_when_marked_untrusted(self):
        row,context=self.source_feedback_retry_record()
        row['attempts'][1]['input']['UNTRUSTED_DRAFT']={'is_evidence':False,'origin':'previous_model_candidate','content':{'news_summary':'old wrong conclusion'}}
        self.assertFalse(generation_policy_compatibility(row,context)['compatible'])
    def test_old_semantic_draft_retry_cannot_claim_new_source_only_policy(self):
        row=self.draft_retry_record();row['result']['_meta']['model_calls'][0]['validation_stage']='fact_or_mechanism'
        self.assertFalse(generation_policy_compatibility(row,{'current_news':{'source':'manual'}})['compatible'])
    def test_source_only_is_not_a_format_truncation_or_high_regeneration_waiver(self):
        for stage in ('parse','truncation',None):
            row,context=self.source_feedback_retry_record();row['result']['_meta']['model_calls'][0]['validation_stage']=stage
            with self.subTest(stage=stage):self.assertFalse(generation_policy_compatibility(row,context)['compatible'])
    def test_source_only_public_reasoning_alias_matches_private_policy(self):
        row,context=self.source_feedback_retry_record();public=copy.deepcopy(row)
        for call in public['result']['_meta']['model_calls']:call['request_reasoning']=call.pop('think')
        self.assertEqual(generation_policy_compatibility(row,context),generation_policy_compatibility(public,context))
    def test_first_pass_does_not_require_new_draft_or_changed_source_inference(self):
        row=self.policy_record(False,'medium');result=generation_policy_compatibility(row,{})
        self.assertTrue(result['compatible']);self.assertEqual(result['repair_draft_policy']['actual_call_count'],1)
        self.assertEqual(result['repair_draft_policy']['reason'],'first_pass_no_repair_draft_needed')
    def test_public_medium_request_configuration_matches_private_policy_result(self):
        root_record=self.draft_retry_record();public_record=copy.deepcopy(root_record)
        for call in public_record['result']['_meta']['model_calls']:
            call['request_reasoning']=call.pop('think')
        self.assertEqual(generation_policy_compatibility(root_record,{}),generation_policy_compatibility(public_record,{}))
    def test_public_independent_high_request_alias_remains_incompatible(self):
        root_record=self.policy_record();public_record=copy.deepcopy(root_record)
        for call in public_record['result']['_meta']['model_calls']:
            call['request_reasoning']=call.pop('think')
        private=generation_policy_compatibility(root_record,{'current_news':{'source':'gassco'}},prepared={})
        public=generation_policy_compatibility(public_record,{'current_news':{'source':'gassco'}},prepared={})
        self.assertEqual(private,public);self.assertFalse(public['compatible'])
        self.assertTrue(public['semantic_high_independent_regeneration_used'])
    def test_independent_high_missing_call_setting_keeps_recorded_path(self):
        for top_level_reasoning in (None,'high'):
            row=self.policy_record();meta=row['result']['_meta']
            meta['model_calls'][1].pop('think')
            if top_level_reasoning:meta['reasoning']=top_level_reasoning
            with self.subTest(top_level_reasoning=top_level_reasoning):
                result=generation_policy_compatibility(row,{'current_news':{'source':'gassco'}},prepared={})
                self.assertFalse(result['compatible']);self.assertTrue(result['semantic_high_independent_regeneration_used'])
    def test_public_nonindependent_high_flag_keeps_previous_configuration_rule(self):
        row=self.policy_record(False,'high');before=generation_policy_compatibility(row,{})
        for call in row['result']['_meta']['model_calls']:call['request_reasoning']=call.pop('think')
        self.assertEqual(before,generation_policy_compatibility(row,{}))
        self.assertTrue(before['compatible'])
    def test_non_operator_independent_high_not_current_production(self):
        row=self.policy_record()
        row['model_call_configuration']={'semantic_high_scope':'all'}
        before=copy.deepcopy(row)
        result=generation_policy_compatibility(row,{'current_news':{'source':'offshore_energy'}},prepared={})
        self.assertFalse(result['compatible'])
        self.assertFalse(result['current_news_is_operator_notice'])
        self.assertEqual(result['recorded_semantic_high_scope'],'all')
        self.assertEqual(row,before)
    def test_old_operator_independent_high_incompatible_when_default_disabled(self):
        result=generation_policy_compatibility(self.policy_record(),{'current_news':{'source':'GASSCO'}},prepared={})
        self.assertFalse(result['compatible'])
        self.assertFalse(result['current_semantic_high_retry_enabled'])
        self.assertEqual(result['reason'],'independent_high_disabled_by_current_default')
        self.assertTrue(result['current_news_is_operator_notice'])
    def test_operator_ledger_cannot_override_disabled_independent_high(self):
        result=generation_policy_compatibility(self.policy_record(),{'current_news':{'source':'manual'}},
                                               prepared={'evidence_ledger':{'notice_facts':[{'id':'N0'}]}})
        self.assertFalse(result['compatible'])
        self.assertTrue(result['current_news_is_operator_notice'])
    def test_old_enabled_configuration_without_actual_high_remains_compatible(self):
        row=self.policy_record(False,'medium')
        row['model_call_configuration']={'semantic_high_retry':True,'semantic_high_scope':'all'}
        row['result']['_meta']['semantic_high_retry_enabled']=True
        for source in ['gassco','offshore_energy']:
            with self.subTest(source=source):
                result=generation_policy_compatibility(row,{'current_news':{'source':source}},prepared={})
                self.assertFalse(result['current_semantic_high_retry_enabled'])
                self.assertFalse(result['semantic_high_independent_regeneration_used'])
                self.assertTrue(result['compatible'])
    def test_window_notice_alone_does_not_qualify_non_operator_current(self):
        result=generation_policy_compatibility(self.policy_record(),{'current_news':{'source':'offshore_energy'}},
                                               prepared={'evidence_ledger':{'notice_facts':[{'id':'N3'}]}})
        self.assertFalse(result['compatible'])
    def test_high_flag_without_recorded_independent_path_does_not_disqualify(self):
        result=generation_policy_compatibility(self.policy_record(False,"high"),{'current_news':{'source':'offshore_energy'}})
        self.assertTrue(result['compatible'])
    def test_independent_high_missing_input_cannot_claim_compatibility(self):
        result=generation_policy_compatibility(self.policy_record(),None)
        self.assertFalse(result['compatible'])
    def test_old_audit_high_metadata_is_checked_when_calls_missing(self):
        record={'audit':{'semantic_independent_regeneration':True,'reasoning':'high'}}
        result=generation_policy_compatibility(record,{'current_news':{'source':'offshore_energy'}},prepared={})
        self.assertFalse(result['compatible'])
    def test_policy_incompatible_success_cannot_pass_record_or_modify_evidence(self):
        import tempfile
        from unittest.mock import patch
        from ttf_news import analysis as a
        row=self.policy_record()
        row.update(case='weak',input_snapshot={'current_news':{'source':'offshore_energy'}},contract_passed=True)
        row['result'].update(news_impact={'direction':'flat'},close_forecast={'direction':None,'evidence_status':'insufficient_evidence'})
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'weak.json';path.write_text(json.dumps(row))
            before=path.read_bytes()
            with patch.object(sys.modules[__name__],'revalidate_result',return_value={'passed':True}), patch.object(a,'prepare_context',return_value=({},{})):
                result=inspect_record(path,{'id':'weak','expected_news':'flat'},'candidate-budget-validation',{})
            self.assertTrue(result['current_logic_revalidation']['passed'])
            self.assertTrue(result['recorded_contract_passed'])
            self.assertFalse(result['passed'])
            self.assertFalse(result['current_generation_policy_compatibility']['compatible'])
            self.assertEqual(path.read_bytes(),before)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT/"data/ttf_news.sqlite3", help="Only opened mode=ro; never included as an absolute report path")
    parser.add_argument("--evidence-dir", type=Path, default=ROOT/"reports/repair_2026-10-20")
    parser.add_argument("--frontend-log", type=Path)
    parser.add_argument('--unit-tests',type=Path,help='Explicit read-only JUnit proof; default prefers final-release-unit-tests.xml then final-unit-tests.xml without falling back if incomplete')
    parser.add_argument("--strict", action="store_true", help="Exit 1 if any required evidence/check is still pending or failing")
    parser.add_argument('--release-decision',action='store_true',help='Read existing report/evidence and write a separate operational release decision; never changes --strict')
    parser.add_argument('--release-checklist',type=Path,default=ROOT/'reports/repair_2026-10-20/release_readiness_checks.json')
    parser.add_argument('--public-copy',action='store_true',help='Explicitly verify derived public artifacts using a source/public byte SHA manifest; private source checks remain the default')
    parser.add_argument('--publication-manifest',type=Path,help='Publication map used only with --public-copy; default reports/publication_manifest.json')
    parser.add_argument('--replay-paths',action='store_true',help='Explicit zero-model-call full replay of the actual 2.3 cohort using saved responses only; writes a separate equivalence proof, never edits original records')
    parser.add_argument("--self-test", action="store_true", help="Run preservation/contract self-tests against in-memory data only")
    args = parser.parse_args()
    if (args.public_copy or args.publication_manifest is not None) and not args.release_decision:
        parser.error('--public-copy/--publication-manifest apply only to --release-decision')
    if args.publication_manifest is not None and not args.public_copy:
        parser.error('--publication-manifest requires explicit --public-copy')
    if args.replay_paths and (args.release_decision or args.self_test or args.strict):
        parser.error('--replay-paths is a separate explicit evidence-producing mode')
    if args.self_test:
        suite=unittest.defaultTestLoader.loadTestsFromTestCase(VerifierSelfTests)
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    if args.replay_paths:
        proof=asyncio.run(build_path_equivalence(args.evidence_dir));destination=args.evidence_dir/PATH_EQUIVALENCE_FILE
        content=json.dumps(proof,ensure_ascii=False,indent=2)+'\n'
        if destination.exists() and destination.read_text()!=content:
            archive=destination.with_name(destination.stem+'.'+sha(destination.read_bytes())[:16]+'.json')
            if not archive.exists():archive.write_bytes(destination.read_bytes())
        destination.write_text(content,encoding='utf-8')
        print(json.dumps({'file':relative(destination),'new_model_calls':0,'records':len(proof['records']),
                          'equivalent_records':proof['equivalent_count'],'requires_new_actual_or_review':[row['source_record_file'] for row in proof['records'] if not row['passed']]},ensure_ascii=False))
        return 0
    if args.release_decision:
        decision=build_release_decision(ROOT/'reports/logic_repair_validation_2026-10-20.json',args.release_checklist,
                                        public_copy=args.public_copy,publication_manifest=args.publication_manifest)
        filename='release_decision_public.json' if args.public_copy else 'release_decision.json'
        destination=ROOT/'reports/repair_2026-10-20'/filename;temporary=destination.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(decision,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temporary.replace(destination)
        print(json.dumps({'status':decision['status'],'strict_report_status':decision['strict_report_status'],'availability':decision['availability'],'blockers':decision['blockers']},ensure_ascii=False))
        return 0 if decision['release_ready'] else 1
    report=build_report(args.db,args.evidence_dir,args.frontend_log,args.unit_tests)
    for extension, content in (("json", json.dumps(report,ensure_ascii=False,indent=2)+"\n"), ("md", markdown(report))):
        destination=ROOT/f"reports/logic_repair_validation_2026-10-20.{extension}"
        temporary=destination.with_suffix(destination.suffix+".tmp")
        temporary.write_text(content,encoding="utf-8")
        temporary.replace(destination)
    print(json.dumps({"status":report["status"],"preservation":report["preservation"]["passed"],
                      "candidate_passed":report["comparison"]["passed_case_count"],"candidate_required":report["comparison"]["required_case_count"],
                      "missing_cases":report["comparison"]["missing_cases"],"failing_cases":report["comparison"]["failing_cases"],
                      "detail_passed":report["detail_validation"]["passed_detail_count"],"detail_failing_cases":report["detail_validation"]["failing_cases"],
                      "unit_status":report["unit_tests"]["status"],"reports":["reports/logic_repair_validation_2026-10-20.json","reports/logic_repair_validation_2026-10-20.md"]},ensure_ascii=False))
    return 1 if args.strict and report["status"]!="passed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
