from datetime import date, datetime

import pytest

from utils.easyverein import MemberDateFilter, is_active_on


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("joinDate", date(2026, 7, 31)),
        ("joinDate__gte", date(2026, 7, 31)),
        ("joinDate__lte", date(2026, 7, 31)),
        ("resignationDate", date(2026, 7, 31)),
        ("resignationDate__gte", date(2026, 7, 31)),
        ("resignationDate__lte", date(2026, 7, 31)),
    ],
)
def test_member_date_filter_serializes_date_without_time(field, value):
    search = MemberDateFilter(**{field: value})

    assert search.model_dump(exclude_unset=True)[field] == "2026-07-31"


def test_member_date_filter_produces_api_query_parameters():
    search = MemberDateFilter(
        resignationDate__gte=date(2026, 7, 31),
        isApplication=False,
    )

    assert search.model_dump(exclude_unset=True, exclude_defaults=True, by_alias=True) == {
        "resignationDate__gte": "2026-07-31",
        "_isApplication": False,
    }


@pytest.mark.parametrize(
    ("resignation_date", "expected"),
    [
        (None, True),
        (date(2026, 7, 31), True),
        (datetime(2026, 7, 31), True),
        (date(2026, 7, 30), False),
        (datetime(2026, 7, 30, 23, 59), False),
    ],
)
def test_is_active_on_accepts_easyverein_date_and_datetime_values(resignation_date, expected):
    assert is_active_on(resignation_date, date(2026, 7, 31)) is expected
