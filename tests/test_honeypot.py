import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

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


def test_honeypot_forwards_evidence_before_reporting_success(monkeypatch):
    monkeypatch.setattr(honeypot.discord.abc, "Messageable", FakeMessageable)
    cog, message, member, mod_channel = _scenario()
    events = []
    message.forward.side_effect = lambda _: events.append("forward")
    member.ban.side_effect = lambda **_: events.append("ban")
    mod_channel.send.side_effect = lambda text: events.append(text)
    asyncio.run(cog.on_message(message))
    assert events[:2] == ["forward", "ban"]
    assert "BANNED" in events[2]


def test_honeypot_reports_failed_ban(monkeypatch):
    monkeypatch.setattr(honeypot.discord.abc, "Messageable", FakeMessageable)
    cog, message, member, mod_channel = _scenario()
    member.ban.side_effect = honeypot.discord.Forbidden(AsyncMock(), "denied")
    asyncio.run(cog.on_message(message))
    assert "BAN FAILED" in mod_channel.send.call_args.args[0]


def test_honeypot_dry_run_never_bans(monkeypatch):
    monkeypatch.setattr(honeypot.discord.abc, "Messageable", FakeMessageable)
    cog, message, member, mod_channel = _scenario(dry_run=True)
    asyncio.run(cog.on_message(message))
    member.ban.assert_not_awaited()
    message.forward.assert_not_awaited()
    mod_channel.send.assert_not_awaited()


def test_failed_success_report_is_not_misreported_as_failed_ban(monkeypatch):
    monkeypatch.setattr(honeypot.discord.abc, 'Messageable', FakeMessageable)
    cog, message, member, mod_channel = _scenario()
    mod_channel.send.side_effect = honeypot.discord.Forbidden(AsyncMock(), 'cannot report')
    asyncio.run(cog.on_message(message))
    member.ban.assert_awaited_once()
    mod_channel.send.assert_awaited_once()
    assert 'BANNED' in mod_channel.send.call_args.args[0]
