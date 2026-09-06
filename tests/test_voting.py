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


def test_failed_ballot_write_is_reported_and_never_confirmed(monkeypatch):
    voting._save_data(election_data())
    before = voting.VOTES_FILE.read_bytes()
    monkeypatch.setattr(voting, "atomic_write_json", Mock(side_effect=OSError("disk full")))
    ix = interaction(done=True)

    assert not asyncio.run(voting._record_votes("1", "A", 1, ix))
    assert voting.VOTES_FILE.read_bytes() == before
    messages = [call.args[0] for call in ix.followup.send.await_args_list]
    assert any("nicht gespeichert" in message for message in messages)
    assert not any(message.startswith("✅") for message in messages)


def test_concurrent_ballots_are_durable_and_capped_at_limit(monkeypatch):
    voting._save_data(election_data(delegated=2))
    monkeypatch.setattr(voting, "_update_vote_embed", AsyncMock())
    first, second = interaction(done=True), interaction(done=True)

    async def record_both():
        return await asyncio.gather(
            voting._record_votes("1", "A", 2, first),
            voting._record_votes("1", "B", 2, second),
        )

    results = asyncio.run(record_both())

    assert results == [True, True]
    saved = voting._load_data()["votes"]["1"]
    assert sum(saved["tallies"].values()) == 3
    assert saved["votes_used"]["42"] == 3


def test_saved_ballots_survive_reload_and_corrupt_state_fails_closed():
    data = election_data()
    data["votes"]["1"]["tallies"]["A"] = 1
    data["votes"]["1"]["votes_used"]["42"] = 1
    voting._save_data(data)
    assert voting._load_data()["votes"]["1"]["tallies"]["A"] == 1

    voting.VOTES_FILE.write_text("{broken", encoding="utf-8")
    with pytest.raises(voting.VotingDataError):
        voting._load_data()


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


def test_closed_state_wins_over_stale_counter_refresh():
    data = election_data(active=False)
    data["votes"]["1"]["tallies"]["A"] = 4
    voting._save_data(data)
    embed = discord.Embed(description="**Status:** 🟢 Offen\n🗳️ **Abgegebene Stimmen:** 1")
    message = SimpleNamespace(embeds=[embed], edit=AsyncMock())
    channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
    bot = SimpleNamespace(get_channel=Mock(return_value=channel))

    asyncio.run(voting._refresh_vote_embed(bot, "1"))

    kwargs = message.edit.await_args.kwargs
    assert "🔴 Geschlossen" in kwargs["embed"].description
    assert "Stimmen:** 4" in kwargs["embed"].description
    assert kwargs["view"] is None


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


def test_department_check_defers_before_slow_api(monkeypatch):
    data = election_data()
    data["sessions"]["1"]["department"] = "Valorant"
    voting._save_data(data)
    ix = interaction()
    observed = []
    ix.response.defer.side_effect = lambda **kwargs: observed.append("defer")

    def get_all(**kwargs):
        return []

    async def to_thread(function, **kwargs):
        observed.append("api")
        return function(**kwargs)

    ix.client.ev_client = SimpleNamespace(member=SimpleNamespace(get_all=get_all))
    monkeypatch.setattr(voting.asyncio, "to_thread", to_thread)
    monkeypatch.setattr(voting, "MemberFilter", Mock(return_value=object()))
    view = voting.VoteView("1")
    asyncio.run(asyncio.wait_for(view.vote_button.callback(ix), timeout=1))
    assert observed == ["defer", "api"]
    ix.followup.send.assert_awaited()


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
