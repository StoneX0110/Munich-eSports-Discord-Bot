import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from cogs import voting


def election_data(*, delegated=0, active=True):
    return {
        "next_session_id": 2,
        "next_vote_id": 2,
        "sessions": {"1": {"active": True, "department": None, "delegated_votes": {"42": delegated}}},
        "votes": {
            "1": {
                "session_id": "1", "title": "Board", "options": ["A", "B"],
                "channel_id": 10, "message_id": 20, "tallies": {"A": 0, "B": 0},
                "votes_used": {}, "active": active,
            }
        },
    }


def interaction(user_id=42, *, done=False):
    response = SimpleNamespace(
        is_done=Mock(return_value=done),
        send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock(),
    )
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, roles=[]), response=response,
        followup=SimpleNamespace(send=AsyncMock()), client=SimpleNamespace(get_channel=Mock(return_value=None)),
        data={"values": ["0"]}, delete_original_response=AsyncMock(),
    )


@pytest.fixture(autouse=True)
def reset_globals(tmp_path, monkeypatch):
    monkeypatch.setattr(voting, "VOTES_FILE", tmp_path / "votes.json")
    voting._active_voters.clear()
    voting._display_tasks.clear()
    voting._display_locks.clear()


def test_dialog_failure_cleans_reservation_and_old_timeout_cannot_clear_newer(monkeypatch):
    token1, token2 = object(), object()
    old = voting.VoteSelectView("1", 2, "42", ["A", "B"], token1)
    new = voting.VoteSelectView("1", 2, "42", ["A", "B"], token2)
    voting._active_voters["1"] = {"42": token2}
    asyncio.run(old.on_timeout())
    assert voting._active_voters["1"]["42"] is token2

    new.selected_option = "A"
    ix = interaction(done=True)
    ix.data = {"values": ["1"]}
    monkeypatch.setattr(voting, "_record_votes", AsyncMock(side_effect=RuntimeError("failed")))
    with pytest.raises(RuntimeError):
        asyncio.run(new.on_count_select(ix))
    assert "1" not in voting._active_voters


def test_concurrent_counter_updates_are_coalesced():
    voting._save_data(election_data())
    message = SimpleNamespace(
        embeds=[discord.Embed(description="**Status:** 🟢 Offen\n🗳️ **Abgegebene Stimmen:** 0")],
        edit=AsyncMock(),
    )
    channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
    bot = SimpleNamespace(get_channel=Mock(return_value=channel))

    async def refresh_many():
        await asyncio.gather(*(voting._update_vote_embed(bot, "1") for _ in range(100)))

    asyncio.run(refresh_many())
    channel.fetch_message.assert_awaited_once_with(20)
    message.edit.assert_awaited_once()


def test_vote_arriving_during_counter_edit_is_not_dropped():
    voting._save_data(election_data())

    async def run():
        editing, release = asyncio.Event(), asyncio.Event()
        descriptions = []

        async def edit(**kwargs):
            descriptions.append(kwargs['embed'].description)
            if len(descriptions) == 1:
                editing.set()
                await release.wait()

        message = SimpleNamespace(
            embeds=[discord.Embed(description='🗳️ **Abgegebene Stimmen:** 0')], edit=edit,
        )
        bot = SimpleNamespace(get_channel=lambda _: SimpleNamespace(fetch_message=AsyncMock(return_value=message)))
        first = asyncio.create_task(voting._update_vote_embed(bot, '1'))
        await editing.wait()
        data = voting._load_data()
        data['votes']['1']['tallies']['A'] = 1
        voting._save_data(data)
        second = asyncio.create_task(voting._update_vote_embed(bot, '1'))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        assert descriptions[-1].endswith('1')

    asyncio.run(run())


def test_failed_initial_defer_releases_dialog_reservation():
    data = election_data()
    data['sessions']['1']['department'] = 'Valorant'
    voting._save_data(data)
    ix = interaction()
    ix.response.defer.side_effect = RuntimeError('request failed')
    with pytest.raises(RuntimeError):
        asyncio.run(voting.VoteView('1').vote_button.callback(ix))
    assert not voting._active_voters


@pytest.mark.parametrize('save_fails', [False, True], ids=['durable-ballots', 'retry-after-disk-failure'])
def test_election_lifecycle(discord_env, monkeypatch, save_fails):
    env = discord_env

    async def run():
        cog = voting.VotingCog(env.bot)
        await cog.session_start.callback(cog, env.interaction())
        await cog.session_delegate.callback(cog, env.interaction(), 1, env.interaction().user, 2)
        start = env.interaction()
        await cog.vote_start.callback(cog, start, 1, 'Board', 'A, B')
        message = start.original_response.return_value

        async def ballot():
            opened = env.interaction()
            await message.view.vote_button.callback(opened)
            dialog = opened.original_response.return_value.view
            await dialog.on_option_select(env.interaction(values=['0']))
            submitted = env.interaction(values=['2'])
            await asyncio.gather(dialog.on_count_select(submitted),
                                 dialog.on_count_select(env.interaction(values=['2'])))
            return submitted

        if save_fails:
            before = voting.VOTES_FILE.read_bytes()
            with monkeypatch.context() as fault:
                fault.setattr('utils.persistence.os.replace', Mock(side_effect=OSError('disk full')))
                failed = await ballot()
            assert 'nicht gespeichert' in failed.followup.send.call_args.args[0]
            assert voting.VOTES_FILE.read_bytes() == before
            assert not list(voting.VOTES_FILE.parent.glob('*.tmp'))
            assert not voting._active_voters
        submitted = await ballot()
        assert submitted.followup.send.call_args.args[0].startswith('✅')
        saved = json.loads(voting.VOTES_FILE.read_text())  # Observe durable storage, not an internal cache.
        assert saved['votes']['1']['tallies'] == {'A': 3, 'B': 0}
        assert saved['votes']['1']['votes_used'] == {'42': 3}
        assert 'Stimmen:** 3' in message.embeds[0].description
        assert not voting._active_voters
        reopened = env.interaction()
        await message.view.vote_button.callback(reopened)
        assert 'alle deine Stimmen' in reopened.response.send_message.call_args.args[0]
        blocked = env.interaction()
        await cog.session_end.callback(cog, blocked, 1)
        assert 'offene Abstimmungen' in blocked.response.send_message.call_args.args[0]
        close = env.interaction()
        await cog.vote_close.callback(cog, close, 1)
        assert '3 Stimmen von 1 Wählern' in close.response.send_message.call_args.kwargs['embed'].description
        assert message.view is None and 'Geschlossen' in message.embeds[0].description
        await cog.session_end.callback(cog, env.interaction(), 1)
        saved = json.loads(voting.VOTES_FILE.read_text())
        assert not saved['sessions']['1']['active'] and not saved['votes']['1']['active']

    asyncio.run(run())


def test_unreadable_election_rejects_voters_without_overwriting_state(discord_env):
    env = discord_env

    async def run():
        cog = voting.VotingCog(env.bot)
        await cog.session_start.callback(cog, env.interaction())
        start = env.interaction()
        await cog.vote_start.callback(cog, start, 1, 'Board', 'A, B')
        voting.VOTES_FILE.write_text('{broken')
        ix = env.interaction()
        await start.original_response.return_value.view.vote_button.callback(ix)
        assert 'nicht sicher gelesen' in ix.response.send_message.call_args.args[0]
        assert voting.VOTES_FILE.read_text() == '{broken'

    asyncio.run(run())
