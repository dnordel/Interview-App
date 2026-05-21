import json
from pathlib import Path

import pytest

from question_runtime_definition_service import (
    QuestionRuntimeDefinitionService,
    default_runtime_definition,
    list_signal_refs,
    next_trait_id,
    normalize_runtime_definition,
)


def test_load_definition_normalizes_existing_runtime_file(tmp_path: Path) -> None:
    traits_dir = tmp_path / 'Trait-Based Scoring'
    traits_dir.mkdir()
    payload = {
        'trait_id': 'T12_Custom_Trait',
        'question': ' What changed? ',
        'core_signals': [{'ref': 'S_CORE', 'weight': '2', 'group': '', 'is_critical': 1}],
        'extended_signal_groups': [{'group_id': 'GROUP1', 'group_label': 'Observations', 'signals': [{'ref': 'S_EXT', 'weight': '1'}]}],
    }
    (traits_dir / 'T12_Custom_Trait.json').write_text(json.dumps(payload), encoding='utf-8')

    service = QuestionRuntimeDefinitionService(traits_dir)
    definition = service.load_definition('trait_12')

    assert definition['trait_id'] == 'T12_Custom_Trait'
    assert definition['question'] == 'What changed?'
    assert definition['core_signals'][0]['group'] == 'Core'
    assert definition['extended_signal_groups'][0]['signals'][0]['group'] == 'Observations'


def test_runtime_definition_service_crud_round_trip(tmp_path: Path) -> None:
    traits_dir = tmp_path / 'Trait-Based Scoring'
    traits_dir.mkdir()
    service = QuestionRuntimeDefinitionService(traits_dir)

    created = service.create_definition('trait_15', 'Calm Leadership', 'Tell me about a challenge.')
    with_group = service.add_extended_group(created, {'group_id': 'LANGUAGE', 'group_label': 'Language'})
    with_core = service.add_core_signal(with_group, {'ref': 'S_CORE', 'label': 'Core signal', 'weight': 2, 'group': 'Observation'})
    with_signal = service.add_group_signal(with_core, 'LANGUAGE', {'ref': 'S_LANG', 'label': 'Language signal', 'weight': 1})
    updated = service.update_group_signal(with_signal, 'LANGUAGE', 'S_LANG', {'label': 'Updated label', 'weight': 3})
    saved = service.save_definition('trait_15', 'Calm Leadership', updated)

    assert saved['trait_id'] == 'T15_Calm_Leadership'
    assert list_signal_refs(saved) == ['S_CORE', 'S_LANG']
    stored_path = traits_dir / 'T15_Calm_Leadership.json'
    assert stored_path.exists()

    reloaded = service.load_definition('trait_15')
    assert reloaded['extended_signal_groups'][0]['signals'][0]['label'] == 'Updated label'
    assert reloaded['extended_signal_groups'][0]['signals'][0]['weight'] == 3.0

    after_delete = service.delete_group_signal(reloaded, 'LANGUAGE', 'S_LANG')
    final_definition = service.delete_core_signal(after_delete, 'S_CORE')
    service.save_definition('trait_15', 'Calm Leadership', final_definition)
    service.delete_definition('trait_15')

    assert not stored_path.exists()


def test_runtime_definition_service_rejects_duplicate_refs_and_negative_weights() -> None:
    definition = default_runtime_definition('trait_3')
    definition['core_signals'] = [{'ref': 'SAME', 'label': 'A', 'weight': 1, 'group': 'Core', 'is_critical': False}]
    definition['extended_signal_groups'] = [
        {'group_id': 'GROUP', 'group_label': 'Group', 'signals': [{'ref': 'SAME', 'label': 'B', 'weight': 1}]}
    ]

    with pytest.raises(ValueError, match='Duplicate signal ref'):
        normalize_runtime_definition(definition)

    service = QuestionRuntimeDefinitionService(Path('.'))
    with pytest.raises(ValueError, match='non-negative number'):
        service.add_core_signal(default_runtime_definition('trait_3'), {'ref': 'S_BAD', 'label': 'bad', 'weight': -1})


def test_next_trait_id_uses_next_numeric_rubric_slot() -> None:
    rubric = {'traits': [{'id': 'trait_1'}, {'id': 'trait_7'}, {'id': 'custom'}]}
    assert next_trait_id(rubric) == 'trait_8'
