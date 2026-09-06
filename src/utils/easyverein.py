"""Compatibility helpers for easyVerein member data."""

import asyncio
from datetime import date, datetime
from typing import Any

from easyverein.core.types import Date
from easyverein.models.member import MemberFilter


class MemberDateFilter(MemberFilter):
    """Serialize member date filters in the format required by API v2.

    python-easyverein 2.4.0 still models these fields as datetimes, but the
    easyVerein v2 API expects date-only values since its July 2026 API change.
    """

    joinDate: Date | None = None
    joinDate__gte: Date | None = None
    joinDate__lte: Date | None = None
    resignationDate: Date | None = None
    resignationDate__gte: Date | None = None
    resignationDate__lte: Date | None = None


def is_active_on(resignation_date: date | datetime | None, day: date) -> bool:
    """Return whether a membership is still active on ``day``."""
    if resignation_date is None:
        return True
    if isinstance(resignation_date, datetime):
        resignation_date = resignation_date.date()
    return resignation_date >= day


async def fetch_active_members(client: Any, *, query: str, today: date) -> list[Any]:
    """Fetch and deduplicate members whose membership is active on ``today``."""
    indefinite = MemberDateFilter(resignationDate__isnull=True, isApplication=False)
    resigning = MemberDateFilter(resignationDate__gte=today, isApplication=False)
    members_indefinite = await asyncio.to_thread(
        client.member.get_all, query=query, search=indefinite
    )
    members_resigning = await asyncio.to_thread(
        client.member.get_all, query=query, search=resigning
    )
    return list({member.id: member for member in members_indefinite + members_resigning}.values())
