"""Comparison CLI changes must stay compatible with the archived analyst."""
import importlib.util
import os
from pathlib import Path
import sys

import pytest


SPEC=importlib.util.spec_from_file_location('ttf_comparison_cli',Path(__file__).resolve().parents[1]/'scripts/validate-news-logic.py')
generator=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


def test_semantic_scope_is_omitted_by_default():
    options=generator.parser.parse_args([])
    assert options.semantic_high_scope is None
    assert generator.analyst_options(options)=={}


@pytest.mark.parametrize('scope',['operator_notice','all'])
def test_candidate_passes_explicit_scope_and_reasoning_without_model_calls(scope):
    options=generator.parser.parse_args(['--engine','candidate','--semantic-high-scope',scope,'--reasoning','medium'])
    calls=[]
    class Candidate:
        def __init__(self,**kwargs):calls.append(kwargs)
    Candidate(**generator.analyst_options(options))
    assert calls==[{'reasoning':'medium','semantic_high_scope':scope}]


@pytest.mark.parametrize('scope',['operator_notice','all'])
def test_original_constructor_never_receives_new_scope(scope):
    options=generator.parser.parse_args(['--engine','original','--semantic-high-scope',scope,'--reasoning','high'])
    calls=[]
    class ArchivedAnalyst:
        def __init__(self,reasoning='medium'):calls.append(reasoning)
    ArchivedAnalyst(**generator.analyst_options(options))
    assert calls==['high']


def test_recorded_configuration_comes_from_effective_instance_attributes():
    class Effective:
        model='test-only-no-network'
        reasoning='medium'
        semantic_high_retry=True
        semantic_high_scope='operator_notice'
    assert generator.analyst_configuration(Effective())=={
        'model':'test-only-no-network','reasoning':'medium',
        'semantic_high_retry':True,'semantic_high_scope':'operator_notice'}
    class Archived:
        model='archived-test-only'
        reasoning='high'
    assert generator.analyst_configuration(Archived())=={
        'model':'archived-test-only','reasoning':'high',
        'semantic_high_retry':None,'semantic_high_scope':None}


def test_invalid_scope_is_rejected_before_execution():
    with pytest.raises(SystemExit):generator.parser.parse_args(['--semantic-high-scope','unsupported'])


@pytest.mark.parametrize('configured',[True,False])
def test_import_respects_configured_tokenizer_cache_and_has_local_default(monkeypatch,tmp_path,configured):
    monkeypatch.setattr(sys,'path',sys.path.copy())
    if configured:
        cache=str(tmp_path/'standard-tokenizer-cache')
        monkeypatch.setenv('TIKTOKEN_CACHE_DIR',cache)
    else:
        cache=str(Path(__file__).resolve().parents[1]/'data/tokenizer')
        monkeypatch.delenv('TIKTOKEN_CACHE_DIR',raising=False)
    spec=importlib.util.spec_from_file_location('ttf_comparison_cache_test',SPEC.origin)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert os.environ['TIKTOKEN_CACHE_DIR']==cache
