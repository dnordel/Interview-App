import pytest
from types import SimpleNamespace

from ui_composition import QuestionRuntimeDefinitionService, QuestionSettingsService, QuestionSettingsWindow


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


class _Listbox:
    def __init__(self):
        self.items = []
        self.selected = ()

    def delete(self, *_args):
        self.items = []

    def insert(self, _index, value):
        self.items.append(value)

    def get(self, index):
        return self.items[index]

    def curselection(self):
        return self.selected



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
        'core_signals': [{'ref': 'S_CORE', 'label': 'Core Signal', 'weight': 1, 'group': 'Core', 'is_critical': False}],
        'extended_signal_groups': [{'group_id': 'LANG', 'group_label': 'Language', 'signals': [{'ref': 'S_LANG', 'label': 'Language Signal', 'weight': 1, 'group': 'Language'}]}],
    }
    window.samples_text = _Text('{"1":"a"}')
    window.trait_id_var = _Var('trait_3')
    window.trait_name_var = _Var('Sample')
    window.weight_var = _Var('2')
    window.priority_var = _Var('critical')
    window.question_text = _Text('Prompt?')
    rubric = {
        'tracks': {'preschool': {'label': 'Preschool'}},
        'traits': [
            {
                'id': 'trait_3',
                'name': 'Sample',
                'priority': 'critical',
                'weight': 2,
                'applicable_tracks': ['preschool'],
                'primary_question': 'Prompt?',
                'descriptors': {'1': 'one', '2': 'two', '3': 'three', '4': 'four', '5': 'five'},
                'sample_answers': {'1': '', '2': '', '3': '', '4': '', '5': ''},
            }
        ],
    }
    window.app = SimpleNamespace(
        rubric=rubric,
        rubric_loader=SimpleNamespace(data=rubric),
    )
    window.track_var = _Var('preschool')
    window.core_signal_list = _Listbox()
    window.group_list = _Listbox()
    window.group_signal_list = _Listbox()
    return window


def _window_with_rubric(tmp_path):
    window = _window_stub(tmp_path)
    rubric = window.app.rubric
    window.service = QuestionSettingsService(tmp_path / 'rubric.json', rubric)
    window.refresh_trait_list = lambda: None
    window.status_var = _Var('')

    def _apply_new_rubric(next_rubric):
        window.app.rubric = next_rubric
        window.app.rubric_loader.data = next_rubric

    window._apply_new_rubric = _apply_new_rubric
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


def test_window_build_signal_payload_allows_negative_weighted_scoring_signals(tmp_path) -> None:
    window = _window_stub(tmp_path)
    window.signal_weight_var.set('-2')

    payload = window._build_signal_payload('core')

    assert payload['weight'] == -2.0



def test_window_build_trait_updates_validates_weight_and_samples(tmp_path) -> None:
    window = _window_stub(tmp_path)
    trait_id, updates = window._build_trait_updates()

    assert trait_id == 'trait_3'
    assert updates['sample_answers']['5'] == ''
    assert updates['primary_question'] == 'Prompt?'
    assert updates['descriptors']['5'] == 'five'
    assert updates['applicable_tracks'] == ['preschool']

    window.weight_var.set('9')
    with pytest.raises(ValueError, match='between 0 and 5'):
        window._build_trait_updates()

    window.weight_var.set('2')
    window.trait_id_var.set('custom')
    with pytest.raises(ValueError, match='trait_<number>'):
        window._build_trait_updates()



def test_window_mutate_signal_definition_routes_core_and_group_crud(tmp_path) -> None:
    window = _window_stub(tmp_path)

    core_definition = window._mutate_signal_definition('core', window._build_signal_payload('core'))
    assert core_definition['core_signals'][-1]['ref'] == 'S_NEW'

    window.signal_definition = core_definition
    group_payload = {
        'ref': 'S_GROUP',
        'label': 'Group Signal',
        'weight': 1.0,
        'group': 'Language',
        'is_critical': False,
    }
    group_definition = window._mutate_signal_definition('LANG', group_payload)

    assert group_definition['extended_signal_groups'][0]['signals'][-1]['ref'] == 'S_GROUP'
    assert group_definition['extended_signal_groups'][0]['signals'][-1]['group'] == 'Language'


def test_window_refresh_signal_lists_and_selection_loads_fields(tmp_path) -> None:
    window = _window_stub(tmp_path)

    window.refresh_signal_lists()

    assert window.core_signal_list.items == ['S_CORE | Core Signal | weight=1.0']
    assert window.group_list.items == ['LANG | Language']
    assert window.group_signal_list.items == ['S_LANG | Language Signal | weight=1.0']

    window.core_signal_list.selected = (0,)
    window._load_selected_core_signal()
    assert window.signal_ref_var.get() == 'S_CORE'
    assert window.signal_label_var.get() == 'Core Signal'

    window.group_signal_list.selected = (0,)
    window._load_selected_group_signal()
    assert window.signal_ref_var.get() == 'S_LANG'
    assert window.signal_group_var.get() == 'Language'


def test_window_trait_save_add_delete_syncs_runtime_definition(tmp_path, monkeypatch) -> None:
    window = _window_with_rubric(tmp_path)

    window.save_trait()
    assert (tmp_path / 'Trait-Based Scoring' / 'T3_Sample.json').exists()

    window.trait_id_var.set('')
    window.trait_name_var.set('New Trait')
    window.question_text = _Text('New prompt?')
    window.add_trait()
    assert window.app.rubric['traits'][-1]['id'] == 'trait_4'
    assert (tmp_path / 'Trait-Based Scoring' / 'T4_New_Trait.json').exists()

    monkeypatch.setattr('ui_composition.messagebox.askyesno', lambda *_args, **_kwargs: True)
    window.trait_id_var.set('trait_4')
    window.delete_trait()
    assert all(trait['id'] != 'trait_4' for trait in window.app.rubric['traits'])
    assert not (tmp_path / 'Trait-Based Scoring' / 'T4_New_Trait.json').exists()
