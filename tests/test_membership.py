"""Membership workflows with only easyVerein/Discord transport mocked."""

import asyncio
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from easyverein.models import CustomField
from easyverein.models.custom_field_select_option import CustomFieldSelectOption

import bot as app
import config
from cogs.department import DepartmentCog
from cogs import voting


@pytest.fixture
def inline_api(monkeypatch):
    async def run(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', run)


def test_department_count_uses_date_only_queries_and_deduplicates(discord_env, inline_api):
    env = discord_env
    member = NS(id=1, customFields=[NS(customField=config.ABTEILUNGEN_FIELD_ID,
                                     selectedOptions=[NS(value='Counter Strike')])])
    get_all = Mock(side_effect=[[member], [member, NS(id=2, customFields=[])]])
    env.bot.ev_client = NS(member=NS(get_all=get_all))
    cog = DepartmentCog(env.bot)
    interaction = env.interaction(roles=[615553053042540564])
    asyncio.run(cog.abteilung_mitglieder.callback(cog, interaction))
    assert '**1** aktive Mitglieder' in interaction.followup.send.call_args.args[0]
    filters = [call.kwargs['search'].model_dump(exclude_unset=True, by_alias=True)
               for call in get_all.call_args_list]
    assert len(filters) == 2 and filters[0]['resignationDate__isnull'] is True
    assert date.fromisoformat(filters[1]['resignationDate__gte'])
    assert all(f['_isApplication'] is False for f in filters)


@pytest.mark.parametrize('resignation', ['none', 'today-date', 'today-datetime', 'past-date', 'past-datetime'])
def test_department_election_checks_membership_before_opening_dialog(discord_env, inline_api, resignation):
    env = discord_env
    today = datetime.now(voting.ZoneInfo('Europe/Berlin')).date()
    end = None if resignation == 'none' else today - timedelta(days=resignation.startswith('past'))
    if resignation.endswith('datetime'):
        end = datetime.combine(end, datetime.min.time())
    member = NS(resignationDate=end, customFields=[NS(
        customField=CustomField.model_construct(id=config.ABTEILUNGEN_FIELD_ID),
        selectedOptions=[CustomFieldSelectOption.model_construct(value='Valorant')],
    )])
    ix = env.interaction()

    def fetch(**kwargs):
        assert ix.response.is_done(), 'API lookup must follow the interaction acknowledgement'
        return [member]

    env.bot.ev_client = NS(member=NS(get_all=Mock(side_effect=fetch)))

    async def run():
        cog = voting.VotingCog(env.bot)
        cog.department_choices = [discord.app_commands.Choice(name='Valorant', value='Valorant')]
        await cog.session_start.callback(cog, env.interaction(), 'Valorant')
        start = env.interaction()
        await cog.vote_start.callback(cog, start, 1, 'Board', 'A, B')
        await start.original_response.return_value.view.vote_button.callback(ix)
        if resignation.startswith('past'):
            assert 'kein Mitglied' in ix.followup.send.call_args.args[0]
        else:
            dialog = ix.followup.send.call_args.kwargs['view']
            assert isinstance(dialog, voting.VoteSelectView)
            await dialog.on_timeout()
        assert not voting._active_voters

    asyncio.run(run())


@pytest.mark.parametrize('state', ['first-run', 'known', 'corrupt'])
def test_daily_sync_preserves_membership_state_and_applies_roles(tmp_path, monkeypatch, inline_api, state):
    path = tmp_path / 'known_members.json'
    if state != 'first-run':
        path.write_text('broken' if state == 'corrupt' else '[1]')
    monkeypatch.setattr(app, 'KNOWN_MEMBERS_FILE', path)
    membership_role = NS(id=config.MEMBERSHIP_ROLE_ID)
    active = NS(id=10**17, roles=[], bot=False, add_roles=AsyncMock(), mention='<@active>')
    former = NS(id=10**17 + 1, roles=[membership_role], bot=False, remove_roles=AsyncMock())
    member = NS(id=1, joinDate=None, customFields=[NS(
        customField=CustomField.model_construct(id=config.DISCORD_ID_FIELD_ID), value=str(active.id),
    )])
    channel = NS(send=AsyncMock())
    guild = NS(members=[active, former], get_role=lambda _: membership_role,
               get_member=lambda uid: active if uid == active.id else None, get_channel=lambda _: channel)
    monkeypatch.setattr(app, 'bot', NS(dry_run=False, get_guild=lambda _: guild))
    monkeypatch.setattr(app, 'ev_client', NS(member=NS(get_all=Mock(side_effect=[[member], []]))))
    asyncio.run(app.daily_task.coro())
    active.add_roles.assert_awaited_once()
    former.remove_roles.assert_awaited_once()
    channel.send.assert_not_awaited()  # No duplicate/first-run welcome or fabricated anniversary.
    if state == 'corrupt':
        assert path.read_text() == 'broken'
    else:
        assert json.loads(path.read_text()) == [1]
