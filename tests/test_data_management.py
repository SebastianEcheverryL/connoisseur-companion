import json
from unittest.mock import patch

import pytest

from connoisseur import data_management as dm

VALID_RECORD = {
    "name": "The Copper Sprout",
    "location": "Asheville",
    "type": "farm-to-table",
    "food_style": "Modern Appalachian",
    "rating": 4.5,
    "price_range": 2,
    "signatures": ["Cast-Iron Smoked Trout"],
    "vibe": "cozy",
    "environment": "reclaimed wood and amber lighting",
    "shortcomings": [],
}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "db.json"
    path.write_text(json.dumps([{"name": "Test Cafe", "location": "Test City", "itemId": 1000001}]))
    return str(path), str(tmp_path / "db.json.bak")


def run_menu(db_paths, inputs, capsys):
    with patch("builtins.input", side_effect=inputs):
        dm.manage_restaurants(*db_paths)
    return capsys.readouterr().out


def test_add_restaurant_uses_llm_and_saves(db, capsys):
    with patch.object(dm, "llm_model", return_value=json.dumps(VALID_RECORD)):
        out = run_menu(db, ["3", "yes", "some description", "6"], capsys)

    data = dm.load_data(db[0])
    assert "Restaurant added" in out
    assert len(data) == 2
    assert data[-1]["name"] == "The Copper Sprout"
    assert data[-1]["itemId"] == 1000002  # max existing id + 1


def test_delete_restaurant(db, capsys):
    out = run_menu(db, ["5", "yes", 0, "6"], capsys)
    assert "Record deleted" in out
    assert dm.load_data(db[0]) == []


def test_write_operation_cancelled_without_confirmation(db, capsys):
    out = run_menu(db, ["5", "no", "6"], capsys)
    assert "Operation cancelled." in out
    assert len(dm.load_data(db[0])) == 1


def test_save_data_creates_backup(db):
    original = dm.load_data(db[0])
    dm.save_data([], db[0], db[1])
    assert dm.load_data(db[1]) == original


def test_auto_repair_loop_recovers_from_invalid_json():
    invalid = json.dumps({"name": "Broken"})  # missing required fields
    with patch.object(dm, "llm_model", side_effect=[invalid, json.dumps(VALID_RECORD)]) as llm:
        result = dm.new_data_entry_process("text", 1000042)
    assert result["name"] == "The Copper Sprout"
    assert result["itemId"] == 1000042
    assert llm.call_count == 2


def test_auto_repair_gives_up_after_max_retries():
    with patch.object(dm, "llm_model", return_value="not json at all") as llm:
        assert dm.new_data_entry_process("text", 1) is None
    assert llm.call_count == dm.MAX_REPAIR_RETRIES + 2  # first attempt + repair retries


def test_extract_json_text_strips_markdown_fences():
    assert dm._extract_json_text('Sure!\n```json\n{"a": 1}\n```') == '{"a": 1}'


def test_parse_field_value_types():
    assert dm.parse_field_value("rating", "4.5") == 4.5
    assert dm.parse_field_value("price_range", "3") == 3
    assert dm.parse_field_value("signatures", "a, b ,") == ["a", "b"]
