"""CPU contracts only: constructed version pairs use exact stored source text.

These fixtures are not evidence of actual historical publisher revisions or of
20b semantic novelty accuracy. No model, network or production database access.
"""
import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT
from ttf_news import structured_core as core
from ttf_news import context


def pair():
    actual = json.loads((ROOT / 'reports/structured_v2_4/api_engine_upgrade_01_artifacts/current_live_actual.json').read_text())
    current = copy.deepcopy(actual['input_snapshot']['current_news'])
    current.update(version=2, published_at='2026-10-21T13:00:00+00:00', updated_at='2026-10-22T15:30:00+00:00',
                   version_seen_at='2026-10-22T15:31:00+00:00', summary_created_at='2026-10-22T15:31:00+00:00',
                   fact_excerpt_created_at='2026-10-22T15:31:00+00:00')
    source = actual['result']['_meta']['structured']['catalog']['prepared']['source_documents']['N0']['body']
    paragraph = next(p for p in source.splitlines(keepends=True) if p.startswith('Through Cheniere'))
    old_contract = paragraph[paragraph.index('In May 2026,'):]
    current['summary'] = current['title']
    current['fact_excerpt'] = paragraph
    previous = copy.deepcopy(current)
    previous.update(version=1, updated_at='2026-10-22T07:00:00+00:00',
                    version_seen_at='2026-10-22T08:00:00+00:00', summary_created_at='2026-10-22T08:00:00+00:00',
                    fact_excerpt_created_at='2026-10-22T08:00:00+00:00', fact_excerpt=old_contract)
    return current, previous, paragraph


def prepared(current, previous):
    class ReadOnlyPrevious:
        def previous_news_version(self, *_):
            return {'version': previous['version'], 'known_at': previous['version_seen_at'], 'data': previous}
    now = '2026-10-22T16:00:00+00:00'
    current = context.enrich_news_revision(ReadOnlyPrevious(), current, now)
    session = {'window_start': '2026-10-22T15:15:00+00:00', 'window_end': '2026-10-23T15:15:00+00:00',
               'cutoff_utc': '2026-10-23T15:15:00+00:00', 'trade_date': '2026-10-23'}
    p = {'as_of': now, 'session': session, 'current_news': {'id': 'N0', 'title': current['title'],
         'time': current['published_at'], 'source': current['source'], 'version': current['version']},
         'window_news': [], 'source_index': [{'alias':'N0','id':current['id'], 'title':current['title'],
          'source':current['source'],'version':current['version']}], 'price_reference': {}}
    return core.attach_source_documents(p, {'current_news': current, 'window_news': []}), current


def fact(kind, eid, novelty, prior=(), realization='future'):
    return {'source_id':'N0','evidence_id':eid,'fact_type':kind,'region_scope':'global_lng',
            'realization':realization,'supply_chain_role':'corporate' if kind=='corporate_contract' else 'lng_export',
            'confidence':0.8,'novelty':novelty,'prior_evidence_ids':list(prior)}


def test_same_changed_paragraph_old_contract_and_added_future_supply_have_distinct_timing():
    current, previous, paragraph = pair()
    originals = copy.deepcopy((current, previous))
    p, enriched = prepared(current, previous)
    doc = p['source_documents']['N0']; bundle = doc['publisher_revision']
    eid = next(k for k,v in core._evidence_cards(p).items() if v['text'].strip()==paragraph.strip())
    pid = next(v['evidence_id'] for v in bundle['previous_evidence'] if v['source_field']=='body' and v['text'].startswith('In May'))
    classified = {'classifications':[fact('corporate_contract',eid,'unchanged',[pid], 'observed_as_reported'),
                                      fact('future_project_development',eid,'added')]}
    catalog = core.build_catalog(p, classified)
    assert catalog.facts[0]['historical_report'] is True
    assert catalog.facts[1]['historical_report'] is False
    assert catalog.mechanisms[0]['allowed_scopes']==['background']
    assert catalog.mechanisms[1]['allowed_scopes']==['expectation','longer_term']
    assert all(v['reported_at']==current['published_at'] for v in catalog.facts)
    assert bundle['publisher_updated_at']==current['updated_at']
    assert core._prior_revision_quotes(catalog.facts[0], 'en').endswith(previous['fact_excerpt'].strip())
    system, data, schema = core.classification_requests(p)[0]['request']
    assert 'novelty' in schema['properties']['classifications']['items']['oneOf'][0]['required']
    assert data['publisher_revisions']['N0']['previous_evidence']
    assert 'A changed paragraph can contain' in system
    assert (current, previous)==originals
    assert enriched['revision_delta']['source_version_metadata']['before']['updated_at']==previous['updated_at']


@pytest.mark.parametrize('mode', ['same_body_new_clock','unchanged_clock','missing_previous_clock','future_known',
                                  'authority_migration','metadata_time_only','api_missing_id','api_id_migration'])
def test_metadata_migration_or_invalid_clock_cannot_refresh_old_source(mode):
    current, previous, _ = pair()
    if mode in {'same_body_new_clock','metadata_time_only'}: previous['fact_excerpt']=current['fact_excerpt']
    if mode=='unchanged_clock': current['updated_at']=previous['updated_at']
    if mode=='missing_previous_clock': previous['updated_at']=None
    if mode=='future_known': current['version_seen_at']='2026-10-22T17:00:00+00:00'
    if mode=='authority_migration': previous['timestamp_source']='publisher API publishedAt'
    if mode=='metadata_time_only': current['first_seen_at']='2026-10-22T15:45:00+00:00'
    if mode in {'api_missing_id','api_id_migration'}:
        current['timestamp_source']=previous['timestamp_source']='publisher API publishedAt'
        current['publisher_article_id']=420500
        previous['publisher_article_id']=None if mode=='api_missing_id' else 310000
    if mode=='future_known':
        with pytest.raises(ValueError,match='absent or ambiguous'):
            prepared(current,previous)
        return
    p,_=prepared(current,previous)
    assert 'publisher_revision' not in p['source_documents']['N0']
    _,data,schema=core.classification_requests(p)[0]['request']
    assert 'publisher_revisions' not in data
    assert 'novelty' not in schema['properties']['classifications']['items']['oneOf'][0]['properties']


def test_explicit_historical_stays_background_even_in_new_source_revision():
    current,previous,paragraph=pair();p,_=prepared(current,previous)
    eid=next(k for k,v in core._evidence_cards(p).items() if v['text'].strip()==paragraph.strip())
    value=fact('measured_supply_increase',eid,'added',realization='historical_as_reported')
    catalog=core.build_catalog(p,{'classifications':[value]})
    assert catalog.facts[0]['historical_report'] is True
    assert catalog.mechanisms[0]['allowed_scopes']==['background']


def test_immutable_prior_binding_and_same_paragraph_novelty_rules():
    current,previous,_=pair();p,_=prepared(current,previous)
    eid=next(k for k,v in core._evidence_cards(p).items() if v['source_field']=='title')
    with pytest.raises(ValueError,match='unchanged complete'):
        core.build_catalog(p,{'classifications':[fact('corporate_contract',eid,'added',realization='observed_as_reported')]})
    body=next(k for k,v in core._evidence_cards(p).items() if v['source_field']=='body' and v['text'].startswith('Through'))
    with pytest.raises(ValueError,match='Prior evidence ID'):
        core.build_catalog(p,{'classifications':[fact('future_project_development',body,'revised',['other-source'])]})


def test_exact_real_same_url_different_api_article_identity_is_not_new_revision():
    fixture=json.loads((PRIVATE/'tests/fixtures/real_publisher_id_migration.json').read_text())
    before=fixture['before'];after=fixture['after'];current=after['data'];previous=before['data']
    assert current['url']==previous['url']
    assert current['publisher_article_id']!=previous['publisher_article_id']
    class Previous:
        def previous_news_version(self,*_):
            return {'version':previous['version'],'known_at':before['known_at'],'data':previous}
    enriched=context.enrich_news_revision(Previous(),current,after['known_at'])
    from ttf_news.calendar import session_at
    from ttf_news.evidence import source_body
    doc={'source_id':current['id'],'source_version':current['version'],'source':current['source'],
         'title':current['title'],'body':source_body(current)}
    p={'as_of':after['known_at'],'session':session_at(after['known_at'])}
    assert core._revision_evidence(enriched,doc,p) is None
    assert enriched['revision_delta']['source_version_metadata']['before']['publisher_article_id']==previous['publisher_article_id']
    assert current==after['data'] and previous==before['data']


def test_chinese_prior_evidence_english_zero_model_render_keeps_ids_not_cjk_prose():
    current,previous,paragraph=pair()
    previous['fact_excerpt']='原有合同继续有效，尚未开始新增供气。'
    p,_=prepared(current,previous)
    eid=next(k for k,v in core._evidence_cards(p).items() if v['text'].strip()==paragraph.strip())
    prior=next(v for v in p['source_documents']['N0']['publisher_revision']['previous_evidence'] if '原有合同' in v['text'])
    catalog=core.build_catalog(p,{'classifications':[fact('future_project_development',eid,'revised',[prior['evidence_id']])]})
    weighted=core.validate_weights({'uses':[{'mechanism_id':'M0','weight':0.5,'confidence':0.4,'scope':'expectation','importance_reason':'weak_indirect_link'}],
                                    'scenarios':{'bullish':[],'base':[],'bearish':['C0']},'history':[]},catalog)
    plan=core.validate_closing({'case':'insufficient_anchor','driver_ids':[],'confidence':0},weighted,catalog)
    result=core.render_plan(plan,catalog,locale='en',detail=True)
    body={k:v for k,v in result.items() if k not in {'_meta','references'}}
    assert not any('\u4e00'<=ch<='\u9fff' for ch in json.dumps(body,ensure_ascii=False))
    line=result['detail']['new_facts'][0]
    assert prior['evidence_id'] in line and prior['text_sha256'] in line
    assert 'source version 1' in line
    assert catalog.facts[0]['source_revision']['prior_evidence'][0]['text']==previous['fact_excerpt']
    assert previous['fact_excerpt'] in core._prior_revision_quotes(catalog.facts[0],'zh')
