import copy
import hashlib
import json
from pathlib import Path
import re

import pytest

from ttf_news.analysis import _parse_result
from ttf_news.translation import prepare_translation, restore_translation
from test_analysis import FakeAnalyst, context, result


FIXTURE = Path(__file__).parent/'fixtures/source_eni_missing_current_reference.json'


def eni_fixture():
    frozen = json.loads(FIXTURE.read_text())
    assert hashlib.sha256(frozen['raw_content'].encode()).hexdigest() == frozen['raw_content_sha256']
    return frozen, json.loads(frozen['raw_content'])


def test_real_eni_missing_current_ref_restores_only_input_provenance_not_analysis():
    frozen, raw = eni_fixture(); metadata = {}; before = frozen['raw_content']
    assert [ref['id'] for ref in raw['references']] == ['N3','N7']
    parsed = _parse_result(before,False,frozen['source_index'],reference_meta=metadata)
    control = copy.deepcopy(raw); control['references'].append({'id':'N0','url':''})
    supplied = _parse_result(json.dumps(control,ensure_ascii=False),False,frozen['source_index'])
    # The result is exactly what resolving supplied valid source metadata gives;
    # no statement, evidence, direction, number or score is supplied by repair.
    assert json.dumps(parsed,ensure_ascii=False,sort_keys=True) == json.dumps(supplied,ensure_ascii=False,sort_keys=True)
    for key in ('news_impact','close_forecast','analysis'):
        for name,value in raw[key].items():
            if isinstance(value,(str,list)):
                assert json.dumps(parsed[key][name],ensure_ascii=False).encode() == json.dumps(value,ensure_ascii=False).encode()
            else:
                assert parsed[key][name] == value
    current = next(row for row in frozen['source_index'] if row['alias']=='N0')
    assert metadata == {'raw_reference_ids':['N3','N7'],'added_by_backend':['N0'],
                        'input_provenance':{'N0':{'id':current['id'],'source':'source_index.N0/current_news'}}}
    assert 'model_cited' not in json.dumps(metadata)
    assert parsed['references'][-1] == {'id':current['id'],'url':current['url'],'title':current['title']}
    assert frozen['raw_content'] == before and json.loads(before) == raw


@pytest.mark.parametrize('defect,expected', [('unknown_id','不属于输入证据'),
                                           ('forged_url','URL与输入来源不同'),
                                           ('empty_refs','references不能为空'),
                                           ('invalid_schema','list'),
                                           ('missing_current_index','缺少当前新闻N0'),
                                           ('no_body_source','正文没有N/E证据引用')])
def test_current_source_addition_does_not_rescue_invalid_refs_or_unattributed_body(defect, expected):
    frozen, raw = eni_fixture(); index = copy.deepcopy(frozen['source_index']); metadata = {}
    if defect=='unknown_id': raw['references'].append({'id':'N999','url':''})
    elif defect=='forged_url': raw['references'][0]['url']='https://wrong.example/invented'
    elif defect=='empty_refs': raw['references']=[]
    elif defect=='invalid_schema': raw['references']='N3,N7'
    elif defect=='missing_current_index': index=[row for row in index if row['alias']!='N0']
    else:
        def remove_ids(value):
            if isinstance(value,dict):return {key:remove_ids(row) for key,row in value.items()}
            if isinstance(value,list):return [remove_ids(row) for row in value]
            return re.sub(r'\[?[NE]\d+\]?', '',value) if isinstance(value,str) else value
        for key in ('news_impact','close_forecast','analysis'):raw[key]=remove_ids(raw[key])
    original = copy.deepcopy(raw)
    with pytest.raises(ValueError,match=expected):
        _parse_result(json.dumps(raw),False,index,reference_meta=metadata)
    assert raw == original and metadata == {}


def test_existing_current_canonical_reference_is_not_added_twice():
    frozen, raw = eni_fixture(); current=next(row for row in frozen['source_index'] if row['alias']=='N0')
    raw['references'].append({'id':current['id'],'url':current['url']})
    metadata = {}
    parsed = _parse_result(json.dumps(raw),False,frozen['source_index'],reference_meta=metadata)
    assert [ref['id'] for ref in parsed['references']].count(current['id']) == 1
    assert metadata['added_by_backend'] == [] and metadata['input_provenance'] == {}
    assert metadata['raw_reference_ids'] == ['N3','N7',current['id']]


def test_window_alias_for_current_source_counts_as_present_and_restoration_does_not_duplicate():
    frozen, raw = eni_fixture(); current=next(row for row in frozen['source_index'] if row['alias']=='N0')
    index=copy.deepcopy(frozen['source_index'])
    index.append({**copy.deepcopy(current),'alias':'N99'})
    raw['references'].append({'id':'N99','url':''})
    # Both aliases refer to the same source; none constitutes another source.
    raw['analysis']['evidence'].append('[N0]当前项目的欧洲交付量未提供 → TTF传导仍需条件分析')
    metadata={}
    parsed=_parse_result(json.dumps(raw),False,index,reference_meta=metadata)
    assert [ref['id'] for ref in parsed['references']].count(current['id'])==1
    assert metadata['raw_reference_ids']==['N3','N7','N99']
    assert metadata['added_by_backend']==[] and metadata['input_provenance']=={}


def test_recovering_multiple_omitted_aliases_adds_current_canonical_source_once():
    frozen, raw=eni_fixture(); current=next(row for row in frozen['source_index'] if row['alias']=='N0')
    index=copy.deepcopy(frozen['source_index']); index.append({**copy.deepcopy(current),'alias':'N99'})
    raw['analysis']['evidence'].append('[N0]与[N99]是同源新闻 → 本窗口只计一次当前来源')
    metadata={}
    parsed=_parse_result(json.dumps(raw),False,index,reference_meta=metadata)
    assert [ref['id'] for ref in parsed['references']].count(current['id'])==1
    assert metadata['added_by_backend']==['N0']


def test_translation_retains_canonical_references_and_backend_provenance():
    frozen, _ = eni_fixture(); metadata = {}
    original = _parse_result(frozen['raw_content'],False,frozen['source_index'],reference_meta=metadata)
    original['_meta']={'reference_resolution':metadata}
    before = copy.deepcopy(original)
    texts,keep=prepare_translation(original)
    assert not any('reference_resolution' in path or 'references' in path for path in texts)
    translated={name:'Conditional source analysis. '+' '.join(re.findall(r'__KEEP_\d+__',text)) for name,text in texts.items()}
    output=restore_translation(original,translated,texts,keep)
    assert original == before and output['references'] == before['references']
    assert output['_meta']['reference_resolution'] == metadata
    assert output['news_impact']['direction'] == original['news_impact']['direction']


async def test_runtime_audits_backend_source_addition_without_retry_or_raw_rewrite():
    value=context(); value['window_news']=[{**copy.deepcopy(value['current_news']),'id':'secondary'}]
    output=result(); output['references']=[{'id':'N1','url':''}]
    output['news_impact']['bullish_evidence']=[text.replace('[N0]','[N1]') for text in output['news_impact']['bullish_evidence']]
    output['analysis']['evidence']=[text.replace('[N0]','[N1]') for text in output['analysis']['evidence']]
    raw=json.dumps(output)
    analyst=FakeAnalyst([raw]); parsed=await analyst.analyze(value)
    assert len(analyst.calls)==1 and not parsed['_meta']['schema_repair_used']
    resolution=parsed['_meta']['reference_resolution']
    assert resolution['raw_reference_ids']==['N1'] and resolution['added_by_backend']==['N0']
    call=parsed['_meta']['model_calls'][0]
    assert call['raw_content']==raw and call['reference_resolution']==resolution
    assert parsed['analysis']['evidence']==output['analysis']['evidence']
