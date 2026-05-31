import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs import honeypot
from cogs.honeypot import HoneypotCog


class FakeMember:
    def __init__(self, *, joined_at):
        self.id = 42
        self.joined_at = joined_at
        self.ban = AsyncMock()


class FakeMessageable:
    def __init__(self):
        self.send = AsyncMock()


def test_trusted_honeypot_member_message_is_deleted(monkeypatch):
    monkeypatch.setattr(honeypot.discord, "Member", FakeMember)
    monkeypatch.setattr(honeypot.discord.abc, "Messageable", FakeMessageable)

    member = FakeMember(joined_at=datetime.now(timezone.utc) - timedelta(days=31))
    mod_channel = FakeMessageable()
    guild = SimpleNamespace(
        id=honeypot.GUILD_ID,
        get_channel=lambda channel_id: mod_channel,
    )
    channel = SimpleNamespace(
        id=honeypot.HONEYPOT_CHANNEL_ID,
        set_permissions=AsyncMock(),
    )
    message = SimpleNamespace(
        author=member,
        channel=channel,
        guild=guild,
        webhook_id=None,
        delete=AsyncMock(),
    )
    bot = SimpleNamespace(user=SimpleNamespace(id=1))
    cog = HoneypotCog(bot)

    asyncio.run(cog.on_message(message))

    channel.set_permissions.assert_awaited_once_with(
        member,
        view_channel=False,
        reason="Honeypot channel post (member joined over 1 month ago)",
    )
    message.delete.assert_awaited_once_with()
    member.ban.assert_not_awaited()
    mod_channel.send.assert_not_awaited()
