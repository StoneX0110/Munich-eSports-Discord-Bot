import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs import honeypot
from cogs.honeypot import HoneypotCog


class FakeMessageable:
    def __init__(self):
        self.send = AsyncMock()


def _scenario(*, dry_run=False):
    member = SimpleNamespace(id=42, ban=AsyncMock())
    mod_channel = FakeMessageable()
    guild = SimpleNamespace(id=honeypot.GUILD_ID, get_channel=lambda _: mod_channel)
    message = SimpleNamespace(
        author=member,
        channel=SimpleNamespace(id=honeypot.HONEYPOT_CHANNEL_ID),
        guild=guild,
        webhook_id=None,
        is_forwardable=lambda: True,
        forward=AsyncMock(),
    )
    bot = SimpleNamespace(user=SimpleNamespace(id=1), dry_run=dry_run)
    return HoneypotCog(bot), message, member, mod_channel


@pytest.mark.parametrize('outcome', ['success', 'ban-failure', 'report-failure', 'dry-run'])
def test_honeypot_evidence_ban_and_result_order(monkeypatch, outcome):
    monkeypatch.setattr(honeypot.discord.abc, 'Messageable', FakeMessageable)
    cog, message, member, mod_channel = _scenario(dry_run=outcome == 'dry-run')
    events = []
    message.forward.side_effect = lambda _: events.append('evidence')

    async def ban(**kwargs):
        events.append('ban')
        if outcome == 'ban-failure':
            raise honeypot.discord.Forbidden(AsyncMock(), 'denied')

    async def report(text):
        events.append(text)
        if outcome == 'report-failure':
            raise honeypot.discord.Forbidden(AsyncMock(), 'cannot report')

    member.ban.side_effect = ban
    mod_channel.send.side_effect = report
    asyncio.run(cog.on_message(message))
    if outcome == 'dry-run':
        assert events == []
    else:
        assert events[:2] == ['evidence', 'ban'] and len(events) == 3
        assert ('BAN FAILED' if outcome == 'ban-failure' else 'BANNED') in events[2]
