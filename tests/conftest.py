"""Discord transport doubles; workflows use real commands, views and JSON stores."""

import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
import discord

import config
from cogs import scheduled_polls, scheduled_reminders, voting


@pytest.fixture
def discord_env(tmp_path, monkeypatch):
    for module, name in ((scheduled_polls, 'POLLS_FILE'), (scheduled_reminders, 'REMINDERS_FILE'), (voting, 'VOTES_FILE')):
        monkeypatch.setattr(module, name, tmp_path / f'{name}.json')
    scheduled_polls._polls_data_cache = None
    scheduled_polls._polls_data_dirty = False
    scheduled_polls._polls_data_lock = asyncio.Lock()
    scheduled_reminders._reminders_data_lock = asyncio.Lock()
    voting._data_lock = asyncio.Lock()
    for state in (scheduled_polls._message_update_tasks, scheduled_polls._message_update_revisions,
                  scheduled_polls._message_edit_locks, voting._active_voters, voting._display_tasks,
                  voting._display_pending, voting._display_locks):
        state.clear()

    messages = {}

    def new_message(content=None, *, embed=None, view=None, **kwargs):
        message = NS(id=len(messages) + 1, content=content, embeds=[embed] if embed else [], view=view)

        async def edit(**changes):
            if 'embed' in changes:
                message.embeds = [changes['embed']]
            if changes.get('view', discord.utils.MISSING) is not discord.utils.MISSING:
                message.view = changes['view']
            return message

        message.edit = AsyncMock(side_effect=edit)
        messages[message.id] = message
        return message

    role = NS(id=200, mention='<@&200>', members=[])
    channel = NS(id=100, send=AsyncMock(side_effect=new_message),
                 fetch_message=AsyncMock(side_effect=lambda message_id: messages[message_id]))
    bot = NS(dry_run=False, get_channel=lambda _: channel,
             get_guild=lambda _: NS(get_role=lambda _: role), add_view=Mock(), wait_until_ready=AsyncMock())

    def interaction(user_id=42, *, roles=None, message=None, values=None):
        response = NS(is_done=Mock(return_value=False), edit_message=AsyncMock())
        result = NS(user=NS(id=user_id, roles=[NS(id=i) for i in (
            roles if roles is not None else [config.DEPARTMENT_HEAD_ROLE_ID, config.MEMBERSHIP_ROLE_ID, role.id]
        )], mention=f'<@{user_id}>', display_name='Member'), channel_id=channel.id,
            client=bot, response=response, message=message, data={'values': values or ['0']},
            delete_original_response=AsyncMock())

        async def respond(*args, **kwargs):
            response.is_done.return_value = True
            result.original_response.return_value = new_message(*args, **kwargs)

        async def defer(**kwargs):
            response.is_done.return_value = True

        result.original_response = AsyncMock()
        response.send_message = AsyncMock(side_effect=respond)
        response.defer = AsyncMock(side_effect=defer)
        response.edit_message = AsyncMock(side_effect=defer)
        result.followup = NS(send=AsyncMock(side_effect=new_message))
        return result

    return NS(bot=bot, role=role, channel=channel, messages=messages, interaction=interaction)
