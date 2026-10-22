"""Real bilingual boundary regression; these tests never call the model."""
import copy
from collections import Counter

import pytest

from ttf_news.translation import PROTECTED, prepare_translation, restore_translation


def test_chinese_adjacent_references_match_their_english_rendering():
    source = '新增事实：N0为未来建设合同，N1、N2为未来公告；E12显示历史背景。'
    target = 'New facts: N0 is a future construction contract, N1 and N2 are future announcements; E12 shows historical background.'
    expected = Counter(['N0', 'N1', 'N2', 'E12'])
    assert Counter(PROTECTED.findall(source)) == expected
    assert Counter(PROTECTED.findall(target)) == expected


def test_ids_are_masked_as_whole_tokens_and_original_analysis_is_preserved():
    original = {'analysis': {'summary': 'N0为合同，N2为公告。'},
                'news_impact': {'direction': 'flat', 'confidence': 0.6},
                'close_forecast': {'direction': None, 'evidence_status': 'insufficient_evidence'},
                'references': [{'id': 'N0', 'url': 'https://example.test/article'}]}
    before = copy.deepcopy(original)
    texts, keep = prepare_translation(original)
    key = next(iter(texts))
    assert keep[key] == ['N0', 'N2']
    output = restore_translation(original, {key: '__KEEP_0__ is a contract; __KEEP_1__ is an announcement.'}, texts, keep)
    assert output['analysis']['summary'] == 'N0 is a contract; N2 is an announcement.'
    assert output['news_impact'] == before['news_impact']
    assert output['close_forecast'] == before['close_forecast']
    assert original == before


@pytest.mark.parametrize('changed', ['A__KEEP_0__ is a fact.', '__KEEP_0___other is a fact.', '__KEEP_0__2 is a fact.'])
def test_placeholder_cannot_be_used_to_create_a_different_reference(changed):
    original = {'analysis': {'summary': 'N0为该新闻。'}}
    texts, keep = prepare_translation(original)
    with pytest.raises(ValueError, match='protected|unverified'):
        restore_translation(original, {next(iter(texts)): changed}, texts, keep)


def test_ascii_word_fragments_are_not_references():
    tokens = PROTECTED.findall('AN0 and E2E and N1_extra; N0为新闻，E2为历史。')
    assert [value for value in tokens if value.startswith(('N', 'E'))] == ['N0', 'E2']


def test_numbers_units_dates_and_bracketed_references_remain_protected():
    source = 'N0引用[N12]和[E3.1]；2026-10-20，变化+1.76€/MWh，容量13.0mcm/d。'
    assert Counter(PROTECTED.findall(source)) == Counter(['N0', '[N12]', '[E3.1]', '2026-10-20', '+1.76', '€/MWh', '13.0', 'mcm/d'])
