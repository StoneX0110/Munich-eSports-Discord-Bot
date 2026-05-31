"""
Unit and integration tests for scheduled reminder helper and logic.
"""

import asyncio
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord

import config
from cogs import scheduled_reminders
from cogs.scheduled_reminders import (
    ScheduledReminderCog,
    _load_reminders_data,
    _save_reminders_data,
    setup,
)


def test_reminders_data_wrappers_delegate_to_configured_store():
    store = MagicMock()
    loaded_data = {"next_scheduled_reminder_id": 1, "scheduled_reminders": {}}
    saved_data = {
        "next_scheduled_reminder_id": 2,
        "scheduled_reminders": {"1": _base_reminder()},
    }
    store.load.return_value = loaded_data

    with patch("cogs.scheduled_reminders._reminders_store", return_value=store):
        assert _load_reminders_data() == loaded_data
        _save_reminders_data(saved_data)

    store.load.assert_called_once_with()
    store.save.assert_called_once_with(saved_data)


def test_reminder_list_reads_latest_file_data_without_cache(tmp_path):
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)
        interaction = _department_head_interaction()
        reminders_file = tmp_path / "scheduled_reminders.json"
        initial_data = {
            "next_scheduled_reminder_id": 2,
            "scheduled_reminders": {"1": _base_reminder(message="First reminder")},
        }
        updated_data = {
            "next_scheduled_reminder_id": 2,
            "scheduled_reminders": {"1": _base_reminder(message="Updated reminder")},
        }

        with patch("cogs.scheduled_reminders.REMINDERS_FILE", reminders_file):
            _save_reminders_data(initial_data)
            await ScheduledReminderCog.reminder_list.callback(cog, interaction)
            first_embed = interaction.response.send_message.call_args.kwargs["embed"]

            _save_reminders_data(updated_data)
            interaction.response.send_message.reset_mock()
            await ScheduledReminderCog.reminder_list.callback(cog, interaction)
            second_embed = interaction.response.send_message.call_args.kwargs["embed"]

        assert "**Nachricht:** First reminder" in first_embed.fields[0].value
        assert "**Nachricht:** Updated reminder" in second_embed.fields[0].value

    asyncio.run(run())


def test_scheduled_reminder_staff_role_comes_from_config():
    assert scheduled_reminders.STAFF_ROLE_ID == config.STAFF_ROLE_ID


def _department_head_interaction():
    role = MagicMock()
    role.id = config.DEPARTMENT_HEAD_ROLE_ID
    interaction = MagicMock()
    interaction.user.id = 123
    interaction.user.roles = [role]
    interaction.channel_id = 456
    interaction.response.send_message = AsyncMock()
    return interaction


def _staff_interaction():
    role = MagicMock()
    role.id = config.STAFF_ROLE_ID
    interaction = MagicMock()
    interaction.user.id = 123
    interaction.user.roles = [role]
    interaction.channel_id = 456
    interaction.response.send_message = AsyncMock()
    return interaction


def _unauthorized_interaction():
    role = MagicMock()
    role.id = 999
    interaction = MagicMock()
    interaction.user.id = 123
    interaction.user.roles = [role]
    interaction.channel_id = 456
    interaction.response.send_message = AsyncMock()
    return interaction


def _reminder_role():
    role = MagicMock()
    role.id = 200
    role.mention = "<@&200>"
    return role


def _base_reminder(**overrides):
    reminder = {
        "channel_id": 100,
        "role_id": 200,
        "weekday": "Mittwoch",
        "hour": 18,
        "message": "Scrim reminder",
        "created_by": 300,
        "created_at": "2026-05-30T12:00:00+02:00",
        "last_sent_date": None,
        "last_sent_at": None,
    }
    reminder.update(overrides)
    return reminder


def test_reminder_create_stores_valid_config():
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)
        interaction = _department_head_interaction()
        data = {"next_scheduled_reminder_id": 1, "scheduled_reminders": {}}

        with patch("cogs.scheduled_reminders._load_reminders_data", return_value=data):
            save_mock = MagicMock()
            with patch("cogs.scheduled_reminders._save_reminders_data", save_mock):
                await ScheduledReminderCog.reminder_create.callback(
                    cog,
                    interaction,
                    _reminder_role(),
                    "mittwoch",
                    18,
                    " Scrim reminder ",
                )

        saved_reminder = save_mock.call_args[0][0]["scheduled_reminders"]["1"]
        assert saved_reminder["channel_id"] == 456
        assert saved_reminder["role_id"] == 200
        assert saved_reminder["weekday"] == "Mittwoch"
        assert saved_reminder["hour"] == 18
        assert saved_reminder["message"] == "Scrim reminder"
        assert saved_reminder["created_by"] == 123
        assert saved_reminder["last_sent_date"] is None
        assert saved_reminder["last_sent_at"] is None
        response = interaction.response.send_message.call_args[0][0]
        assert response.startswith("✅ Wiederkehrender Reminder #1 erstellt!")
        assert "**Zeitplan:** Mittwoch um 18:00" in response

    asyncio.run(run())


def test_reminder_create_allows_staff_role():
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)
        interaction = _staff_interaction()
        data = {"next_scheduled_reminder_id": 1, "scheduled_reminders": {}}

        with patch("cogs.scheduled_reminders._load_reminders_data", return_value=data):
            save_mock = MagicMock()
            with patch("cogs.scheduled_reminders._save_reminders_data", save_mock):
                await ScheduledReminderCog.reminder_create.callback(
                    cog,
                    interaction,
                    _reminder_role(),
                    "Mittwoch",
                    18,
                    "Scrim reminder",
                )

        save_mock.assert_called_once()
        response = interaction.response.send_message.call_args[0][0]
        assert response.startswith("✅ Wiederkehrender Reminder #1 erstellt!")

    asyncio.run(run())


def test_reminder_create_rejects_unauthorized_user():
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)
        interaction = _unauthorized_interaction()

        await ScheduledReminderCog.reminder_create.callback(
            cog,
            interaction,
            _reminder_role(),
            "Mittwoch",
            18,
            "Scrim reminder",
        )

        interaction.response.send_message.assert_called_once()
        message = interaction.response.send_message.call_args[0][0]
        assert "Nur Abteilungsleiter oder Staff" in message
        assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True

    asyncio.run(run())


def test_reminder_create_rejects_invalid_weekday():
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)
        interaction = _department_head_interaction()

        await ScheduledReminderCog.reminder_create.callback(
            cog,
            interaction,
            _reminder_role(),
            "Wednesday",
            18,
            "Scrim reminder",
        )

        message = interaction.response.send_message.call_args[0][0]
        assert "Ungültiger Reminder-Wochentag" in message
        assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True

    asyncio.run(run())


def test_reminder_create_rejects_invalid_hour():
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)
        interaction = _department_head_interaction()

        await ScheduledReminderCog.reminder_create.callback(
            cog,
            interaction,
            _reminder_role(),
            "Mittwoch",
            24,
            "Scrim reminder",
        )

        message = interaction.response.send_message.call_args[0][0]
        assert "Ungültige Reminder-Uhrzeit" in message
        assert "`0` bis `23`" in message
        assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True

    asyncio.run(run())


def test_reminder_create_rejects_empty_message():
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)
        interaction = _department_head_interaction()

        await ScheduledReminderCog.reminder_create.callback(
            cog,
            interaction,
            _reminder_role(),
            "Mittwoch",
            18,
            "   ",
        )

        message = interaction.response.send_message.call_args[0][0]
        assert "Nachricht darf nicht leer sein" in message
        assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True

    asyncio.run(run())


def test_reminder_create_rejects_message_over_discord_limit():
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)
        interaction = _department_head_interaction()
        too_long = "x" * (2000 - len("<@&200>\n") + 1)

        await ScheduledReminderCog.reminder_create.callback(
            cog,
            interaction,
            _reminder_role(),
            "Mittwoch",
            18,
            too_long,
        )

        message = interaction.response.send_message.call_args[0][0]
        assert "Nachricht ist zu lang" in message
        assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True

    asyncio.run(run())


def test_reminder_list_shows_schedule_last_sent_and_preview():
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)
        interaction = _department_head_interaction()
        data = {
            "next_scheduled_reminder_id": 3,
            "scheduled_reminders": {
                "1": _base_reminder(last_sent_date="2026-05-27", last_sent_at="2026-05-27T18:00:00+02:00"),
                "2": _base_reminder(channel_id=101, role_id=201, weekday="Freitag", hour=5, message="Line 1\nLine 2"),
            },
        }

        with patch("cogs.scheduled_reminders._load_reminders_data", return_value=data):
            await ScheduledReminderCog.reminder_list.callback(cog, interaction)

        embed = interaction.response.send_message.call_args.kwargs["embed"]
        first, second = embed.fields
        assert "**Rolle:** <@&200>" in first.value
        assert "**Kanal:** <#100>" in first.value
        assert "**Zeitplan:** Mittwoch um 18:00" in first.value
        assert "**Zuletzt gesendet:** 2026-05-27" in first.value
        assert "**Nachricht:** Scrim reminder" in first.value
        assert "**Zeitplan:** Freitag um 05:00" in second.value
        assert "**Zuletzt gesendet:** nie" in second.value
        assert "**Nachricht:** Line 1 Line 2" in second.value
        assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True

    asyncio.run(run())


def test_reminder_delete_removes_existing_reminder():
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)
        interaction = _department_head_interaction()
        data = {
            "next_scheduled_reminder_id": 2,
            "scheduled_reminders": {"1": _base_reminder()},
        }

        with patch("cogs.scheduled_reminders._load_reminders_data", return_value=data):
            save_mock = MagicMock()
            with patch("cogs.scheduled_reminders._save_reminders_data", save_mock):
                await ScheduledReminderCog.reminder_delete.callback(cog, interaction, 1)

        assert data["scheduled_reminders"] == {}
        save_mock.assert_called_once()
        interaction.response.send_message.assert_called_once_with(
            "✅ Wiederkehrender Reminder #1 wurde gelöscht.",
            ephemeral=True,
        )

    asyncio.run(run())


def test_reminder_delete_reports_missing_reminder():
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)
        interaction = _department_head_interaction()
        data = {"next_scheduled_reminder_id": 1, "scheduled_reminders": {}}

        with patch("cogs.scheduled_reminders._load_reminders_data", return_value=data):
            await ScheduledReminderCog.reminder_delete.callback(cog, interaction, 1)

        interaction.response.send_message.assert_called_once_with(
            "❌ Wiederkehrender Reminder #1 nicht gefunden.",
            ephemeral=True,
        )

    asyncio.run(run())


def test_scheduled_reminder_loop_runs_hourly_checks():
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)

        with patch("cogs.scheduled_reminders.datetime") as datetime_mock:
            now = datetime(2026, 5, 27, 18, 42, tzinfo=ZoneInfo("Europe/Berlin"))
            datetime_mock.now.return_value = now
            cog._handle_sending = AsyncMock()

            await cog.scheduled_reminder_loop.coro(cog)

        cog._handle_sending.assert_called_once_with(date(2026, 5, 27), 18)

    asyncio.run(run())


def test_scheduled_reminder_loop_uses_hourly_interval():
    loop = ScheduledReminderCog.scheduled_reminder_loop
    assert loop.hours == 1.0
    assert loop.minutes == 0.0
    assert loop.seconds == 0.0
    assert loop.time is None


def test_handle_sending_on_matching_schedule_updates_state():
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)
        channel = AsyncMock()
        bot.get_channel.return_value = channel
        guild = MagicMock()
        role = _reminder_role()
        guild.get_role.return_value = role
        bot.get_guild.return_value = guild
        data = {
            "next_scheduled_reminder_id": 2,
            "scheduled_reminders": {"1": _base_reminder()},
        }

        with patch("cogs.scheduled_reminders._load_reminders_data", return_value=data):
            save_mock = MagicMock()
            with patch("cogs.scheduled_reminders._save_reminders_data", save_mock):
                sent = await cog._handle_sending(date(2026, 5, 27), 18)

        assert sent is True
        channel.send.assert_called_once()
        content = channel.send.call_args[0][0]
        kwargs = channel.send.call_args.kwargs
        assert content == "<@&200>\nScrim reminder"
        assert isinstance(kwargs["allowed_mentions"], discord.AllowedMentions)
        assert kwargs["allowed_mentions"].everyone is False
        assert kwargs["allowed_mentions"].users is False
        assert kwargs["allowed_mentions"].roles == [role]
        saved = save_mock.call_args[0][0]["scheduled_reminders"]["1"]
        assert saved["last_sent_date"] == "2026-05-27"
        assert saved["last_sent_at"] is not None

    asyncio.run(run())


def test_handle_sending_skips_mismatched_schedule():
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)
        channel = AsyncMock()
        bot.get_channel.return_value = channel
        data = {
            "next_scheduled_reminder_id": 2,
            "scheduled_reminders": {"1": _base_reminder()},
        }

        with patch("cogs.scheduled_reminders._load_reminders_data", return_value=data):
            save_mock = MagicMock()
            with patch("cogs.scheduled_reminders._save_reminders_data", save_mock):
                sent_wrong_hour = await cog._handle_sending(date(2026, 5, 27), 17)
                sent_wrong_day = await cog._handle_sending(date(2026, 5, 28), 18)

        assert sent_wrong_hour is False
        assert sent_wrong_day is False
        channel.send.assert_not_called()
        save_mock.assert_not_called()

    asyncio.run(run())


def test_handle_sending_skips_automatic_duplicate_for_same_date():
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)
        channel = AsyncMock()
        bot.get_channel.return_value = channel
        data = {
            "next_scheduled_reminder_id": 2,
            "scheduled_reminders": {
                "1": _base_reminder(last_sent_date="2026-05-27"),
            },
        }

        with patch("cogs.scheduled_reminders._load_reminders_data", return_value=data):
            save_mock = MagicMock()
            with patch("cogs.scheduled_reminders._save_reminders_data", save_mock):
                sent = await cog._handle_sending(date(2026, 5, 27), 18)

        assert sent is False
        channel.send.assert_not_called()
        save_mock.assert_not_called()

    asyncio.run(run())


def test_handle_sending_force_sends_despite_duplicate_for_same_date():
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)
        channel = AsyncMock()
        bot.get_channel.return_value = channel
        guild = MagicMock()
        role = _reminder_role()
        guild.get_role.return_value = role
        bot.get_guild.return_value = guild
        data = {
            "next_scheduled_reminder_id": 2,
            "scheduled_reminders": {
                "1": _base_reminder(last_sent_date="2026-05-27"),
            },
        }

        with patch("cogs.scheduled_reminders._load_reminders_data", return_value=data):
            save_mock = MagicMock()
            with patch("cogs.scheduled_reminders._save_reminders_data", save_mock):
                sent = await cog._handle_sending(date(2026, 5, 27), 18, reminder_id="1", force=True)

        assert sent is True
        channel.send.assert_called_once()
        save_mock.assert_called_once()

    asyncio.run(run())


def test_handle_sending_missing_channel_or_role_does_not_mark_sent():
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)
        bot.get_channel.return_value = None
        data = {
            "next_scheduled_reminder_id": 2,
            "scheduled_reminders": {"1": _base_reminder()},
        }

        with patch("cogs.scheduled_reminders._load_reminders_data", return_value=data):
            save_mock = MagicMock()
            with patch("cogs.scheduled_reminders._save_reminders_data", save_mock):
                sent = await cog._handle_sending(date(2026, 5, 27), 18)

        assert sent is False
        assert data["scheduled_reminders"]["1"]["last_sent_date"] is None
        save_mock.assert_not_called()

    asyncio.run(run())


def test_handle_sending_http_exception_does_not_mark_sent():
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)
        channel = AsyncMock()
        channel.send.side_effect = discord.HTTPException(MagicMock(), "boom")
        bot.get_channel.return_value = channel
        guild = MagicMock()
        role = _reminder_role()
        guild.get_role.return_value = role
        bot.get_guild.return_value = guild
        data = {
            "next_scheduled_reminder_id": 2,
            "scheduled_reminders": {"1": _base_reminder()},
        }

        with patch("cogs.scheduled_reminders._load_reminders_data", return_value=data):
            save_mock = MagicMock()
            with patch("cogs.scheduled_reminders._save_reminders_data", save_mock):
                sent = await cog._handle_sending(date(2026, 5, 27), 18)

        assert sent is False
        assert data["scheduled_reminders"]["1"]["last_sent_date"] is None
        save_mock.assert_not_called()

    asyncio.run(run())


def test_trigger_send_sends_selected_reminder_immediately():
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)
        interaction = _department_head_interaction()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        data = {
            "next_scheduled_reminder_id": 2,
            "scheduled_reminders": {"1": _base_reminder()},
        }

        with patch.object(cog, "_handle_sending", AsyncMock(return_value=True)) as send_mock:
            with patch("cogs.scheduled_reminders._load_reminders_data", return_value=data):
                await ScheduledReminderCog.trigger_send.callback(cog, interaction, 1)

        interaction.response.defer.assert_called_once_with(ephemeral=True)
        _, kwargs = send_mock.call_args
        assert kwargs["reminder_id"] == "1"
        assert kwargs["force"] is True
        interaction.followup.send.assert_called_once_with("✅ Trigger-Send für Reminder #1 ausgeführt!")

    asyncio.run(run())


def test_trigger_send_reports_missing_reminder():
    async def run():
        bot = MagicMock()
        cog = ScheduledReminderCog(bot)
        interaction = _department_head_interaction()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        data = {"next_scheduled_reminder_id": 1, "scheduled_reminders": {}}

        with patch("cogs.scheduled_reminders._load_reminders_data", return_value=data):
            await ScheduledReminderCog.trigger_send.callback(cog, interaction, 1)

        interaction.followup.send.assert_called_once_with("❌ Reminder #1 nicht gefunden.")

    asyncio.run(run())


def test_setup_registers_cog():
    async def run():
        bot = MagicMock()
        bot.add_cog = AsyncMock()
        await setup(bot)
        bot.add_cog.assert_called_once()
        cog = bot.add_cog.call_args[0][0]
        assert isinstance(cog, ScheduledReminderCog)

    asyncio.run(run())


def test_bot_loads_scheduled_reminders_extension():
    bot_source = Path("src/bot.py").read_text(encoding="utf-8")
    assert 'bot.load_extension("cogs.scheduled_reminders")' in bot_source
