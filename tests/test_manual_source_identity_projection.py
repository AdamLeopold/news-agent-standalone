from pathlib import Path
import copy,json,hashlib
import pytest
from ttf_news import analysis as a

from test_production_price_unit_scopes import verified_historical_bytes
D=Path(__file__).resolve().parent
R=next(parent for parent in D.parents if (parent/'pyproject.toml').is_file())
FIX=json.loads((D/'fixtures/manual_source_identity_actual_v2_3.json').read_text())

def context(role='manual_brief'):
    row=next(item for item in FIX['records'] if item['role']==role)
    verified_historical_bytes(R/row['source_record']['file'],row['source_record']['sha256'],project_root=R)
    snapshot=copy.deepcopy(row['input_snapshot'])
    assert hashlib.sha256(json.dumps(snapshot,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()==row['input_model_json_sha256']
    return snapshot

def target(c):
    return next(row for row in c['window_news'] if row['id']=='90bfbf6adb1183517cbc00ca')

def assert_not_projected(c):
    before=copy.deepcopy(c)
    prepared,meta=a.prepare_context(c,False,reserve_tokens=32)
    assert 'current_news_source_identity_projection' not in meta
    assert 'same_as_window_news_id' not in prepared['current_news']
    assert prepared['current_news']['temporal_state']==a._news_card(before['current_news'],'N0',300,a._datetime(before['as_of']))['temporal_state']
    assert c==before
    return prepared,meta

def test_exact_complete_source_projects_known_clocks_and_deduplicates_only_its_alias():
    c=context();original=copy.deepcopy(c)
    p,m=a.prepare_context(c,False,reserve_tokens=32)
    assert c==original
    assert p['current_news']['temporal_state']=='scheduled_notice'
    assert p['current_news']['same_as_window_news_id']=='N1'
    assert p['current_news']['operational']['event_start_utc']=='2026-10-22T04:00:00+00:00'
    assert p['current_news']['operational']['event_end_utc']=='2026-10-23T04:00:00+00:00'
    assert p['window_news'][0]['same_as_current_news_id']=='N0'
    assert p['window_news'][0]['temporal_state']=='scheduled_notice'
    notices=p['evidence_ledger']['notice_facts']
    assert sum(row['id'] in {'N0','N1'} for row in notices)==1
    assert next(row for row in notices if row['id']=='N0')['state']=='scheduled_notice'
    assert len(m['source_index'])==len(c['window_news'])+1+len([r for r in m['source_index'] if r['alias'].startswith('E')])
    assert m['source_index'][0]['id']==original['current_news']['id']
    assert m['source_index'][1]['id']==target(original)['id']
    proof=m['current_news_source_identity_projection']
    assert proof['source_message_id']=='2026-252-A________________001'
    assert proof['generated_by_model'] is False and proof['original_input_unchanged'] is True
    assert proof==m['source_index'][0]['source_identity_projection']
    assert p['evidence_ledger']['focus_duplicate_window_alias']=='N1'

def test_detail_timing_and_source_contract_are_consistently_future():
    c=context();p,m=a.prepare_context(c,True,reserve_tokens=32)
    assert p['current_news']['temporal_state']=='scheduled_notice'
    assert p['current_news'].get('settlement_timing')
    assert p['window_news'][0]['same_as_current_news_id']=='N0'
    facts=a._source_fact_checks(p)
    assert facts and 'reported_fact' not in json.dumps(facts,ensure_ascii=False)
    assert m['current_news_source_identity_projection']['window_alias']=='N1'

@pytest.mark.parametrize('change',['message_body','message_field','body','title','no_title','current_clock','donor_clock','donor_status','status','not_known','no_known_time','two_intervals','source_index'])
def test_different_unknown_or_ambiguous_identity_cannot_borrow_clocks(change):
    c=context();row=target(c)
    if change=='message_body':c['current_news']['summary']=c['current_news']['summary'].replace('2026-252-A________________001','2026-252-B________________001')
    elif change=='message_field':c['current_news']['source_message_id']='another-message-id'
    elif change=='body':c['current_news']['summary']+=' Source correction: independent message.'
    elif change=='title':c['current_news']['title']+=' revised'
    elif change=='no_title':c['current_news']['title']=''
    elif change=='current_clock':c['current_news']['event_start']='2026-10-21T04:00:00Z'
    elif change=='donor_clock':row['event_start']='2026-10-21T04:00:00Z'
    elif change=='donor_status':row['event_status']='Dismissed'
    elif change=='status':c['current_news']['event_status']='Dismissed'
    elif change=='not_known':row['version_seen_at']='2026-10-22T00:00:00Z'
    elif change=='no_known_time':
        for field in a._AVAILABLE_TIMES:row.pop(field,None)
    elif change=='two_intervals':
        other=copy.deepcopy(row);other.update(id='distinct-interval',event_start='2026-10-24T04:00:00Z',event_end='2026-10-25T04:00:00Z');c['window_news'].append(other)
    elif change=='source_index':
        aliases=[f'N{i}' for i in range(1,len(c['window_news'])+1)]
        source_index=[a._source(c['current_news'],'N0'),*[a._source(r,alias) for r,alias in zip(c['window_news'],aliases)]]
        source_index[1]['id']='another-original-source'
        original=copy.deepcopy(c['current_news'])
        projected,alias,proof=a._project_exact_manual_source_identity(c['current_news'],c['window_news'],aliases,source_index,a._datetime(c['as_of']))
        assert projected==original and alias is None and proof is None
        return
    assert_not_projected(c)

def test_same_compressed_facts_do_not_prove_source_identity():
    c=context();before=a._news_card(c['current_news'],'N0',300,a._datetime(c['as_of']))['facts']
    c['current_news']['summary']=c['current_news']['summary'].replace('2026-252-A________________001','2026-252-C________________001')
    after=a._news_card(c['current_news'],'N0',300,a._datetime(c['as_of']))['facts']
    assert before==after
    assert_not_projected(c)


def test_genuine_matching_dismissed_title_and_source_status_still_project():
    c=context();c['current_news']['title']=c['current_news']['title'].replace('(Active)','(Dismissed)')
    row=target(c);row['title']=c['current_news']['title'];row['event_status']='Dismissed'
    original=copy.deepcopy(c);p,m=a.prepare_context(c,False,reserve_tokens=32)
    assert c==original
    assert p['current_news']['temporal_state']=='dismissed_notice'
    assert p['window_news'][0]['same_as_current_news_id']=='N0'
    assert p['current_news']['operational']['event_status']=='Dismissed'
    assert m['current_news_source_identity_projection']['copied_source_fields']['event_status']=='Dismissed'
