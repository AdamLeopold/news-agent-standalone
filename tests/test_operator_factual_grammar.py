"""Portable finite schema regressions; no model, HTTP, DB or service calls."""
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from ttf_news import analysis as a, semantics as s
from ttf_news.context import market_baseline

HERE=Path(__file__).resolve().parent
ROOT=next(p for p in (HERE,*HERE.parents) if (p/'pyproject.toml').is_file())
FIX=json.loads((HERE/'fixtures/operator_factual_grammar_contexts_v2_3.json').read_text())
ROWS={row['case']:row for row in FIX['records']}
PROFILES=[(case,detail) for case in ROWS for detail in (False,True)]

def prepared(case='eleventh_manual',detail=False):
    row=ROWS[case]
    digest=hashlib.sha256(json.dumps(row['context'],ensure_ascii=False,separators=(',',':'),sort_keys=True).encode()).hexdigest()
    assert digest==row['input_sha256']
    ctx=copy.deepcopy(row['context']);data,meta=a.prepare_context(ctx,detail,reserve_tokens=32)
    return ctx,data,meta

def original_schema(detail,data):
    with patch.object(a,'_operator_factual_prose_schema',lambda schema,prepared:schema):
        return a.response_schema(detail,data)

@pytest.mark.parametrize('case,detail',PROFILES)
def test_four_profiles_retain_facts_decision_fields_and_history(case,detail):
    _,data,_=prepared(case,detail);before=copy.deepcopy(data)
    schema=a.response_schema(detail,data);base=original_schema(detail,data)
    assert data==before and schema['$comment']==a.OPERATOR_PROSE_CONTRACT
    assert schema['properties']['source_fact_checks']==base['properties']['source_fact_checks']
    assert schema['properties']['source_fact_checks']['const']==a._source_fact_checks(data)
    assert schema['required']==base['required']
    for definition,fields in [('NewsImpact',('direction','contextual_news_score','confidence','signal_strength')),
                              ('CloseForecast',('direction','confidence','evidence_status'))]:
        for field in fields:
            assert schema['$defs'][definition]['properties'][field]==base['$defs'][definition]['properties'][field]
    assert schema['$defs']['NewsImpact']['properties']['direction']['enum']==['extreme_up','up','flat','down','extreme_down']
    assert schema['$defs']['CloseForecast']['properties']['direction']==base['$defs']['CloseForecast']['properties']['direction']
    assert schema['$defs']['Analysis']['properties']['historical_comparisons']==base['$defs']['Analysis']['properties']['historical_comparisons']
    if detail:
        assert schema['$defs']['DetailAnalysis']['properties']['historical_comparisons']==base['$defs']['DetailAnalysis']['properties']['historical_comparisons']
    prefixes=a._operator_evidence_prefixes(data)
    for field in ('bullish_evidence','bearish_evidence','neutralizing_evidence'):
        for value in schema['$defs']['NewsImpact']['properties'][field]['items'].get('enum',[]):
            assert any(value.startswith(prefix) for prefix in prefixes)

@pytest.mark.parametrize('case,detail',PROFILES)
def test_nullable_risk_enum_is_on_string_branch_only(case,detail):
    _,data,_=prepared(case,detail);schema=a.response_schema(detail,data)
    node=schema['$defs']['CloseForecast']['properties']['extreme_move_risk']
    assert 'enum' not in node
    strings=[branch for branch in node['anyOf'] if branch.get('type')=='string']
    assert len(strings)==1 and [branch for branch in node['anyOf'] if branch.get('type')=='null']==[{'type':'null'}]
    branch=strings[0]
    assert branch['maxLength']==300 and len(branch['enum'])==2
    assert all(isinstance(value,str) and 0<len(value)<=300 for value in branch['enum'])
    def admitted(value):
        return value is None or (isinstance(value,str) and value in branch['enum'] and len(value)<=branch['maxLength'])
    assert admitted(None) and all(admitted(value) for value in branch['enum'])
    assert not admitted('') and not admitted('无来源的固定风险结论') and not admitted(0)

@pytest.mark.parametrize('mode',['ordinary','dismissed','missing_anchor'])
def test_non_scope_keeps_original_schema(mode):
    _,data,_=prepared()
    if mode=='ordinary':data['current_news']['source']='eia';data['evidence_ledger']['notice_facts']=[]
    elif mode=='dismissed':data['evidence_ledger']['notice_facts'][0]['state']='dismissed_notice'
    else:data['price_reference']['market_baseline']['available']=False
    assert a.response_schema(False,data)==original_schema(False,data)

@pytest.mark.parametrize('mode',['valid','not_compared','future_known'])
def test_revision_relief_requires_known_valid_comparison(mode):
    _,data,_=prepared();card=next(row for row in data['window_news'] if row['id']=='N2')
    if mode=='not_compared':card['revision_delta']['comparison_status']='unavailable'
    elif mode=='future_known':card['revision_delta']['previous_known_at']='2030-01-01T00:00:00Z'
    schema=a.response_schema(False,data)
    options=schema['$defs']['NewsImpact']['properties']['bearish_evidence']['items'].get('enum',[])
    assert bool(options)==(mode=='valid')
    if options:
        assert all('相对上一公告版本' in value and '不是实流恢复确认' in value for value in options)
    else:
        assert '相对上一公告版本约束期缩短' not in a._json(schema['$defs']['CloseForecast']['properties']['basis'])
        flat=next(branch for branch in schema['$defs']['CloseForecast']['oneOf'] if branch['properties']['direction']['const']=='flat')
        assert '若后续独立证据确认的供给改善' in flat['properties']['basis']['enum'][0]

@pytest.mark.parametrize('delta,word',[(.178,'持平'),(-.178,'持平'),(.31,'上涨'),(-.31,'下跌')])
def test_baseline_continuation_uses_existing_flat_band(delta,word):
    ctx,_,_=prepared();reference=ctx['price_reference']
    reference['intraday']['value']=reference['previous_price']+delta
    reference['market_baseline']=market_baseline(reference,ctx['session'],ctx['as_of'])
    data,_=a.prepare_context(ctx,False,reserve_tokens=32);schema=a.response_schema(False,data)
    assert '收盘相对前日可能'+word in schema['$defs']['CloseForecast']['properties']['basis']['enum'][0]

def test_existing_twenty_one_bad_raws_still_reject_independently_of_auxiliary_table():
    rows=json.loads((ROOT/'tests/fixtures/future_capacity_pressure_old_bad_v2_3.json').read_text())['records']
    assert len(rows)==21
    for row in rows:
        assert hashlib.sha256(row['raw_content'].encode()).hexdigest()==row['raw_sha256']
        ctx=copy.deepcopy(row['input_snapshot']);data,meta=a.prepare_context(ctx)
        result=a._parse_result(row['raw_content'],False,meta['source_index'],ctx);errors=[]
        checks=[lambda:a._validate_notice_source_facts(result,ctx,data),
                lambda:a._validate_prediction_semantics(result,ctx,False),
                lambda:a._validate_historical_reactions(result,data),
                lambda:a._validate_priced_in(result,data)]
        for check in checks:
            try:check()
            except (ValueError,TypeError) as error:errors.append(str(error))
        errors.extend(s.collect_analysis_semantic_errors(result,ctx,data,source_index=meta['source_index']))
        assert any('source_fact_checks' not in error for error in errors),row['case']


def test_oneof_binds_main_direction_and_rechecks_immutable_v2_actuals():
    for case,detail in PROFILES:
        ctx,data,meta=prepared(case,detail);schema=a.response_schema(detail,data)
        parent=schema['$defs']['CloseForecast'];branches=parent['oneOf']
        a._validate_notice_source_facts({'analysis':{'summary':'[N0]公告未来容量受限，实际流量未知。'}},ctx,data)
        assert len(branches)==6
        assert {branch['properties']['direction']['const'] for branch in branches}=={'extreme_up','up','flat','down','extreme_down',None}
        raw=next(row['raw_content'] for row in FIX['v2_actual_controls'] if row['case']==case)
        result=a._parse_result(raw,False,meta['source_index'],ctx)
        for branch in branches:
            props=branch['properties'];out=copy.deepcopy(result)
            for field in ('direction','evidence_status'):out['close_forecast'][field]=props[field]['const']
            for field in ('summary','basis'):out['close_forecast'][field]=props[field]['enum'][0]
            assert props['confidence']==parent['properties']['confidence']
            a._validate_notice_source_facts(out,ctx,data)
            a._validate_generated_close_contract(out,data)
            wrong=copy.deepcopy(out)
            other=next(row for row in branches if row['properties']['direction']['const']!=props['direction']['const'])
            wrong['close_forecast']['summary']=other['properties']['summary']['enum'][0]
            with pytest.raises(ValueError,match='主方向与主summary/basis'):
                a._validate_generated_close_contract(wrong,data)
    for row in FIX['v2_actual_controls']:
        assert hashlib.sha256(row['raw_content'].encode()).hexdigest()==row['raw_sha256']
        ctx,data,meta=prepared(row['case']);result=a._parse_result(row['raw_content'],False,meta['source_index'],ctx)
        if row['matches_main_direction']:a._validate_generated_close_contract(result,data)
        else:
            with pytest.raises(ValueError,match='主方向与主summary/basis'):
                a._validate_generated_close_contract(result,data)
