"""Recurring-poll workflows and distinct concurrency regressions."""

import asyncio
import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

import config
from cogs import scheduled_polls
from cogs.scheduled_polls import ScheduledPollCog, ScheduledPollView


@pytest.fixture(autouse=True)
def reset_scheduled_polls_cache():
    scheduled_polls._polls_data_cache = None
    scheduled_polls._polls_data_dirty = False
    scheduled_polls._message_update_tasks.clear()
    scheduled_polls._message_update_revisions.clear()
    scheduled_polls._message_edit_locks.clear()
    yield
    scheduled_polls._polls_data_cache = None
    scheduled_polls._polls_data_dirty = False


def _department_head_interaction():
    role = MagicMock()
    role.id = 748509968172449802  # DEPARTMENT_HEAD_ROLE_ID
    interaction = MagicMock()
    interaction.user.id = 123
    interaction.user.roles = [role]
    interaction.channel_id = 456
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def test_queued_click_from_replaced_message_is_rejected_after_lock_release():
    async def run():
        view = ScheduledPollView("1")
        interaction = MagicMock()
        interaction.user.id = 111
        interaction.user.roles = [MagicMock(id=200)]
        interaction.message.id = 999
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        data = {"next_scheduled_poll_id": 2, "scheduled_polls": {"1": {
            "role_id": 200,
            "active_instance": {"message_id": 1000, "target_week_start": "2026-06-01", "responses": {}},
        }}}
        with patch("cogs.scheduled_polls._load_polls_data", return_value=data):
            await view.make_callback("Montag")(interaction)
        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once_with(
            "❌ Diese Umfrage ist nicht mehr aktiv.", ephemeral=True
        )
        assert data["scheduled_polls"]["1"]["active_instance"]["responses"] == {}
    asyncio.run(run())


def test_post_failure_keeps_old_interactive_instance():
    async def run():
        bot = MagicMock()
        channel = MagicMock()
        channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "failed"))
        channel.fetch_message = AsyncMock()
        bot.get_channel.return_value = channel
        cog = ScheduledPollCog(bot)
        old = {"message_id": 999, "target_week_start": "2026-05-25", "responses": {}}
        data = {"next_scheduled_poll_id": 2, "scheduled_polls": {"1": {
            "channel_id": 100, "role_id": 200, "weekday": "Mittwoch",
            "active_instance": old,
        }}}
        with patch("cogs.scheduled_polls._load_polls_data", return_value=data), \
             patch("cogs.scheduled_polls._save_polls_data") as save:
            await cog._handle_posting(date(2026, 5, 27))
        assert data["scheduled_polls"]["1"]["active_instance"] == old
        channel.fetch_message.assert_not_awaited()
        save.assert_not_called()
    asyncio.run(run())


def test_concurrent_votes_coalesce_and_publish_latest_state():
    async def run():
        view = ScheduledPollView("1")
        message = MagicMock()
        message.id = 999
        message.edit = AsyncMock()
        data = {"next_scheduled_poll_id": 2, "scheduled_polls": {"1": {
            "role_id": 200,
            "active_instance": {"message_id": 999, "target_week_start": "2026-06-01", "responses": {}},
        }}}
        interactions = []
        for user_id in range(10**17, 10**17 + 100):
            interaction = MagicMock()
            interaction.user.id = user_id
            interaction.user.roles = [MagicMock(id=200)]
            interaction.message = message
            interaction.response.defer = AsyncMock()
            interaction.followup.send = AsyncMock()
            interactions.append(interaction)
        with patch("cogs.scheduled_polls._load_polls_data", return_value=data):
            await asyncio.gather(*(view.make_callback("Montag")(i) for i in interactions))
        assert len(data["scheduled_polls"]["1"]["active_instance"]["responses"]) == 100
        assert message.edit.await_count <= 2
        assert "Montag (01.06.) [100]" in message.edit.call_args.kwargs["embed"].description
        assert all(i.response.defer.await_count == 1 for i in interactions)
        for day in scheduled_polls.WEEKDAYS[1:]:
            await asyncio.gather(*(view.make_callback(day)(i) for i in interactions))
        embed = message.edit.call_args.kwargs['embed']
        assert len(embed.description) <= 4096
        assert embed.description.count('[100]') == 7
    asyncio.run(run())


def test_new_instance_survives_failed_old_message_archival():
    async def run():
        bot = MagicMock()
        channel = MagicMock()
        new_message = MagicMock(id=1000)
        channel.send = AsyncMock(return_value=new_message)
        channel.fetch_message = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "failed"))
        bot.get_channel.return_value = channel
        cog = ScheduledPollCog(bot)
        old = {"message_id": 999, "target_week_start": "2026-05-25", "responses": {}}
        data = {"next_scheduled_poll_id": 2, "scheduled_polls": {"1": {
            "channel_id": 100, "role_id": 200, "weekday": "Mittwoch", "active_instance": old,
        }}}
        with patch("cogs.scheduled_polls._load_polls_data", return_value=data), \
             patch("cogs.scheduled_polls._save_polls_data") as save:
            await cog._handle_posting(date(2026, 5, 27))
        assert data["scheduled_polls"]["1"]["active_instance"]["message_id"] == 1000
        save.assert_called_once()
        channel.fetch_message.assert_awaited_once_with(999)
    asyncio.run(run())


def test_vote_defers_before_waiting_for_data_lock():
    async def run():
        view = ScheduledPollView("1")
        interaction = MagicMock()
        interaction.user.id = 111
        interaction.user.roles = [MagicMock(id=200)]
        interaction.message.id = 999
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        data = {"next_scheduled_poll_id": 2, "scheduled_polls": {"1": {
            "role_id": 200,
            "active_instance": {"message_id": 1000, "target_week_start": "2026-06-01", "responses": {}},
        }}}
        with patch("cogs.scheduled_polls._load_polls_data", return_value=data):
            await scheduled_polls._polls_data_lock.acquire()
            task = asyncio.create_task(view.make_callback("Montag")(interaction))
            await asyncio.sleep(0)
            interaction.response.defer.assert_awaited_once()
            assert not task.done()
            scheduled_polls._polls_data_lock.release()
            await task
    asyncio.run(run())


def test_delete_defers_then_waits_for_poll_lifecycle():
    async def run():
        bot = MagicMock()
        cog = ScheduledPollCog(bot)
        interaction = _department_head_interaction()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        data = {"next_scheduled_poll_id": 2, "scheduled_polls": {"1": {
            "channel_id": 100, "role_id": 200, "active_instance": None,
        }}}
        lock = cog._lifecycle_locks["1"]
        await lock.acquire()
        with patch("cogs.scheduled_polls._load_polls_data", return_value=data), \
             patch("cogs.scheduled_polls._save_polls_data"):
            task = asyncio.create_task(ScheduledPollCog.poll_delete.callback(cog, interaction, 1))
            await asyncio.sleep(0)
            interaction.response.defer.assert_awaited_once_with(ephemeral=True)
            assert "1" in data["scheduled_polls"]
            lock.release()
            await task
        assert "1" not in data["scheduled_polls"]
        interaction.followup.send.assert_awaited_once()
    asyncio.run(run())


def test_vote_during_replacement_does_not_cancel_posting():
    async def run():
        bot = MagicMock()
        channel = MagicMock()
        bot.get_channel.return_value = channel
        old = {'message_id': 999, 'target_week_start': '2026-05-25', 'responses': {}}
        data = {'scheduled_polls': {'1': {
            'channel_id': 100, 'role_id': 200, 'weekday': 'Mittwoch', 'active_instance': old,
        }}}
        retired_message = MagicMock(edit=AsyncMock())
        channel.fetch_message = AsyncMock(return_value=retired_message)

        async def send(**kwargs):
            old['responses']['111'] = ['Montag']
            return MagicMock(id=1000)

        channel.send = send
        with patch.object(scheduled_polls, '_load_polls_data', return_value=data), \
             patch.object(scheduled_polls, '_save_polls_data'):
            await ScheduledPollCog(bot)._handle_posting(date(2026, 5, 27))
        assert data['scheduled_polls']['1']['active_instance']['message_id'] == 1000
        assert '<@111>' in retired_message.edit.call_args.kwargs['embed'].description
        assert retired_message.edit.call_args.kwargs['view'] is None

    asyncio.run(run())


@pytest.mark.parametrize(('manager_role', 'start', 'expected'), [
    (config.DEPARTMENT_HEAD_ROLE_ID, None, '2026-06-01'),
    (config.STAFF_ROLE_ID, 'Freitag', '2026-05-29'),
    (config.STAFF_ROLE_ID, 'Mittwoch', '2026-06-03'),
])
def test_weekly_poll_lifecycle(discord_env, manager_role, start, expected):
    env = discord_env

    async def run():
        cog = ScheduledPollCog(env.bot)
        await cog.poll_create.callback(cog, env.interaction(roles=[manager_role]), env.role,
                                       'mittwoch', 'Sonntag', 18, start)
        await cog._handle_posting(date(2026, 5, 26))
        env.channel.send.assert_not_awaited()
        await cog._handle_posting(date(2026, 5, 27))
        message = env.messages[max(env.messages)]
        assert expected == json.loads(scheduled_polls.POLLS_FILE.read_text())['scheduled_polls']['1']['active_instance']['target_week_start']
        assert message.view.children[0].label == (start or 'Montag')[:2]
        denied = env.interaction(roles=[], message=message)
        await message.view.make_callback('Montag')(denied)
        assert '❌' in denied.followup.send.call_args.args[0]
        for day in ['Montag', 'Dienstag', 'Dienstag', 'Keine Zeit', 'Keine Zeit', 'Mittwoch']:
            await message.view.make_callback(day)(env.interaction(message=message))
        assert '[1]' in message.embeds[0].description
        await cog.scheduled_poll_flush_loop.coro(cog)
        scheduled_polls._polls_data_cache = None  # Process restart: read the actual JSON again.
        await cog._handle_posting(date(2026, 5, 27))
        assert env.channel.send.await_count == 1
        instance = json.loads(scheduled_polls.POLLS_FILE.read_text())['scheduled_polls']['1']['active_instance']
        assert instance['responses'] == {'42': ['Mittwoch']}
        env.role.members = [env.interaction(i).user for i in [42, *range(10**17, 10**17 + 149)]]
        for member in env.role.members:
            member.bot = False
        await cog._handle_reminders(date(2026, 5, 31), 17)
        assert env.channel.send.await_count == 1
        await cog._handle_reminders(date(2026, 5, 31), 18)
        reminders = [call.args[0] for call in env.channel.send.await_args_list[1:]]
        assert len(reminders) > 1 and all(len(text) <= 2000 for text in reminders)
        assert '<@42>' not in ''.join(reminders) and f'<@{10**17 + 148}>' in ''.join(reminders)
        sent = env.channel.send.await_count
        await cog._handle_reminders(date(2026, 5, 31), 18)
        assert env.channel.send.await_count == sent
        old_view = message.view
        await cog._handle_posting(date(2026, 6, 3))
        assert message.view is None and 'Geschlossen' in message.embeds[0].title
        stale = env.interaction(message=message)
        await old_view.make_callback('Montag')(stale)
        assert 'nicht mehr aktiv' in stale.followup.send.call_args.args[0]
        await cog.poll_delete.callback(cog, env.interaction(roles=[manager_role]), 1)
        assert not json.loads(scheduled_polls.POLLS_FILE.read_text())['scheduled_polls']

    asyncio.run(run())


@pytest.mark.parametrize('overrides', [
    {'roles': []}, {'posting_day': 'Sunday'}, {'week_start_day': 'bad'},
    {'reminder_weekday': 'Sonntag'}, {'reminder_hour': 18},
    {'reminder_weekday': 'bad', 'reminder_hour': 18},
    {'reminder_weekday': 'Sonntag', 'reminder_hour': -1},
    {'reminder_weekday': 'Sonntag', 'reminder_hour': 24},
])
def test_invalid_poll_never_reaches_storage(discord_env, overrides):
    args = {'posting_day': 'Mittwoch', **overrides}
    admin = discord_env.interaction(roles=args.pop('roles', None))
    cog = ScheduledPollCog(discord_env.bot)
    asyncio.run(cog.poll_create.callback(cog, admin, discord_env.role, **args))
    assert admin.response.send_message.call_args.args[0].startswith('❌')
    assert not scheduled_polls.POLLS_FILE.exists()
