import pytest

from question_runtime_definition_service import QuestionRuntimeDefinitionService
from question_settings_window import QuestionSettingsWindow


class _Var:
    def __init__(self, value=''):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _BoolVar(_Var):
    pass


class _Text:
    def __init__(self, value=''):
        self.value = value

    def get(self, *_args):
        return self.value



def _window_stub(tmp_path):
    window = QuestionSettingsWindow.__new__(QuestionSettingsWindow)
    window.runtime_service = QuestionRuntimeDefinitionService(tmp_path / 'Trait-Based Scoring')
    window.runtime_service.traits_dir.mkdir()
    window.signal_ref_var = _Var('S_NEW')
    window.signal_label_var = _Var('New Signal')
    window.signal_weight_var = _Var('2')
    window.signal_group_var = _Var('Observation')
    window.signal_is_critical_var = _BoolVar(True)
    window.group_id_var = _Var('LANG')
    window.group_label_var = _Var('Language')
    window.signal_definition = {
        'trait_id': 'T3_Sample',
        'question': 'Q?',
        'core_signals': [],
        'extended_signal_groups': [{'group_id': 'LANG', 'group_label': 'Language', 'signals': []}],
    }
    window.samples_text = _Text('{"1":"a"}')
    window.trait_id_var = _Var('trait_3')
    window.trait_name_var = _Var('Sample')
    window.weight_var = _Var('2')
    window.priority_var = _Var('critical')
    window.question_text = _Text('Prompt?')
    return window



def test_window_build_signal_payload_normalizes_signal_fields(tmp_path) -> None:
    window = _window_stub(tmp_path)

    payload = window._build_signal_payload('core')

    assert payload == {
        'ref': 'S_NEW',
        'label': 'New Signal',
        'weight': 2.0,
        'group': 'Observation',
        'is_critical': True,
    }



def test_window_build_trait_updates_validates_weight_and_samples(tmp_path) -> None:
    window = _window_stub(tmp_path)
    trait_id, updates = window._build_trait_updates()

    assert trait_id == 'trait_3'
    assert updates['sample_answers']['5'] == ''
    assert updates['primary_question'] == 'Prompt?'

    window.weight_var.set('9')
    with pytest.raises(ValueError, match='between 0 and 5'):
        window._build_trait_updates()



def test_window_mutate_signal_definition_routes_core_and_group_crud(tmp_path) -> None:
    window = _window_stub(tmp_path)

    core_definition = window._mutate_signal_definition('core', window._build_signal_payload('core'))
    assert core_definition['core_signals'][0]['ref'] == 'S_NEW'

    window.signal_definition = core_definition
    group_payload = {
        'ref': 'S_GROUP',
        'label': 'Group Signal',
        'weight': 1.0,
        'group': 'Language',
        'is_critical': False,
    }
    group_definition = window._mutate_signal_definition('LANG', group_payload)

    assert group_definition['extended_signal_groups'][0]['signals'][0]['ref'] == 'S_GROUP'
    assert group_definition['extended_signal_groups'][0]['signals'][0]['group'] == 'Language'
