import pytest
from app.utils.json_parser import parse_json_robustly

def test_parse_valid_json():
    text = '{"question": "What is SOP?", "options": {"A": "Standard", "B": "Normal"}}'
    data = parse_json_robustly(text)
    assert data["question"] == "What is SOP?"

def test_parse_json_with_markdown():
    text = '```json\n{"question": "What is SOP?", "options": {"A": "Standard", "B": "Normal"}}\n```'
    data = parse_json_robustly(text)
    assert data["question"] == "What is SOP?"

def test_parse_json_with_conversational_text():
    text = 'Here is the JSON you requested:\n{"question": "What is SOP?", "options": {"A": "Standard", "B": "Normal"}}\nHope this helps!'
    data = parse_json_robustly(text)
    assert data["question"] == "What is SOP?"

def test_parse_json_with_trailing_comma():
    text = '{"question": "What is SOP?", "options": {"A": "Standard", "B": "Normal",},}'
    data = parse_json_robustly(text)
    assert data["options"]["B"] == "Normal"

def test_parse_python_dict_literal():
    text = "{'question': 'What is SOP?', 'options': {'A': 'Standard', 'B': 'Normal'}}"
    data = parse_json_robustly(text)
    assert data["question"] == "What is SOP?"

def _mcq_obj(i):
    return (
        f'{{"question": "Q{i}?", "options": {{"A": "a", "B": "b", "C": "c", "D": "d"}}, '
        f'"correct_option": "A", "explanation": "e"}}'
    )

def test_parse_truncated_array_recovers_complete_objects():
    """A cut-off array (unterminated last string) still yields the objects
    Gemini finished — the root cause of the 'unterminated string literal'
    ingestion failures."""
    text = f'[{_mcq_obj(1)}, {_mcq_obj(2)}, {{"question": "Q3?"'
    data = parse_json_robustly(text)
    assert isinstance(data, list)
    assert [q['question'] for q in data] == ['Q1?', 'Q2?']

def test_parse_array_missing_closing_bracket():
    """Array truncated before the final ']' still parses."""
    text = f'[{_mcq_obj(1)}, {_mcq_obj(2)}'
    data = parse_json_robustly(text)
    assert isinstance(data, list)
    assert len(data) == 2

def test_parse_missing_comma_between_fields():
    """'Expecting , delimiter' style output — comma forgotten between keys."""
    text = (
        '{"question": "Q1?" "options": {"A": "a", "B": "b", "C": "c", "D": "d"}, '
        '"correct_option": "A"}'
    )
    data = parse_json_robustly(text)
    assert data['question'] == 'Q1?'
    assert data['correct_option'] == 'A'

def test_parse_missing_comma_between_array_elements():
    text = f'[{_mcq_obj(1)} {_mcq_obj(2)}]'
    data = parse_json_robustly(text)
    assert isinstance(data, list)
    assert len(data) == 2

def test_missing_comma_fixer_leaves_valid_json_untouched():
    """Regression guard: the repair must never corrupt strings that contain
    structural characters like '}' followed by '{'."""
    from app.utils.json_parser import _fix_missing_commas
    text = '{"options": {"A": "a}", "B": "b"}, "text": "} {"}'  # noqa: E501
    data = parse_json_robustly(text)
    assert data['options']['A'] == 'a}'
    assert data['text'] == '} {'
    # Exercise the repair directly on quoted values that mimic those
    # structural characters — it must leave them untouched.
    assert _fix_missing_commas('{"v": "} { and ] ["}') == '{"v": "} { and ] ["}'
    assert _fix_missing_commas('{"a": "x", "b": "y"}') == '{"a": "x", "b": "y"}'
