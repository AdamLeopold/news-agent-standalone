"""Fixture preparation preserves frozen replays and adds isolated quote controls."""
import copy
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

from ttf_news.calendar import parse_time,session_at
from ttf_news.context import market_baseline


SPEC=importlib.util.spec_from_file_location('ttf_comparison_fixture_generator',Path(__file__).resolve().parents[1]/'scripts/validate-news-logic.py')
generator=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)
ASOF='2026-10-20T12:10:58.642680+00:00'


def fixtures():
    context={'as_of':ASOF,'session':session_at(ASOF),'current_news':{'id':'fixture','summary':'欧洲实际供气减少40mcm/d。'},
             'window_news':[],'historical_events':[],'price_reference':{'available':False}}
    result=[]
    for name,direction in (('supply_loss_40','up'),('supply_restore_40','down')):
        result.append({'id':name,'synthetic':True,'original_context':copy.deepcopy(context),
                       'candidate_context':copy.deepcopy(context),'expected_news':direction})
    weak=copy.deepcopy(context)
    weak['price_reference']={'available':True,'contract':'ICE:NOV26','previous_price':88.04,'price_date':'2026-10-19',
                             'price_type':'reference','verified':False,'extreme_threshold':4.4,
                             'intraday':{'contract':'ICE:NOV26','value':89.8,'observed_at':'2026-10-20T12:00:00+00:00',
                                         'available_at':'2026-10-20T12:10:48+00:00','minimum_delay_minutes':15,'stale':False}}
    weak['price_reference']['market_baseline']=market_baseline(weak['price_reference'],weak['session'],ASOF)
    result.append({'id':'weak_news_observed_market_up','synthetic':True,'original_context':copy.deepcopy(weak),
                   'candidate_context':weak,'expected_news':'flat','expected_close':'up'})
    real=copy.deepcopy(context)
    real['current_news']['revision_delta']={'changed_fields':{'event_end':{'before':'old','after':'new'}}}
    result.append({'id':'real_replay','synthetic':False,'original_context':copy.deepcopy(context),
                   'candidate_context':real,'expected_news':None})
    return result


def object_bytes(raw):
    text=raw.decode();decoder=json.JSONDecoder();position=text.index('[')+1;rows=[]
    while True:
        while text[position].isspace() or text[position]==',':position+=1
        if text[position]==']':return rows
        _,end=decoder.raw_decode(text,position)
        rows.append(text[position:end].encode());position=end


def test_new_controls_add_only_flat_reference_and_direction_sign_contract():
    before=fixtures();saved=copy.deepcopy(before)
    result=generator.add_flat_baseline_controls(before)
    assert result[:len(before)]==before==saved
    for row,sign in zip(result[-2:],('up','down')):
        assert row['synthetic'] and row['requires_original_comparison'] is False
        assert row['expected_close_sign']==row['expected_news']==sign
        assert 'expected_close' not in row
        for key in ('original_context','candidate_context'):
            reference=row[key]['price_reference'];baseline=reference['market_baseline'];quote=reference['intraday']
            assert reference['synthetic_fixture'] and quote['synthetic_fixture']
            assert quote['contract']==reference['contract']==baseline['contract']
            assert reference['previous_price']==quote['value']==88.04
            assert baseline['available'] and baseline['observed_delta']==0 and baseline['observed_direction']=='flat'
            assert parse_time(quote['observed_at'])<parse_time(quote['available_at'])<parse_time(ASOF)
            assert (parse_time(quote['available_at'])-parse_time(quote['observed_at'])).total_seconds()>=15*60


def test_appending_preserves_each_original_object_byte_for_byte():
    before=json.dumps(fixtures(),ensure_ascii=False,indent=4).encode()+b'\n'
    added=generator.add_flat_baseline_controls(json.loads(before))[-2:]
    after=generator.append_json_rows(before,added)
    assert object_bytes(after)[:4]==object_bytes(before)
    assert json.loads(after)[:4]==json.loads(before)


def test_prepare_existing_fixture_is_idempotent_and_does_not_reset_real_context(tmp_path):
    target=tmp_path/'comparison_cases.json'
    before=json.dumps(fixtures(),ensure_ascii=False,indent=4).encode()+b'\n'
    target.write_bytes(before)
    with patch.object(generator,'OUT',tmp_path):
        generator.prepare()
        first=target.read_bytes()
        generator.prepare()
    assert target.read_bytes()==first
    assert (tmp_path/'comparison_cases_v2.json').read_bytes()==before
    assert object_bytes(first)[:4]==object_bytes(before)
    assert json.loads(first)[3]['candidate_context']==fixtures()[3]['candidate_context']


def test_revision_enrichment_is_reproducible_from_known_prior_versions_without_writes():
    before={'id':'notice','source':'gassco','version':1,'summary':'Technical capacity: 79.8 mcm/d; available: 59.8; unavailable: 20.',
            'event_start':'2026-10-20T08:00:00Z','event_end':'2026-10-20T14:00:00Z','event_status':'Active'}
    after={**before,'version':2,'summary':'Technical capacity: 79.8 mcm/d; available: 60; unavailable: 19.8.',
           'version_seen_at':'2026-10-20T10:00:00Z'}
    context={'as_of':ASOF,'current_news':after,'window_news':[copy.deepcopy(after)],'price_reference':{'frozen':'unchanged'}}
    saved=copy.deepcopy(context)
    class Archive:
        calls=[]
        def previous_news_version(self,identifier,version,as_of):
            self.calls.append((identifier,version,as_of))
            return {'version':1,'known_at':'2026-10-20T09:00:00Z','data':copy.deepcopy(before)}
    archive=Archive()
    enriched=generator.enrich_real_replay(archive,context)
    assert context==saved and enriched['price_reference']==context['price_reference']
    assert enriched['current_news']['revision_delta']==enriched['window_news'][0]['revision_delta']
    assert enriched['current_news']['revision_delta']['capacity_changes']['unavailable_capacity']['delta']=='-0.2'
    assert archive.calls==[('notice',2,ASOF),('notice',2,ASOF)]
