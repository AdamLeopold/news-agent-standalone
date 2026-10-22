"""Semantic repair uses complete sources/diagnostics, never failed conclusions."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from ttf_news.analysis import (AnalysisError,ContextError,SOURCE_FEEDBACK_INSTRUCTION,
    SOURCE_FEEDBACK_VERSION,REPAIR_DRAFT_INSTRUCTION,source_feedback_payload,_json,
    _prepare_medium_repair)
from ttf_news.prompts import system_prompt
from test_analysis import FakeAnalyst,context,result


def wrong_candidate():
    value=result()
    value['analysis']['priced_in_comment']='市场已经完全计价（这是前一轮候选的独特错误句）'
    return value


def test_complete_distinct_local_errors_are_not_truncated_or_candidate_prose():
    long_error='analysis.summary：'+('完整本地验证器诊断，不可截断。'*40)
    second='news_impact.bullish_evidence[0]：另一个独立错误，须完整核对。'
    previous={'validation_errors':[long_error,second,long_error],
              'raw_content':json.dumps(wrong_candidate()),'validation_error':'short fallback'}
    before=copy.deepcopy(previous)
    payload=source_feedback_payload(previous,'fallback')
    assert payload=={'version':SOURCE_FEEDBACK_VERSION,'is_evidence':False,
                    'origin':'local_validator','errors':[long_error,second]}
    assert len(payload['errors'][0])>320 and previous==before
    assert '独特错误句' not in _json(payload)


async def test_semantic_second_request_recomputes_from_sources_and_full_feedback_only():
    bad=wrong_candidate();first=json.dumps(bad,ensure_ascii=False)
    analyst=FakeAnalyst([first,json.dumps(result(),ensure_ascii=False)])
    output=await analyst.analyze(context())
    assert len(analyst.calls)==2
    first_system,first_data,_=analyst.calls[0];second_system,second_data,_=analyst.calls[1]
    calls=output['_meta']['model_calls']
    assert calls[0]['validation_stage']=='fact_or_mechanism'
    assert first_system==system_prompt(False)
    assert second_system==system_prompt(False)+SOURCE_FEEDBACK_INSTRUCTION
    assert REPAIR_DRAFT_INSTRUCTION not in second_system
    assert 'UNTRUSTED_DRAFT' not in second_data
    payload=second_data['REPAIR_FEEDBACK']
    assert payload==source_feedback_payload(calls[0],calls[0]['validation_error'])
    assert payload['errors']==calls[0]['validation_errors']
    for field in ('current_news','window_news','previous_sessions','historical_events','price_reference','evidence_ledger'):
        assert second_data[field]==first_data[field]
    assert bad['analysis']['priced_in_comment'] not in second_system+_json(second_data)
    assert calls[1]['repair_draft']['included'] is False
    assert calls[1]['semantic_source_only_feedback'] is True
    audit=calls[1]['repair_feedback']
    assert audit['full_diagnostics'] is True and audit['error_count']==len(payload['errors'])
    assert audit['payload_sha256']==hashlib.sha256(_json(payload).encode()).hexdigest()
    assert audit['errors_sha256']==hashlib.sha256(_json(payload['errors']).encode()).hexdigest()
    assert audit['previous_raw_sha256']==hashlib.sha256(first.encode()).hexdigest()
    assert output['_meta']['semantic_source_only_feedback'] is True
    assert calls[0]['raw_content']==first


async def test_two_semantic_failures_still_stop_after_two_and_keep_both_originals():
    first=json.dumps(wrong_candidate(),ensure_ascii=False);analyst=FakeAnalyst([first,first])
    with pytest.raises(AnalysisError) as failure:await analyst.analyze(context())
    assert len(analyst.calls)==2
    audit=failure.value.audit;assert audit['semantic_source_only_feedback'] is True
    assert len(audit['model_calls'])==2
    assert all(row['raw_content']==first for row in audit['model_calls'])
    assert audit['model_calls'][1]['repair_feedback']['full_diagnostics'] is True


@pytest.mark.parametrize('failure_kind',['language','truncation'])
async def test_format_and_truncation_keep_original_bounded_draft_policy(failure_kind):
    candidate=result()
    if failure_kind=='language':
        candidate['analysis']['summary']='This entirely English explanation must be regenerated in Chinese.'
        first=json.dumps(candidate)
    else:
        first={'message':{'content':json.dumps(candidate)},'done_reason':'length',
               'prompt_eval_count':1200,'eval_count':3072}
    analyst=FakeAnalyst([first,json.dumps(result())]);out=await analyst.analyze(context())
    system,data,_=analyst.calls[1]
    assert REPAIR_DRAFT_INSTRUCTION in system and SOURCE_FEEDBACK_INSTRUCTION not in system
    assert 'REPAIR_FEEDBACK' not in data and data['UNTRUSTED_DRAFT']['is_evidence'] is False
    assert out['_meta']['model_calls'][0]['validation_stage'] in {'parse','truncation'}
    assert not out['_meta']['model_calls'][1].get('semantic_source_only_feedback')


def test_complete_semantic_diagnostics_use_one_bounded_24k_profile_when_needed():
    actual=json.loads((Path(__file__).parent/'fixtures/final_output_scope_actuals_v2_3.json').read_text())['real_eni']
    full_error='本地验证器完整诊断：'+('字段有独立来源事实需要逐项核对。'*180)
    previous={'raw_content':actual['attempts'][0]['response']['message']['content'],
              'validation_errors':[full_error],'done_reason':'stop'}
    system=system_prompt(False)+SOURCE_FEEDBACK_INSTRUCTION
    data,meta,draft=_prepare_medium_repair(actual['input_snapshot'],False,system,
        'short visible error',previous,'medium',include_draft=False)
    assert (meta['context_window'],meta['input_token_ceiling'],meta['output_token_limit'])==(24576,20480,3072)
    assert meta['repair_context_expansion']['reserved_tokens']==1024
    assert data['REPAIR_FEEDBACK']['errors']==[full_error]
    assert 'UNTRUSTED_DRAFT' not in data and draft['included'] is False
    assert {r['id'] for r in data['window_news']}=={r['id'] for r in actual['attempts'][0]['input']['window_news']}
    huge=copy.deepcopy(previous);huge['validation_errors']=[full_error*10];before=copy.deepcopy(huge)
    with pytest.raises(ContextError) as failure:
        _prepare_medium_repair(actual['input_snapshot'],False,system,'short',huge,'medium',include_draft=False)
    assert huge==before
    assert failure.value.repair_context_expansion['terminal']=='complete_request_exceeds_24k_profile'


def test_feedback_is_reserved_before_normal_profile_selection_not_skipped_to_24k():
    error='本地验证器逐字段完整诊断。'*200
    previous={'raw_content':json.dumps(wrong_candidate()),'validation_errors':[error],'done_reason':'stop'}
    data,meta,_=_prepare_medium_repair(context(),False,system_prompt(False)+SOURCE_FEEDBACK_INSTRUCTION,
        'short',previous,'medium',include_draft=False)
    assert meta['context_window']==16384 and meta['input_token_ceiling']==12800
    assert 'repair_context_expansion' not in meta
    assert data['REPAIR_FEEDBACK']['errors']==[error]
    assert meta['input_token_estimate']<=12800
