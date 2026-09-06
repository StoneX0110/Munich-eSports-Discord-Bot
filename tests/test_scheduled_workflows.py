"""Common schedule workflows, through commands and real persistence."""

import asyncio
import json
from datetime import date
from unittest.mock import Mock

import pytest

from cogs import scheduled_polls as polls, scheduled_reminders as reminders


@pytest.fixture(params=['poll', 'reminder'])
def schedule(request, discord_env):
    if request.param == 'poll':
        cog = polls.ScheduledPollCog(discord_env.bot)
        return cog, cog.poll_create, cog.poll_list, cog.poll_delete, polls.POLLS_FILE, (discord_env.role, 'Mittwoch')
    cog = reminders.ScheduledReminderCog(discord_env.bot)
    return cog, cog.reminder_create, cog.reminder_list, cog.reminder_delete, reminders.REMINDERS_FILE, (discord_env.role, 'Mittwoch', 18, 'x' * 160)


def test_create_list_and_delete_large_schedule(discord_env, schedule):
    cog, create, listing, delete, path, args = schedule

    async def run():
        for _ in range(60):
            await create.callback(cog, discord_env.interaction(), *args)
        listed = discord_env.interaction()
        await listing.callback(cog, listed)
        pages = [c.kwargs['embed'] for c in listed.followup.send.await_args_list]
        assert len(pages) > 1
        assert all(len(page) <= 6000 and len(page.fields) <= 25 for page in pages)
        assert sum(len(page.fields) for page in pages) == 60
        assert pages[0].fields[0].name == '#1' and pages[-1].fields[-1].name == '#60'
        await delete.callback(cog, discord_env.interaction(), 60)
        records = json.loads(path.read_text())
        assert '60' not in records.get('scheduled_polls', records.get('scheduled_reminders'))
        missing = discord_env.interaction()
        await delete.callback(cog, missing, 60)
        calls = missing.followup.send.await_args_list + missing.response.send_message.await_args_list
        assert any('nicht gefunden' in call.args[0] for call in calls)

    asyncio.run(run())


@pytest.mark.parametrize('failure', ['corrupt', 'read', 'write'])
def test_storage_failure_preserves_schedules(discord_env, schedule, monkeypatch, failure):
    cog, create, _, delete, path, args = schedule

    async def run():
        await create.callback(cog, discord_env.interaction(), *args)
        if failure == 'corrupt':
            path.write_text('{broken')
        before = path.read_bytes()
        polls._polls_data_cache = None
        with monkeypatch.context() as fault:
            if failure == 'read':
                fault.setattr(type(path), 'read_text', Mock(side_effect=OSError('read failure')))
            elif failure == 'write':
                fault.setattr('utils.persistence.os.replace', Mock(side_effect=OSError('disk failure')))
            with pytest.raises((OSError, json.JSONDecodeError)):
                await delete.callback(cog, discord_env.interaction(), 1)
        assert path.read_bytes() == before
        assert not list(path.parent.glob('*.tmp'))
        if failure == 'write' and isinstance(cog, polls.ScheduledPollCog):
            await cog.scheduled_poll_flush_loop.coro(cog)
            assert not json.loads(path.read_text())['scheduled_polls']

    asyncio.run(run())


def test_dry_run_preserves_config_and_suppresses_delivery(discord_env, schedule):
    cog, create, _, _, path, args = schedule

    async def run():
        await create.callback(cog, discord_env.interaction(), *args)
        before = path.read_bytes()
        discord_env.bot.dry_run = True
        if isinstance(cog, polls.ScheduledPollCog):
            await cog._handle_posting(date(2026, 5, 27))
            await cog._handle_reminders(date(2026, 5, 27), 18)
        else:
            await cog._handle_sending(date(2026, 5, 27), 18)
        discord_env.channel.send.assert_not_awaited()
        assert path.read_bytes() == before

    asyncio.run(run())
