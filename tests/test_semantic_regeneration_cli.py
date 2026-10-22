"""New source-fact cohort validation only: no database or model calls."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest


ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('ttf_semantic_regeneration_cli',ROOT/'scripts/validate-news-logic.py')
cli=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)
FIXTURE=ROOT/'reports/repair_2026-10-20/semantic_regeneration_cases_v2_2.json'


def test_new_twenty_case_file_keeps_old_contexts_and_expectations():
    before=FIXTURE.read_bytes()
    cases,identity=cli.load_case_file(FIXTURE)
    original=json.loads((ROOT/'reports/repair_2026-10-20/comparison_cases.json').read_text())
    anchors=json.loads((ROOT/'reports/repair_2026-10-20/anchor_cases_v2_1.json').read_text())
    by_id={row['id']:row for row in cases}
    assert len(cases)==len(by_id)==20
    assert set(by_id)=={row['id'] for row in original+anchors}|{'real_future_well_b1'}
    for row in original+anchors:
        fresh=by_id[row['id']]
        assert fresh['candidate_context']==row['candidate_context']
        assert {key:value for key,value in fresh.items() if key.startswith('expected_')}=={key:value for key,value in row.items() if key.startswith('expected_')}
    well=by_id['real_future_well_b1']
    assert well['expected_news'] is None and well['expected_close'] is None
    assert well['expected_close_status']=='insufficient_evidence' and well['required_explicit_future_well_action'] is True
    assert well['synthetic'] is False and well['source_analysis_id']=='b1e9c166-8851-4f7c-9803-b9c72329f6a9'
    assert identity['explicit_case_file'] and FIXTURE.read_bytes()==before


@pytest.mark.parametrize('text,expected',[
    ('[N0]该井将永久封堵并弃置。',True),
    ('[N0]The well will now be permanently plugged and abandoned.',True),
    ('[N0]此前尚未封堵，但下一步将封堵并弃置。',True),
    ('[N0]该井已永久封堵并弃置。',False),
    ('[N0]永久封井。',False),
    ('[N2]该井将永久封堵。',False),
    ('[N0]如果该井将封堵，供应可能下降。',False),
    ('[N0]该井不会将永久封堵。',False),
])
def test_real_well_source_contract_retains_explicit_future_without_direction_rule(text,expected):
    result={'news_impact':{'direction':'flat','summary':text},'close_forecast':{'direction':None,'evidence_status':'insufficient_evidence'}}
    before=copy.deepcopy(result)
    check=cli.explicit_future_well_action_check(result,{'required_explicit_future_well_action':True})
    assert check['required'] is True and check['passed'] is expected
    assert result==before


def test_no_extra_source_contract_is_imposed_on_existing_comparison_cases():
    result={'news_impact':{'direction':'up','summary':'明确供气减少。'}}
    assert cli.explicit_future_well_action_check(result,{})=={'required':False,'passed':True,'evidence_paths':[]}
