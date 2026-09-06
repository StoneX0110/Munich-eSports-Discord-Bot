"""Reminder command-to-storage-to-delivery workflows."""

import asyncio
import json
from datetime import date
from unittest.mock import AsyncMock

import discord
import pytest

import config
from cogs import scheduled_reminders
from cogs.scheduled_reminders import ScheduledReminderCog


@pytest.mark.parametrize('manager_role', [config.DEPARTMENT_HEAD_ROLE_ID, config.STAFF_ROLE_ID])
def test_reminder_lifecycle(discord_env, manager_role):
    env = discord_env

    async def run():
        cog = ScheduledReminderCog(env.bot)
        admin = env.interaction(roles=[manager_role])
        await cog.reminder_create.callback(cog, admin, env.role, 'mittwoch', 18, 'Scrim @everyone')
        stored = json.loads(scheduled_reminders.REMINDERS_FILE.read_text())
        assert stored['scheduled_reminders']['1']['weekday'] == 'Mittwoch'
        cog = ScheduledReminderCog(env.bot)  # Reload from disk, without a cached store.
        await cog._handle_sending(date(2026, 5, 26), 18)
        await cog._handle_sending(date(2026, 5, 27), 17)
        env.channel.send.assert_not_awaited()
        await cog._handle_sending(date(2026, 5, 27), 18)
        await cog._handle_sending(date(2026, 5, 27), 18)
        env.channel.send.assert_awaited_once()
        call = env.channel.send.call_args
        assert call.args[0] == '<@&200>\nScrim @everyone'
        assert call.kwargs['allowed_mentions'].to_dict() == {'roles': [200], 'parse': []}
        listed = env.interaction(roles=[manager_role])
        await cog.reminder_list.callback(cog, listed)
        text = listed.response.send_message.call_args.kwargs['embed'].fields[0].value
        assert '2026-05-27' in text and 'Scrim @everyone' in text and '18:00' in text
        await cog.trigger_send.callback(cog, env.interaction(roles=[manager_role]), 1)
        assert env.channel.send.await_count == 2
        await cog.reminder_delete.callback(cog, env.interaction(roles=[manager_role]), 1)
        assert not json.loads(scheduled_reminders.REMINDERS_FILE.read_text())['scheduled_reminders']
        missing = env.interaction(roles=[manager_role])
        await cog.trigger_send.callback(cog, missing, 1)
        assert 'nicht gefunden' in missing.followup.send.call_args.args[0]

    asyncio.run(run())


@pytest.mark.parametrize('overrides', [
    {'roles': []}, {'weekday': 'Sunday'}, {'hour': -1}, {'hour': 24},
    {'message': '  '}, {'message': 'x' * 2000},
])
def test_invalid_reminder_never_reaches_storage(discord_env, overrides):
    args = {'weekday': 'Mittwoch', 'hour': 18, 'message': 'Scrim', **overrides}
    admin = discord_env.interaction(roles=args.pop('roles', None))
    cog = ScheduledReminderCog(discord_env.bot)
    asyncio.run(cog.reminder_create.callback(cog, admin, discord_env.role, **args))
    assert admin.response.send_message.call_args.args[0].startswith('❌')
    assert not scheduled_reminders.REMINDERS_FILE.exists()
    discord_env.channel.send.assert_not_awaited()


@pytest.mark.parametrize('failure', ['missing_channel', 'missing_role', 'discord', 'dry_run'])
def test_unsent_reminder_remains_retryable(discord_env, failure):
    env = discord_env

    async def run():
        cog = ScheduledReminderCog(env.bot)
        await cog.reminder_create.callback(cog, env.interaction(), env.role, 'Mittwoch', 18, 'Scrim')
        if failure == 'missing_channel':
            env.bot.get_channel = lambda _: None
        elif failure == 'missing_role':
            env.bot.get_guild = lambda _: None
        elif failure == 'discord':
            env.channel.send.side_effect = discord.Forbidden(AsyncMock(), 'denied')
        else:
            env.bot.dry_run = True
        await cog.trigger_send.callback(cog, env.interaction(), 1)
        record = json.loads(scheduled_reminders.REMINDERS_FILE.read_text())['scheduled_reminders']['1']
        assert record['last_sent_date'] is None
        if failure != 'discord':
            env.channel.send.assert_not_awaited()

    asyncio.run(run())
