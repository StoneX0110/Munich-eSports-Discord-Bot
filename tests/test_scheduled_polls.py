"""
Unit and integration tests for scheduled polls helper and logic.
"""

import asyncio
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo
import pytest

from scheduled_polls import (
    ScheduledPollCog,
    ScheduledPollView,
    _load_polls_data,
    _save_polls_data,
    _is_department_head,
    _normalize_weekday,
    _get_target_dates,
    _build_poll_embed,
    setup,
)

def test_load_polls_data_missing(tmp_path):
    """Verify that missing poll configuration file defaults gracefully."""
    test_file = tmp_path / "non_existent.json"
    with patch("scheduled_polls.POLLS_FILE", test_file):
        data = _load_polls_data()
        assert data["next_scheduled_poll_id"] == 1
        assert data["scheduled_polls"] == {}

def test_load_polls_data_missing_returns_independent_default(tmp_path):
    """Verify fallback data cannot mutate the module-level default."""
    test_file = tmp_path / "non_existent.json"
    with patch("scheduled_polls.POLLS_FILE", test_file):
        data = _load_polls_data()
        data["scheduled_polls"]["1"] = {"weekday": "Montag"}
        fresh_data = _load_polls_data()

    assert fresh_data["scheduled_polls"] == {}

def test_save_and_load_polls_data(tmp_path):
    """Verify that save and load roundtrip maintains dict structure and values."""
    test_file = tmp_path / "test_polls.json"
    test_data = {
        "next_scheduled_poll_id": 42,
        "scheduled_polls": {
            "1": {
                "channel_id": 12345,
                "question": "Fav game?",
                "options": ["LoL", "CS"]
            }
        }
    }

    with patch("scheduled_polls.POLLS_FILE", test_file):
        _save_polls_data(test_data)
        loaded_data = _load_polls_data()
        assert loaded_data == test_data

def test_load_polls_data_corrupt(tmp_path, caplog):
    """Verify corrupt JSON logs the error and gracefully defaults."""
    test_file = tmp_path / "test_polls_corrupt.json"
    test_file.write_text("invalid json string", encoding="utf-8")

    with patch("scheduled_polls.POLLS_FILE", test_file):
        with caplog.at_level("ERROR"):
            data = _load_polls_data()
            assert data["next_scheduled_poll_id"] == 1
            assert data["scheduled_polls"] == {}
            assert any("Corrupt scheduled polls file detected" in record.message for record in caplog.records)

def test_load_polls_data_io_error():
    """Verify that OSError is raised when reading the polls file fails."""
    mock_file = MagicMock()
    mock_file.exists.return_value = True
    mock_file.read_text.side_effect = OSError("Read error")
    with patch("scheduled_polls.POLLS_FILE", mock_file):
        with pytest.raises(OSError):
            _load_polls_data()

def test_save_polls_data_io_error():
    """Verify that OSError is raised when writing the polls file fails."""
    mock_file = MagicMock()
    mock_file.write_text.side_effect = OSError("Write error")
    with patch("scheduled_polls.POLLS_FILE", mock_file):
        with pytest.raises(OSError):
            _save_polls_data({})


# ---------------------------------------------------------------------------
# Tests for _is_department_head
# ---------------------------------------------------------------------------

def test_is_department_head_true():
    """Verify department head detection with matching role."""
    member = MagicMock()
    role = MagicMock()
    role.id = 748509968172449802  # DEPARTMENT_HEAD_ROLE_ID
    member.roles = [role]
    assert _is_department_head(member) is True

def test_is_department_head_false():
    """Verify non-department-head is rejected."""
    member = MagicMock()
    role = MagicMock()
    role.id = 999999999
    member.roles = [role]
    assert _is_department_head(member) is False


# ---------------------------------------------------------------------------
# Tests for _normalize_weekday
# ---------------------------------------------------------------------------

def test_normalize_weekday_valid():
    """Verify valid weekday names are normalized correctly."""
    assert _normalize_weekday("mittwoch") == "Mittwoch"
    assert _normalize_weekday("Montag") == "Montag"
    assert _normalize_weekday("FREITAG") == "Freitag"

def test_normalize_weekday_invalid():
    """Verify invalid weekday names return None."""
    assert _normalize_weekday("Wednesday") is None
    assert _normalize_weekday("notaday") is None
    assert _normalize_weekday("") is None


# ---------------------------------------------------------------------------
# Tests for _get_target_dates and _build_poll_embed
# ---------------------------------------------------------------------------

def test_get_target_dates():
    # Post day is Wednesday 2026-05-27. Target week starts Monday 2026-06-01
    post_day = date(2026, 5, 27)
    target_monday, days = _get_target_dates(post_day)
    assert target_monday == date(2026, 6, 1)
    assert len(days) == 7
    assert days[0] == "01.06."
    assert days[6] == "07.06."

def test_build_poll_embed():
    responses = {
        "123": ["Montag", "Mittwoch"],
        "456": ["Keine Zeit"]
    }
    embed = _build_poll_embed("123456", "2026-06-01", responses)
    assert "Rolle:" not in embed.description
    assert "<@&123456>" not in embed.description
    assert "Montag (01.06.) [1]" in embed.description
    assert "Dienstag (02.06.) [0]" in embed.description
    assert "Keine Zeit [1]" in embed.description


# ---------------------------------------------------------------------------
# Tests for scheduled poll creation
# ---------------------------------------------------------------------------

def _department_head_interaction():
    role = MagicMock()
    role.id = 748509968172449802  # DEPARTMENT_HEAD_ROLE_ID
    interaction = MagicMock()
    interaction.user.id = 123
    interaction.user.roles = [role]
    interaction.channel_id = 456
    interaction.response.send_message = AsyncMock()
    return interaction


def _poll_role():
    role = MagicMock()
    role.id = 200
    role.mention = "<@&200>"
    return role


def test_poll_create_without_reminder_stores_no_reminder_config():
    async def run():
        bot = MagicMock()
        cog = ScheduledPollCog(bot)
        interaction = _department_head_interaction()
        data = {"next_scheduled_poll_id": 1, "scheduled_polls": {}}

        with patch("scheduled_polls._load_polls_data", return_value=data):
            save_mock = MagicMock()
            with patch("scheduled_polls._save_polls_data", save_mock):
                await ScheduledPollCog.poll_create.callback(
                    cog,
                    interaction,
                    _poll_role(),
                    "mittwoch",
                )

        saved_poll = save_mock.call_args[0][0]["scheduled_polls"]["1"]
        assert saved_poll["weekday"] == "Mittwoch"
        assert saved_poll["reminder_weekday"] is None
        assert saved_poll["reminder_hour"] is None
        response = interaction.response.send_message.call_args[0][0]
        assert "**Reminder:** keine" in response

    asyncio.run(run())


def test_poll_create_with_valid_reminder_stores_reminder_config():
    async def run():
        bot = MagicMock()
        cog = ScheduledPollCog(bot)
        interaction = _department_head_interaction()
        data = {"next_scheduled_poll_id": 1, "scheduled_polls": {}}

        with patch("scheduled_polls._load_polls_data", return_value=data):
            save_mock = MagicMock()
            with patch("scheduled_polls._save_polls_data", save_mock):
                await ScheduledPollCog.poll_create.callback(
                    cog,
                    interaction,
                    _poll_role(),
                    "Mittwoch",
                    "sonntag",
                    18,
                )

        saved_poll = save_mock.call_args[0][0]["scheduled_polls"]["1"]
        assert saved_poll["reminder_weekday"] == "Sonntag"
        assert saved_poll["reminder_hour"] == 18
        response = interaction.response.send_message.call_args[0][0]
        assert "**Reminder:** Sonntag um 18:00" in response

    asyncio.run(run())


def test_poll_create_rejects_partial_reminder_config():
    async def run():
        bot = MagicMock()
        cog = ScheduledPollCog(bot)
        interaction = _department_head_interaction()

        await ScheduledPollCog.poll_create.callback(
            cog,
            interaction,
            _poll_role(),
            "Mittwoch",
            "Sonntag",
            None,
        )

        interaction.response.send_message.assert_called_once()
        message = interaction.response.send_message.call_args[0][0]
        assert "Reminder-Wochentag und Reminder-Uhrzeit" in message
        assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True

    asyncio.run(run())


def test_poll_create_rejects_invalid_reminder_weekday():
    async def run():
        bot = MagicMock()
        cog = ScheduledPollCog(bot)
        interaction = _department_head_interaction()

        await ScheduledPollCog.poll_create.callback(
            cog,
            interaction,
            _poll_role(),
            "Mittwoch",
            "Wednesday",
            18,
        )

        interaction.response.send_message.assert_called_once()
        message = interaction.response.send_message.call_args[0][0]
        assert "Ungültiger Reminder-Wochentag" in message
        assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True

    asyncio.run(run())


def test_poll_create_rejects_invalid_reminder_hour():
    async def run():
        bot = MagicMock()
        cog = ScheduledPollCog(bot)
        interaction = _department_head_interaction()

        await ScheduledPollCog.poll_create.callback(
            cog,
            interaction,
            _poll_role(),
            "Mittwoch",
            "Sonntag",
            24,
        )

        interaction.response.send_message.assert_called_once()
        message = interaction.response.send_message.call_args[0][0]
        assert "Reminder-Uhrzeit" in message
        assert "`0` bis `23`" in message
        assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True

    asyncio.run(run())


def test_poll_list_shows_reminder_schedule():
    async def run():
        bot = MagicMock()
        cog = ScheduledPollCog(bot)
        interaction = _department_head_interaction()
        data = {
            "next_scheduled_poll_id": 3,
            "scheduled_polls": {
                "1": {
                    "channel_id": 100,
                    "role_id": 200,
                    "weekday": "Mittwoch",
                    "reminder_weekday": "Sonntag",
                    "reminder_hour": 5,
                },
                "2": {
                    "channel_id": 101,
                    "role_id": 201,
                    "weekday": "Freitag",
                    "reminder_weekday": None,
                    "reminder_hour": None,
                },
            },
        }

        with patch("scheduled_polls._load_polls_data", return_value=data):
            await ScheduledPollCog.poll_list.callback(cog, interaction)

        embed = interaction.response.send_message.call_args.kwargs["embed"]
        first, second = embed.fields
        assert "**Reminder:** Sonntag um 05:00" in first.value
        assert "**Reminder:** keine" in second.value
        assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Tests for background scheduling
# ---------------------------------------------------------------------------

def test_handle_reminders_skips_poll_without_reminder_config():
    async def run():
        bot = MagicMock()
        cog = ScheduledPollCog(bot)
        channel = AsyncMock()
        bot.get_channel.return_value = channel

        data = {
            "next_scheduled_poll_id": 2,
            "scheduled_polls": {
                "1": {
                    "channel_id": 100,
                    "role_id": 200,
                    "weekday": "Mittwoch",
                    "reminder_weekday": None,
                    "reminder_hour": None,
                    "active_instance": {
                        "message_id": 999,
                        "posted_at": "2026-05-27T08:00:00+02:00",
                        "target_week_start": "2026-06-01",
                        "reminded": False,
                        "responses": {},
                    },
                }
            },
        }
        with patch("scheduled_polls._load_polls_data", return_value=data):
            await cog._handle_reminders(date(2026, 5, 31), 18)  # Sunday

        channel.send.assert_not_called()

    asyncio.run(run())


def test_handle_reminders_skips_mismatched_reminder_time():
    async def run():
        bot = MagicMock()
        cog = ScheduledPollCog(bot)
        channel = AsyncMock()
        bot.get_channel.return_value = channel

        data = {
            "next_scheduled_poll_id": 2,
            "scheduled_polls": {
                "1": {
                    "channel_id": 100,
                    "role_id": 200,
                    "weekday": "Mittwoch",
                    "reminder_weekday": "Sonntag",
                    "reminder_hour": 18,
                    "active_instance": {
                        "message_id": 999,
                        "posted_at": "2026-05-27T08:00:00+02:00",
                        "target_week_start": "2026-06-01",
                        "reminded": False,
                        "responses": {},
                    },
                }
            },
        }
        with patch("scheduled_polls._load_polls_data", return_value=data):
            await cog._handle_reminders(date(2026, 5, 31), 17)  # Sunday, wrong hour
            await cog._handle_reminders(date(2026, 5, 30), 18)  # Saturday, right hour

        channel.send.assert_not_called()

    asyncio.run(run())


def test_scheduled_poll_loop_runs_hourly_checks():
    async def run():
        bot = MagicMock()
        cog = ScheduledPollCog(bot)

        with patch("scheduled_polls.datetime") as datetime_mock:
            now = datetime(2026, 5, 31, 18, 42, tzinfo=ZoneInfo("Europe/Berlin"))
            datetime_mock.now.return_value = now
            cog._handle_posting = AsyncMock()
            cog._handle_reminders = AsyncMock()

            await cog.scheduled_poll_loop.coro(cog)

        cog._handle_posting.assert_not_called()
        cog._handle_reminders.assert_called_once_with(date(2026, 5, 31), 18)

    asyncio.run(run())


def test_scheduled_poll_loop_uses_hourly_interval():
    loop = ScheduledPollCog.scheduled_poll_loop
    assert loop.hours == 1.0
    assert loop.minutes == 0.0
    assert loop.seconds == 0.0
    assert loop.time is None


def test_scheduled_poll_loop_posts_only_during_eight_o_clock_hour():
    async def run():
        bot = MagicMock()
        cog = ScheduledPollCog(bot)

        with patch("scheduled_polls.datetime") as datetime_mock:
            now = datetime(2026, 5, 27, 8, 13, tzinfo=ZoneInfo("Europe/Berlin"))
            datetime_mock.now.return_value = now
            cog._handle_posting = AsyncMock()
            cog._handle_reminders = AsyncMock()

            await cog.scheduled_poll_loop.coro(cog)

        cog._handle_posting.assert_called_once_with(date(2026, 5, 27))
        cog._handle_reminders.assert_called_once_with(date(2026, 5, 27), 8)

    asyncio.run(run())


def test_handle_posting_on_matching_weekday(tmp_path):
    async def run():
        bot = MagicMock()
        cog = ScheduledPollCog(bot)
        channel = AsyncMock()
        sent_msg = MagicMock()
        sent_msg.id = 555
        channel.send = AsyncMock(return_value=sent_msg)
        bot.get_channel.return_value = channel

        polls_file = tmp_path / "polls.json"
        data = {
            "next_scheduled_poll_id": 2,
            "scheduled_polls": {
                "1": {
                    "channel_id": 100,
                    "role_id": 200,
                    "weekday": "Mittwoch",
                    "created_by": 1,
                    "created_at": "2026-05-23T08:00:00+02:00",
                    "reminder_weekday": None,
                    "reminder_hour": None,
                    "active_instance": None,
                }
            },
        }

        with patch("scheduled_polls.POLLS_FILE", polls_file):
            with patch("scheduled_polls._load_polls_data", return_value=data.copy()):
                save_mock = MagicMock()
                with patch("scheduled_polls._save_polls_data", save_mock):
                    await cog._handle_posting(date(2026, 5, 27))  # Wednesday

        channel.send.assert_called_once()
        save_mock.assert_called_once()
        saved = save_mock.call_args[0][0]
        instance = saved["scheduled_polls"]["1"]["active_instance"]
        assert instance is not None
        assert instance["message_id"] == 555
        assert instance["target_week_start"] == "2026-06-01"
        assert instance["reminded"] is False
        assert instance["responses"] == {}

    asyncio.run(run())

def test_handle_posting_skips_existing_instance_for_same_target_week():
    async def run():
        bot = MagicMock()
        cog = ScheduledPollCog(bot)
        channel = AsyncMock()
        bot.get_channel.return_value = channel

        data = {
            "next_scheduled_poll_id": 2,
            "scheduled_polls": {
                "1": {
                    "channel_id": 100,
                    "role_id": 200,
                    "weekday": "Mittwoch",
                    "created_by": 1,
                    "created_at": "2026-05-23T08:00:00+02:00",
                    "reminder_weekday": "Sonntag",
                    "reminder_hour": 18,
                    "active_instance": {
                        "message_id": 999,
                        "posted_at": "2026-05-27T08:05:00+02:00",
                        "target_week_start": "2026-06-01",
                        "reminded": False,
                        "responses": {},
                    },
                }
            },
        }

        with patch("scheduled_polls._load_polls_data", return_value=data):
            save_mock = MagicMock()
            with patch("scheduled_polls._save_polls_data", save_mock):
                await cog._handle_posting(date(2026, 5, 27))  # Wednesday

        channel.send.assert_not_called()
        save_mock.assert_not_called()

    asyncio.run(run())

def test_handle_posting_uses_poll_data_lock():
    async def run():
        class FakeAsyncLock:
            def __init__(self):
                self.enter_count = 0

            async def __aenter__(self):
                self.enter_count += 1

            async def __aexit__(self, exc_type, exc, tb):
                return False

        bot = MagicMock()
        cog = ScheduledPollCog(bot)
        channel = AsyncMock()
        sent_msg = MagicMock()
        sent_msg.id = 555
        channel.send = AsyncMock(return_value=sent_msg)
        bot.get_channel.return_value = channel

        data = {
            "next_scheduled_poll_id": 2,
            "scheduled_polls": {
                "1": {
                    "channel_id": 100,
                    "role_id": 200,
                    "weekday": "Mittwoch",
                    "created_by": 1,
                    "created_at": "2026-05-23T08:00:00+02:00",
                    "reminder_weekday": None,
                    "reminder_hour": None,
                    "active_instance": None,
                }
            },
        }
        fake_lock = FakeAsyncLock()
        with patch("scheduled_polls._polls_data_lock", fake_lock, create=True):
            with patch("scheduled_polls._load_polls_data", return_value=data.copy()):
                with patch("scheduled_polls._save_polls_data"):
                    await cog._handle_posting(date(2026, 5, 27))

        assert fake_lock.enter_count == 1

    asyncio.run(run())


def test_handle_reminders_pings_non_voters():
    async def run():
        bot = MagicMock()
        cog = ScheduledPollCog(bot)
        channel = AsyncMock()
        bot.get_channel.return_value = channel

        guild = MagicMock()
        role = MagicMock()
        voter = MagicMock()
        voter.bot = False
        voter.id = 111
        voter.mention = "<@111>"
        non_voter = MagicMock()
        non_voter.bot = False
        non_voter.id = 222
        non_voter.mention = "<@222>"
        role.members = [voter, non_voter]
        role.id = 200
        guild.get_role.return_value = role
        bot.get_guild.return_value = guild

        data = {
            "next_scheduled_poll_id": 2,
            "scheduled_polls": {
                "1": {
                    "channel_id": 100,
                    "role_id": 200,
                    "weekday": "Mittwoch",
                    "reminder_weekday": "Sonntag",
                    "reminder_hour": 18,
                    "active_instance": {
                        "message_id": 999,
                        "posted_at": "2026-05-27T08:00:00+02:00",
                        "target_week_start": "2026-06-01",
                        "reminded": False,
                        "responses": {"111": ["Montag"]},
                    },
                }
            },
        }

        with patch("scheduled_polls._load_polls_data", return_value=data.copy()):
            save_mock = MagicMock()
            with patch("scheduled_polls._save_polls_data", save_mock):
                await cog._handle_reminders(date(2026, 5, 31), 18)  # Sunday

        channel.send.assert_called_once()
        reminder = channel.send.call_args[0][0]
        assert "<@222>" in reminder
        assert "<@111>" not in reminder
        save_mock.assert_called_once()
        assert save_mock.call_args[0][0]["scheduled_polls"]["1"]["active_instance"]["reminded"] is True

    asyncio.run(run())

def test_trigger_reminder_forces_selected_poll_without_reminder_config():
    async def run():
        bot = MagicMock()
        cog = ScheduledPollCog(bot)
        dept_head_role = MagicMock()
        dept_head_role.id = 748509968172449802  # DEPARTMENT_HEAD_ROLE_ID
        interaction = MagicMock()
        interaction.user.roles = [dept_head_role]
        interaction.response.defer = AsyncMock()
        interaction.response.send_message = AsyncMock()
        interaction.followup.send = AsyncMock()

        with patch.object(cog, "_handle_reminders", AsyncMock()) as reminder_mock:
            data = {
                "next_scheduled_poll_id": 2,
                "scheduled_polls": {
                    "1": {
                        "channel_id": 100,
                        "role_id": 200,
                        "weekday": "Mittwoch",
                        "reminder_weekday": None,
                        "reminder_hour": None,
                        "active_instance": {
                            "message_id": 999,
                            "posted_at": "2026-05-27T08:00:00+02:00",
                            "target_week_start": "2026-06-01",
                            "reminded": True,
                            "responses": {},
                        },
                    }
                },
            }
            with patch("scheduled_polls._load_polls_data", return_value=data):
                with patch("scheduled_polls._save_polls_data"):
                    await ScheduledPollCog.trigger_reminder.callback(cog, interaction, 1)

        reminder_mock.assert_awaited_once()
        _, kwargs = reminder_mock.call_args
        assert kwargs["poll_id"] == "1"
        assert kwargs["force"] is True
        assert data["scheduled_polls"]["1"]["active_instance"]["reminded"] is False
        interaction.followup.send.assert_called_once_with(
            "✅ Trigger-Reminder für Umfrage #1 ausgeführt!"
        )

    asyncio.run(run())

def test_poll_button_allows_users_with_poll_role():
    async def run():
        view = ScheduledPollView("1")
        role = MagicMock()
        role.id = 200
        interaction = MagicMock()
        interaction.user.id = 111
        interaction.user.roles = [role]
        interaction.response.send_message = AsyncMock()
        interaction.response.edit_message = AsyncMock()

        data = {
            "next_scheduled_poll_id": 2,
            "scheduled_polls": {
                "1": {
                    "channel_id": 100,
                    "role_id": 200,
                    "weekday": "Mittwoch",
                    "active_instance": {
                        "message_id": 999,
                        "posted_at": "2026-05-27T08:00:00+02:00",
                        "target_week_start": "2026-06-01",
                        "reminded": False,
                        "responses": {},
                    },
                }
            },
        }

        with patch("scheduled_polls._load_polls_data", return_value=data):
            save_mock = MagicMock()
            with patch("scheduled_polls._save_polls_data", save_mock):
                await view.make_callback("Montag")(interaction)

        interaction.response.send_message.assert_not_called()
        interaction.response.edit_message.assert_called_once()
        save_mock.assert_called_once()
        assert data["scheduled_polls"]["1"]["active_instance"]["responses"] == {
            "111": ["Montag"]
        }

    asyncio.run(run())

def test_poll_button_rejects_users_without_poll_role():
    async def run():
        view = ScheduledPollView("1")
        role = MagicMock()
        role.id = 999
        interaction = MagicMock()
        interaction.user.id = 111
        interaction.user.roles = [role]
        interaction.response.send_message = AsyncMock()
        interaction.response.edit_message = AsyncMock()

        data = {
            "next_scheduled_poll_id": 2,
            "scheduled_polls": {
                "1": {
                    "channel_id": 100,
                    "role_id": 200,
                    "weekday": "Mittwoch",
                    "active_instance": {
                        "message_id": 999,
                        "posted_at": "2026-05-27T08:00:00+02:00",
                        "target_week_start": "2026-06-01",
                        "reminded": False,
                        "responses": {},
                    },
                }
            },
        }

        with patch("scheduled_polls._load_polls_data", return_value=data):
            save_mock = MagicMock()
            with patch("scheduled_polls._save_polls_data", save_mock):
                await view.make_callback("Montag")(interaction)

        interaction.response.send_message.assert_called_once_with(
            "❌ Nur Mitglieder der Umfrage-Rolle können abstimmen.",
            ephemeral=True,
        )
        interaction.response.edit_message.assert_not_called()
        save_mock.assert_not_called()
        assert data["scheduled_polls"]["1"]["active_instance"]["responses"] == {}

    asyncio.run(run())

def test_dev_triggers_allow_department_heads_without_extra_user_id_gate():
    async def run():
        bot = MagicMock()
        cog = ScheduledPollCog(bot)
        role = MagicMock()
        role.id = 748509968172449802  # DEPARTMENT_HEAD_ROLE_ID

        post_interaction = MagicMock()
        post_interaction.user.roles = [role]
        post_interaction.response.defer = AsyncMock()
        post_interaction.response.send_message = AsyncMock()
        post_interaction.followup.send = AsyncMock()

        reminder_interaction = MagicMock()
        reminder_interaction.user.roles = [role]
        reminder_interaction.response.defer = AsyncMock()
        reminder_interaction.response.send_message = AsyncMock()
        reminder_interaction.followup.send = AsyncMock()

        data = {"next_scheduled_poll_id": 1, "scheduled_polls": {}}
        with patch("scheduled_polls._load_polls_data", return_value=data):
            await ScheduledPollCog.trigger_post.callback(cog, post_interaction, 1)
            await ScheduledPollCog.trigger_reminder.callback(cog, reminder_interaction, 1)

        post_interaction.response.defer.assert_called_once_with(ephemeral=True)
        post_interaction.response.send_message.assert_not_called()
        post_interaction.followup.send.assert_called_once_with("❌ Umfrage #1 nicht gefunden.")
        reminder_interaction.response.defer.assert_called_once_with(ephemeral=True)
        reminder_interaction.response.send_message.assert_not_called()
        reminder_interaction.followup.send.assert_called_once_with("❌ Umfrage #1 nicht gefunden.")

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Tests for module setup entrypoint
# ---------------------------------------------------------------------------

def test_setup_registers_cog():
    async def run():
        bot = MagicMock()
        bot.add_cog = AsyncMock()
        await setup(bot)
        bot.add_cog.assert_called_once()
        cog = bot.add_cog.call_args[0][0]
        assert isinstance(cog, ScheduledPollCog)

    asyncio.run(run())
