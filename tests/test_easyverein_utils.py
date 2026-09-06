import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from utils.easyverein import MemberDateFilter, fetch_active_members, is_active_on


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


def test_fetch_active_members_queries_both_active_groups_and_deduplicates(monkeypatch):
    async def run_inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr("utils.easyverein.asyncio.to_thread", run_inline)
    client = SimpleNamespace(member=SimpleNamespace(get_all=MagicMock()))
    first = SimpleNamespace(id=1)
    duplicate = SimpleNamespace(id=1)
    second = SimpleNamespace(id=2)
    client.member.get_all.side_effect = [[first], [duplicate, second]]

    result = asyncio.run(fetch_active_members(client, query="{id}", today=date(2026, 7, 31)))

    assert {member.id for member in result} == {1, 2}
    assert client.member.get_all.call_count == 2
    searches = [call.kwargs["search"] for call in client.member.get_all.call_args_list]
    assert any(search.resignationDate__isnull is True for search in searches)
    assert any(search.resignationDate__gte == date(2026, 7, 31) for search in searches)
