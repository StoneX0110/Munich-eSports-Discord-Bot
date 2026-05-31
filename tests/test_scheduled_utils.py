"""
Unit tests for shared scheduled-cog utilities.
"""

from datetime import date
from unittest.mock import MagicMock

import pytest

from utils.scheduled import (
    JsonScheduleStore,
    member_has_any_role,
    normalize_weekday,
    weekday_choices,
    weekday_name,
)


def _default_data():
    return {
        "next_id": 1,
        "items": {},
    }


def test_store_load_missing_file_returns_independent_default(tmp_path):
    store = JsonScheduleStore(
        file_path=tmp_path / "missing.json",
        default_factory=_default_data,
        logger=MagicMock(),
        corrupt_log_message="corrupt",
        read_error_log_message="read error",
        write_error_log_message="write error",
    )

    first = store.load()
    first["items"]["1"] = {"weekday": "Montag"}
    second = store.load()

    assert second == {"next_id": 1, "items": {}}


def test_store_save_and_load_roundtrip(tmp_path):
    store = JsonScheduleStore(
        file_path=tmp_path / "schedule.json",
        default_factory=_default_data,
        logger=MagicMock(),
        corrupt_log_message="corrupt",
        read_error_log_message="read error",
        write_error_log_message="write error",
    )
    data = {
        "next_id": 2,
        "items": {
            "1": {
                "weekday": "Mittwoch",
                "message": "Training",
            }
        },
    }

    store.save(data)

    assert store.load() == data


def test_store_load_corrupt_json_logs_and_returns_default(tmp_path):
    schedule_file = tmp_path / "corrupt.json"
    schedule_file.write_text("not json", encoding="utf-8")
    logger = MagicMock()
    store = JsonScheduleStore(
        file_path=schedule_file,
        default_factory=_default_data,
        logger=logger,
        corrupt_log_message="Corrupt scheduled data.",
        read_error_log_message="read error",
        write_error_log_message="write error",
    )

    assert store.load() == {"next_id": 1, "items": {}}
    logger.exception.assert_called_once_with("Corrupt scheduled data.")


def test_store_load_io_error_is_logged_and_reraised():
    file_path = MagicMock()
    file_path.exists.return_value = True
    file_path.read_text.side_effect = OSError("Read error")
    logger = MagicMock()
    store = JsonScheduleStore(
        file_path=file_path,
        default_factory=_default_data,
        logger=logger,
        corrupt_log_message="corrupt",
        read_error_log_message="Failed to read scheduled data.",
        write_error_log_message="write error",
    )

    with pytest.raises(OSError):
        store.load()

    logger.exception.assert_called_once_with("Failed to read scheduled data.")


def test_store_save_io_error_is_logged_and_reraised():
    file_path = MagicMock()
    file_path.write_text.side_effect = OSError("Write error")
    logger = MagicMock()
    store = JsonScheduleStore(
        file_path=file_path,
        default_factory=_default_data,
        logger=logger,
        corrupt_log_message="corrupt",
        read_error_log_message="read error",
        write_error_log_message="Failed to save scheduled data.",
    )

    with pytest.raises(OSError):
        store.save({})

    logger.exception.assert_called_once_with("Failed to save scheduled data.")


def test_normalize_weekday_accepts_german_weekday_names_case_insensitively():
    assert normalize_weekday("mittwoch") == "Mittwoch"
    assert normalize_weekday("Montag") == "Montag"
    assert normalize_weekday("FREITAG") == "Freitag"


def test_normalize_weekday_rejects_unknown_values():
    assert normalize_weekday("Wednesday") is None
    assert normalize_weekday("notaday") is None
    assert normalize_weekday("") is None


def test_weekday_name_returns_german_weekday_for_date():
    assert weekday_name(date(2026, 5, 30)) == "Samstag"


def test_member_has_any_role_matches_role_ids():
    member = MagicMock()
    first_role = MagicMock()
    first_role.id = 10
    second_role = MagicMock()
    second_role.id = 20
    member.roles = [first_role, second_role]

    assert member_has_any_role(member, {20, 30}) is True
    assert member_has_any_role(member, {30, 40}) is False


def test_weekday_choices_filters_by_current_input():
    choices = weekday_choices("tag")

    assert [choice.name for choice in choices] == [
        "Montag",
        "Dienstag",
        "Donnerstag",
        "Freitag",
        "Samstag",
        "Sonntag",
    ]
    assert [choice.value for choice in choices] == [
        "Montag",
        "Dienstag",
        "Donnerstag",
        "Freitag",
        "Samstag",
        "Sonntag",
    ]
